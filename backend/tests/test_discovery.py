import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.tracker.discovery import Discoverer, youtube_archive
from app.tracker.models import Entry, date_value, sequence_value
from app.tracker.parser import parse_feed, parse_page
from app.tracker.urls import (
    DiscoveryError,
    SafeFetcher,
    canonical_url,
    public_addresses,
)

ASURA = "https://asurascans.com/comics/the-nebulas-civilization-53fc8424"
RR = "https://www.royalroad.com/fiction/21220/mother-of-learning"
YT = "https://www.youtube.com/channel/UCRIgIJQWuBJ0Cv_VlU3USNA"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "raw,base,expected",
    [
        (
            "../chapter/2",
            "https://site.example/series/book/",
            "https://site.example/series/chapter/2",
        ),
        (
            "//site.example/post/2#comments",
            "https://site.example/",
            "https://site.example/post/2",
        ),
        (
            "/post/2?utm_source=x&b=2&a=1",
            "https://site.example",
            "https://site.example/post/2?a=1&b=2",
        ),
        (
            "https://youtu.be/abcdefghijk?t=20",
            "",
            "https://www.youtube.com/watch?v=abcdefghijk",
        ),
        (
            "https://www.youtube.com/shorts/abcdefghijk?feature=share",
            "",
            "https://www.youtube.com/watch?v=abcdefghijk",
        ),
        (
            "https://www.youtube.com/watch?v=abcdefghijk&list=PL123&index=2",
            "",
            "https://www.youtube.com/watch?v=abcdefghijk",
        ),
        ("https://site.example/?p=10", "", "https://site.example/?p=10"),
    ],
)
def test_canonical_forms(raw, base, expected):
    assert canonical_url(raw, base) == expected


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://u:p@site.example/",
        "https://site.example:9999",
        "https://[broken/",
        "https://site.example／other/",
        "https://" + "a" * 64 + ".example/",
        "#comments",
        "",
    ],
)
def test_unsafe_urls(url):
    with pytest.raises(DiscoveryError):
        canonical_url(url, "https://site.example")


@pytest.mark.parametrize(
    "title,url,expected",
    [
        ("Chapter 9.5", "/chapter/999", 9.5),
        ("Episode 100", "/video/123", 100),
        ("1. Good Morning Brother", "/chapter/301778/1-good-morning-brother", 1),
        ("Epilogue", "/fiction/21220/mother-of-learning/chapter/123456/epilogue", None),
        ("Afterword", "/post/999", None),
    ],
)
def test_numbers(title, url, expected):
    assert sequence_value(title, url) == expected


def test_real_asura_all_140_chapters_and_dates():
    result, _, _ = parse_page(
        (FIXTURES / "asura.html").read_text(encoding="utf-8"), ASURA
    )
    assert len(result.entries) == result.expected_count == 140
    assert {e.number for e in result.entries} == set(range(1, 141))
    assert all(e.published_at for e in result.entries)


def test_real_royalroad_all_109_including_unnumbered():
    result, _, _ = parse_page(
        (FIXTURES / "royalroad.html").read_text(encoding="utf-8"), RR
    )
    assert len(result.entries) == result.expected_count == 109
    assert result.entries[0].number == 1
    assert any(e.title == "Epilogue" and e.number is None for e in result.entries)
    assert all(e.published_at for e in result.entries)


async def test_asura_simultaneous_release_dates_keep_every_chapter_in_numeric_order():
    f = FakeFetcher({ASURA: (FIXTURES / "asura.html").read_text(encoding="utf-8")})
    result = await Discoverer(f).scan(ASURA)
    assert [e.number for e in result.entries] == list(range(1, 141))


def test_wrong_fiction_and_navigation_are_excluded():
    html = f'<nav><a href="{RR}/chapter/1/nav">Nav chapter</a></nav><a href="/fiction/999/other/chapter/1/no">Chapter 1</a><a href="{RR}/chapter/3/three">3. Three</a>'
    result, _, _ = parse_page(html, RR)
    assert len(result.entries) == 1 and result.entries[0].number == 3


def test_collapsed_sections_decimals_tracking_duplicates():
    html = '<details hidden><a href="/story/chapter-10">Chapter 10</a><a href="/story/chapter-9.5">Chapter 9.5</a><a href="/story/chapter-10?utm_source=home#comments">Chapter 10</a></details><footer><a href="/about">About</a></footer>'
    result, _, _ = parse_page(html, "https://site.example/story")
    assert len(result.entries) == 2
    assert {e.number for e in result.entries} == {9.5, 10}


