import json
from pathlib import Path

import pytest

from app.tracker.discovery import Discoverer
from app.tracker.fenrir import fenrir_endpoint, fenrir_scan
from app.tracker.store import Store
from app.tracker.urls import DiscoveryError
from tests.test_discovery import FakeFetcher

SOURCE = "https://fenrirealm.com/series/the-speedrun-manual-of-miss-witch"
FIXTURE = Path(__file__).parent / "fixtures" / "fenrir-chapters.json"


def rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


async def test_public_index_only_includes_parts_and_paid_metadata():
    fetcher = FakeFetcher(
        {fenrir_endpoint(SOURCE): FIXTURE.read_text(encoding="utf-8")}
    )
    result = await Discoverer(fetcher, max_pages=1).scan(SOURCE)
    assert len(result.entries) == result.expected_count == 7
    assert result.title == "The Speedrun Manual of Miss Witch"
    assert result.kind == "novel" and result.coverage == "complete"
    assert result.order_hint == "source" and result.pages_scanned == 1
    assert fetcher.calls == [fenrir_endpoint(SOURCE)]
    assert [e.url.split("/")[-1] for e in result.entries] == [
        "1",
        "154-1",
        "154-2",
        "154-3",
        "154-4",
        "310",
        "311",
    ]
    assert all(e.published_at and e.date_precision == "time" for e in result.entries)
    assert result.entries[-1].availability == "paid"
    assert result.entries[0].published_at == "2025-12-11T08:11:48+00:00"
    assert result.entries[-1].published_at == "2026-08-01T10:11:38+00:00"
    assert all("updated_at" not in e.date_source for e in result.entries)
    assert not any(
        k in result.to_dict()["entries"][0] for k in ("bought", "bypass", "user")
    )


async def test_group_order_and_double_digit_parts_survive_storage_and_latest(tmp_path):
    base = rows()[0]
    samples = [
        base
        | {
            "slug": "volume-one/1-2",
            "part": 2,
            "index": 0,
            "group": {"slug": "volume-one", "name": "Volume one", "index": 1},
        },
        base
        | {
            "slug": "volume-one/1-10",
            "part": 10,
            "index": 1,
            "group": {"slug": "volume-one", "index": 1},
        },
        base
        | {
            "slug": "volume-one/50",
            "number": 50,
            "index": 2,
            "group": {"slug": "volume-one", "index": 1},
        },
        base
        | {
            "slug": "",
            "number": 1,
            "index": 0,
            "group": {"slug": "volume-two", "name": "Volume two", "index": 2},
        },
    ]
    f = FakeFetcher({fenrir_endpoint(SOURCE): json.dumps(list(reversed(samples)))})
    result = await Discoverer(f, max_pages=1).scan(SOURCE)
    store = Store(tmp_path / "fenrir.sqlite3")
    item = store.create(store.save_scan(result.to_dict()))
    listing = store.links(item["id"], direction="asc")
    assert listing["sort_used"] == "source"
    assert [e["url"].removeprefix(SOURCE + "/") for e in listing["links"]] == [
        "volume-one/1-2",
        "volume-one/1-10",
        "volume-one/50",
        "volume-two/1",
    ]
    assert item["latest_link"]["url"] == SOURCE + "/volume-two/1"
    assert (
        store.links(item["id"], direction="desc")["links"][0]["url"]
        == item["latest_link"]["url"]
    )
    assert (
        store.links(item["id"], sort="number", direction="desc")["links"][0]["number"]
        == 50
    )
    lid = listing["links"][0]["id"]
    store.update("links", lid, {"read": True, "favorite": True})
    assert store.merge(item["id"], result.to_dict()) == 0
    reopened = Store(store.path)
    saved = reopened.links(item["id"], direction="asc")["links"][0]
    assert saved["id"] == lid and saved["read"] and saved["favorite"]
    assert reopened.item(item["id"])["order_hint"] == "source"


