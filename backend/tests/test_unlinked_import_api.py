import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.entry_identity import record_id
from app.tracker.models import Entry, Scan
from app.tracker.store import Store
from tests.test_store_api import FakeDiscoverer


@pytest.fixture
def client(tmp_path):
    app = create_app(
        tmp_path / "library.db", FakeDiscoverer(), auth_config={"required": False}
    )
    with TestClient(app) as session:
        yield session


def preview(client, text, filename="study.csv", **params):
    result = client.post(
        "/api/scans/import",
        params={"filename": filename, **params},
        content=text.encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert result.status_code == 200, result.text
    return result.json()


def saved_links(client, iid, **params):
    result = client.get(
        f"/api/items/{iid}/links",
        params={"sort": "source", "direction": "asc", **params},
    )
    assert result.status_code == 200, result.text
    return result.json()["links"]


@pytest.mark.parametrize(
    ("filename", "text"),
    [
        (
            "study.csv",
            "Title,Notes\n3. Longest Substring Without Repeating Characters,Sliding window + last-seen indices\n56. Merge Intervals,Sort by start then merge\n347. Top K Frequent Elements,Min-heap and frequency buckets",
        ),
        (
            "study.txt",
            "**3. Longest Substring Without Repeating Characters**Sliding window + last-seen indices**56. Merge Intervals**Sort by start then merge**347. Top K Frequent Elements**Min-heap and frequency buckets",
        ),
    ],
)
def test_unlinked_preview_save_progress_and_reopen(client, filename, text):
    scan = preview(client, text, filename)
    assert len(scan["entries"]) == 3
    assert all(entry["url"] == "" for entry in scan["entries"])
    assert len({entry["source_id"] for entry in scan["entries"]}) == 3
    assert "Sliding window" in scan["entries"][0]["context"]
    assert client.get("/api/items").json() == []
    response = client.post(
        "/api/items",
        json={"scan_id": scan["scan_id"], "title": "Study plan", "read_indices": [1]},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["title"] == "Study plan" and item["total_count"] == 3
    assert item["read_count"] == 1
    rows = saved_links(client, item["id"])
    assert [row["read"] for row in rows] == [False, True, False]
    assert all(row["url"] == "" for row in rows)
    response = client.patch(
        f"/api/links/{rows[0]['id']}",
        json={"read": True, "favorite": True, "ignored": True},
    )
    assert response.status_code == 200, response.text
    muted = saved_links(client, item["id"], filter="ignored")
    assert len(muted) == 1
    assert muted[0]["read"] and muted[0]["favorite"]
    reopened = Store(client.app.state.store.path)
    assert reopened.item(item["id"])["total_count"] == 3
    assert (
        reopened.links(item["id"], filter="ignored")["links"][0]["id"] == rows[0]["id"]
    )
    assert client.post(f"/api/items/{item['id']}/refresh").status_code == 422
    assert client.post("/api/refresh").json()["checked"] == 0
    assert not client.app.state.discoverer.calls


@pytest.mark.parametrize("title", ["One", "3. Longest Substring"])
def test_unlinked_reimport_reorders_updates_notes_and_can_add_a_url(client, title):
    scan = preview(client, f"Title,Notes\n{title},First notes\nTwo,Second notes")
    response = client.post("/api/items", json={"scan_id": scan["scan_id"]})
    assert response.status_code == 201, response.text
    iid = response.json()["id"]
    first, second = saved_links(client, iid)
    assert (
        client.patch(
            f"/api/links/{first['id']}", json={"read": True, "favorite": True}
        ).status_code
        == 200
    )
    revised = preview(
        client,
        f"Title,Notes,URL\nTwo,Updated second notes,\n{title},Updated first notes,https://example.org/one\nThree,New notes,",
        item_id=iid,
    )
    assert len(revised["entries"]) == 3
    response = client.post(
        f"/api/items/{iid}/import", json={"scan_id": revised["scan_id"]}
    )
    assert response.status_code == 200, response.text
    rows = saved_links(client, iid)
    assert len(rows) == 3
    assert [row["title"] for row in rows] == ["Two", title, "Three"]
    assert rows[0]["id"] == second["id"]
    assert rows[1]["id"] == first["id"]
    assert rows[1]["url"] == "https://example.org/one"
    assert rows[1]["read"] and rows[1]["favorite"]
    assert "Updated first notes" in rows[1]["context"]
    assert rows[2]["is_new"]
    assert not client.app.state.discoverer.calls


def test_explicit_no_url_column_ignores_urls_without_navigating(client):
    scan = preview(
        client,
        "Title,Reference\nOne,https://example.org/one\nTwo,https://example.org/two",
        url_column=-1,
    )
    assert len(scan["entries"]) == 2
    assert all(entry["url"] == "" for entry in scan["entries"])
    assert scan["csv"]["selected"]["url"] == -1
    assert "https://example.org/one" in scan["entries"][0]["context"]
    assert not client.app.state.discoverer.calls
    assert (
        client.post(
            "/api/scans/import?filename=study.csv&url_column=-2",
            content=b"Title\nOne",
        ).status_code
        == 422
    )


def test_identity_migration_keeps_unlinked_records_and_user_state(tmp_path):
    store = Store(tmp_path / "library.db")
    scan = Scan(
        "document:test",
        "Study plan",
        entries=[
            Entry("", title, source_id=record_id("study", title), position=i)
            for i, title in enumerate(["One", "Two"])
        ],
    ).to_dict() | {"source_type": "document", "source_name": "study.txt"}
    item = store.create(store.save_scan(scan), read_indices=[1])
    before = store.links(item["id"], sort="source", direction="asc")["links"]
    store.update("links", before[0]["id"], {"favorite": True, "ignored": True})
    with store.connection() as db:
        db.execute("PRAGMA user_version=7")
    reopened = Store(store.path)
    assert reopened.item(item["id"])["total_count"] == 2
    with reopened.connection() as db:
        rows = db.execute("SELECT * FROM links ORDER BY position").fetchall()
    assert [row["id"] for row in rows] == [row["id"] for row in before]
    assert rows[0]["favorite"] and rows[0]["ignored"] and rows[1]["read"]
