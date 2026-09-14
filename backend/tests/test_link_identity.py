import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.tracker.models import Entry, Scan
from app.tracker.parser import merge_entries
from app.tracker.recipes import analyze
from app.tracker.store import Store
from app.tracker.urls import content_key
from tests.test_asura_dates import HTML, SOURCE

OLD = SOURCE + "/chapter/166"
NEW = OLD.replace("53fc8424", "6f7fe6eb")


def listing(*entries):
    return Scan(SOURCE, "Dungeon Odyssey", entries=list(entries)).to_dict()


@pytest.mark.parametrize(
    "url",
    [
        NEW,
        NEW + "/",
        NEW + "#comments",
        NEW + "?utm_source=reader",
        NEW.replace("https://", "http://www."),
    ],
)
def test_asura_rotating_suffix_is_the_same_chapter(url):
    assert content_key(OLD) == content_key(url)


@pytest.mark.parametrize(
    "url",
    [
        NEW.replace("166", "167"),
        NEW + ".5",
        NEW.replace("dungeon-odyssey", "another-series"),
        NEW + "?language=es",
        NEW.replace("asurascans.com", "other.example"),
        NEW.replace("6f7fe6eb", "special-edition"),
    ],
)
def test_distinct_content_is_never_merged_by_chapter_number_alone(url):
    assert content_key(OLD) != content_key(url)


def test_general_url_normalization_keeps_meaningful_query_values():
    assert content_key(
        "https://BLOG.example:443/post/?b=2&utm_medium=feed&a=1#comments"
    ) == content_key("https://blog.example/post?a=1&b=2")
    assert content_key("https://blog.example/post?id=1") != content_key(
        "https://blog.example/post?id=2"
    )


def test_duplicate_entries_update_in_place_and_retain_best_date(tmp_path):
    store = Store(tmp_path / "library.db")
    item = store.create(
        store.save_scan(
            listing(
                Entry(OLD, "Old", "2026-09-01T00:00:00+00:00", 166),
                Entry(NEW, "Newest title", number=166),
            )
        ),
        read_indices=[1],
    )
    row = store.links(item["id"])["links"][0]
    assert item["total_count"] == 1 and row["read"]
    assert row["url"] == NEW and row["title"] == "Newest title"
    assert row["published_at"].startswith("2026-09-01")
    store.update("links", row["id"], {"favorite": True, "ignored": True})
    refreshed = listing(Entry(NEW, "Updated title", "2026-09-02T00:00:00+00:00", 166))
    # Overlapping refresh commits cannot create an extra copy either.
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert (
            list(pool.map(lambda _: store.merge(item["id"], refreshed), range(4)))
            == [0] * 4
        )
    after = store.links(item["id"], filter="ignored")["links"][0]
    assert after["id"] == row["id"] and after["read"] and after["favorite"]
    assert after["title"] == "Updated title" and after["published_at"].startswith(
        "2026-09-02"
    )
    assert store.item(item["id"])["total_count"] == 1


def seed_legacy(path):
    with patch("app.tracker.store.identity", side_effect=lambda url: url):
        store = Store(path)
        item = store.create(
            store.save_scan(
                listing(
                    Entry(
                        OLD,
                        "Old",
                        "2026-09-01T12:00:00+00:00",
                        166,
                        date_precision="time",
                        date_source="Astro chapter published_at",
                    )
                )
            )
        )
        original = store.links(item["id"])["links"][0]
        store.update("links", original["id"], {"read": True, "favorite": True})
        store.merge(item["id"], listing(Entry(NEW, "New title")))
        with store.connection() as db:
            db.execute("UPDATE links SET ignored=1,deleted=1 WHERE url=?", (NEW,))
            db.execute("PRAGMA user_version=7")
    return item, original


def test_migration_repairs_existing_duplicates_preserves_states_and_is_idempotent(
    tmp_path,
):
    path = tmp_path / "library.db"
    item, original = seed_legacy(path)
    store = Store(path)
    with store.connection() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM links")]
        assert db.execute("PRAGMA user_version").fetchone()[0] >= 8
    assert len(rows) == 1
    row = rows[0]
    assert (
        row["id"] == original["id"]
        and row["discovered_at"] == original["discovered_at"]
    )
    assert all(row[k] for k in ("read", "favorite", "ignored", "deleted"))
    assert not row["is_new"]
    assert row["url"] == NEW and row["title"] == "New title" and row["number"] == 166
    assert (
        row["published_at"] == original["published_at"]
        and row["date_source"] == original["date_source"]
    )
    assert store.merge(item["id"], listing(Entry(NEW, "Latest title", number=166))) == 0
    assert store.item(item["id"])["total_count"] == 0  # Trash stays in Trash.
    with store.connection() as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM links")]
    reopened = Store(path)
    with reopened.connection() as db:
        assert before == [tuple(r) for r in db.execute("SELECT * FROM links")]


def test_migration_failure_rolls_back_removed_duplicates_and_version(tmp_path):
    path = tmp_path / "library.db"
    seed_legacy(path)
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM links ORDER BY id").fetchall()
        db.execute(
            "CREATE TRIGGER fail_identity BEFORE UPDATE OF identity ON links BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="simulated write failure"):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM links ORDER BY id").fetchall() == before
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7


def test_exact_url_duplicates_with_legacy_keys_are_repaired_per_item(tmp_path):
    path = tmp_path / "library.db"
    with patch("app.tracker.store.time", side_effect=range(1000, 10000, 8)):
        store = Store(path)
        a = store.create(store.save_scan(listing(Entry(OLD, "Original"))))
        b = store.create(
            store.save_scan(
                Scan(
                    "https://example.org/list",
                    "Another list",
                    entries=[Entry(OLD, "Shared link")],
                ).to_dict()
            )
        )
    with store.connection() as db:
        db.execute("UPDATE links SET identity='legacy-key' WHERE item_id=?", (a["id"],))
    store.merge(a["id"], listing(Entry(OLD, "Updated")))
    with store.connection() as db:
        db.execute("PRAGMA user_version=7")
    store = Store(path)
    assert store.item(a["id"])["total_count"] == store.item(b["id"])["total_count"] == 1


def test_discovery_collapses_asura_aliases_before_preview():
    entries = merge_entries(
        [
            Entry(OLD, "Chapter 166"),
            Entry(NEW, "Chapter 166"),
            Entry(NEW + ".5", "Chapter 166.5"),
        ]
    )
    assert len(entries) == 2
    assert entries[0].url == NEW


def test_light_and_deep_refresh_update_rotated_asura_urls_without_duplicates(tmp_path):
    initial, recipe, _ = analyze(HTML, SOURCE)
    assert recipe
    store = Store(tmp_path / "library.db")
    item = store.create(store.save_scan(initial[0].to_dict()), mark_read=True)
    source = SOURCE.replace("53fc8424", "6f7fe6eb")
    html = HTML.replace("53fc8424", "6f7fe6eb")
    # A URL shape change falls back safely and learns a fresh lightweight recipe.
    changed, recipe, _ = analyze(html, source, recipe=recipe)
    light, _, used = analyze(html, source, recipe=recipe)
    assert used
    deep, _, _ = analyze(html, source)
    for result in (changed, light, deep, light):
        assert store.merge(item["id"], result[0].to_dict()) == 0
    links = store.links(item["id"])["links"]
    assert len(links) == 7 and all(e["read"] and "6f7fe6eb" in e["url"] for e in links)
    assert len({e["identity"] for e in links}) == 7