def test_query_content_links_are_not_collapsed():
    result, _, _ = parse_page(
        '<article><a href="/?p=1">First post</a></article><article><a href="/?p=2">Second post</a></article>',
        "https://site.example/",
    )
    assert len(result.entries) == 2


def test_json_ld_and_embedded_next_page():
    html = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@graph": [
                    {
                        "@type": "BlogPosting",
                        "headline": "Entry",
                        "url": "/posts/one",
                        "datePublished": "2026-01-02",
                    }
                ],
                "nextPageUrl": "/blog?page=2",
            }
        )
        + "</script>"
    )
    result, pages, _ = parse_page(html, "https://site.example/blog")
    assert result.entries[0].published_at.startswith("2026-01-02")
    assert pages == ["https://site.example/blog?page=2"]


def test_youtube_embedded_video_formats():
    data = {
        "contents": [
            {
                "videoRenderer": {
                    "videoId": "abcdefghijk",
                    "title": {"runs": [{"text": "Video one"}]},
                }
            },
            {
                "videoRenderer": {
                    "videoId": "12345678901",
                    "title": {"simpleText": "Video two"},
                }
            },
        ]
    }
    result, _, _ = parse_page(
        "<script>var ytInitialData = " + json.dumps(data) + ";</script>", YT
    )
    assert len(result.entries) == 2 and result.entries[1].title == "Video two"


def test_custom_selector_and_path():
    html = '<div id="chosen"><a href="/essays/one">First</a><a href="/news/no">Other</a></div><a href="/essays/two">Second</a>'
    result, _, _ = parse_page(html, "https://site.example", "#chosen a", "/essays/")
    assert [e.title for e in result.entries] == ["First"]
    with pytest.raises(DiscoveryError):
        parse_page(html, "https://site.example", "[[[[")


def test_atom_namespaces_next_and_relative_urls():
    text = '<feed xmlns="http://www.w3.org/2005/Atom"><title>A blog</title><link rel="next" href="?page=2"/><entry><title>One</title><link href="/post/one"/><published>2026-09-01T12:00:00Z</published></entry></feed>'
    result, pages = parse_feed(text, "https://blog.example/feed")
    assert result.entries[0].url == "https://blog.example/post/one"
    assert result.entries[0].published_at == "2026-09-01T12:00:00+00:00"
    assert pages == ["https://blog.example/feed?page=2"]


def test_rss_rfc_dates_and_podcast_enclosure():
    text = '<rss><channel><title>Show</title><item><title>Episode 2</title><pubDate>Tue, 08 Sep 2026 12:00:00 GMT</pubDate><enclosure url="https://cdn.example/two.mp3"/></item></channel></rss>'
    result, _ = parse_feed(text, "https://site.example/feed")
    assert result.entries[0].number == 2 and result.entries[0].published_at.startswith(
        "2026-09-08"
    )


def test_json_feed_and_next_page():
    content = json.dumps(
        {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Blog",
            "next_url": "?page=2",
            "items": [
                {
                    "id": "one",
                    "url": "https://site.example/one",
                    "title": "One",
                    "date_published": "2026-09-01T10:00:00Z",
                }
            ],
        }
    )
    result, pages, _ = parse_page(content, "https://site.example/feed.json")
    assert len(result.entries) == 1 and result.entries[0].published_at
    assert pages == ["https://site.example/feed.json?page=2"]


async def test_royalroad_bom_feed_and_short_alias_deduplicate():
    html = (
        '<a href="'
        + RR
        + '/chapter/301778/one">1. One</a><link rel="alternate" type="application/rss+xml" href="/syndication/21220"/>'
    )
    feed = '\ufeff<?xml version="1.0"?><rss><channel><item><title>Mother of Learning - One</title><link>https://www.royalroad.com/fiction/chapter/301778</link><pubDate>Sun, 28 Oct 2018 21:34:43 GMT</pubDate></item></channel></rss>'
    f = FakeFetcher({RR: html, "https://www.royalroad.com/syndication/21220": feed})
    result = await Discoverer(f).scan(RR)
    assert (
        len(result.entries) == 1
        and result.entries[0].published_at
        and result.entries[0].title == "1. One"
    )
    assert not result.warnings


