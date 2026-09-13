import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.models import Entry, Scan
from app.tracker.store import Store
from app.tracker.urls import DiscoveryError

ROOT = "https://blog.example/"


def scan(entries=None):
    return Scan(
        ROOT,
        "A blog",
        "blog",
        entries
        if entries is not None
        else [Entry(ROOT + "posts/one", "One", "2026-01-01T00:00:00+00:00", 1)],
        pages_scanned=1,
        methods=["page"],
    )


class FakeDiscoverer:
    def __init__(self):
        self.result = scan()
        self.calls = []

    async def scan(self, url, *args, **kwargs):
        self.calls.append(url)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def client(tmp_path):
    fake = FakeDiscoverer()
    app = create_app(tmp_path / "test.sqlite3", fake)
    with TestClient(app) as client:
        client.fake = fake
        yield client


def add(client, **kw):
    s = client.post("/api/scans", json={"url": ROOT})
    assert s.status_code == 200, s.text
    r = client.post("/api/items", json={"scan_id": s.json()["scan_id"], **kw})
    assert r.status_code == 201, r.text
    return r.json()


def entries(client, item, **params):
    return client.get(f"/api/items/{item['id']}/links", params=params).json()


def test_initial_backlog_not_new_and_persistence(client):
    item = add(client)
    assert item["unread_count"] == 1 and item["new_count"] == 0
    reopened = Store(client.app.state.store.path)
    assert reopened.item(item["id"])["title"] == "A blog"


def test_initial_mark_read_and_auto_read_preference(client):
    item = add(client, mark_read=True, auto_read=False)
    assert item["read_count"] == 1 and not item["auto_read"]


def test_duplicate_source_conflict(client):
    add(client)
    sid = client.post("/api/scans", json={"url": ROOT}).json()["scan_id"]
    assert client.post("/api/items", json={"scan_id": sid}).status_code == 409


def test_trailing_slash_source_alias_conflict(client):
    client.fake.result = Scan(
        "https://site.example/blog/",
        "Blog",
        entries=[Entry("https://site.example/blog/one", "One")],
    )
    add(client)
    client.fake.result.url = "https://site.example/blog"
    sid = client.post("/api/scans", json={"url": ROOT}).json()["scan_id"]
    assert client.post("/api/items", json={"scan_id": sid}).status_code == 409


def test_no_untrusted_scan_payload(client):
    assert (
        client.post(
            "/api/items",
            json={"scan_id": "invented", "url": "http://localhost", "entries": []},
        ).status_code
        == 422
    )


def test_refresh_preserves_read_favorite_ignore_and_new_badges(client):
    item = add(client)
    lid = entries(client, item)["links"][0]["id"]
    client.patch(
        "/api/links/" + lid, json={"read": True, "favorite": True, "ignored": True}
    )
    client.fake.result = scan(
        [
            Entry(ROOT + "posts/one", "One edited", "2026-01-01T00:00:00+00:00", 1),
            Entry(ROOT + "posts/two", "Two", "2026-01-02T00:00:00+00:00", 2),
        ]
    )
    r = client.post(f"/api/items/{item['id']}/refresh").json()
    assert r["new_count"] == 1
    old = entries(client, item, filter="ignored")["links"][0]
    assert (
        old["read"]
        and old["favorite"]
        and old["ignored"]
        and old["title"] == "One edited"
    )
    assert client.post(f"/api/items/{item['id']}/refresh").json()["new_count"] == 0
    new = entries(client, item, filter="new")["links"]
    assert len(new) == 1
    client.patch("/api/links/" + new[0]["id"], json={"read": True})
    assert entries(client, item, filter="new")["total"] == 0


def test_failed_and_empty_refresh_keep_existing_data_and_last_success(client):
    item = add(client)
    for failure in [DiscoveryError("Access denied"), scan([])]:
        client.fake.result = failure
        r = client.post(f"/api/items/{item['id']}/refresh").json()
        assert not r["ok"]
        after = client.get("/api/items/" + item["id"]).json()
        assert (
            after["total_count"] == 1
            and after["last_checked_at"] == item["last_checked_at"]
            and after["error"]
        )


def test_missing_links_are_retained(client):
    item = add(client)
    client.fake.result = scan([Entry(ROOT + "posts/two", "Two", number=2)])
    client.post(f"/api/items/{item['id']}/refresh")
    assert entries(client, item)["total"] == 2


def test_partial_refresh_preserves_source_neighbor_order(client):
    client.fake.result = scan(
        [
            Entry(ROOT + n, n, position=i)
            for i, n in enumerate(["One", "Interlude", "Two"])
        ]
    )
    item = add(client)
    client.fake.result = scan(
        [Entry(ROOT + n, n, position=i) for i, n in enumerate(["One", "Two", "Three"])]
    )
    client.post(f"/api/items/{item['id']}/refresh")
    assert [e["title"] for e in entries(client, item)["links"]] == [
        "One",
        "Interlude",
        "Two",
        "Three",
    ]


