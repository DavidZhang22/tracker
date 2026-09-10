from time import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.discovery import Discoverer
from app.tracker.models import Entry, Scan
from app.tracker.store import Store
from tests.test_discovery import FakeFetcher


def make_item(store, count=8, source="https://example.com/series"):
    scan = Scan(
        source,
        "Series",
        entries=[
            Entry(
                f"{source}/{i}",
                f"Chapter {i}",
                number=i,
                published_at=f"2026-09-{i % 28 + 1:02}T00:00:00+00:00",
            )
            for i in range(count)
        ],
    )
    return store.create(store.save_scan(scan.to_dict()))


def test_all_matching_selection_crosses_pages_and_bulk_body_limit(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3")) as client:
        store = client.app.state.store
        item = make_item(store, 2100)
        selection = client.post(
            f"/api/items/{item['id']}/link-selection", json={"sort": "number"}
        )
        assert selection.status_code == 200
        ids = selection.json()["ids"]
        assert len(ids) == 2100
        result = client.post(
            "/api/links/bulk",
            json={"item_id": item["id"], "ids": ids, "action": "favorite"},
        )
        assert result.status_code == 200, result.text
        assert store.links(item["id"], filter="favorites")["total"] == 2100
        assert (
            client.post(
                "/api/links/bulk",
                json={"item_id": item["id"], "ids": ["x"] * 5000, "action": "read"},
            ).status_code
            == 422
        )


@pytest.mark.parametrize(
    "sort,direction",
    [("number", "asc"), ("date", "desc"), ("title", "desc"), ("source", "asc")],
)
def test_ranges_follow_view_order_filters_and_exclude_anchor(tmp_path, sort, direction):
    store = Store(tmp_path / "db.sqlite3")
    item = make_item(store)
    all_rows = store.links(item["id"], limit=20)["links"]
    store.update("links", all_rows[1]["id"], {"ignored": True})
    store.bulk_selected("links", [all_rows[2]["id"]], "delete", item["id"])
    view = dict(filter="all", search="Chapter", sort=sort, direction=direction)
    rows = store.links(item["id"], **view)["links"]
    ids = [r["id"] for r in rows]
    assert store.link_selection(item["id"], **view)["ids"] == ids
    anchor = ids[3]
    assert store.read_range(item["id"], anchor, "before", **view)["updated"] == 3
    assert {r["id"] for r in store.links(item["id"], filter="read")["links"]} == set(
        ids[:3]
    )
    assert (
        store.read_range(item["id"], anchor, "after", **view)["updated"] == len(ids) - 4
    )
    assert [r["id"] for r in store.links(item["id"], filter="unread")["links"]] == [
        anchor
    ]
    assert not store.links(item["id"], filter="ignored")["links"][0]["read"]
    assert not store.links(item["id"], filter="trash")["links"][0]["read"]


def test_range_missing_or_foreign_anchor_changes_nothing(tmp_path, monkeypatch):
    store = Store(tmp_path / "db.sqlite3")
    one = make_item(store)
    now = time() + 8
    monkeypatch.setattr("app.tracker.store.time", lambda: now)
    two = make_item(store, source="https://other.example/")
    foreign = store.link_selection(two["id"])["ids"][0]
    with pytest.raises(ValueError):
        store.read_range(one["id"], foreign, "before")
    assert store.item(one["id"])["read_count"] == 0
    with pytest.raises(ValueError):
        store.read_range(
            one["id"],
            store.link_selection(one["id"])["ids"][0],
            "after",
            filter="trash",
        )


def test_range_null_dates_ties_search_and_active_unread_view(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    item = make_item(store, 120)
    with store.connection() as db:
        db.execute("UPDATE links SET published_at=NULL WHERE number > 100")
    view = dict(filter="unread", search="Chapter 1", sort="date", direction="desc")
    ids = store.link_selection(item["id"], **view)["ids"]
    assert ids == [r["id"] for r in store.links(item["id"], **view, limit=200)["links"]]
    store.read_range(item["id"], ids[5], "after", **view)
    assert store.item(item["id"])["read_count"] == len(ids) - 6


def test_keywords_persist_for_refresh_and_preserve_progress(tmp_path):
    source = "https://example.com/series"
    html = '<article><a href="/series/1">Chapter 1</a><span>English</span></article><article><a href="/series/2">Chapter 2</a><span>French</span></article>'
    fake = FakeFetcher({source: html})
    with TestClient(create_app(tmp_path / "db.sqlite3", Discoverer(fake))) as client:
        result = client.post("/api/scans", json={"url": source, "keywords": "English"})
        assert result.status_code == 200, result.text
        assert len(result.json()["entries"]) == 1
        item = client.post(
            "/api/items", json={"scan_id": result.json()["scan_id"]}
        ).json()
        assert item["keywords"] == "English"
        lid = client.get(f"/api/items/{item['id']}/links").json()["links"][0]["id"]
        client.patch(f"/api/links/{lid}", json={"read": True, "favorite": True})
        refresh = client.post(f"/api/items/{item['id']}/refresh")
        assert refresh.json()["ok"]
        saved = client.get(f"/api/items/{item['id']}/links").json()["links"]
        assert len(saved) == 1 and saved[0]["read"] and saved[0]["favorite"]
        assert (
            client.patch(
                f"/api/items/{item['id']}",
                json={"keywords": ",".join(str(i) for i in range(11))},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/items/{item['id']}/read-range",
                json={"anchor_id": lid, "side": "before", "sort": "number"},
            ).status_code
            == 200
        )