async def test_redirect_to_private_address_is_rejected_before_connecting():
    import httpx

    real_client = httpx.AsyncClient
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    async def resolve(host):
        if host == "127.0.0.1":
            raise DiscoveryError("Private target blocked")
        return ["93.184.216.34"]

    with (
        patch("app.tracker.urls.public_addresses", side_effect=resolve),
        patch(
            "app.tracker.urls.httpx.AsyncClient",
            side_effect=lambda **kw: real_client(
                transport=httpx.MockTransport(handler), **kw
            ),
        ),
        pytest.raises(DiscoveryError, match="Private target"),
    ):
        await SafeFetcher().get("https://site.example")
    assert len(requests) == 1
    assert (
        requests[0].url.host == "93.184.216.34"
        and requests[0].headers["host"] == "site.example"
    )
    assert requests[0].extensions["sni_hostname"] == b"site.example"


async def test_trailing_slash_redirect_preserves_relative_link_base():
    import httpx

    real_client = httpx.AsyncClient
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/blog":
            return httpx.Response(301, headers={"location": "/blog/"})
        return httpx.Response(
            200, text='<article><a href="entry">An entry</a></article>'
        )

    with (
        patch("app.tracker.urls.public_addresses", return_value=["93.184.216.34"]),
        patch(
            "app.tracker.urls.httpx.AsyncClient",
            side_effect=lambda **kw: real_client(
                transport=httpx.MockTransport(handler), **kw
            ),
        ),
    ):
        source, text = await SafeFetcher().get("https://site.example/blog")
    result, _, _ = parse_page(text, source)
    assert paths == ["/blog", "/blog/"]
    assert result.entries[0].url == "https://site.example/blog/entry"


def test_content_ref_parameter_is_preserved():
    assert (
        canonical_url("https://site.example/read?ref=2") != "https://site.example/read"
    )


def test_prefixed_feed_title_cannot_turn_royalroad_id_into_number():
    assert (
        sequence_value(
            "Mother of Learning - Epilogue",
            "https://www.royalroad.com/fiction/chapter/123456",
        )
        is None
    )


def test_youtube_redirect_route_never_runs_archive_subprocess():
    with patch("app.tracker.discovery.subprocess.run") as run:
        with pytest.raises(DiscoveryError):
            youtube_archive("https://www.youtube.com/redirect?q=http://127.0.0.1")
        run.assert_not_called()


async def test_youtube_playlist_scope_and_authored_order():
    source = "https://www.youtube.com/playlist?list=PLtest"
    html = (
        "<script>var ytInitialData = "
        + json.dumps({"videoId": "unrelated00", "title": {"simpleText": "Suggested"}})
        + "</script>"
    )

    def loader(url):
        return (
            "Playlist",
            [
                Entry("https://www.youtube.com/watch?v=abcdefghijk", "First"),
                Entry("https://www.youtube.com/watch?v=12345678901", "Second"),
            ],
            [],
        )

    f = FakeFetcher({source: html})
    result = await Discoverer(f, youtube_loader=loader).scan(source)
    assert [e.title for e in result.entries] == ["First", "Second"]
    assert f.calls == [source]


def test_no_invented_relative_dates_and_entity_rejected():
    assert date_value("3 days ago") is None
    assert date_value("123") is None
    with pytest.raises(DiscoveryError):
        parse_feed("<!DOCTYPE foo><rss/>", "https://site.example")


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def get(self, url):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return url, value


async def test_pagination_cycles_and_numeric_sort():
    root = "https://site.example/story"
    fetcher = FakeFetcher(
        {
            root: '<a href="/story/chapter-10">Chapter 10</a><a rel="next" href="?page=2">Next</a>',
            root
            + "?page=2": '<details><a href="/story/chapter-2">Chapter 2</a></details><a rel="next" href="/story">Next</a>',
        }
    )
    result = await Discoverer(fetcher).scan(root)
    assert [e.number for e in result.entries] == [2, 10]
    assert len(fetcher.calls) == 2


async def test_failed_pagination_returns_visible_warning_and_saved_results():
    root = "https://site.example/story"
    f = FakeFetcher(
        {
            root: '<a href="/chapter/1">Chapter 1</a><a rel="next" href="?page=2">Next</a>',
            root + "?page=2": DiscoveryError("Blocked"),
        }
    )
    result = await Discoverer(f).scan(root)
    assert len(result.entries) == 1 and any("Blocked" in w for w in result.warnings)


async def test_page_limit_and_expected_count_warning():
    root = "https://site.example/story"
    f = FakeFetcher(
        {
            root: '<h2>50 Chapters</h2><a href="/chapter/1">Chapter 1</a><a rel="next" href="?page=2">Next</a>'
        }
    )
    result = await Discoverer(f, max_pages=1).scan(root)
    assert any("limit" in w for w in result.warnings) and any(
        "reports 50" in w for w in result.warnings
    )