@pytest.mark.parametrize(
    "source",
    [
        "https://fenrirealm.com/series/another-story",
        "https://www.fenrirealm.com/series/another-story/?ref=abc",
    ],
)
def test_adapter_generalizes_to_other_series_and_canonicalizes_links(source):
    assert (
        fenrir_endpoint(source)
        == "https://fenrirealm.com/api/new/v2/series/another-story/chapters"
    )
    result = fenrir_scan(json.dumps(rows()), source)
    assert all(
        e.url.startswith("https://fenrirealm.com/series/another-story/")
        and "?" not in e.url
        for e in result.entries
    )


@pytest.mark.parametrize(
    "source",
    [
        "https://fenrirealm.com.evil.example/series/book",
        "https://other.example/series/book",
        SOURCE + "/1",
        "https://fenrirealm.com/api/new/v2/reviews",
    ],
)
def test_endpoint_is_scoped_to_known_series_routes(source):
    assert fenrir_endpoint(source) is None


def test_hostile_paths_and_changed_payloads_do_not_become_links():
    base = rows()[0]
    data = [
        base,
        *[
            base | {"slug": slug}
            for slug in (
                "../other",
                "volume/../../outside",
                "https://evil.example",
                "x?next=http://localhost",
                "%2e%2e/private",
            )
        ],
        None,
        base | {"group": "not an object"},
    ]
    result = fenrir_scan(json.dumps(data), SOURCE)
    assert len(result.entries) == 1 and result.coverage == "partial" and result.warnings
    for text in ("{}", '{"data":[]}', "<h1>Sign in</h1>"):
        with pytest.raises(DiscoveryError):
            fenrir_scan(text, SOURCE)


def test_date_fallbacks_and_missing_dates_are_honest():
    base = rows()[0]
    result = fenrir_scan(
        json.dumps(
            [
                base
                | {"locked": {"price": 0, "unlocked_at": None}, "created_at": "invalid"}
            ]
        ),
        SOURCE,
    )
    assert result.entries[0].date_kind == "updated"
    assert "updated_at" in result.entries[0].date_source
    result = fenrir_scan(
        json.dumps([base | {"locked": {}, "created_at": None, "updated_at": None}]),
        SOURCE,
    )
    assert result.entries[0].published_at is None
    assert result.entries[0].availability == ""


def test_large_indexes_are_capped_and_report_partial_coverage(monkeypatch):
    monkeypatch.setattr("app.tracker.fenrir.MAX_LINKS", 2)
    result = fenrir_scan(FIXTURE.read_text(encoding="utf-8"), SOURCE)
    assert len(result.entries) == 2 and result.expected_count == 7
    assert result.coverage == "partial" and result.warnings


async def test_source_refusal_stops_without_trying_chapter_pages():
    f = FakeFetcher({fenrir_endpoint(SOURCE): DiscoveryError("Source refused access")})
    with pytest.raises(DiscoveryError):
        await Discoverer(f).scan(SOURCE)
    assert f.calls == [fenrir_endpoint(SOURCE)]


def test_existing_library_migration_preserves_links_and_progress(tmp_path):
    store = Store(tmp_path / "existing.sqlite3")
    scan = fenrir_scan(FIXTURE.read_text(encoding="utf-8"), SOURCE).to_dict()
    item = store.create(store.save_scan(scan))
    link = store.links(item["id"])["links"][0]
    store.update("links", link["id"], {"read": True, "favorite": True})
    with store.connection() as db:
        db.execute("ALTER TABLE items DROP COLUMN order_hint")
        db.execute("PRAGMA user_version=2")
    migrated = Store(store.path)
    assert migrated.item(item["id"])["order_hint"] == ""
    assert migrated.merge(item["id"], scan) == 0
    saved = migrated.links(item["id"])["links"][0]
    assert saved["id"] == link["id"] and saved["read"] and saved["favorite"]
    assert migrated.item(item["id"])["order_hint"] == "source"
