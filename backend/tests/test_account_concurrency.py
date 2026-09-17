import asyncio
import threading

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import HTTPException

from app.main import create_app
from app.tracker import accounts
from tests.test_accounts import CONFIG, ORIGIN, PASSWORD
from tests.test_store_api import FakeDiscoverer

NEW_PASSWORD = "replacement-password"


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "tracker.sqlite3", FakeDiscoverer(), CONFIG)


@pytest.mark.parametrize("operation", ["user", "store"])
async def test_slow_account_storage_does_not_block_health(app, monkeypatch, operation):
    auth = app.state.accounts
    auth.create("alice", PASSWORD)
    _, token = auth.login_session("alice", PASSWORD)
    entered, released, finished = (threading.Event() for _ in range(3))
    original = getattr(auth, operation)

    def blocked(*args):
        entered.set()
        released.wait(2)
        finished.set()
        return original(*args)

    monkeypatch.setattr(auth, operation, blocked)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url=ORIGIN
    ) as client:
        pending = asyncio.create_task(
            client.get("/api/items", headers={"Cookie": f"{accounts.COOKIE}={token}"})
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            response = await asyncio.wait_for(client.get("/api/health"), 1)
            assert response.status_code == 200
            assert not finished.is_set(), "Storage blocked the HTTP event loop"
        finally:
            released.set()
            response = await pending
        assert response.status_code == 200


async def test_deleted_account_between_session_and_store_returns_unauthorized(
    app, monkeypatch
):
    auth = app.state.accounts
    auth.create("alice", PASSWORD)
    _, token = auth.login_session("alice", PASSWORD)
    original = auth.user

    def delete_after_lookup(value):
        user = original(value)
        assert user is not None
        auth.delete(user)
        return user

    monkeypatch.setattr(auth, "user", delete_after_lookup)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url=ORIGIN
    ) as client:
        response = await client.get(
            "/api/items", headers={"Cookie": f"{accounts.COOKIE}={token}"}
        )
    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in to continue."}
    assert response.headers["cache-control"] == "no-store"
    assert original(token) is None


def test_password_reset_fences_an_in_flight_login(app, monkeypatch):
    auth = app.state.accounts
    user = auth.create("alice", PASSWORD)
    original = auth.new_session

    def reset_before_session(uid, expected_hash):
        auth.password(uid, NEW_PASSWORD)
        return original(uid, expected_hash)

    monkeypatch.setattr(auth, "new_session", reset_before_session)
    with pytest.raises(HTTPException) as error:
        auth.login_session("alice", PASSWORD)
    assert error.value.status_code == 401
    assert auth.login("alice", NEW_PASSWORD)["id"] == user["id"]
    with auth.connection() as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_rehash_cannot_overwrite_a_concurrent_password_reset(app, monkeypatch):
    auth = app.state.accounts
    user = auth.create("alice", PASSWORD)
    old_hash = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(
        PASSWORD
    )
    with auth.connection() as db:
        db.execute(
            "UPDATE users SET password_hash=? WHERE id=?", (old_hash, user["id"])
        )
    original = accounts.hash_password

    def reset_during_rehash(value):
        if value == PASSWORD:
            auth.password(user["id"], NEW_PASSWORD)
        return original(value)

    monkeypatch.setattr(accounts, "hash_password", reset_during_rehash)
    with pytest.raises(HTTPException) as error:
        auth.login_session("alice", PASSWORD)
    assert error.value.status_code == 401
    assert auth.login("alice", NEW_PASSWORD)["id"] == user["id"]
    with pytest.raises(HTTPException):
        auth.login("alice", PASSWORD)


def test_concurrent_password_changes_cannot_overwrite_newer_credentials(
    app, monkeypatch
):
    auth = app.state.accounts
    user = auth.create("alice", PASSWORD)
    original = accounts.hash_password

    def reset_during_hash(value):
        if value == "superseded-password":
            auth.password(user["id"], NEW_PASSWORD)
        return original(value)

    monkeypatch.setattr(accounts, "hash_password", reset_during_hash)
    with pytest.raises(HTTPException) as error:
        auth.change_password(user, PASSWORD, "superseded-password")
    assert error.value.status_code == 401
    assert auth.login("alice", NEW_PASSWORD)["id"] == user["id"]
    with auth.connection() as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
