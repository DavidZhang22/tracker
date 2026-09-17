import asyncio
import json

import pytest

from app.tracker import browser_client
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.listing_recipes import cache_key, cached_listing, infer, learn
from app.tracker.models import Entry, Scan
from app.tracker.urls import DiscoveryError, RequestBudget, SafeFetcher, request_budget

SOURCE = "https://example.org/series"
API = "https://example.org/api/chapters?page=1"


def payload(start=1, following=None):
    return {
        "posts": [
            {
                "url": f"https://example.org/series/chapter/{i}",
                "title": f"Chapter {i}",
                "published_at": f"2026-09-{i:02}T00:00:00Z",
                "language": "English",
            }
            for i in range(start, start + 2)
        ],
        "links": {"next": following},
    }


def dom(start=1):
    return (
        "<html><title>Example series</title><body><main>"
        + "".join(
            f'<article><a href="/series/chapter/{i}">Chapter {i}</a><time datetime="2026-09-{i:02}T00:00:00Z">September {i}, 2026</time><span>English</span></article>'
            for i in range(start, start + 2)
        )
        + "</main></body></html>"
    )


class Listings:
    def __init__(self, pages):
        self.cache = FetchCache()
        self.pages, self.calls = pages, []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        return url, json.dumps(self.pages[url])


def rendered():
    return Scan(
        SOURCE,
        "Example series",
        entries=[Entry(row["url"], row["title"]) for row in payload()["posts"]],
    )


@pytest.mark.asyncio
async def test_learned_listing_validates_next_urls_and_refreshes_all_pages_without_html():
    page2 = "https://example.org/api/chapters?page=2"
    first = payload(following=page2)
    f = Listings({API: first, page2: payload(3)})
    result = await learn(f, SOURCE, [{"url": API, "data": first}], rendered(), 40)
    assert len(result.entries) == 4 and result.coverage == "complete"
    assert f.calls == [page2]
    assert f.cache.get(cache_key(SOURCE))["recipe"]
    f.calls.clear()
    result = await cached_listing(f, SOURCE, 40)
    assert f.calls == [API, page2]
    assert len(result.entries) == 4 and all(e.published_at for e in result.entries)


@pytest.mark.asyncio
async def test_changed_layout_invalidates_recipe_instead_of_saving_bad_links():
    f = Listings({API: payload()})
    recipe = infer({"url": API, "data": payload()}, SOURCE, rendered().entries)
    f.cache.put(cache_key(SOURCE), {"recipe": recipe})
    f.pages[API] = {"error": "API has changed"}
    assert await cached_listing(f, SOURCE, 40) is None
    assert not f.cache.get(cache_key(SOURCE)).get("recipe")


@pytest.mark.asyncio
async def test_private_browser_page_does_not_learn_or_reuse_shared_listing():
    f = Listings({API: payload()})
    token = request_budget.set(RequestBudget(cacheable=False))
    try:
        assert (
            await learn(f, SOURCE, [{"url": API, "data": payload()}], rendered(), 40)
            is None
        )
        assert not f.cache.get(cache_key(SOURCE))
        recipe = infer({"url": API, "data": payload()}, SOURCE, rendered().entries)
        f.cache.put(cache_key(SOURCE), {"recipe": recipe})
        assert await cached_listing(f, SOURCE, 40) is None
        assert f.calls == []
    finally:
        request_budget.reset(token)


@pytest.mark.asyncio
async def test_private_listing_page_prevents_saving_derived_recipe():
    page2 = "https://example.org/api/chapters?page=2"
    first = payload(following=page2)

    class PrivateListings(Listings):
        async def get(self, url, **kwargs):
            request_budget.get().cacheable = False
            return await super().get(url, **kwargs)

    f = PrivateListings({page2: payload(3)})
    token = request_budget.set(RequestBudget())
    try:
        result = await learn(f, SOURCE, [{"url": API, "data": first}], rendered(), 40)
        assert len(result.entries) == 4
        assert not f.cache.get(cache_key(SOURCE))
    finally:
        request_budget.reset(token)


@pytest.mark.parametrize(
    "following",
    [
        API,
        "https://evil.example/next",
        "http://169.254.169.254/latest/meta-data",
        "/delete",
        "/api/chapters?token=private",
        "/different-endpoint",
    ],
)
@pytest.mark.asyncio
async def test_bad_pagination_cannot_create_a_reusable_recipe(following):
    first = payload(following=following)
    f = Listings({API: first})
    result = await learn(f, SOURCE, [{"url": API, "data": first}], rendered(), 40)
    assert result is None and not f.cache.get(cache_key(SOURCE))
    assert f.calls == []


