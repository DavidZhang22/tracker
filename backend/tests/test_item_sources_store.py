import json
from unittest.mock import patch

import pytest

from app.tracker.models import Entry, Scan
from app.tracker.store import Store

ROOT = "https://example.org/series"


def payload(entries=None, url=ROOT, **extra):
    scan = Scan(
        url,
        "Original collection",
        entries=entries
        if entries is not None
        else [Entry(ROOT + "/one", "One"), Entry(ROOT + "/two", "Two")],
        methods=["page"],
        pages_scanned=3,
        order_hint="source",
        warnings=["Original warning"],
        requests_made=4,
    ).to_dict()
    return scan | extra


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "library.sqlite3")


def create(store, **extra):
    with store.connection() as db:
        db.execute("DELETE FROM addition_cooldown")
    return store.create(store.save_scan(payload(**extra)))


def preview(store, iid, entries=None, **extra):
    return store.save_scan(
        payload(
            entries=entries
            if entries is not None
            else [Entry("", "Manual task", context="Practice")],
            source_type="document",
            source_name="Manual entries",
            manual_import=True,
            import_item_id=iid,
            **extra,
        )
    )


def raw_item(store, iid):
    with store.connection() as db:
        return dict(db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone())


def raw_links(store, iid):
    with store.connection() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM links WHERE item_id=? ORDER BY position,id", (iid,)
            )
        ]


def test_legacy_sources_derive_from_web_identity_and_imports_stay_offline(store):
    web = create(store)
    imported = create(store, url="document:upload", source_type="document")
    csv = create(store, url="csv:upload", source_type="csv")
    with store.connection() as db:
        db.execute("ALTER TABLE items DROP COLUMN source_urls")
    migrated = Store(store.path)
    assert migrated.item(web["id"])["source_urls"] == [ROOT]
    assert migrated.refresh_source(web["id"])["source_urls"] == [ROOT]
    assert migrated.item(imported["id"])["source_urls"] == []
    assert migrated.item(csv["id"])["source_urls"] == []
    assert migrated.refresh_sources() == [
        {"id": web["id"], "url": ROOT, "source_urls": [ROOT]}
    ]
    assert raw_item(migrated, web["id"])["source_urls"] is None


def test_source_edits_are_normalized_offline_and_survive_refresh_and_restart(store):
    item = create(store)
    urls = [
        "https://EXAMPLE.org/new/?utm_source=tracker",
        "https://other.example.org/feed",
    ]
    with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS lookup")):
        store.update("items", item["id"], {"source_urls": urls})
    expected = ["https://example.org/new", urls[1]]
    store.merge(item["id"], payload())
    reopened = Store(store.path)
    assert reopened.item(item["id"])["source_urls"] == expected
    assert reopened.item(item["id"])["url"] == ROOT
    assert reopened.refresh_sources()[0] == {
        "id": item["id"],
        "url": expected[0],
        "source_urls": expected,
    }


def test_explicit_empty_sources_disable_refresh_and_import_sources_enable_it(store):
    item = create(store)
    imported = create(store, url="document:upload", source_type="document")
    store.update("items", item["id"], {"source_urls": []})
    store.update("items", imported["id"], {"source_urls": [ROOT]})
    assert store.item(item["id"])["source_urls"] == []
    assert store.refresh_sources() == [
        {"id": imported["id"], "url": ROOT, "source_urls": [ROOT]}
    ]
    assert store.item(imported["id"])["source_type"] == "document"
    assert store.item(imported["id"])["url"] == "document:upload"
    store.update("items", imported["id"], {"favorite": True, "ignored": True})
    assert store.refresh_sources() == []
    store.update("items", imported["id"], {"ignored": False})
    store.bulk_selected("items", [imported["id"]], "delete")
    assert store.refresh_sources() == []