def test_ignore_excludes_counts_restore_keeps_state(client):
    item = add(client)
    lid = entries(client, item)["links"][0]["id"]
    client.patch("/api/links/" + lid, json={"ignored": True, "favorite": True})
    after = client.get("/api/items/" + item["id"]).json()
    assert after["unread_count"] == 0 and after["ignored_count"] == 1
    client.patch("/api/links/" + lid, json={"ignored": False})
    assert entries(client, item, filter="favorites")["total"] == 1


def test_refresh_all_skips_ignored_items(client):
    item = add(client)
    client.patch("/api/items/" + item["id"], json={"ignored": True})
    before = len(client.fake.calls)
    r = client.post("/api/refresh").json()
    assert r["checked"] == 0 and len(client.fake.calls) == before


def test_acknowledge_does_not_mark_read(client):
    item = add(client)
    client.fake.result = scan([Entry(ROOT + "posts/two", "Two", number=2)])
    client.post(f"/api/items/{item['id']}/refresh")
    r = client.post(f"/api/items/{item['id']}/acknowledge").json()
    assert r["new_count"] == 0 and r["unread_count"] == 2


def test_bulk_read_excludes_ignored(client):
    item = add(client)
    lid = entries(client, item)["links"][0]["id"]
    client.patch("/api/links/" + lid, json={"ignored": True})
    client.post(f"/api/items/{item['id']}/read")
    assert not entries(client, item, filter="ignored")["links"][0]["read"]


def test_numeric_sort_decimal_and_pagination(client):
    client.fake.result = scan(
        [Entry(ROOT + str(n), "Chapter " + str(n), number=n) for n in (10, 2, 9.5, 1)]
    )
    item = add(client)
    assert [e["number"] for e in entries(client, item, limit=2)["links"]] == [1, 2]
    assert [e["number"] for e in entries(client, item, offset=2, limit=2)["links"]] == [
        9.5,
        10,
    ]
    assert (
        entries(client, item, sort="number", direction="desc")["links"][0]["number"]
        == 10
    )


def test_missing_numbers_use_source_order_not_opaque_ids(client):
    client.fake.result = scan(
        [
            Entry(ROOT + "a", "Chapter 1", number=1, position=0),
            Entry(ROOT + "b", "Interlude", position=1),
            Entry(ROOT + "c", "Chapter 2", number=2, position=2),
        ]
    )
    item = add(client)
    result = entries(client, item)
    assert (
        result["sort_used"] == "source" and result["links"][1]["title"] == "Interlude"
    )


def test_publication_sort_uses_parsed_dates_not_url_numbers(client):
    client.fake.result = scan(
        [
            Entry(ROOT + "999", "Earlier", "2020-01-01T00:00:00+00:00"),
            Entry(ROOT + "1", "Later", "2026-01-01T00:00:00+00:00"),
        ]
    )
    item = add(client)
    assert entries(client, item)["links"][0]["title"] == "Earlier"


def test_royalroad_slug_change_retains_link_identity(client):
    client.fake.result = scan(
        [
            Entry(
                "https://www.royalroad.com/fiction/21220/book/chapter/301778/old",
                "1. Old",
                number=1,
            )
        ]
    )
    item = add(client)
    lid = entries(client, item)["links"][0]["id"]
    client.patch("/api/links/" + lid, json={"read": True})
    client.fake.result = scan(
        [
            Entry(
                "https://www.royalroad.com/fiction/21220/book/chapter/301778/new",
                "1. New",
                number=1,
            )
        ]
    )
    assert client.post(f"/api/items/{item['id']}/refresh").json()["new_count"] == 0
    assert (
        entries(client, item)["links"][0]["id"] == lid
        and entries(client, item)["links"][0]["read"]
    )


def test_search_escapes_wildcards_and_missing_records(client):
    item = add(client)
    assert entries(client, item, search="%")["total"] == 0
    assert client.get("/api/items/missing").status_code == 404
    assert client.patch("/api/links/missing", json={"read": True}).status_code == 404
    assert client.get(f"/api/items/{item['id']}/links?limit=100000").status_code == 422