async def test_feed_and_html_merge_without_duplicate():
    root = "https://site.example/blog"
    f = FakeFetcher(
        {
            root: '<link rel="alternate" type="application/rss+xml" href="/feed"/><article><a href="/posts/one">One</a></article>',
            "https://site.example/feed": "<rss><channel><item><title>One</title><link>https://site.example/posts/one?utm_medium=feed</link><pubDate>Tue, 08 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>",
        }
    )
    result = await Discoverer(f).scan(root)
    assert len(result.entries) == 1 and result.entries[0].published_at


async def test_youtube_continuation_loader_and_partial_warning():
    f = FakeFetcher(
        {
            YT: "<title>Channel</title>",
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCRIgIJQWuBJ0Cv_VlU3USNA": DiscoveryError(
                "Feed unavailable"
            ),
        }
    )

    def loader(url):
        return (
            "Channel",
            [Entry("https://www.youtube.com/watch?v=abcdefghijk", "One")],
            [],
        )

    result = await Discoverer(f, youtube_loader=loader).scan(YT)
    assert len(result.entries) == 1 and "YouTube archive" in result.methods


def test_youtube_nested_archive_flattening(monkeypatch):
    monkeypatch.setenv("TRACKER_YOUTUBE_ARCHIVE", "1")
    data = {
        "title": "Channel",
        "entries": [
            {"entries": [{"id": "abcdefghijk", "title": "One"}]},
            {"id": "12345678901", "title": "Two"},
        ],
    }
    with patch("app.tracker.discovery.subprocess.run") as run:
        run.return_value.stdout = json.dumps(data)
        run.return_value.stderr = ""
        run.return_value.returncode = 0
        _title, entries, _warnings = youtube_archive(YT)
        assert len(entries) == 2 and "--skip-download" in run.call_args.args[0]


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "169.254.169.254", "::1", "10.0.0.1"]
)
async def test_private_network_blocked(host):
    with pytest.raises(DiscoveryError):
        await public_addresses(host)


async def test_mixed_public_private_dns_blocked():
    with (
        patch.object(
            asyncio.get_running_loop(),
            "getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("8.8.8.8", 443)),
                (2, 1, 6, "", ("127.0.0.1", 443)),
            ],
        ),
        pytest.raises(DiscoveryError),
    ):
        await public_addresses("site.example")


async def test_arxiv_atom_feed_uses_abstract_links_and_publication_dates_only():
    source = canonical_url(
        "https://export.arxiv.org/api/query?search_query=all:language&max_results=2"
    )
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <id>http://arxiv.org/api/example</id>
      <title>ArXiv Query: search_query=all:language</title>
      <link href="https://export.arxiv.org/api/query?search_query=all:language"
            rel="self" type="application/atom+xml"/>
      <opensearch:totalResults>1200</opensearch:totalResults>
      <entry>
        <id>http://arxiv.org/abs/2609.12345v1</id>
        <updated>2026-09-19T12:00:00Z</updated>
        <published>2026-09-17T09:00:00Z</published>
        <title>A synthetic language study</title>
        <summary>Publication metadata for feed parser regression coverage.</summary>
        <link title="pdf" href="http://arxiv.org/pdf/2609.12345v1"
              rel="related" type="application/pdf"/>
        <link href="http://arxiv.org/abs/2609.12345v1" rel="alternate"
              type="text/html"/>
      </entry>
      <entry>
        <id>http://arxiv.org/abs/2609.10001v2</id>
        <updated>2026-09-20T12:00:00Z</updated>
        <published>2026-09-16T10:30:00Z</published>
        <title>A synthetic compiler study</title>
        <link href="http://arxiv.org/abs/2609.10001v2" rel="alternate"
              type="text/html"/>
        <link title="pdf" href="http://arxiv.org/pdf/2609.10001v2"
              rel="related" type="application/pdf"/>
      </entry>
    </feed>"""
    fetcher = FakeFetcher({source: feed})
    result = await Discoverer(fetcher).scan(source)
    assert result.methods == ["feed"]
    assert [(entry.url, entry.published_at) for entry in result.entries] == [
        ("http://arxiv.org/abs/2609.10001v2", "2026-09-16T10:30:00+00:00"),
        ("http://arxiv.org/abs/2609.12345v1", "2026-09-17T09:00:00+00:00"),
    ]
    assert all(entry.date_kind == "published" for entry in result.entries)
    assert fetcher.calls == [source]
    assert any(
        "full historical archive is not guaranteed" in w for w in result.warnings
    )