@pytest.mark.parametrize(
    "urls",
    [
        None,
        ROOT,
        [None],
        ["javascript:alert(1)"],
        ["file:///etc/passwd"],
        ["http://localhost"],
        ["http://127.0.0.1"],
        ["http://[::1]"],
        ["http://service.internal"],
        ["http://2130706433"],
        ["http://user:pass@example.org"],
        ["https://example.org:8080"],
        [ROOT, ROOT + "/"],
        ["https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        [f"https://example.org/{i}" for i in range(6)],
    ],
)
def test_invalid_source_edits_reject_all_fields_atomically(store, urls):
    item = create(store)
    before = raw_item(store, item["id"])
    with pytest.raises(ValueError):
        store.update(
            "items", item["id"], {"source_urls": urls, "title": "Must not save"}
        )
    assert raw_item(store, item["id"]) == before


def test_append_consumes_preview_without_changing_item_metadata_or_existing_progress(
    store,
):
    item = create(store)
    iid = item["id"]
    store.update(
        "items",
        iid,
        {
            "source_urls": [ROOT, "https://other.example.org/feed"],
            "title": "My collection",
            "favorite": True,
            "ignored": True,
            "auto_read": False,
            "selector": "article a",
            "include_path": "/chapters",
            "keywords": "English",
            "source_method": "page",
            "description_override": "My notes",
        },
    )
    rows = raw_links(store, iid)
    store.group_links(iid, [rows[1]["id"]], "merge", sort="source", direction="asc")
    store.update(
        "links", rows[0]["id"], {"read": True, "favorite": True, "ignored": True}
    )
    store.failure(iid, "Source currently unavailable")
    before_item = raw_item(store, iid)
    before_links = raw_links(store, iid)
    sid = preview(
        store,
        iid,
        [
            Entry("", "Manual task", context="Practice"),
            Entry("https://example.org/new", "New"),
        ],
    )
    assert store.append_entries(iid, sid) == 2
    assert raw_item(store, iid) == before_item
    after_links = raw_links(store, iid)
    assert after_links[:2] == before_links
    assert [row["title"] for row in after_links[2:]] == ["Manual task", "New"]
    assert all(row["is_new"] and not row["read"] for row in after_links[2:])
    assert after_links[2]["context"] == "Practice" and after_links[2]["url"] == ""
    with pytest.raises(ValueError, match="expired"):
        store.append_entries(iid, sid)
    assert len(raw_links(store, iid)) == 4


def test_append_merges_duplicates_and_preserves_progress_and_source_order(store):
    item = create(store)
    iid = item["id"]
    original = raw_links(store, iid)[0]
    store.update(
        "links", original["id"], {"read": True, "favorite": True, "ignored": True}
    )
    store.bulk_selected("links", [original["id"]], "delete", iid)
    sid = preview(
        store,
        iid,
        [
            Entry(ROOT + "/one/", "One updated"),
            Entry("", "Practice task"),
            Entry("", "Practice task"),
        ],
    )
    assert store.append_entries(iid, sid) == 1
    rows = raw_links(store, iid)
    assert [row["title"] for row in rows] == ["One updated", "Two", "Practice task"]
    assert rows[0]["id"] == original["id"]
    assert all(rows[0][key] for key in ("read", "favorite", "ignored", "deleted"))


@pytest.mark.parametrize(
    "changes",
    [
        {"import_item_id": "some-other-item"},
        {"manual_import": False},
        {"source_type": "web"},
        {"entries": []},
    ],
)
def test_append_rejects_unbound_or_empty_previews_without_consuming_them(
    store, changes
):
    item = create(store)
    scan = (
        payload(source_type="document", manual_import=True, import_item_id=item["id"])
        | changes
    )
    sid = store.save_scan(scan)
    before = raw_links(store, item["id"])
    with pytest.raises(ValueError, match="Preview entries"):
        store.append_entries(item["id"], sid)
    assert raw_links(store, item["id"]) == before
    with store.connection() as db:
        assert db.execute("SELECT 1 FROM scans WHERE id=?", (sid,)).fetchone()


def test_append_rejects_trash_missing_items_and_expired_previews(store):
    item = create(store)
    sid = preview(store, item["id"])
    with pytest.raises(KeyError):
        store.append_entries("missing", sid)
    store.bulk_selected("items", [item["id"]], "delete")
    with pytest.raises(ValueError, match="Trash"):
        store.append_entries(item["id"], sid)
    store.bulk_selected("items", [item["id"]], "restore")
    with store.connection() as db:
        db.execute("UPDATE scans SET created_at='2000-01-01' WHERE id=?", (sid,))
    with pytest.raises(ValueError, match="expired"):
        store.append_entries(item["id"], sid)
    assert len(raw_links(store, item["id"])) == 2


@pytest.mark.parametrize("quota", ["MAX_LINKS", "MAX_LIBRARY_LINKS"])
def test_append_quota_failure_rolls_back_all_updates_and_keeps_preview(
    store, monkeypatch, quota
):
    item = create(store)
    sid = preview(
        store, item["id"], [Entry(ROOT + "/one", "Must not update"), Entry("", "New")]
    )
    monkeypatch.setattr("app.tracker.store." + quota, 2)
    before = raw_links(store, item["id"])
    with pytest.raises(ValueError, match="storage limit"):
        store.append_entries(item["id"], sid)
    assert raw_links(store, item["id"]) == before
    with store.connection() as db:
        assert db.execute("SELECT 1 FROM scans WHERE id=?", (sid,)).fetchone()


@pytest.mark.parametrize("source_type", ["csv", "document"])
def test_append_accepts_explicitly_bound_upload_previews(store, source_type):
    item = create(store)
    scan = payload(
        [Entry("", "Uploaded task")],
        source_type=source_type,
        append_entries=True,
        import_item_id=item["id"],
        source_name="Tasks",
    )
    sid = store.save_scan(scan)
    before = raw_item(store, item["id"])
    assert store.append_entries(item["id"], sid) == 1
    assert raw_item(store, item["id"]) == before


def test_update_accepts_five_sources(store):
    item = create(store)
    urls = [f"https://example.org/feed/{n}" for n in range(5)]
    store.update("items", item["id"], {"source_urls": urls})
    assert store.item(item["id"])["source_urls"] == urls
    assert json.loads(raw_item(store, item["id"])["source_urls"]) == urls


def test_refresh_source_precondition_prevents_stale_commits_and_error_writes(store):
    item = create(store)
    iid = item["id"]
    original_sources = item["source_urls"]
    store.update("items", iid, {"source_urls": ["https://example.org/new-source"]})
    before_item, before_links = raw_item(store, iid), raw_links(store, iid)
    assert (
        store.merge(iid, payload([Entry(ROOT + "/stale", "Stale")]), original_sources)
        is None
    )
    assert store.failure(iid, "Stale error", original_sources) is False
    assert raw_item(store, iid) == before_item
    assert raw_links(store, iid) == before_links
    assert (
        store.merge(
            iid,
            payload([Entry(ROOT + "/fresh", "Fresh")]),
            ["https://example.org/new-source"],
        )
        == 1
    )
    assert (
        store.failure(iid, "Current error", ["https://example.org/new-source"]) is True
    )
    assert store.item(iid)["error"] == "Current error"


def test_refresh_source_precondition_also_preserves_trashed_item(store):
    item = create(store)
    iid = item["id"]
    store.bulk_selected("items", [iid], "delete")
    before_item, before_links = raw_item(store, iid), raw_links(store, iid)
    assert (
        store.merge(iid, payload([Entry(ROOT + "/new", "New")]), item["source_urls"])
        is None
    )
    assert store.failure(iid, "Stale error", item["source_urls"]) is False
    assert raw_item(store, iid) == before_item
    assert raw_links(store, iid) == before_links


@pytest.mark.parametrize("flag", ["manual_import", "append_entries"])
def test_add_entries_previews_cannot_be_consumed_by_reimport(store, flag):
    item = create(store, url="document:tasks", source_type="document")
    scan = payload(
        [Entry("", "New task")],
        source_type="document",
        source_name="Wrong metadata",
        import_item_id=item["id"],
    ) | {flag: True}
    sid = store.save_scan(scan)
    before_item, before_links = (
        raw_item(store, item["id"]),
        raw_links(store, item["id"]),
    )
    with pytest.raises(ValueError):
        store.import_csv(item["id"], sid)
    assert raw_item(store, item["id"]) == before_item
    assert raw_links(store, item["id"]) == before_links
    assert store.append_entries(item["id"], sid) == 1


def test_separate_uploaded_links_with_same_title_keep_distinct_urls(store):
    from app.tracker.csv_import import parse_csv

    item = create(store)
    iid = item["id"]
    for suffix in ("one", "two", "one"):
        scan = parse_csv(
            f"Title,URL\nOverview,https://content.example.org/{suffix}\n".encode(),
            "links.csv",
        )
        scan.update(import_item_id=iid, append_entries=True)
        added = store.append_entries(iid, store.save_scan(scan))
    assert added == 0
    urls = {row["url"] for row in raw_links(store, iid)}
    assert urls == {
        ROOT + "/one",
        ROOT + "/two",
        "https://content.example.org/one",
        "https://content.example.org/two",
    }


def test_append_distinguishes_unlinked_notes_and_matches_existing_import_content(store):
    from app.tracker.csv_import import parse_csv

    initial = parse_csv(b"Title,Notes\nPractice,First approach\n", "notes.csv")
    iid = store.create(store.save_scan(initial))["id"]
    original = raw_links(store, iid)[0]
    store.update("links", original["id"], {"read": True, "favorite": True})
    for notes, expected_added in [
        ("First approach", 0),
        ("Second approach", 1),
        ("Second approach", 0),
    ]:
        scan = parse_csv(f"Title,Notes\nPractice,{notes}\n".encode(), "notes.csv")
        scan.update(import_item_id=iid, append_entries=True)
        assert store.append_entries(iid, store.save_scan(scan)) == expected_added
    rows = raw_links(store, iid)
    assert len(rows) == 2
    assert rows[0]["id"] == original["id"] and rows[0]["read"] and rows[0]["favorite"]
    assert "First approach" in rows[0]["context"]
    assert "Second approach" in rows[1]["context"]