def test_cross_origin_mutation_and_dns_rebinding_host_rejected(client):
    assert (
        client.post(
            "/api/scans", json={"url": ROOT}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/items", headers={"Host": "evil.example"}).status_code == 400


def test_bulk_link_actions_and_tombstones_survive_refresh(client):
    item = add(client)
    lid = entries(client, item)["links"][0]["id"]

    def bulk(action):
        return client.post(
            "/api/links/bulk",
            json={"ids": [lid], "item_id": item["id"], "action": action},
        )

    for action in ["favorite", "ignore", "delete"]:
        assert bulk(action).json()["updated"] == 1
    assert entries(client, item)["total"] == 0
    assert client.get("/api/items/" + item["id"]).json()["unread_count"] == 0
    assert client.post(f"/api/items/{item['id']}/refresh").json()["new_count"] == 0
    deleted = entries(client, item, filter="trash")["links"][0]
    assert deleted["favorite"] and deleted["ignored"] and deleted["deleted"]
    assert bulk("restore").status_code == 200
    assert bulk("unignore").status_code == 200
    assert entries(client, item)["total"] == 1
    assert bulk("read").status_code == 200
    assert entries(client, item)["links"][0]["read"]
    assert bulk("unread").status_code == 200
    assert not entries(client, item)["links"][0]["read"]


def test_deleted_items_are_recoverable_and_skipped_by_refresh_all(client):
    item = add(client)
    assert (
        client.post(
            "/api/items/bulk", json={"ids": [item["id"]], "action": "delete"}
        ).status_code
        == 200
    )
    assert client.get("/api/items").json() == []
    assert client.get("/api/items?trash=true").json()[0]["id"] == item["id"]
    assert client.post("/api/refresh").json()["checked"] == 0
    assert (
        client.post(
            "/api/items/bulk", json={"ids": [item["id"]], "action": "restore"}
        ).status_code
        == 200
    )
    assert client.get("/api/items").json()[0]["total_count"] == 1


def test_bulk_invalid_selection_is_atomic_and_cannot_cross_collections(client):
    item = add(client)
    lid = entries(client, item)["links"][0]["id"]
    for ids, iid in [([lid, "missing"], item["id"]), ([lid], "other")]:
        r = client.post(
            "/api/links/bulk", json={"ids": ids, "item_id": iid, "action": "favorite"}
        )
        assert r.status_code == 404
        assert not entries(client, item)["links"][0]["favorite"]
    assert (
        client.post("/api/links/bulk", json={"ids": [], "action": "delete"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/items/bulk", json={"ids": [item["id"]], "action": "DROP TABLE"}
        ).status_code
        == 422
    )


def test_date_metadata_and_unknown_dates_sort_last_in_both_directions(client):
    client.fake.result = scan(
        [
            Entry(
                ROOT + "one",
                "One",
                "2026-01-01T12:00:00+00:00",
                date_kind="listed",
                date_source="row timestamp",
                date_precision="time",
            ),
            Entry(ROOT + "unknown", "Unknown", position=1),
            Entry(ROOT + "two", "Two", "2026-02-01T12:00:00+00:00", position=2),
        ]
    )
    item = add(client)
    for direction in ["asc", "desc"]:
        rows = entries(client, item, sort="date", direction=direction)["links"]
        assert rows[-1]["title"] == "Unknown"
        one = next(e for e in rows if e["title"] == "One")
        assert (
            one["date_kind"] == "listed"
            and one["date_source"] == "row timestamp"
            and one["date_precision"] == "time"
        )
    client.fake.result = scan([Entry(ROOT + "one", "One")])
    client.post(f"/api/items/{item['id']}/refresh")
    one = next(e for e in entries(client, item)["links"] if e["title"] == "One")
    assert one["published_at"] and one["date_source"] == "row timestamp"


def test_cached_refresh_keeps_source_checked_time(client):
    client.fake.result.checked_at = "2026-09-08T00:00:00+00:00"
    item = add(client)
    client.fake.result.cached = True
    r = client.post(f"/api/items/{item['id']}/refresh").json()
    assert r["cached"] and r["checked_at"] == item["last_checked_at"]
    assert (
        client.get("/api/items/" + item["id"]).json()["last_checked_at"]
        == item["last_checked_at"]
    )


def test_upcoming_filter_uses_event_time_not_discovery_time(client):
    client.fake.result = scan(
        [
            Entry(
                ROOT + "future",
                "Future",
                "2099-01-01T00:00:00+00:00",
                date_kind="scheduled",
            ),
            Entry(
                ROOT + "past",
                "Past",
                "2020-01-01T00:00:00+00:00",
                date_kind="scheduled",
            ),
            Entry(
                ROOT + "post",
                "Post",
                "2099-01-01T00:00:00+00:00",
                date_kind="published",
            ),
        ]
    )
    item = add(client)
    assert [e["title"] for e in entries(client, item, filter="upcoming")["links"]] == [
        "Future"
    ]


def test_v1_database_migration_preserves_existing_user_state(tmp_path):
    import sqlite3

    dbpath = tmp_path / "v1.sqlite3"
    store = Store(dbpath)
    sid = store.save_scan(scan().to_dict())
    item = store.create(sid)
    lid = store.links(item["id"])["links"][0]["id"]
    store.update("links", lid, {"read": True, "favorite": True})
    with sqlite3.connect(dbpath) as db:
        for col in [
            "deleted",
            "date_kind",
            "date_source",
            "date_precision",
            "summary",
            "availability",
        ]:
            db.execute(f"ALTER TABLE links DROP COLUMN {col}")
        for col in ["deleted", "requests_made", "cache_hits", "coverage"]:
            db.execute(f"ALTER TABLE items DROP COLUMN {col}")
        db.execute("PRAGMA user_version=1")
    migrated = Store(dbpath)
    e = migrated.links(item["id"])["links"][0]
    assert e["id"] == lid and e["read"] and e["favorite"] and not e["deleted"]
