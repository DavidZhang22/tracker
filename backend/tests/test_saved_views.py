import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from app.main import create_app
from app.tracker.accounts import COOKIE
from app.tracker.saved_views import list_views, save_view
from app.tracker.store import Store
from tests.test_accounts import CONFIG, PASSWORD, client, sign_up
from tests.test_store_api import FakeDiscoverer


@pytest.fixture
def users(tmp_path):
    app = create_app(tmp_path / "library.db", FakeDiscoverer(), CONFIG)
    alice, bob, anonymous = (client(app) for _ in range(3))
    sign_up(alice, "alice")
    sign_up(bob, "bob")
    return app, alice, bob, anonymous


def test_saved_views_are_private_persistent_and_support_edit_delete(users):
    app, alice, bob, anonymous = users
    assert anonymous.get("/api/views").status_code == 401
    body = {
        "name": "  Weekend reading  ",
        "query": "science fiction",
        "filter": "unread",
        "kind": "novel",
        "sort": "title",
        "search_mode": "local",
    }
    response = alice.post("/api/views", json=body)
    assert response.status_code == 201
    view = response.json()
    assert view["name"] == "Weekend reading"
    assert bob.get("/api/views").json() == []
    assert bob.patch(f"/api/views/{view['id']}", json=body).status_code == 404
    assert bob.delete(f"/api/views/{view['id']}").status_code == 404
    assert (
        alice.patch(
            f"/api/views/{view['id']}", json=body | {"name": "Evenings"}
        ).json()["id"]
        == view["id"]
    )
    store = app.state.accounts.store(app.state.accounts.user(alice.cookies.get(COOKIE)))
    assert list_views(Store(store.path))[0]["name"] == "Evenings"
    assert alice.delete(f"/api/views/{view['id']}").json() == {"ok": True}
    assert alice.get("/api/views").json() == []
    assert not app.state.discoverer.calls


@pytest.mark.parametrize(
    "change",
    [
        {"name": " "},
        {"name": "x" * 41},
        {"name": "a\nb"},
        {"query": "x" * 201},
        {"kind": ""},
        {"kind": "arbitrary"},
        {"filter": "all; DELETE"},
        {"sort": "random"},
        {"search_mode": "unknown"},
        {"extra": "ignored"},
    ],
)
def test_invalid_saved_view_does_not_write(users, change):
    _, alice, _, _ = users
    assert (
        alice.post("/api/views", json={"name": "Reading"} | change).status_code == 422
    )
    assert alice.get("/api/views").json() == []


def test_view_names_are_unique_and_writes_require_same_origin(users):
    _, alice, _, _ = users
    assert alice.post("/api/views", json={"name": "Reading"}).status_code == 201
    assert alice.post("/api/views", json={"name": " reading "}).status_code == 422
    assert (
        alice.post(
            "/api/views",
            json={"name": "Other"},
            headers={"Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )
    assert len(alice.get("/api/views").json()) == 1


def test_concurrent_creates_cannot_exceed_cap(tmp_path):
    store = Store(tmp_path / "library.db")

    def create(index):
        try:
            return save_view(store, {"name": str(index)})
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(16)))
    assert sum(r is not None for r in results) == len(list_views(store)) == 12
    first = list_views(store)[0]
    assert save_view(store, {"name": "Edited"}, first["id"])["id"] == first["id"]


def test_export_and_deletion_include_saved_views(users):
    app, alice, bob, _ = users
    alice.post("/api/views", json={"name": "Private view", "query": "private search"})
    bob.post("/api/views", json={"name": "Bob's view"})
    store = app.state.accounts.store(app.state.accounts.user(alice.cookies.get(COOKIE)))
    exported = alice.post(
        "/api/auth/export", json={"current_password": PASSWORD}
    ).json()
    assert (
        json.loads(exported["saved_views"][0]["payload"])["query"] == "private search"
    )
    result = alice.post(
        "/api/auth/delete",
        json={"current_password": PASSWORD, "confirmation": "DELETE"},
    )
    assert result.status_code == 200 and not result.json()["cleanup_pending"]
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM saved_views").fetchone()[0] == 0
    assert bob.get("/api/views").json()[0]["name"] == "Bob's view"


def test_database_failure_does_not_report_saved_view(users, monkeypatch):
    _, alice, _, _ = users

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr("app.tracker.api.save_view", fail)
    response = alice.post("/api/views", json={"name": "Reading"})
    assert (
        response.status_code == 503
        and response.json()["code"] == "DATABASE_UNAVAILABLE"
    )
    assert alice.get("/api/views").json() == []
