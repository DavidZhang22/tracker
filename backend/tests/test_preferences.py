import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.models import Entry, Scan
from app.tracker.store import Store
from tests.test_accounts import CONFIG, sign_up
from tests.test_accounts import client as account_client
from tests.test_store_api import ROOT, FakeDiscoverer


@pytest.fixture
def client(tmp_path):
    scanner = FakeDiscoverer()
    scanner.result = Scan(
        ROOT,
        "Series",
        entries=[Entry(ROOT + str(i), f"Chapter {i}", number=i) for i in range(1, 32)],
    )
    with TestClient(create_app(tmp_path / "library.db", scanner)) as client:
        yield client


def preview(client):
    return client.post("/api/scans", json={"url": ROOT}).json()["scan_id"]


def test_selected_read_links_match_preview_indices_and_survive_refresh(client):
    response = client.post(
        "/api/items", json={"scan_id": preview(client), "read_indices": [0, 25, 30, 0]}
    )
    assert response.status_code == 201
    item = response.json()
    assert (
        item["read_count"] == 3
        and item["unread_count"] == 28
        and item["new_count"] == 0
    )
    iid = item["id"]
    links = client.get(f"/api/items/{iid}/links").json()["links"]
    assert [e["number"] for e in links[:2]] == [31, 30]
    assert {e["number"] for e in links if e["read"]} == {1, 26, 31}
    assert client.post(f"/api/items/{iid}/refresh").json()["ok"]
    assert client.get(f"/api/items/{iid}").json()["read_count"] == 3


@pytest.mark.parametrize(
    "selection", [[31], [-1], [True], [0.5], ["1"], list(range(5000))]
)
def test_invalid_read_selection_does_not_consume_scan_or_cooldown(client, selection):
    sid = preview(client)
    assert (
        client.post(
            "/api/items", json={"scan_id": sid, "read_indices": selection}
        ).status_code
        == 422
    )
    assert client.get("/api/items").json() == []
    assert (
        client.post(
            "/api/items", json={"scan_id": sid, "read_indices": [0]}
        ).status_code
        == 201
    )


def test_ambiguous_read_modes_are_rejected_atomically(client):
    sid = preview(client)
    assert (
        client.post(
            "/api/items", json={"scan_id": sid, "mark_read": True, "read_indices": [0]}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/items", json={"scan_id": sid, "mark_read": True}).json()[
            "read_count"
        ]
        == 31
    )


def test_preferences_persist_across_store_restart_and_sort_and_creation_use_them(
    client,
):
    original = client.get("/api/settings").json()
    assert original["link_direction"] == "desc" and original["refresh_mode"] == "light"
    changed = client.patch(
        "/api/settings", json={"link_direction": "asc", "auto_read": False}
    ).json()
    assert changed == original | {"link_direction": "asc", "auto_read": False}
    assert Store(client.app.state.store.path).settings() == changed
    item = client.post("/api/items", json={"scan_id": preview(client)}).json()
    assert not item["auto_read"]
    iid = item["id"]
    assert client.get(f"/api/items/{iid}/links").json()["links"][0]["number"] == 1
    assert (
        client.get(f"/api/items/{iid}/links?direction=desc").json()["links"][0][
            "number"
        ]
        == 31
    )
    selection = client.post(f"/api/items/{iid}/link-selection", json={}).json()["ids"]
    assert (
        selection[0] == client.get(f"/api/items/{iid}/links").json()["links"][0]["id"]
    )


def test_apply_read_on_open_is_opt_in_excludes_trash_and_keeps_progress(client):
    store = client.app.state.store
    item = client.post(
        "/api/items", json={"scan_id": preview(client), "read_indices": [0]}
    ).json()
    with store.connection() as db:
        db.execute("DELETE FROM addition_cooldown")
    other = store.create(
        store.save_scan(
            Scan(
                ROOT + "second", "Second", entries=[Entry(ROOT + "second/1", "One")]
            ).to_dict()
        )
    )
    store.bulk_selected("items", [other["id"]], "delete")
    client.patch("/api/settings", json={"auto_read": False})
    assert store.item(item["id"])["auto_read"]
    assert (
        client.patch(
            "/api/settings", json={"auto_read": False, "apply_auto_read": True}
        ).status_code
        == 200
    )
    assert not store.item(item["id"])["auto_read"]
    assert (
        store.item(item["id"])["read_count"] == 1
        and store.item(other["id"])["auto_read"]
    )


