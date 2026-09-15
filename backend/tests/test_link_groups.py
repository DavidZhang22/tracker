import pytest

from app.tracker.link_groups import pattern_ids
from app.tracker.models import Entry
from app.tracker.store import Store
from tests.test_store_api import ROOT, add, entries, scan
from tests.test_store_api import client as client


@pytest.fixture
def library(client):
    client.fake.result = scan(
        [Entry(f"{ROOT}part/{i}", f"Chapter {i}", number=i) for i in range(1, 61)]
    )
    item = add(client)
    rows = entries(client, item, sort="number", direction="asc", limit=200)["links"]
    return client, item, rows


def group(library, indices, action="merge", **view):
    c, item, rows = library
    return c.post(
        f"/api/items/{item['id']}/link-groups",
        json={
            "ids": [rows[index]["id"] for index in indices],
            "action": action,
            "sort": "number",
            "direction": "asc",
            **view,
        },
    )


def test_consecutive_merge_is_one_group_and_split_preserves_individual_progress(
    library,
):
    c, item, rows = library
    c.patch(f"/api/links/{rows[0]['id']}", json={"read": True})
    c.patch(f"/api/links/{rows[1]['id']}", json={"favorite": True, "ignored": True})
    c.patch(f"/api/links/{rows[1]['id']}", json={"ignored": False})
    result = group(library, [1, 2])
    assert result.status_code == 200 and result.json()["updated"] == 2
    data = entries(c, item, direction="asc")
    merged = data["links"][0]
    assert data["total"] == 58 and merged["link_count"] == 3
    assert [r["id"] for r in merged["members"]] == [r["id"] for r in rows[:3]]
    assert merged["favorite"] and not merged["read"]
    assert c.get(f"/api/items/{item['id']}").json()["total_count"] == 58
    assert group(library, [0], "separate").json()["updated"] == 2
    again = entries(c, item, direction="asc")["links"]
    assert again[0]["read"] and not again[1]["read"] and again[1]["favorite"]


def test_selection_of_first_row_and_page_boundary(library):
    c, item, rows = library
    assert group(library, [0]).json() == {"updated": 0, "skipped": 1}
    assert group(library, [0, 1, 2, 50]).json() == {"updated": 3, "skipped": 1}
    data = entries(c, item, direction="asc", limit=200)
    paired = next(row for row in data["links"] if row["id"] == rows[49]["id"])
    assert [row["id"] for row in paired["members"]] == [
        row["id"] for row in rows[49:51]
    ]


def test_merge_uses_current_filtered_order_and_rejects_stale_or_foreign_ids(library):
    c, item, rows = library
    before = entries(c, item)["total"]
    assert group(library, [1, 8], search="Chapter 2").status_code == 404
    assert entries(c, item)["total"] == before
    assert group(library, [1], direction="desc").status_code == 200
    merged = next(
        row
        for row in entries(c, item, direction="asc")["links"]
        if row["id"] == rows[2]["id"]
    )
    assert [row["id"] for row in merged["members"]] == [rows[2]["id"], rows[1]["id"]]
    assert group(library, [1]).status_code == 404
    result = c.post(
        f"/api/items/{item['id']}/link-groups",
        json={"ids": [rows[0]["id"], "foreign"], "action": "merge"},
    )
    assert result.status_code == 404


def test_merging_existing_groups_keeps_flat_members_and_survives_refresh_restart(
    library,
):
    c, item, rows = library
    group(library, [1, 3])
    assert group(library, [2]).json()["updated"] == 1
    assert c.post(f"/api/items/{item['id']}/refresh").status_code == 200
    store = Store(c.app.state.store.path)
    first = store.links(item["id"], direction="asc")["links"][0]
    assert first["link_count"] == 4 and len({m["url"] for m in first["members"]}) == 4
    assert store.links(item["id"])["total"] == 57
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM links").fetchone()[0] == 60


@pytest.mark.parametrize(
    "action,field,value",
    [
        ("favorite", "favorite", True),
        ("read", "read", True),
        ("ignore", "ignored", True),
        ("delete", "deleted", True),
    ],
)
def test_group_actions_reach_every_member_and_trash_does_not_return_on_refresh(
    library, action, field, value
):
    c, item, rows = library
    group(library, [1, 2])
    r = c.post(
        "/api/links/bulk",
        json={"ids": [rows[0]["id"]], "item_id": item["id"], "action": action},
    )
    assert r.status_code == 200
    c.post(f"/api/items/{item['id']}/refresh")
    filter = (
        "trash" if action == "delete" else "ignored" if action == "ignore" else "all"
    )
    grouped = entries(c, item, direction="asc", filter=filter)["links"][0]
    assert grouped[field] == value and all(
        member[field] == value for member in grouped["members"]
    )
    if action == "delete":
        assert group(library, [0], "separate", filter="trash").status_code == 422
        c.post(
            "/api/links/bulk",
            json={"ids": [rows[0]["id"]], "item_id": item["id"], "action": "restore"},
        )
        assert entries(c, item, direction="asc")["links"][0]["link_count"] == 3


