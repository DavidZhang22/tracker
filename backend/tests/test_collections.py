"""Recorded public layouts plus synthetic boundary cases. No network in unit tests."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.tracker.adapters import codeforces_scan, wetried_series
from app.tracker.cache import FetchCache
from app.tracker.dates import evidence
from app.tracker.discovery import Discoverer
from app.tracker.models import date_value, sequence_value
from app.tracker.parser import parse_page
from app.tracker.urls import DiscoveryError, RequestBudget, SafeFetcher, request_budget
from tests.test_discovery import FakeFetcher

FIX = Path(__file__).parent / "fixtures"
WT = "https://wetriedtls.com/series/climbing-the-tower-with-time-stop-ability"
CF = "https://codeforces.com/contests"


def fixture(name):
    return (FIX / name).read_text(encoding="utf8")


def flight_html():
    return (
        "<h1>Climbing the Tower</h1><script>self.__next_f.push("
        + json.dumps(
            [
                1,
                '2a:{"noise":"not a chapter"}\n2c:["$","$L35",null,{"series_id":82,"series_slug":"climbing-the-tower-with-time-stop-ability"}]',
            ]
        )
        + ")</script>"
    )


def test_serialized_flight_reads_later_record_and_series_scope():
    assert wetried_series(flight_html(), WT) == 82
    with pytest.raises(DiscoveryError):
        wetried_series(flight_html().replace("series_id", "unrelated_id"), WT)
    assert (
        wetried_series(flight_html(), "https://elsewhere.example/series/test") is None
    )


async def test_wetried_public_paid_metadata_and_all_dates():
    f = FakeFetcher(
        {
            WT: flight_html(),
            "https://api.wetriedtls.com/chapters/82?query=&order=desc&page=1&perPage=30": fixture(
                "wetried-chapters.json"
            ),
            "https://api.wetriedtls.com/chapters/82/paid?query=&order=desc": fixture(
                "wetried-paid.json"
            ),
        }
    )
    r = await Discoverer(f).scan(WT)
    assert len(r.entries) == r.expected_count == 4
    assert r.coverage == "complete" and r.pages_scanned == 3
    assert [e.number for e in r.entries] == [160, 161, 162, 169]
    assert all(
        e.published_at and e.date_kind == "listed" and e.date_precision == "time"
        for e in r.entries
    )
    assert r.entries[-1].availability == "paid"
    assert "Nightmare" in r.entries[-1].title
    assert all("/chapter-" not in u for u in f.calls)


async def test_wetried_pagination_bounded_and_partial_failures_visible():
    data = json.loads(fixture("wetried-chapters.json"))
    data["meta"].update(last_page=2, total=10)
    f = FakeFetcher(
        {
            WT: flight_html(),
            "https://api.wetriedtls.com/chapters/82?query=&order=desc&page=1&perPage=30": json.dumps(
                data
            ),
        }
    )
    r = await Discoverer(f, max_pages=2).scan(WT)
    assert len(f.calls) == 2 and r.coverage == "partial"
    assert any("limit" in w for w in r.warnings)


async def test_codeforces_one_api_request_utc_dates_and_no_id_sequence():
    f = FakeFetcher(
        {
            "https://codeforces.com/api/contest.list?gym=false": fixture(
                "codeforces.json"
            )
        }
    )
    r = await Discoverer(f).scan(CF)
    assert len(r.entries) == 4 and len(f.calls) == 1
    assert all(
        e.number is None and e.date_kind == "scheduled" and e.date_precision == "time"
        for e in r.entries
    )
    assert r.entries[-1].published_at > r.entries[0].published_at
    assert r.entries[0].url.startswith("https://codeforces.com/contest/")
    assert r.coverage == "complete"
    with pytest.raises(DiscoveryError):
        codeforces_scan('{"status":"FAILED"}', CF)


@pytest.mark.parametrize(
    "name,url,count",
    [
        ("hn.html", "https://news.ycombinator.com/", 4),
        ("xkcd.html", "https://xkcd.com/archive/", 4),
        ("github-releases.html", "https://github.com/astral-sh/ruff/releases", 2),
    ],
)
def test_real_collection_layouts_exclude_auxiliary_links(name, url, count):
    r, _, _ = parse_page(fixture(name), url)
    assert len(r.entries) == count
    assert all(e.published_at for e in r.entries)
    if name == "hn.html":
        assert all(
            e.date_kind == "listed" and "ycombinator" not in e.url for e in r.entries
        )
    if name == "github-releases.html":
        assert r.entries[0].published_at == "2026-09-03T17:20:13+00:00"
        assert all("/releases/tag/" in e.url and e.number is None for e in r.entries)
    if name == "xkcd.html":
        assert r.entries[0].published_at.startswith("2026-09-07")
        assert all(e.date_precision == "day" for e in r.entries)


def test_dates_never_bleed_from_adjacent_cards_or_unrelated_page_footer():
    html = '<div class="cards"><div class="card"><h2><a href="/post/one">One</a></h2><time datetime="2026-09-01"/></div><div class="card"><h2><a href="/post/two">Two</a></h2></div></div><footer>2026-09-08</footer>'
    r, _, _ = parse_page(html, "https://blog.example/")
    assert r.entries[0].published_at and r.entries[1].published_at is None
    simple = '<div><div><a href="/post/one">One</a><time datetime="2026-09-01"/></div><div><a href="/post/two">Two</a></div></div>'
    r, _, _ = parse_page(simple, "https://blog.example/")
    assert r.entries[1].published_at is None


def test_ambiguous_dates_stay_unknown_and_labeled_release_beats_signature_date():
    html = '<article><h2><a href="/post/one">One</a></h2><time datetime="2026-01-01"/><time datetime="2026-02-02"/></article>'
    assert parse_page(html, "https://example.com/")[0].entries[0].published_at is None
    html = html.replace(
        '<time datetime="2026-01-01"/>',
        '<div>Published <time datetime="2026-01-01"/></div>',
    )
    assert (
        parse_page(html, "https://example.com/")[0]
        .entries[0]
        .published_at.startswith("2026-01-01")
    )


@pytest.mark.parametrize("value", ["2026", "2026-09", "3 days ago", "1234"])
def test_incomplete_dates_do_not_acquire_today(value):
    assert date_value(value) is None


def test_semantic_versions_are_not_decimal_chapters():
    assert sequence_value("0.16.6") is None
    assert sequence_value("v2.12.4-beta") is None
    assert sequence_value("Chapter 16.6") == 16.6


def test_dates_in_urls_are_labeled_inferred_and_unzoned_clocks_use_date_only():
    e = parse_page('<a href="/2026/09/08/a-post">A post</a>', "https://example.com/")[
        0
    ].entries[0]
    assert e.date_kind == "inferred" and e.published_at.startswith("2026-09-08")
    d = evidence("2026-09-08T23:30:00", "time")
    assert (
        d["date_precision"] == "day"
        and d["published_at"] == "2026-09-08T00:00:00+00:00"
    )
    assert "time zone unspecified" in d["date_source"]


async def test_updated_feed_date_does_not_replace_publication_date():
    source = "https://example.com/"
    # Keep one entry undated so the advertised feed is needed.
    html = '<link rel="alternate" type="application/atom+xml" href="/feed"/><article><a href="/post/one">One</a><time datetime="2026-01-01"/></article><article><a href="/post/two">Two</a></article>'
    feed = '<feed><entry><title>One</title><link href="/post/one"/><updated>2026-09-08T12:00:00Z</updated></entry></feed>'
    r = await Discoverer(
        FakeFetcher({source: html, "https://example.com/feed": feed})
    ).scan(source)
    one = next(e for e in r.entries if e.title == "One")
    assert one.published_at.startswith("2026-01-01") and one.date_precision == "day"


def test_generic_readmore_uses_its_row_title_and_date():
    html = '<table><tr><td>A useful event</td><td><time datetime="2026-09-08T12:30:00Z"/></td><td><a href="/event/123">Enter</a></td></tr></table>'
    e = parse_page(html, "https://events.example/")[0].entries[0]
    assert e.title == "A useful event" and e.published_at == "2026-09-08T12:30:00+00:00"


def test_pagination_prefers_next_and_only_one_equivalent_feed():
    html = '<nav class="pagination"><a href="?page=2">2</a><a href="?page=100">100</a><a href="?page=2" rel="next">Next</a></nav><link rel="alternate" type="application/rss+xml" href="/feed"/><link rel="alternate" type="application/atom+xml" href="/atom"/>'
    _, pages, feeds = parse_page(html, "https://example.com/archive")
    assert pages == ["https://example.com/archive?page=2"] and len(feeds) == 1


@pytest.fixture
def transport():
    real = httpx.AsyncClient
    calls = []
    state = {
        "status": 200,
        "headers": {"etag": '"one"'},
        "text": '<article><a href="/post/one">One</a></article>',
    }

    def handler(req):
        calls.append(req)
        return httpx.Response(
            state["status"], headers=state["headers"], text=state["text"]
        )

    with (
        patch("app.tracker.urls.public_addresses", return_value=["93.184.216.34"]),
        patch(
            "app.tracker.urls.httpx.AsyncClient",
            side_effect=lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
        ),
    ):
        yield calls, state


async def test_cache_is_persistent_and_conditional_304_reuses_body(tmp_path, transport):
    calls, state = transport
    path = tmp_path / "cache.sqlite3"
    f = SafeFetcher(FetchCache(path), interval=0)
    first = await f.get("https://example.com/")
    assert (
        await SafeFetcher(FetchCache(path), interval=0).get("https://example.com/")
        == first
    )
    assert len(calls) == 1
    old = f.cache.get("https://example.com/")
    old["expires"] = 0
    f.cache.put("https://example.com/", old)
    state.update(status=304, text="")
    assert await f.get("https://example.com/") == first
    assert calls[-1].headers["if-none-match"] == '"one"'


async def test_retry_after_stops_other_urls_on_same_host(transport):
    calls, state = transport
    state.update(status=429, headers={"Retry-After": "3600"})
    f = SafeFetcher(interval=0)
    with pytest.raises(DiscoveryError):
        await f.get("https://example.com/one")
    with pytest.raises(DiscoveryError, match="pause"):
        await f.get("https://example.com/two")
    assert len(calls) == 1
    assert f.cache.get("backoff:example.com")["expires"] >= time.time() + 3500


async def test_redirects_spend_request_budget(transport):
    calls, state = transport
    state.update(status=302, headers={"location": "/redirect"})
    token = request_budget.set(RequestBudget(1))
    try:
        with pytest.raises(DiscoveryError, match="request budget"):
            await SafeFetcher(interval=0).get("https://example.com/")
        assert len(calls) == 1
    finally:
        request_budget.reset(token)


async def test_repeat_concurrent_scans_use_one_result_and_keep_actual_checked_time(
    transport,
):
    calls, state = transport
    d = Discoverer(SafeFetcher(interval=0))
    results = await asyncio.gather(
        d.scan("https://example.com/"), d.scan("https://example.com/")
    )
    assert len(calls) == 1 and results[1].cached
    assert results[1].checked_at == results[0].checked_at
    assert results[1].requests_made == 0 and results[0].requests_made == 1
    assert results[1].cache_hits == 1


async def test_same_host_requests_are_paced(transport):
    with patch("app.tracker.urls.asyncio.sleep", new_callable=AsyncMock) as sleep:
        f = SafeFetcher(interval=2)
        await asyncio.gather(
            f.get("https://example.com/one"), f.get("https://example.com/two")
        )
        assert sleep.call_args_list[-1].args[0] > 1.5
