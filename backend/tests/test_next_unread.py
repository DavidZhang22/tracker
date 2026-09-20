import pytest

from app.tracker.models import Entry, Scan
from app.tracker.store import Store

ROOT = "https://example.org/series/"


def create(tmp_path, entries, **kwargs):
    store = Store(tmp_path / "library.db")
    item = store.create(
        store.save_scan(Scan(ROOT, "Reading", entries=entries, **kwargs).to_dict())
    )
    return store, item


def test_next_unread_follows_number_and_skips_read_muted_trash_and_future(tmp_path):
    entries = [
        Entry(ROOT + str(i), f"Chapter {i}", number=i, position=10 - i)
        for i in range(1, 7)
    ]
    entries[3].published_at = "2099-01-01T00:00:00+00:00"
    entries[3].date_kind = "scheduled"
    store, item = create(tmp_path, entries)
    rows = store.links(item["id"], sort="number", direction="asc")["links"]
    assert item["next_unread"]["number"] == 1
    store.update("links", rows[0]["id"], {"read": True})
    store.update("links", rows[1]["id"], {"ignored": True})
    store.bulk_selected("links", [rows[2]["id"]], "delete", item_id=item["id"])
    assert store.item(item["id"])["next_unread"]["number"] == 5
    assert store.item(item["id"])["latest_link"]["number"] == 6
    store.update("links", rows[4]["id"], {"read": True})
    store.update("links", rows[5]["id"], {"read": True})
    assert store.item(item["id"])["next_unread"] is None


@pytest.mark.parametrize("mode,expected", [("date", "Early"), ("source", "First")])
def test_next_unread_uses_dates_or_source_order_when_numbers_missing(
    tmp_path, mode, expected
):
    entries = [
        Entry(
            ROOT + "first",
            "First",
            published_at="2026-02-01T00:00:00+00:00",
            position=0,
        ),
        Entry(
            ROOT + "early",
            "Early",
            published_at="2026-01-01T00:00:00+00:00",
            position=1,
        ),
    ]
    _, item = create(tmp_path, entries, order_hint="source" if mode == "source" else "")
    assert item["next_unread"]["title"] == expected


def test_muted_and_trashed_items_have_no_next_unread(tmp_path):
    store, item = create(tmp_path, [Entry(ROOT + "1", "Chapter 1", number=1)])
    store.update("items", item["id"], {"ignored": True, "favorite": True})
    assert store.item(item["id"])["next_unread"] is None
    store.update("items", item["id"], {"ignored": False})
    store.bulk_selected("items", [item["id"]], "delete")
    assert store.item(item["id"])["next_unread"] is None


def test_linkless_and_merged_entries_return_navigation_metadata_without_marking_read(
    tmp_path,
):
    store, item = create(
        tmp_path,
        [
            Entry("", "Chapter 1", number=1, position=0),
            Entry(ROOT + "2", "Chapter 2", number=2, position=1),
            Entry(ROOT + "3", "Chapter 3", number=3, position=2),
        ],
    )
    assert item["next_unread"]["url"] == "" and item["next_unread"]["link_count"] == 1
    rows = store.links(item["id"], direction="asc")["links"]
    store.update("links", rows[0]["id"], {"read": True})
    store.group_links(
        item["id"], [rows[1]["id"]], "merge", sort="number", direction="asc"
    )
    next_entry = store.item(item["id"])["next_unread"]
    assert next_entry["id"] == rows[0]["id"] and next_entry["link_count"] == 2
    assert not next_entry["read"]
    assert store.links(item["id"], direction="asc")["links"][0]["members"][0]["read"]


def test_latest_preserves_merged_group_metadata(tmp_path):
    store, item = create(
        tmp_path,
        [
            Entry(ROOT + "1", "Chapter 1", number=1),
            Entry(ROOT + "2", "Chapter 2", number=2, position=1),
        ],
    )
    rows = store.links(item["id"], direction="asc")["links"]
    store.group_links(
        item["id"], [rows[1]["id"]], "merge", sort="number", direction="asc"
    )
    latest = store.item(item["id"])["latest_link"]
    assert latest["link_count"] == 2 and latest["id"] == rows[0]["id"]


def test_continue_landing_page_uses_explicit_order_and_correct_unread_offset(tmp_path):
    entries = [
        Entry(
            ROOT + str(i),
            f"Chapter {i}",
            number=i,
            position=100 - i,
            published_at="2099-01-01T00:00:00+00:00",
            date_kind="scheduled",
        )
        for i in range(1, 62)
    ]
    entries.append(Entry("", "Chapter 62", number=62, position=38))
    entries.append(Entry(ROOT + "announcement", "Announcement", position=0))
    store, item = create(tmp_path, entries)
    announcement = next(
        row
        for row in store.links(item["id"], limit=200)["links"]
        if row["title"] == "Announcement"
    )
    store.update("links", announcement["id"], {"ignored": True})
    entry = store.item(item["id"])["next_unread"]
    assert entry["number"] == 62 and entry["sort"] == "number" and entry["offset"] == 50
    landing = store.links(
        item["id"],
        filter="unread",
        sort=entry["sort"],
        direction="asc",
        offset=entry["offset"],
    )
    assert any(row["id"] == entry["id"] for row in landing["links"])
