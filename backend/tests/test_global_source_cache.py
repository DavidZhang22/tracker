import asyncio
import sqlite3
import time
from contextlib import closing
from unittest.mock import AsyncMock

import httpx
import pytest

from app.tracker.cache import FetchCache, cache_epochs
from app.tracker.discovery import Discoverer
from app.tracker.source_cache import MAX_NEW_SOURCES, SharedWork, identity
from app.tracker.urls import (
    DiscoveryError,
    RequestBudget,
    SafeFetcher,
    request_budget,
    response_key,
)

SOURCE = "https://books.example/comics/story-6f7fe6eb"
ALIAS = "https://books.example/comics/story-6f7fe6e"
HTML = '<main><article><h2><a href="/chapter/1">Chapter 1</a></h2><span lang="en">English</span><time datetime="2026-09-16"></time></article><article><h2><a href="/chapter/2">Chapter 2</a></h2><span lang="fr">French</span><time datetime="2026-09-15"></time></article></main>'


@pytest.fixture
def network(monkeypatch):
    calls = []
    state = {"headers": {}, "status": 200, "body": HTML}
    client = httpx.AsyncClient

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0.005)
        path = request.url.path
        if path.endswith("6f7fe6e") or "-random-" in path:
            return httpx.Response(301, headers={"Location": SOURCE})
        return httpx.Response(
            state["status"], text=state["body"], headers=state["headers"]
        )

    monkeypatch.setattr(
        "app.tracker.urls.public_addresses", AsyncMock(return_value=["93.184.216.34"])
    )
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return calls, state


@pytest.mark.parametrize("alias_first", [False, True])
async def test_alias_and_destination_share_scan_across_libraries_and_restart(
    tmp_path, network, monkeypatch, alias_first
):
    calls, _ = network
    path = tmp_path / "shared.db"
    analyzed = []
    analyze = Discoverer._analyze_page

    async def counted(self, *args):
        analyzed.append(args[1])
        return await analyze(self, *args)

    monkeypatch.setattr(Discoverer, "_analyze_page", counted)
    first_url, second_url = (ALIAS, SOURCE) if alias_first else (SOURCE, ALIAS)
    first = await Discoverer(SafeFetcher(FetchCache(path), interval=0)).scan(
        first_url, deep=True
    )
    other = Discoverer(SafeFetcher(FetchCache(path), interval=0))
    second = await other.scan(second_url, deep=True)
    third = await other.scan(ALIAS, deep=True)
    assert first.entries == second.entries == third.entries
    assert len(calls) == 2 and analyzed == [SOURCE]
    assert second.cached and third.cached and third.requests_made == 0
    assert first.checked_at == second.checked_at == third.checked_at
    second.entries[0].title = "Library-local change"
    assert (await other.scan(SOURCE)).entries == first.entries


async def test_concurrent_unknown_alias_and_destination_analyze_once(
    tmp_path, network, monkeypatch
):
    path = tmp_path / "shared.db"
    calls, _ = network
    analyzed = []
    analyze = Discoverer._analyze_page

    async def counted(self, *args):
        analyzed.append(args[1])
        await asyncio.sleep(0.04)
        return await analyze(self, *args)

    monkeypatch.setattr(Discoverer, "_analyze_page", counted)
    scanners = [Discoverer(SafeFetcher(FetchCache(path), interval=0)) for _ in range(4)]
    results = await asyncio.gather(
        *(
            scanners[i % 4].scan(ALIAS if i % 2 else SOURCE, deep=True)
            for i in range(12)
        )
    )
    assert len(calls) == 2 and analyzed == [SOURCE]
    assert all(r.entries == results[0].entries for r in results)


async def test_five_minute_expiry_is_not_extended_by_hits(
    tmp_path, network, monkeypatch
):
    now = [time.time()]
    monkeypatch.setattr("time.time", lambda: now[0])
    scanner = Discoverer(SafeFetcher(FetchCache(tmp_path / "cache.db"), interval=0))
    first = await scanner.scan(SOURCE, deep=True)
    now[0] += 299
    hit = await scanner.scan(SOURCE, deep=True)
    assert hit.cached and hit.checked_at == first.checked_at and len(network[0]) == 1
    now[0] += 1.01
    assert not (await scanner.scan(SOURCE, deep=True)).cached
    assert len(network[0]) == 2