@pytest.mark.parametrize(
    "body",
    [
        {"link_direction": "sideways"},
        {"link_sort": "random"},
        {"library_sort": "url"},
        {"refresh_mode": "force"},
        {"unknown": True},
        {"apply_auto_read": True},
    ],
)
def test_invalid_preferences_do_not_replace_valid_preferences(client, body):
    before = client.get("/api/settings").json()
    assert client.patch("/api/settings", json=body).status_code == 422
    assert client.get("/api/settings").json() == before


def test_settings_remain_isolated_and_scan_selection_cannot_cross_accounts(tmp_path):
    app = create_app(tmp_path / "library.db", FakeDiscoverer(), CONFIG)
    alice, bob, anonymous = (account_client(app) for _ in range(3))
    sign_up(alice, "alice")
    sign_up(bob, "bob")
    assert anonymous.get("/api/settings").status_code == 401
    alice.patch("/api/settings", json={"link_direction": "asc", "auto_read": False})
    assert bob.get("/api/settings").json()["link_direction"] == "desc"
    sid = preview(alice)
    assert (
        bob.post("/api/items", json={"scan_id": sid, "read_indices": [0]}).status_code
        == 422
    )
    assert (
        alice.post("/api/items", json={"scan_id": sid, "read_indices": [0]}).status_code
        == 201
    )


def test_database_failure_reports_unsaved_settings(client, monkeypatch):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr(client.app.state.store, "update_settings", fail)
    response = client.patch("/api/settings", json={"auto_read": False})
    assert (
        response.status_code == 503
        and response.json()["code"] == "DATABASE_UNAVAILABLE"
    )


def test_read_selection_save_failure_rolls_back_item_and_keeps_preview(
    client, monkeypatch
):
    sid = preview(client)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("write failed after inserting links")

    with monkeypatch.context() as patcher:
        patcher.setattr("app.tracker.store.save_observations", fail)
        assert (
            client.post(
                "/api/items", json={"scan_id": sid, "read_indices": [0, 30]}
            ).status_code
            == 503
        )
    assert client.get("/api/items").json() == []
    assert (
        client.post(
            "/api/items", json={"scan_id": sid, "read_indices": [0, 30]}
        ).json()["read_count"]
        == 2
    )


def test_legacy_library_migration_keeps_links_flags_and_progress(client):
    item = client.post(
        "/api/items", json={"scan_id": preview(client), "read_indices": [0]}
    ).json()
    with client.app.state.store.connection() as db:
        db.execute(
            "DROP TABLE preferences"
        )  # Recreate a pre-settings disposable test library.
        db.execute("PRAGMA user_version=6")
    migrated = Store(client.app.state.store.path)
    assert migrated.item(item["id"]) == item
    assert migrated.settings()["link_direction"] == "desc"


def test_refresh_preference_and_explicit_light_override(client):
    scanner = client.app.state.discoverer
    modes = []
    original = scanner.scan

    async def observe(*args, **kwargs):
        modes.append(kwargs.get("deep", False))
        return await original(*args, **kwargs)

    scanner.scan = observe
    item = client.post("/api/items", json={"scan_id": preview(client)}).json()
    client.patch("/api/settings", json={"refresh_mode": "deep"})
    client.post(f"/api/items/{item['id']}/refresh")
    client.post(f"/api/items/{item['id']}/refresh?deep=false")
    client.post("/api/refresh?stream=true")
    client.post("/api/refresh?deep=false")
    assert modes == [True, True, False, True, False]
