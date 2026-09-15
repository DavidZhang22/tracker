"""Synthetic contracts from Enma's page code; live API access returned HTTP 403."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.discovery import Discoverer
from app.tracker.enma import API, parse_episodes, series_slug
from app.tracker.limits import MAX_LINKS
from app.tracker.source_methods import detect_source_method
from app.tracker.urls import DiscoveryError, SafeFetcher

SLUG = "grand-blue-dreaming-season-3-199111"
SOURCE = "https://www.enma.lol/watch/" + SLUG
ENDPOINT = API + SLUG


def row(number, identity=None, **values):
    return {
        "id": f"{SLUG}?ep={identity or number}",
        "episode_no": number,
        "title": f"Title {number}",
        "japanese_title": "Japanese episode title",
        "filler": False,
        **values,
    }


def payload(rows, total=None):
    return {
        "results": {
            "episodes": rows,
            "totalEpisodes": len(rows) if total is None else total,
        }
    }


class Episodes:
    def __init__(self, data):
        self.data, self.calls = data, []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        assert (
            url == ENDPOINT and not kwargs
        )  # No episode, stream, player, or info requests.
        if isinstance(self.data, Exception):
            raise self.data
        return url, json.dumps(self.data)


@pytest.mark.parametrize("method", ["auto", "enma"])
@pytest.mark.parametrize("deep", [False, True])
@pytest.mark.asyncio
async def test_both_refresh_modes_use_one_listing_and_keep_the_source_episode(
    method, deep, monkeypatch
):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "must-not-be-used")
    fetcher = Episodes(payload([row(10, 7), row(2, 800), row(1, 900)]))
    source = SOURCE + "?ep=800"
    scan = await Discoverer(fetcher).scan(source, source_method=method, deep=deep)
    assert fetcher.calls == [ENDPOINT]
    assert scan.methods == ["Enma episodes API"] and scan.analysis_mode == "light"
    assert [e.number for e in scan.entries] == [1, 2, 10]
    assert [e.position for e in scan.entries] == [0, 1, 2]
    assert source in {e.url for e in scan.entries}
    assert scan.expected_count == 3 and scan.coverage == "complete"
    assert all(e.published_at is None for e in scan.entries)


@pytest.mark.parametrize(
    "source",
    [
        SOURCE,
        SOURCE + "/",
        SOURCE + "?ep=9",
        SOURCE.replace("www.", ""),
        SOURCE + "?utm_source=test#episodes",
    ],
)
def test_enma_detection_needs_no_network(source):
    assert series_slug(source) == SLUG
    assert detect_source_method(source)["source_method"] == "enma"


@pytest.mark.parametrize(
    "source",
    [
        SOURCE + "?language=English",
        SOURCE + "?ep=1&ep=2",
        SOURCE + "?ep=",
        SOURCE + "?ep=0",
        SOURCE + "/episode/1",
        SOURCE.replace("www.enma.lol", "www.enma.lol.evil.example"),
        SOURCE.replace("www.enma.lol", "api.enma.lol"),
        "https://www.enma.lol/home",
    ],
)
def test_detection_preserves_scope(source):
    assert series_slug(source) is None
    assert detect_source_method(source)["source_method"] == "auto"


def test_duplicates_replace_metadata_and_only_explicit_publication_dates_are_used():
    scan = parse_episodes(
        payload(
            [
                row(2, published_at="2026-09-10T12:00:00Z"),
                row(1, title="Old title", nextEpisodeSchedule="2026-10-01"),
                row(1, title="Updated title", updated_at="2026-09-10"),
            ],
            total=2,
        ),
        SOURCE,
    )
    assert scan.coverage == "complete" and len(scan.entries) == 2
    assert scan.entries[0].title == "Episode 1: Updated title"
    assert scan.entries[0].published_at is None
    assert scan.entries[1].published_at == "2026-09-10T12:00:00+00:00"
    assert scan.entries[1].date_source == "Enma episode published_at"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        row(3, id="other-series-123?ep=3"),
        row(3, id="http://127.0.0.1/private"),
        row(3, id=SLUG + "?ep=3&token=secret"),
        row(3, id=SLUG + "?ep=3#foo"),
        row(True),
        row(float("inf")),
        row(float("nan")),
        row(10**400),
        row(-1),
        row("3"),
    ],
)
def test_bad_records_cannot_create_invented_links_or_false_complete_scans(bad):
    scan = parse_episodes(payload([row(1), bad], total=2), SOURCE)
    assert [e.url for e in scan.entries] == [SOURCE + "?ep=1"]
    assert scan.coverage == "partial" and scan.warnings


@pytest.mark.parametrize(
    "data",
    [
        {},
        [],
        {"results": None},
        payload([None]),
        payload([], total=10),
        payload([], total=True),
        {"success": False, **payload([])},
        {"results": {"episodes": {}, "totalEpisodes": 0}},
    ],
)
def test_invalid_or_empty_failed_responses_never_claim_success(data):
    with pytest.raises(DiscoveryError, match="Saved links were kept"):
        parse_episodes(data, SOURCE)


def test_empty_series_and_bounded_large_inventory():
    empty = parse_episodes(payload([]), SOURCE)
    assert empty.coverage == "complete" and empty.expected_count == 0
    scan = parse_episodes(payload([row(i) for i in range(1, 5100)]), SOURCE)
    assert len(scan.entries) == MAX_LINKS and scan.coverage == "partial"
    assert any("4,999" in w for w in scan.warnings)


@pytest.mark.asyncio
async def test_keyword_and_url_filters_use_episode_metadata():
    fetcher = Episodes(payload([row(1), row(2, filler=True)]))
    result = await Discoverer(fetcher).scan(
        SOURCE, keywords="Filler", source_method="enma"
    )
    assert [e.number for e in result.entries] == [2] and len(fetcher.calls) == 1
    result = await Discoverer(fetcher).scan(SOURCE, include_path="ep=1")
    assert [e.number for e in result.entries] == [1]


@pytest.mark.asyncio
async def test_access_denials_remain_cached_and_never_start_the_browser(monkeypatch):
    calls = []

    async def addresses(host):
        assert host == "api.enma.lol"
        return ["93.184.216.34"]

    def deny(request):
        calls.append(request)
        assert request.url.path == "/api/episodes/" + SLUG
        return httpx.Response(403, headers={"Retry-After": "900"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr("app.tracker.urls.public_addresses", addresses)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: client_class(transport=httpx.MockTransport(deny), **kw),
    )
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "must-not-be-used")
    fetcher = SafeFetcher()
    discoverer = Discoverer(fetcher)
    for deep in (False, True):
        with pytest.raises(DiscoveryError, match=r"Enma blocked.*HTTP 403"):
            await discoverer.scan(SOURCE, deep=deep)
    assert len(calls) == 1
    assert fetcher.cache.get("backoff:api.enma.lol")


def test_failed_refresh_preserves_links_read_state_and_favorites(tmp_path):
    fetcher = Episodes(payload([row(1), row(2)]))
    app = create_app(tmp_path / "library.sqlite3", Discoverer(fetcher))
    with TestClient(app) as client:
        detection = client.post("/api/source-method/detect", json={"url": SOURCE})
        assert detection.json()["source_method"] == "enma" and fetcher.calls == []
        preview = client.post("/api/scans", json={"url": SOURCE}).json()
        assert preview["source_method"] == "enma"
        item = client.post(
            "/api/items", json={"scan_id": preview["scan_id"], "mark_read": True}
        ).json()
        assert item["source_method"] == "enma"
        link = item["latest_link"]["id"]
        app.state.store.update("links", link, {"favorite": True})
        before = app.state.store.links(item["id"])
        fetcher.data = DiscoveryError("The source refused access (HTTP 403).")
        response = client.post(f"/api/items/{item['id']}/refresh?deep=true").json()
        assert not response["ok"] and "Enma blocked" in response["error"]
        assert app.state.store.links(item["id"]) == before
