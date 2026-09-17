import asyncio
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.tracker import browser_client
from app.tracker.discovery import Discoverer
from app.tracker.embedded_lists import extract
from app.tracker.models import Entry
from app.tracker.parser import has_dynamic_pagination, parse_page

SOURCE = "https://publisher.example/news/"
CONTROL_CASES = json.loads(
    (Path(__file__).parent / "fixtures/pagination-controls.json").read_text()
)
CONTROL_HTML = {case["name"]: case["html"] for case in CONTROL_CASES}


def rows(count=6):
    return [
        {
            "title": f"Release announcement {i}",
            "action": {"payload": {"url": f"/news/update-{i}"}},
            "publishedAt": f"2026-09-{i:02}T12:00:00Z",
            "category": {"title": "Updates"},
            "media": {"url": f"/image-{i}.jpg"},
        }
        for i in range(1, count + 1)
    ]


def listing():
    initial = "".join(
        f'<article><a href="/news/update-{i}">Release announcement {i}</a><time datetime="2026-09-{i:02}T12:00:00Z"></time></article>'
        for i in (1, 2)
    )
    return (
        "<html><title>News</title><main>"
        + initial
        + '</main><button>SHOW MORE</button><script type="application/json">'
        + json.dumps({"props": {"page": {"sections": [{"items": rows()}]}}})
        + "</script></html>"
    )


def test_nested_hydration_lists_recover_titles_dates_and_context_without_fetches():
    scan, pages, _ = parse_page(listing(), SOURCE)
    assert len(scan.entries) == 6
    assert len({e.url for e in scan.entries}) == 6
    assert all(e.published_at and "update-" in e.url for e in scan.entries)
    assert any(e.context == "Updates" for e in scan.entries)
    assert "embedded data" in scan.methods and not pages
    assert any("load-more control" in w for w in scan.warnings)


def test_hydration_requires_multiple_matching_visible_titles_and_valid_records():
    visible = {
        SOURCE + "update-1": ["Release announcement 1"],
        SOURCE + "update-2": ["Release announcement 2"],
    }
    assert len(extract({"items": rows()}, visible, SOURCE)) == 6
    assert not extract(
        {"items": rows()}, {SOURCE + "update-1": ["Release announcement 1"]}, SOURCE
    )
    assert not extract(
        {"items": rows()}, {u: ["Different title"] for u in visible}, SOURCE
    )
    invalid = rows()
    invalid[-1]["action"]["payload"]["url"] = "javascript:alert(1)"
    assert not extract({"items": invalid}, visible, SOURCE)
    undated = [{k: v for k, v in r.items() if k != "publishedAt"} for r in rows()]
    assert not extract({"items": undated}, visible, SOURCE)
    unrelated = rows()
    unrelated[-1]["action"]["payload"]["url"] = "http://127.0.0.1/admin"
    assert len(extract({"items": unrelated}, visible, SOURCE)) == 5


@pytest.mark.parametrize(
    "control",
    [
        "<button>SHOW MORE</button>",
        '<button aria-label="Show more articles"><svg/></button>',
        "<nav><button>Next</button></nav>",
        '<button aria-label="Next page">→</button>',
        '<a role="button" href="#">View more</a>',
        '<nav><a href="#" aria-label="Goto Next Page">Next</a></nav>',
    ],
)
def test_dynamic_control_detection(control):
    assert has_dynamic_pagination(BeautifulSoup(control, "html.parser"))


@pytest.mark.parametrize(
    "control",
    [
        "<p>We plan to show more articles soon.</p>",
        "<form><button>Show more</button></form>",
        "<button disabled>Show more</button>",
        '<button aria-disabled="true">Next page</button>',
        '<div class="carousel"><button>Next page</button></div>',
        "<button>Next</button>",
    ],
)
def test_non_listing_controls_are_not_clicked(control):
    assert not has_dynamic_pagination(BeautifulSoup(control, "html.parser"))


@pytest.mark.parametrize("case", CONTROL_CASES, ids=lambda case: case["name"])
def test_prose_expansion_and_listing_controls(case):
    assert (
        has_dynamic_pagination(BeautifulSoup(case["html"], "html.parser"))
        == case["pagination"]
    )


