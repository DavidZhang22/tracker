import asyncio
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.tracker.arxiv import parse, scan_arxiv
from app.tracker.arxiv_urls import request_url, translate
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.source_methods import detect_source_method
from app.tracker.urls import (
    DiscoveryError,
    SafeFetcher,
    canonical_url,
    content_key,
    response_key,
)

EXACT = "https://export.arxiv.org/api/query?search_query=all:%22domain%20specific%20language%22&start=0&max_results=200"
HOFFMANN = "https://arxiv.org/search/cs?query=Hoffmann+et+al.+2022&searchtype=all&abstracts=show&order=&size=200"


def feed(ids=("2203.15556v1",), total=None, start=0):
    rows = "".join(
        f"""<entry><id>http://arxiv.org/abs/{i}</id><title>Paper {i}</title>
    <published>2022-03-29T12:00:00Z</published><updated>2023-01-01T12:00:00Z</updated>
    <summary>An abstract about language models.</summary><author><name>Hoffmann</name></author>
    <category term="cs.LG"/><link href="https://arxiv.org/pdf/{i}" title="pdf"/>
    </entry>"""
        for i in ids
    )
    return f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
    <opensearch:totalResults>{len(ids) if total is None else total}</opensearch:totalResults>
    <opensearch:startIndex>{start}</opensearch:startIndex>{rows}</feed>"""


@pytest.mark.parametrize(
    "url",
    [
        EXACT,
        HOFFMANN,
        "https://arxiv.org/abs/2203.15556",
        "https://arxiv.org/pdf/hep-th/9901001v2.pdf",
        "https://arxiv.org/list/cs.AI/recent",
    ],
)
def test_detection_and_translation(url):
    assert detect_source_method(url)["source_method"] == "arxiv"
    assert urlsplit(translate(url).url()).hostname == "export.arxiv.org"


def test_exact_phrase_wire_format_survives_cache_normalization():
    assert request_url(canonical_url(EXACT)) == EXACT
    assert translate(EXACT).url() == EXACT
    assert (
        translate(
            "https://arxiv.org/search/?query=%22domain+specific+language%22&size=200"
        ).url()
        == EXACT
    )


def test_unquoted_terms_are_not_silently_changed_to_phrase():
    query = translate("https://arxiv.org/search/?query=domain+specific+language")
    assert (
        query.params["search_query"] == "all:domain AND all:specific AND all:language"
    )


def test_hoffmann_keeps_subject_and_all_search_terms():
    query = translate(HOFFMANN)
    assert (
        query.params["search_query"]
        == "(all:Hoffmann AND all:et AND all:al. AND all:2022) AND cat:cs.*"
    )
    assert query.params["max_results"] == 200
    assert "sortBy" not in query.params


@pytest.mark.parametrize(
    "field,prefix",
    [
        ("author", "au"),
        ("title", "ti"),
        ("abstract", "abs"),
        ("comments", "co"),
        ("journal_ref", "jr"),
        ("report_num", "rn"),
    ],
)
def test_fields_boolean_operators_and_sort_are_preserved(field, prefix):
    query = translate(
        f"https://arxiv.org/search/?query=%22a+b%22+OR+(c+ANDNOT+d)&searchtype={field}&order=-submitted_date&start=50&size=100"
    )
    assert (
        query.params["search_query"]
        == f'{prefix}:"a b" OR ({prefix}:c ANDNOT {prefix}:d)'
    )
    assert query.params["sortBy"] == "lastUpdatedDate"
    assert query.params["sortOrder"] == "descending"
    assert query.params["start"] == 50


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/search/?query=a&query=b",
        "https://arxiv.org/search/?query=a&searchtype=doi",
        "https://arxiv.org/search/?query=%22bad",
        "https://arxiv.org/search/?query=(a",
        "https://arxiv.org/search/?query=a+OR",
        "https://arxiv.org/search/?query=a&start=-5",
        "https://export.arxiv.org/api/query?id_list=bad",
        "https://arxiv.org.evil.example/search/?query=a",
        "https://name@arxiv.org/search/?query=a",
    ],
)
def test_invalid_or_unsupported_scope_fails_before_network(url):
    with pytest.raises(DiscoveryError):
        translate(url)


def test_dates_abstract_links_context_and_version_identity():
    total, count, entries = parse(feed(), 0, 200)
    assert total == count == len(entries) == 1
    entry = entries[0]
    assert entry.url == "https://arxiv.org/abs/2203.15556"
    assert entry.published_at.startswith("2022-03-29")
    assert entry.date_kind == "published"
    assert "Hoffmann cs.LG" in entry.context
    assert content_key("http://arxiv.org/abs/2203.15556v3") == content_key(entry.url)


class Fetcher:
    def __init__(self, respond):
        self.calls, self.respond = [], respond
        self.cache = FetchCache()

    async def get(self, url):
        self.calls.append(url)
        value = self.respond(parse_qs(urlsplit(url).query))
        if isinstance(value, Exception):
            raise value
        return url, value


async def test_automatic_and_explicit_scans_share_cache_without_html_models(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.tracker.discovery.parser_version", lambda: pytest.fail("No model needed")
    )
    fetcher = Fetcher(lambda q: feed())
    scanner = Discoverer(fetcher)
    result = await scanner.scan(HOFFMANN)
    assert result.methods == ["arXiv API"] and len(result.entries) == 1
    assert (await scanner.scan(HOFFMANN, source_method="arxiv")).cached
    assert len(fetcher.calls) == 1
    assert "cat:cs.*" in parse_qs(urlsplit(fetcher.calls[0]).query)["search_query"][0]


async def test_pagination_stops_at_total_and_never_opens_content():
    fetcher = Fetcher(
        lambda q: feed(
            ("2203.1555" + str(int(q["start"][0])) + "v1",),
            total=3,
            start=int(q["start"][0]),
        )
    )
    result = await scan_arxiv(
        fetcher, EXACT.replace("max_results=200", "max_results=1"), 10
    )
    assert len(result.entries) == 3 and result.coverage == "complete"
    assert len(fetcher.calls) == 3
    assert all(urlsplit(url).path == "/api/query" for url in fetcher.calls)


async def test_later_failure_keeps_partial_results_without_retry():
    fetcher = Fetcher(
        lambda q: DiscoveryError("HTTP 406") if q["start"] != ["0"] else feed(total=3)
    )
    result = await scan_arxiv(
        fetcher, EXACT.replace("max_results=200", "max_results=1"), 10
    )
    assert result.coverage == "partial" and len(result.entries) == 1
    assert result.expected_count == 3 and "406" in result.warnings[-1]
    assert len(fetcher.calls) == 2


async def test_repeated_pages_and_request_budget_stop():
    fetcher = Fetcher(lambda q: feed(total=100, start=int(q["start"][0])))
    result = await scan_arxiv(
        fetcher, EXACT.replace("max_results=200", "max_results=1"), 10
    )
    assert result.coverage == "partial" and len(fetcher.calls) == 2
    fetcher.calls.clear()
    result = await scan_arxiv(
        fetcher, EXACT.replace("max_results=200", "max_results=1"), 1
    )
    assert result.coverage == "partial" and len(fetcher.calls) == 1


async def test_empty_results_are_complete():
    result = await scan_arxiv(Fetcher(lambda q: feed(())), EXACT, 10)
    assert result.coverage == "complete" and result.expected_count == 0


@pytest.mark.parametrize(
    "body",
    [
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id><title>Error</title><summary>bad query</summary></entry></feed>',
        '<!DOCTYPE feed [<!ENTITY x "bad">]><feed>&x;</feed>',
        "<html>blocked</html>",
        feed(start=200),
    ],
)
def test_api_errors_entities_and_wrong_page_are_rejected(body):
    with pytest.raises(DiscoveryError):
        parse(body, 0, 200)


async def test_fetcher_wire_url_daily_cache_and_honest_identity(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(200, text=feed())

    monkeypatch.setattr(
        "app.tracker.urls.public_addresses", AsyncMock(return_value=["93.184.216.34"])
    )
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    cache = FetchCache()
    await SafeFetcher(cache, interval=0).get(EXACT)
    assert requests[0].url.query.decode() == urlsplit(EXACT).query
    assert requests[0].headers["User-Agent"].startswith("Trackify/")
    key = response_key(canonical_url(EXACT))
    cached = cache.get(key)
    cached["checked"] -= 3600
    cache.put(key, cached)
    await SafeFetcher(cache, interval=0).get(canonical_url(EXACT))
    assert len(requests) == 1
    assert cached["expires"] - cached["checked"] >= 86400


async def test_arxiv_gate_serializes_workers_and_rss(tmp_path):
    caches = [FetchCache(tmp_path / "cache.sqlite3") for _ in range(2)]
    times = []
    active = 0

    async def request(fetcher, host):
        nonlocal active
        async with fetcher._polite_gate(host):
            assert active == 0
            active += 1
            times.append(time.monotonic())
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(
        request(SafeFetcher(caches[0]), "export.arxiv.org"),
        request(SafeFetcher(caches[1]), "rss.arxiv.org"),
    )
    assert times[1] - times[0] >= 3


async def test_link_cap_keeps_existing_records(monkeypatch):
    monkeypatch.setattr("app.tracker.arxiv.MAX_LINKS", 1)
    result = await scan_arxiv(
        Fetcher(lambda q: feed(("2203.15556v1", "2203.15557v1"))), EXACT, 10
    )
    assert result.coverage == "partial" and len(result.entries) == 1
    assert "link limit" in result.warnings[-1]


async def test_refusal_pauses_other_arxiv_hosts_without_retry(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "600"})

    monkeypatch.setattr(
        "app.tracker.urls.public_addresses", AsyncMock(return_value=["93.184.216.34"])
    )
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    cache = FetchCache()
    with pytest.raises(DiscoveryError, match="429"):
        await SafeFetcher(cache, interval=0).get(EXACT)
    with pytest.raises(DiscoveryError, match="requested a pause"):
        await SafeFetcher(cache, interval=0).get("https://rss.arxiv.org/rss/cs.AI")
    assert len(calls) == 1
    assert cache.get("backoff:arxiv")["expires"] > time.time() + 590


async def test_daily_cache_eviction_does_not_trigger_early_refetch(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, text=feed())

    monkeypatch.setattr(
        "app.tracker.urls.public_addresses", AsyncMock(return_value=["93.184.216.34"])
    )
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    cache = FetchCache()
    await SafeFetcher(cache, interval=0).get(EXACT)
    cache.memory.clear()
    with pytest.raises(DiscoveryError, match="checked recently"):
        await SafeFetcher(cache, interval=0).get(EXACT)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "suffix",
    [
        "terms-0-term=alpha&terms-00-term=beta",
        "terms-0-term=alpha&terms-0-field=title&terms-00-field=author",
    ],
)
def test_numeric_row_aliases_cannot_overwrite_filters(suffix):
    with pytest.raises(DiscoveryError, match="repeated fields"):
        translate("https://arxiv.org/search/advanced?" + suffix)


def test_invalid_subject_selection_is_disclosed():
    query = translate(
        "https://arxiv.org/search/advanced?terms-0-term=alpha&classification-physics=y&classification-physics_archives=cs&classification-computer_science=maybe"
    )
    assert query.params["search_query"] == "all:alpha"
    assert any("Physics" in note for note in query.notes)
    assert any("computer science" in note for note in query.notes)
