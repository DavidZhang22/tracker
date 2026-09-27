import pytest

from app.tracker.models import Entry
from tests.test_store_api import ROOT, add, entries, scan
from tests.test_store_api import client as client


@pytest.mark.parametrize("merged", [False, True])
def test_filter_counts_match_display_groups_including_muted_and_trash(client, merged):
    client.fake.result = scan(
        [Entry(f"{ROOT}part/{i}", f"Entry {i}", number=i) for i in range(8)]
    )
    item = add(client)
    rows = entries(client, item, sort="number", direction="asc")["links"]
    if merged:
        response = client.post(
            f"/api/items/{item['id']}/link-groups",
            json={
                "ids": [rows[1]["id"], rows[7]["id"]],
                "action": "merge",
                "sort": "number",
                "direction": "asc",
            },
        )
        assert response.status_code == 200
    with client.app.state.store.connection() as db:
        for index, row in enumerate(rows):
            db.execute(
                "UPDATE links SET favorite=?,ignored=?,deleted=?,read=?,is_new=? WHERE id=?",
                (
                    index in (0, 1, 2, 3, 6, 7),
                    index == 2,
                    index in (3, 6, 7),
                    index in (1, 4),
                    index in (0, 1, 5),
                    row["id"],
                ),
            )
    result = client.get(f"/api/items/{item['id']}").json()
    expected = {
        "total_count": 4 if merged else 5,
        "favorites_count": 1 if merged else 2,
        "ignored_count": 1,
        "unread_count": 2,
        "read_count": 1 if merged else 2,
        "new_count": 2,
        "trash_count": 2 if merged else 3,
    }
    assert {key: result[key] for key in expected} == expected
    fields = {
        "favorites": "favorites_count",
        "ignored": "ignored_count",
        "unread": "unread_count",
        "read": "read_count",
        "new": "new_count",
        "trash": "trash_count",
    }
    for filter, field in fields.items():
        assert result[field] == entries(client, item, filter=filter)["total"]
    assert (
        result["total_count"] - result["ignored_count"]
        == entries(client, item)["total"]
    )
    library_item = client.get("/api/items").json()[0]
    assert {key: library_item[key] for key in expected} == expected


def test_empty_item_filter_counts_are_zero_and_do_not_include_other_items(client):
    item = add(client)
    row = entries(client, item)["links"][0]
    client.post(
        "/api/links/bulk",
        json={"ids": [row["id"]], "item_id": item["id"], "action": "delete"},
    )
    result = client.get(f"/api/items/{item['id']}").json()
    assert result["total_count"] == result["favorites_count"] == 0
    assert result["trash_count"] == 1
    with client.app.state.store.connection() as db:
        db.execute("UPDATE addition_cooldown SET completed_at=0")
    client.fake.result = scan([Entry(ROOT + "other/one", "Other")])
    client.fake.result.url = ROOT + "other/"
    other = add(client)
    assert other["favorites_count"] == other["trash_count"] == 0
    library = {row["id"]: row for row in client.get("/api/items").json()}
    assert library[item["id"]]["trash_count"] == 1
    assert library[other["id"]]["trash_count"] == 0


def test_selected_acknowledge_clears_entire_group_without_changing_read_or_other_rows(
    client,
):
    client.fake.result = scan(
        [Entry(f"{ROOT}part/{i}", f"Entry {i}", number=i) for i in range(4)]
    )
    item = add(client)
    rows = entries(client, item, sort="number", direction="asc")["links"]
    client.post(
        f"/api/items/{item['id']}/link-groups",
        json={
            "ids": [rows[1]["id"]],
            "action": "merge",
            "sort": "number",
            "direction": "asc",
        },
    )
    store = client.app.state.store
    with store.connection() as db:
        db.execute("UPDATE links SET is_new=1")
        db.execute("UPDATE links SET read=1 WHERE id=?", (rows[0]["id"],))
    response = client.post(
        "/api/links/bulk",
        json={
            "ids": [rows[1]["id"]],
            "item_id": item["id"],
            "action": "acknowledge",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"updated": 1, "action": "acknowledge"}
    with store.connection() as db:
        after = {row["id"]: dict(row) for row in db.execute("SELECT * FROM links")}
    assert [after[row["id"]]["is_new"] for row in rows] == [0, 0, 1, 1]
    assert [after[row["id"]]["read"] for row in rows] == [1, 0, 0, 0]
    result = client.get(f"/api/items/{item['id']}").json()
    assert result["new_count"] == 2 and result["unread_count"] == 3


def test_selected_acknowledge_is_atomic_and_requires_link_item_scope(client):
    item = add(client)
    lid = entries(client, item)["links"][0]["id"]
    with client.app.state.store.connection() as db:
        db.execute("UPDATE links SET is_new=1")
    for ids, iid, status in [
        ([lid, "missing"], item["id"], 404),
        ([lid], "foreign-item", 404),
        ([lid], None, 422),
    ]:
        response = client.post(
            "/api/links/bulk",
            json={"ids": ids, "item_id": iid, "action": "acknowledge"},
        )
        assert response.status_code == status
        assert entries(client, item)["links"][0]["is_new"]
    response = client.post(
        "/api/items/bulk", json={"ids": [item["id"]], "action": "acknowledge"}
    )
    assert response.status_code == 422
    assert entries(client, item)["links"][0]["is_new"]