def chapter_listing(count=3):
    return (
        "<h1>A Traveler's Story</h1>"
        + CONTROL_HTML["desktop prose with button wrapper"]
        + CONTROL_HTML["anonymous mobile prose control"]
        + "<section><h2>3 Chapters</h2>"
        + "".join(
            f'<article><a href="/series/story/chapter/{i}">Chapter {i}</a><time datetime="2026-09-01"></time></article>'
            for i in range(1, count + 1)
        )
        + "</section>"
    )


def reviews():
    return (
        '<section id="reviews-pagination"><article><a class="review" href="/reviews/1">Reader review</a>'
        '<a href="/series/story/chapter/1">A reference to Chapter 1</a></article>'
        '<div><nav aria-label="Pagination"><div><button aria-label="Next page">Next</button>'
        "</div></nav></div></section>"
    )


def test_auxiliary_pagination_requires_all_tracked_links_outside_its_region():
    source = "https://publisher.example/series/story"
    soup = BeautifulSoup(chapter_listing() + reviews(), "html.parser")
    chapters = [Entry(f"{source}/chapter/{i}", f"Chapter {i}") for i in (1, 2, 3)]
    review = Entry("https://publisher.example/reviews/1", "Reader review")
    assert has_dynamic_pagination(soup)  # No selection context: remain conservative.
    assert not has_dynamic_pagination(soup, source, chapters)
    assert has_dynamic_pagination(soup, source, [review])
    assert has_dynamic_pagination(soup, source, chapters + [review])
    assert has_dynamic_pagination(soup, source, chapters + [Entry("", "Chapter 4")])
    assert has_dynamic_pagination(soup, source, [Entry(source + "/unseen", "Unseen")])
    assert has_dynamic_pagination(soup, source, [])


@pytest.mark.parametrize("region", ["comments", "reader-reviews", "discussion"])
async def test_complete_listing_skips_unrelated_reader_pagination(monkeypatch, region):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "fixture")
    source = "https://publisher.example/series/story"

    class Fetcher:
        async def get(self, url):
            return url, chapter_listing() + reviews().replace(
                "reviews-pagination", region
            )

    async def render(*args):
        pytest.fail(
            "Pagination of untracked reader responses must not start the browser"
        )

    monkeypatch.setattr(browser_client, "render", render)
    result = await Discoverer(Fetcher()).scan(source)
    assert len(result.entries) == result.expected_count == 3
    assert result.coverage == "complete"


def test_selectors_and_chapter_pagination_remain_eligible():
    source = "https://publisher.example/series/story"
    html = chapter_listing() + reviews()
    selected, _, _ = parse_page(
        html, source, selector="a[href*='/series/story/chapter/']"
    )
    assert len(selected.entries) == 3
    assert any("load-more control" in warning for warning in selected.warnings)
    chapters, _, _ = parse_page(
        html.replace('id="reviews-pagination"', 'id="chapters"'), source
    )
    assert any("load-more control" in warning for warning in chapters.warnings)


def test_auxiliary_pager_without_content_cannot_prove_selection_scope():
    source = "https://publisher.example/series/story"
    soup = BeautifulSoup(
        chapter_listing()
        + '<div id="comments-pager"><nav><button>Next page</button></nav></div>',
        "html.parser",
    )
    assert has_dynamic_pagination(
        soup, source, [Entry(source + "/chapter/1", "Chapter 1")]
    )


@pytest.mark.parametrize("deep", [False, True])
async def test_complete_chapter_listing_with_synopsis_does_not_start_browser(
    monkeypatch, deep
):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "fixture")
    requests = []

    class Fetcher:
        async def get(self, url):
            requests.append(url)
            return url, chapter_listing()

    async def render(*args):
        pytest.fail(
            "A prose expander must not start the browser for a complete listing"
        )

    monkeypatch.setattr(browser_client, "render", render)
    source = "https://publisher.example/series/story"
    result = await Discoverer(Fetcher()).scan(source, deep=deep)
    assert len(result.entries) == result.expected_count == 3
    assert result.coverage == "complete" and requests == [source]
    assert not any("load-more control" in warning for warning in result.warnings)