def test_recipe_requires_corresponding_links_and_explicit_pagination_metadata():
    assert (
        infer(
            {"url": API, "data": {"posts": payload()["posts"]}},
            SOURCE,
            rendered().entries,
        )
        is None
    )
    assert (
        infer(
            {"url": API, "data": payload()},
            SOURCE,
            [Entry(SOURCE + "/unrelated", "Other")],
        )
        is None
    )
    assert (
        infer(
            {"url": API + "&api_key=secret", "data": payload()},
            SOURCE,
            rendered().entries,
        )
        is None
    )


@pytest.mark.asyncio
async def test_browser_snapshots_merge_virtualized_rows_keep_dates_and_skip_learning_for_keywords(
    monkeypatch,
):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "test-worker")
    f = Listings({SOURCE: {}})
    browser_calls = []

    async def render(*args):
        browser_calls.append(True)
        return {
            "snapshots": [dom(1), dom(3)],
            "captured": [{"url": API, "data": payload()}],
        }

    monkeypatch.setattr(browser_client, "render", render)
    result = await Discoverer(f).scan(
        SOURCE, keywords="English", source_method="browser", deep=True
    )
    assert len(result.entries) == 4
    assert len({e.url for e in result.entries}) == 4 and all(
        e.published_at for e in result.entries
    )
    assert browser_calls == [True] and not f.cache.get(cache_key(SOURCE))
    assert result.coverage == "partial"


@pytest.mark.asyncio
async def test_automatic_scan_uses_browser_for_a_javascript_shell(monkeypatch):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "test-worker")
    shell = '<html><title>Example series</title><script src="/list.js"></script></html>'
    calls = []

    class HTML:
        async def get(self, url, **kwargs):
            return url, shell

    async def render(fetcher, source, initial):
        calls.append((source, initial))
        return {"snapshots": [dom()], "captured": []}

    monkeypatch.setattr(browser_client, "render", render)
    result = await Discoverer(HTML()).scan(SOURCE)
    assert calls == [(SOURCE, shell)]
    assert len(result.entries) == 2 and "Browser JavaScript" in result.methods
    assert not any("JavaScript may be unavailable" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_explicit_browser_error_is_clear_and_ordinary_pages_skip_the_worker(
    monkeypatch,
):
    monkeypatch.delenv("TRACKER_BROWSER_SOCKET", raising=False)
    with pytest.raises(DiscoveryError, match="not enabled"):
        await browser_client.render(Listings({}), SOURCE)
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "never-open-this")

    class HTML:
        async def get(self, url, **kwargs):
            return url, dom()

    assert len((await Discoverer(HTML()).scan(SOURCE)).entries) == 2


@pytest.mark.parametrize(
    "method,resource,url",
    [
        ("POST", "fetch", API),
        ("GET", "image", API),
        ("GET", "document", SOURCE + "/chapter/1"),
        ("GET", "fetch", API + "&token=secret"),
        ("GET", "fetch", "https://example.org/logout"),
        ("GET", "fetch", "file:///etc/passwd"),
        ("GET", "script", "http://user:pass@example.org/a.js"),
    ],
)
def test_broker_rejects_mutations_credentials_and_content_navigation(
    method, resource, url
):
    assert not browser_client.allowed_request(
        {"method": method, "resource": resource, "url": url}, SOURCE
    )


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/", "http://169.254.169.254/", "http://168.63.129.16/"]
)
@pytest.mark.asyncio
async def test_browser_broker_routes_private_targets_through_safe_fetcher(
    monkeypatch, url
):
    messages = [
        {"type": "fetch", "id": 1, "method": "GET", "resource": "fetch", "url": url},
        {"type": "result", "snapshots": []},
    ]
    reader = asyncio.StreamReader()
    for message in messages:
        reader.feed_data(json.dumps(message).encode() + b"\n")
    reader.feed_eof()

    class Writer:
        responses = []

        def write(self, raw):
            self.responses.append(json.loads(raw))

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    writer = Writer()

    async def connect(*args, **kwargs):
        return reader, writer

    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "fake")
    monkeypatch.setattr(asyncio, "open_unix_connection", connect, raising=False)
    await browser_client.render(SafeFetcher(), SOURCE)
    assert writer.responses[-1] == {"id": 1, "error": True}