async def test_different_filters_reuse_body_without_sharing_selection(network):
    scanner = Discoverer(SafeFetcher(interval=0))
    english = await scanner.scan(SOURCE, keywords="English", deep=True)
    french = await scanner.scan(SOURCE, keywords="French", deep=True)
    all_links = await scanner.scan(SOURCE, deep=True)
    assert [e.number for e in english.entries] == [1]
    assert [e.number for e in french.entries] == [2]
    assert len(all_links.entries) == 2 and len(network[0]) == 1


async def test_cached_source_does_not_wait_for_another_download_on_same_host(
    monkeypatch,
):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    client = httpx.AsyncClient

    async def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/slow":
            entered.set()
            await release.wait()
        return httpx.Response(200, text=HTML)

    monkeypatch.setattr(
        "app.tracker.urls.public_addresses", AsyncMock(return_value=["93.184.216.34"])
    )
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    fetcher = SafeFetcher(interval=0)
    expected = await fetcher.get(SOURCE)
    slow = asyncio.create_task(fetcher.get("https://books.example/slow"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert await asyncio.wait_for(fetcher.get(SOURCE), 1) == expected
        assert not slow.done()
        assert calls == ["/comics/story-6f7fe6eb", "/slow"]
    finally:
        release.set()
        await slow


async def test_queued_host_request_respects_new_backoff(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    client = httpx.AsyncClient

    async def respond(request):
        calls.append(request.url.path)
        entered.set()
        await release.wait()
        return httpx.Response(429, headers={"Retry-After": "600"})

    monkeypatch.setattr(
        "app.tracker.urls.public_addresses", AsyncMock(return_value=["93.184.216.34"])
    )
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    fetcher = SafeFetcher(interval=0)
    first = asyncio.create_task(fetcher.get(SOURCE))
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(fetcher.get("https://books.example/other"))
    try:
        await asyncio.sleep(0.03)
        assert not second.done()
    finally:
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
    assert len(calls) == 1
    assert all(isinstance(r, DiscoveryError) for r in results)
    assert "HTTP 429" in str(results[0]) and "requested a pause" in str(results[1])


async def test_same_suffix_different_series_and_query_variants_do_not_collide(network):
    scanner = Discoverer(SafeFetcher(interval=0))
    urls = [
        SOURCE,
        SOURCE.replace("story-", "different-story-"),
        SOURCE + "?page=2",
        SOURCE.replace("books.example", "other.example"),
    ]
    for url in urls:
        await scanner.scan(url, deep=True)
    assert len(network[0]) == len(urls)
    assert len({response_key(url) for url in urls}) == len(urls)
    await scanner.scan(SOURCE + "?utm_source=random#fragment", deep=True)
    assert len(network[0]) == len(urls)


async def test_evicted_body_still_cannot_be_refetched_by_another_library(
    tmp_path, network
):
    path = tmp_path / "cache.db"
    await SafeFetcher(FetchCache(path), interval=0).get(SOURCE)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM cache")
    with pytest.raises(DiscoveryError, match="checked recently"):
        await SafeFetcher(FetchCache(path), interval=0).get(SOURCE)
    assert len(network[0]) == 1


@pytest.mark.parametrize("disk", [False, True])
async def test_erasure_cannot_reset_fetch_cooldown_or_retain_response(
    network, tmp_path, disk
):
    cache = FetchCache(tmp_path / "cache.db" if disk else None)
    fetcher = SafeFetcher(cache, interval=0)
    await fetcher.get(SOURCE)
    cache.clear()
    assert cache.get(response_key(SOURCE)) is None
    with pytest.raises(DiscoveryError, match="checked recently"):
        await fetcher.get(SOURCE)
    assert len(network[0]) == 1


@pytest.mark.parametrize("control", ["no-store", "private, max-age=300"])
async def test_private_response_and_derived_scans_are_not_shared(network, control):
    calls, state = network
    state["headers"] = {"Cache-Control": control}
    fetcher = SafeFetcher(interval=0)
    assert (await Discoverer(fetcher).scan(SOURCE)).entries
    assert not fetcher.cache.memory
    with pytest.raises(DiscoveryError, match="checked recently"):
        await Discoverer(fetcher).scan(SOURCE, deep=True)
    assert len(calls) == 1


async def test_redirect_handoff_preserves_original_request_and_byte_budget(
    network, monkeypatch
):
    sizes = []
    take = RequestBudget.take_bytes

    def counted(self, size):
        sizes.append(size)
        return take(self, size)

    monkeypatch.setattr(RequestBudget, "take_bytes", counted)
    scanner = Discoverer(SafeFetcher(interval=0), max_pages=2)
    first = await scanner.scan(ALIAS, deep=True)
    assert first.requests_made == 2 and len(network[0]) == 2
    assert len(first.entries) == 2
    assert sum(sizes) == len(HTML.encode())


async def test_cancelled_network_work_retains_cooldown(network, monkeypatch):
    fetcher = SafeFetcher(interval=0)
    entered = asyncio.Event()

    async def cancelled_request(*args):
        args[-1][0] = True
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(fetcher, "_request", cancelled_request)
    task = asyncio.create_task(fetcher.get(SOURCE))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(DiscoveryError, match="checked recently"):
        await fetcher.get(SOURCE)


async def test_exhausted_budget_does_not_cool_down_an_unrequested_url(network):
    fetcher = SafeFetcher(interval=0)
    token = request_budget.set(RequestBudget(0))
    try:
        with pytest.raises(DiscoveryError, match="request budget"):
            await fetcher.get(SOURCE)
    finally:
        request_budget.reset(token)
    assert not network[0]
    await fetcher.get(SOURCE)
    assert len(network[0]) == 1


async def test_private_redirect_response_can_be_used_once_without_refetch(network):
    network[1]["headers"] = {"Cache-Control": "no-store"}
    fetcher = SafeFetcher(interval=0)
    first = await Discoverer(fetcher).scan(ALIAS, deep=True)
    assert len(first.entries) == 2 and first.requests_made == 2
    assert len(network[0]) == 2 and not fetcher.cache.memory


async def test_cache_database_failure_cannot_trigger_uncached_fetch(
    network, monkeypatch
):
    fetcher = SafeFetcher(interval=0)

    def unavailable(*_):
        raise sqlite3.OperationalError("test-only outage")

    monkeypatch.setattr(fetcher.cache.coordinator, "claim", unavailable)
    with pytest.raises(sqlite3.OperationalError):
        await fetcher.get(SOURCE)
    assert not network[0]


async def test_error_and_empty_result_cooldowns_prevent_retry_storms(network):
    calls, state = network
    fetcher = SafeFetcher(interval=0)
    state["status"] = 404
    for _ in range(2):
        with pytest.raises(DiscoveryError, match="Could not load"):
            await fetcher.get(SOURCE)
    assert len(calls) == 1
    state.update(status=200, body="<main>No entries yet</main>")
    scanner = Discoverer(fetcher)
    first = await scanner.scan("https://empty.example/")
    second = await scanner.scan("https://empty.example/", deep=True)
    assert not first.entries and second.cached and len(calls) == 2


async def test_credentialed_responses_are_partitioned_and_redirects_never_forward_secrets(
    network,
):
    fetcher = SafeFetcher(interval=0)
    await fetcher.get(SOURCE, secret_query={"key": "one"})
    await fetcher.get(SOURCE, secret_query={"key": "two"})
    await fetcher.get(SOURCE)
    await fetcher.get(SOURCE, secret_query={"key": "one"})
    assert len(network[0]) == 3
    with pytest.raises(DiscoveryError, match="Credentials were not forwarded"):
        await fetcher.get(ALIAS, secret_query={"key": "secret"})
    assert len(network[0]) == 4
    assert fetcher.cache.coordinator.resolve(ALIAS) == ALIAS


async def test_redirect_to_private_network_is_not_remembered(network, monkeypatch):
    calls, _ = network

    async def addresses(host):
        if len(calls):
            raise DiscoveryError(
                "Local and private network addresses cannot be scanned."
            )
        return ["93.184.216.34"]

    monkeypatch.setattr("app.tracker.urls.public_addresses", addresses)
    fetcher = SafeFetcher(interval=0)
    with pytest.raises(DiscoveryError, match="private"):
        await fetcher.get(ALIAS)
    assert len(calls) == 1 and fetcher.cache.coordinator.resolve(ALIAS) == ALIAS


async def test_random_suffixes_have_shared_host_budget_and_do_not_rescan_target(
    tmp_path, network
):
    path = tmp_path / "shared.db"
    # A known source remains usable even after unknown-URL admission is exhausted.
    first = Discoverer(SafeFetcher(FetchCache(path), interval=0))
    await first.scan(SOURCE, deep=True)
    for index in range(MAX_NEW_SOURCES - 1):
        other = Discoverer(SafeFetcher(FetchCache(path), interval=0))
        result = await other.scan(SOURCE + f"-random-{index}", deep=True)
        assert result.cached
    with pytest.raises(DiscoveryError, match="Too many new source URLs"):
        await first.scan(SOURCE + "-random-overflow", deep=True)
    assert len(network[0]) == MAX_NEW_SOURCES
    assert sum(r.url.path.endswith("6f7fe6eb") for r in network[0]) == 1
    assert (await first.scan(SOURCE, deep=True)).cached


@pytest.mark.parametrize("disk", [False, True])
def test_clear_erases_aliases_and_inflight_writes_but_keeps_anonymous_host_counts(
    tmp_path, disk
):
    cache = FetchCache(tmp_path / "cache.db" if disk else None)
    coordinator = cache.coordinator
    coordinator.remember(ALIAS, SOURCE, 300)
    coordinator.admit_source("books.example")
    coordinator.claim("test", "owner")
    token = cache_epochs.set({id(cache): cache.generation})
    try:
        cache.clear()
        coordinator.remember(ALIAS, SOURCE, 300)
        coordinator.finish("test", "owner", 300)
        assert coordinator.resolve(ALIAS) == ALIAS
        with pytest.raises(DiscoveryError, match="cache reset"):
            coordinator.claim("new", "owner")
    finally:
        cache_epochs.reset(token)
    for _ in range(MAX_NEW_SOURCES - 1):
        coordinator.admit_source("books.example")
    with pytest.raises(DiscoveryError, match="Too many"):
        coordinator.admit_source("www.books.example")


def test_expired_owner_and_bounded_metadata(tmp_path, monkeypatch):
    coordinator = FetchCache(tmp_path / "cache.db").coordinator
    now = [time.time()]
    monkeypatch.setattr("time.time", lambda: now[0])
    assert coordinator.claim("key", "crashed", lease=60)[0] == "owner"
    now[0] += 61
    assert coordinator.claim("key", "other")[0] == "recent"
    now[0] += 240
    assert coordinator.claim("key", "other")[0] == "owner"
    coordinator.finish("key", "crashed", 0)
    assert coordinator.claim("key", "third")[0] == "busy"
    monkeypatch.setattr("app.tracker.source_cache.MAX_RECORDS", 1)
    with pytest.raises(DiscoveryError, match="cache is busy"):
        coordinator.claim("another", "owner")
    assert identity("https://books.example/private-query") not in str(coordinator.gates)


async def test_cancelled_waiter_does_not_release_another_owner(tmp_path):
    coordinator = FetchCache(tmp_path / "cache.db").coordinator
    coordinator.claim("key", "leader")

    async def waiting():
        async with SharedWork(coordinator, "key"):
            pytest.fail("Waiter must not own this lease")

    waiter = asyncio.create_task(waiting())
    await asyncio.sleep(0.03)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert coordinator.claim("key", "third")[0] == "busy"


@pytest.mark.parametrize(
    "status, message",
    [
        (400, "HTTP 400"),
        (404, "HTTP 404: page not found"),
        (406, "refused access (HTTP 406)"),
        (410, "HTTP 410: page no longer available"),
        (500, "server returned an error (HTTP 500)"),
        (502, "server returned an error (HTTP 502)"),
    ],
)
async def test_http_errors_preserve_status_and_shared_cooldown(
    tmp_path, network, status, message
):
    calls, state = network
    state.update(status=status, body="")
    path = tmp_path / "cache.db"
    for _ in range(2):
        with pytest.raises(DiscoveryError) as error:
            await SafeFetcher(FetchCache(path), interval=0).get(SOURCE)
        assert message in str(error.value)
        assert "HTTPStatusError" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "source, guidance",
    [
        (
            "https://arxiv.org/search/?searchtype=all&query=domain+specific+language&abstracts=show&size=200&order=",
            "Automated access should use its official API or RSS feed",
        ),
        (
            "https://export.arxiv.org/api/query?search_query=all:language",
            "The arXiv API refused this request",
        ),
        ("https://arxiv.org.unrelated.example/search/", "The source refused access"),
    ],
)
async def test_arxiv_denial_guidance_does_not_retry_or_assume_api_access(
    network, source, guidance
):
    calls, state = network
    state.update(status=406, body="")
    fetcher = SafeFetcher(interval=0)
    for _ in range(2):
        with pytest.raises(DiscoveryError) as error:
            await fetcher.get(source)
        assert "HTTP 406" in str(error.value)
        assert guidance in str(error.value)
    assert len(calls) == 1
    if "unrelated" not in source:
        assert "import links from a file" in str(error.value)
    with pytest.raises(DiscoveryError, match="requested a pause"):
        await fetcher.get(source + "&another=1")
    assert len(calls) == 1