async def test_missing_chapters_still_use_browser_despite_synopsis_expanders(
    monkeypatch,
):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "fixture")
    calls = []

    class Fetcher:
        async def get(self, url):
            return url, chapter_listing(2)

    async def render(*args):
        calls.append(True)
        return {"snapshots": [chapter_listing()], "captured": []}

    monkeypatch.setattr(browser_client, "render", render)
    result = await Discoverer(Fetcher()).scan("https://publisher.example/series/story")
    assert calls == [True] and len(result.entries) == 3


@pytest.mark.parametrize(
    "url,allowed",
    [
        (SOURCE + "?page=2", True),
        (SOURCE + "?cursor=abc", True),
        (SOURCE + "page/2/", True),
        (SOURCE + "update-3", False),
        ("https://other.example/news/?page=2", False),
        (SOURCE + "?page=2&token=secret", False),
        (SOURCE + "?delete=1", False),
        ("http://127.0.0.1/news/?page=2", False),
        ("javascript:alert(1)", False),
    ],
)
def test_browser_navigation_requires_safe_listing_coordinates(url, allowed):
    assert bool(browser_client.pagination_target(url, SOURCE)) == allowed


@pytest.mark.asyncio
async def test_show_more_with_existing_links_automatically_uses_browser(monkeypatch):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "fixture")
    calls = []

    class Fetcher:
        async def get(self, url, **kwargs):
            return url, listing().split("<script")[0]

    async def render(*args):
        calls.append(True)
        return {"snapshots": [listing()], "captured": []}

    monkeypatch.setattr(browser_client, "render", render)
    result = await Discoverer(Fetcher()).scan(SOURCE)
    assert calls == [True] and len(result.entries) == 6


@pytest.mark.asyncio
async def test_hidden_hydration_records_skip_redundant_browser_but_full_refresh_can_check(
    monkeypatch,
):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "fixture")
    calls = []

    class Fetcher:
        async def get(self, url, **kwargs):
            return url, listing()

    async def render(*args):
        calls.append(True)
        return {"snapshots": [listing()], "captured": []}

    monkeypatch.setattr(browser_client, "render", render)
    result = await Discoverer(Fetcher()).scan(SOURCE)
    assert len(result.entries) == 6 and calls == []
    assert result.coverage == "partial" and "hydrated listing" in result.methods
    result = await Discoverer(Fetcher()).scan(SOURCE, deep=True)
    assert len(result.entries) == 6 and calls == [True]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.googletagmanager.com/gtm.js",
        "https://connect.facebook.net/en_US/fbevents.js",
        "https://www.datadoghq-browser-agent.com/rum.js",
    ],
)
def test_browser_skips_telemetry_resources(url):
    assert not browser_client.allowed_request(
        {"method": "GET", "resource": "script", "url": url}, SOURCE
    )


@pytest.mark.asyncio
async def test_browser_broker_approves_pagination_but_denies_article_navigation(
    monkeypatch,
):
    page2 = SOURCE + "?page=2"
    messages = [
        {"type": "navigate", "url": page2},
        {
            "type": "fetch",
            "id": 1,
            "method": "GET",
            "resource": "document",
            "url": page2,
        },
        {"type": "navigate", "url": SOURCE + "update-3"},
        {
            "type": "fetch",
            "id": 2,
            "method": "GET",
            "resource": "document",
            "url": SOURCE + "update-3",
        },
        {"type": "result", "snapshots": ["<p>Listing</p>"], "snapshot_urls": [page2]},
    ]
    reader = asyncio.StreamReader()
    for m in messages:
        reader.feed_data(json.dumps(m).encode() + b"\n")
    reader.feed_eof()

    class Writer:
        replies = []

        def write(self, raw):
            self.replies.append(json.loads(raw))

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    writer = Writer()

    async def connect(*args, **kwargs):
        return reader, writer

    class Fetcher:
        calls = []

        async def get(self, url):
            self.calls.append(url)
            return url, "<p>Page two</p>"

    fetcher = Fetcher()
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "fixture")
    monkeypatch.setattr(asyncio, "open_unix_connection", connect, raising=False)
    result = await browser_client.render(fetcher, SOURCE)
    assert result["snapshot_urls"] == [page2]
    assert fetcher.calls == [page2]
    assert writer.replies[1] == {"allowed": True}
    assert writer.replies[3] == {"allowed": False}
    assert writer.replies[4] == {"id": 2, "error": True}