def test_single_patch_and_range_actions_update_group_members(library):
    c, item, rows = library
    group(library, [1, 2])
    c.patch(f"/api/links/{rows[1]['id']}", json={"ignored": True})
    assert entries(c, item, filter="ignored")["total"] == 1
    c.patch(f"/api/links/{rows[0]['id']}", json={"ignored": False})
    c.post(
        f"/api/items/{item['id']}/read-range",
        json={
            "anchor_id": rows[3]["id"],
            "side": "before",
            "direction": "asc",
            "sort": "number",
        },
    )
    grouped = entries(c, item, direction="asc")["links"][0]
    assert grouped["read"] and all(member["read"] for member in grouped["members"])


def test_search_matches_urls_context_unicode_and_group_members_literally(library):
    c, item, rows = library
    store = c.app.state.store
    with store.connection() as db:
        db.execute(
            "UPDATE links SET title=?,summary=?,language=? WHERE id=?",
            ("Straße 100%", "Remote engineering", "English", rows[1]["id"]),
        )
    group(library, [1])
    for query in ["STRASSE", "100%", "remote", "English", "part/2"]:
        data = entries(c, item, search=query)
        assert rows[0]["id"] in [r["id"] for r in data["links"]]
    assert entries(c, item, search="%_")["total"] == 0
    assert entries(c, item, search="  Remote  ")["total"] == 1


def test_pattern_selection_combines_interval_and_range_across_pages(library):
    c, item, rows = library
    response = c.post(
        f"/api/items/{item['id']}/link-selection",
        json={
            "direction": "asc",
            "sort": "number",
            "pattern": {"every": 2, "starting": 2, "first": 1, "last": 49},
        },
    )
    assert response.json()["ids"] == [r["id"] for r in rows[1:49:2]]
    response = c.post(
        f"/api/items/{item['id']}/link-selection",
        json={"direction": "asc", "pattern": {"every": 3, "starting": 1, "first": 48}},
    )
    assert response.json()["ids"] == [rows[i - 1]["id"] for i in (49, 52, 55, 58)]
    assert len(pattern_ids(list(range(60)), every=1, first=1, last=49)) == 49


@pytest.mark.parametrize(
    "pattern",
    [
        {"every": 0},
        {"every": 5000},
        {"every": 2, "starting": 3},
        {"first": 50, "last": 1},
        {"every": "2;DROP TABLE links"},
    ],
)
def test_invalid_patterns_are_rejected(library, pattern):
    c, item, _ = library
    assert (
        c.post(
            f"/api/items/{item['id']}/link-selection", json={"pattern": pattern}
        ).status_code
        == 422
    )


def test_alias_dedup_keeps_manual_group_reachable(tmp_path):
    store = Store(tmp_path / "tracker.sqlite3")
    original = [
        Entry(ROOT + "old", "Original", number=1, source_id="one"),
        Entry(ROOT + "new", "Alias", number=2),
        Entry(ROOT + "third", "Third", number=3),
    ]
    item = store.create(store.save_scan(scan(original).to_dict()))
    rows = store.links(item["id"], direction="asc")["links"]
    store.group_links(item["id"], [rows[2]["id"]], "merge", direction="asc")
    store.merge(
        item["id"],
        scan(
            [Entry(ROOT + "new", "Updated", number=1, source_id="one"), original[2]]
        ).to_dict(),
    )
    data = store.links(item["id"])
    assert data["total"] == 1 and data["links"][0]["link_count"] == 2
    assert {m["url"] for m in data["links"][0]["members"]} == {
        ROOT + "new",
        ROOT + "third",
    }


def test_alias_dedup_never_restores_a_trashed_group(tmp_path):
    store = Store(tmp_path / "tracker.sqlite3")
    original = [
        Entry(ROOT + "old", "Original", number=1, source_id="one"),
        Entry(ROOT + "new", "Alias", number=2),
        Entry(ROOT + "third", "Third", number=3),
    ]
    item = store.create(store.save_scan(scan(original).to_dict()))
    rows = store.links(item["id"], direction="asc")["links"]
    store.group_links(item["id"], [rows[2]["id"]], "merge", direction="asc")
    store.bulk_selected("links", [rows[0]["id"]], "delete", item["id"])
    store.merge(
        item["id"],
        scan(
            [Entry(ROOT + "new", "Updated", number=1, source_id="one"), original[2]]
        ).to_dict(),
    )
    assert store.links(item["id"])["total"] == 0
    assert store.links(item["id"], filter="trash")["links"][0]["link_count"] == 2
