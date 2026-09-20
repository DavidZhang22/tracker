import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from app.main import create_app
from app.tracker.accounts import COOKIE, Accounts, digest
from tests.test_accounts import CONFIG, PASSWORD, add, client
from tests.test_store_api import FakeDiscoverer


@pytest.fixture
def app(tmp_path):
    return create_app(
        tmp_path / "tracker.sqlite3",
        FakeDiscoverer(),
        CONFIG | {"public_signup": True, "signup_code": ""},
    )


def register(c, name):
    return c.post("/api/auth/register", json={"username": name, "password": PASSWORD})


def test_public_signup_needs_no_invite_and_keeps_libraries_private(app):
    alice, bob = client(app), client(app)
    assert alice.get("/api/auth/status").json() == {
        "required": True,
        "recovery_available": False,
        "registration": True,
        "invite_required": False,
        "user": None,
    }
    assert alice.get("/api/items").status_code == 401
    assert register(alice, "alice").status_code == 201
    item = add(alice)
    assert register(bob, "bob").status_code == 201
    assert bob.get("/api/items").json() == []
    assert bob.get(f"/api/items/{item['id']}").status_code == 404
    assert bob.post(f"/api/items/{item['id']}/refresh").status_code == 404
    assert (
        bob.post(
            "/api/items/bulk", json={"ids": [item["id"]], "action": "delete"}
        ).status_code
        == 404
    )
    assert alice.get(f"/api/items/{item['id']}").status_code == 200
    with app.state.accounts.connection() as db:
        assert not any(row[0] for row in db.execute("SELECT legacy_library FROM users"))


def test_public_signup_preserves_origin_and_password_checks(app):
    c = client(app)
    assert (
        c.post(
            "/api/auth/register",
            headers={"Origin": "https://other.example"},
            json={"username": "alice", "password": PASSWORD},
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/auth/register", json={"username": "alice", "password": "123456789"}
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/api/auth/register", json={"username": "alice", "password": "1234567890"}
        ).status_code
        == 201
    )


def test_signup_modes_can_change_without_revoking_sessions_or_data(app):
    c = client(app)
    register(c, "alice")
    item = add(c)
    cookie = c.cookies.get(COOKIE)
    closed = create_app(
        app.state.store.path, FakeDiscoverer(), CONFIG | {"signup_code": ""}
    )
    c2 = client(closed)
    c2.cookies.set(COOKIE, cookie)
    assert c2.get(f"/api/items/{item['id']}").status_code == 200
    assert not c2.get("/api/auth/status").json()["registration"]
    assert register(c2, "bob").status_code == 403
    invited = create_app(app.state.store.path, FakeDiscoverer(), CONFIG)
    assert client(invited).get("/api/auth/status").json()["invite_required"]
    assert register(client(invited), "bob").status_code == 403
    reopened = create_app(
        app.state.store.path, FakeDiscoverer(), CONFIG | {"public_signup": True}
    )
    assert not client(reopened).get("/api/auth/status").json()["invite_required"]
    assert register(client(reopened), "bob").status_code == 201


def test_public_signup_quota_persists_and_does_not_block_login(app):
    c = client(app)
    for name in ("alice", "bob", "carol"):
        assert register(c, name).status_code == 201
    restarted = create_app(
        app.state.store.path, FakeDiscoverer(), CONFIG | {"public_signup": True}
    )
    again = client(restarted)
    response = register(again, "david")
    assert (
        response.status_code == 429
        and 1 <= int(response.headers["retry-after"]) <= 3601
    )
    assert (
        again.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 200
    )
    with restarted.state.accounts.connection() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 3
        assert db.execute("SELECT address_hash FROM registrations LIMIT 1").fetchone()[
            0
        ] == digest("testclient")


def test_global_quota_and_retention(app):
    auth = app.state.accounts
    now = time.time()
    with auth.connection() as db:
        db.executemany(
            "INSERT INTO registrations VALUES (?,?)",
            [(digest(str(i)), now) for i in range(20)],
        )
    assert register(client(app), "alice").status_code == 429
    with auth.connection() as db:
        db.execute("UPDATE registrations SET created=?", (now - 3601,))
    auth.maintenance()
    with auth.connection() as db:
        assert db.execute("SELECT count(*) FROM registrations").fetchone()[0] == 0
    assert register(client(app), "alice").status_code == 201


def test_concurrent_signups_share_quota_and_duplicates_do_not_charge(tmp_path):
    auth = Accounts(tmp_path / "tracker.sqlite3", public_signup=True)
    auth.create("alice", PASSWORD, signup_address="same-ip")
    with pytest.raises(ValueError):
        auth.create("alice", PASSWORD, signup_address="same-ip")
    auth.create("bob", PASSWORD, signup_address="same-ip")

    def attempt(i):
        try:
            auth.create(f"person-{i}", PASSWORD, signup_address="same-ip")
            return 201
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(201) == 1 and results.count(429) == 7
    with auth.connection() as db:
        assert db.execute("SELECT count(*) FROM registrations").fetchone()[0] == 3


def test_public_signup_fails_closed_when_accounts_storage_is_down(app, monkeypatch):
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("Storage offline")

    monkeypatch.setattr(app.state.accounts, "connection", unavailable)
    response = register(client(app), "alice")
    assert (
        response.status_code == 503
        and response.json()["code"] == "DATABASE_UNAVAILABLE"
    )
