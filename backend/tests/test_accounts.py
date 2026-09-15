import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.accounts import COOKIE, Accounts
from tests.test_store_api import ROOT, FakeDiscoverer

ORIGIN = "https://tracker.example.com"
PASSWORD = "test-only-long-password"
CONFIG = {
    "required": True,
    "origin": ORIGIN,
    "signup_code": "test-only-invitation-code-123",
}


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "tracker.sqlite3", FakeDiscoverer(), CONFIG)


def client(app):
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def sign_up(c, name):
    r = c.post(
        "/api/auth/register",
        json={
            "username": name,
            "password": PASSWORD,
            "invite_code": CONFIG["signup_code"],
        },
    )
    assert r.status_code == 201, r.text
    return r


def add(c):
    scan = c.post("/api/scans", json={"url": ROOT})
    assert scan.status_code == 200, scan.text
    item = c.post("/api/items", json={"scan_id": scan.json()["scan_id"]})
    assert item.status_code == 201, item.text
    return item.json()


def test_all_private_routes_require_session_before_any_scan(app):
    c = client(app)
    for method, path, body in [
        ("get", "/api/items", None),
        ("post", "/api/scans", {"url": ROOT}),
        ("post", "/api/source-method/detect", {"url": ROOT}),
        ("post", "/api/refresh", {}),
        ("patch", "/api/links/arbitrary", {"read": True}),
        ("post", "/api/items/bulk", {"action": "delete", "ids": ["x"]}),
        ("post", "/api/items/arbitrary/link-groups", {"action": "merge", "ids": ["x"]}),
    ]:
        response = getattr(c, method)(
            path, **({"json": body} if body is not None else {})
        )
        assert response.status_code == 401
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/status").json() == {
        "required": True,
        "registration": True,
        "invite_required": True,
        "user": None,
    }
    assert not app.state.discoverer.calls


def test_ten_character_password_boundary_on_signup_and_change(app):
    c = client(app)
    payload = {
        "username": "boundary",
        "password": "a" * 9,
        "invite_code": CONFIG["signup_code"],
    }
    assert c.post("/api/auth/register", json=payload).status_code == 422
    payload["password"] = "a" * 10
    assert c.post("/api/auth/register", json=payload).status_code == 201
    assert (
        c.post(
            "/api/auth/password",
            json={"current_password": "a" * 10, "new_password": "b" * 9},
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/api/auth/password",
            json={"current_password": "a" * 10, "new_password": "b" * 10},
        ).status_code
        == 200
    )


def test_passwords_and_sessions_are_hashed_and_cookies_secure(app):
    c = client(app)
    r = sign_up(c, "alice")
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    token = c.cookies.get(COOKIE)
    with app.state.accounts.connection() as db:
        row = db.execute("SELECT password_hash FROM users").fetchone()
        assert row[0].startswith("$argon2id$") and PASSWORD not in row[0]
        session = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert session != token and len(session) == 64
    assert c.get("/api/auth/status").json()["user"] == {"username": "alice"}
    assert "no-store" in c.get("/api/items").headers["cache-control"]


def test_two_accounts_cannot_read_mutate_refresh_or_bulk_other_library(app):
    alice, bob = client(app), client(app)
    sign_up(alice, "alice")
    sign_up(bob, "bob")
    item = add(alice)
    lid = alice.get(f"/api/items/{item['id']}/links").json()["links"][0]["id"]
    assert bob.get("/api/items").json() == []
    paths = [
        ("get", f"/api/items/{item['id']}", None),
        ("get", f"/api/items/{item['id']}/links", None),
        ("post", f"/api/items/{item['id']}/refresh", {}),
        ("post", f"/api/items/{item['id']}/read", {}),
        ("patch", f"/api/items/{item['id']}", {"favorite": True}),
        ("patch", f"/api/links/{lid}", {"read": True}),
        (
            "post",
            "/api/links/bulk",
            {"ids": [lid], "item_id": item["id"], "action": "delete"},
        ),
        ("post", "/api/items/bulk", {"ids": [item["id"]], "action": "delete"}),
        (
            "post",
            f"/api/items/{item['id']}/link-groups",
            {"ids": [lid], "action": "merge"},
        ),
    ]
    for method, path, body in paths:
        assert (
            getattr(bob, method)(
                path, **({"json": body} if body is not None else {})
            ).status_code
            == 404
        )
    assert bob.post("/api/refresh").json()["checked"] == 0
    second = add(bob)
    assert second["id"] != item["id"]
    assert alice.get("/api/items").json()[0]["total_count"] == 1


def test_preview_ids_and_refresh_all_are_scoped_to_the_account(app):
    alice, bob = client(app), client(app)
    sign_up(alice, "alice")
    sign_up(bob, "bob")
    sid = alice.post("/api/scans", json={"url": ROOT}).json()["scan_id"]
    assert bob.post("/api/items", json={"scan_id": sid}).status_code == 422
    assert alice.post("/api/items", json={"scan_id": sid}).status_code == 201
    assert bob.post("/api/refresh").json()["checked"] == 0
    assert alice.post("/api/refresh").json()["checked"] == 1


def test_logout_expiry_and_server_restart(app):
    c = client(app)
    sign_up(c, "alice")
    item = add(c)
    token = c.cookies.get(COOKIE)
    restarted = create_app(app.state.store.path, FakeDiscoverer(), CONFIG)
    again = client(restarted)
    again.cookies.set(COOKIE, token)
    assert again.get("/api/items").json()[0]["id"] == item["id"]
    assert c.post("/api/auth/logout").status_code == 200
    assert again.get("/api/items").status_code == 401
    assert (
        c.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 200
    )
    with app.state.accounts.connection() as db:
        db.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert c.get("/api/items").status_code == 401


def test_password_change_revokes_other_sessions_and_old_password(app):
    alice, other = client(app), client(app)
    sign_up(alice, "alice")
    assert (
        other.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert (
        alice.post(
            "/api/auth/password",
            json={"current_password": "wrong", "new_password": "new-test-password-123"},
        ).status_code
        == 401
    )
    assert (
        alice.post(
            "/api/auth/password",
            json={
                "current_password": PASSWORD,
                "new_password": "new-test-password-123",
            },
        ).status_code
        == 200
    )
    assert other.get("/api/items").status_code == 401
    assert alice.get("/api/items").status_code == 200
    assert (
        other.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 401
    )


def test_origin_missing_or_forged_rejected_and_registration_not_open(app):
    c = client(app)
    for origin in ["https://evil.example", "null", ""]:
        r = c.post(
            "/api/auth/login",
            headers={"Origin": origin},
            json={"username": "alice", "password": PASSWORD},
        )
        assert r.status_code == 403
    assert (
        TestClient(app, base_url=ORIGIN)
        .post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        .status_code
        == 403
    )
    assert (
        c.post(
            "/api/auth/register",
            json={"username": "alice", "password": PASSWORD, "invite_code": "wrong"},
        ).status_code
        == 403
    )
    app.state.accounts.signup_code = ""
    assert not c.get("/api/auth/status").json()["registration"]
    assert (
        c.post(
            "/api/auth/register",
            json={
                "username": "alice",
                "password": PASSWORD,
                "invite_code": CONFIG["signup_code"],
            },
        ).status_code
        == 403
    )


def test_account_attempts_throttled_without_revealing_user_existence(app):
    c = client(app)
    for _ in range(10):
        assert (
            c.post(
                "/api/auth/login", json={"username": "missing", "password": PASSWORD}
            ).status_code
            == 401
        )
    r = c.post("/api/auth/login", json={"username": "missing", "password": PASSWORD})
    assert r.status_code == 429 and r.headers["retry-after"] == "900"


def test_existing_library_is_claimed_only_privately_once(app):
    original = app.state.store
    sid = original.save_scan(app.state.discoverer.result.to_dict())
    item = original.create(sid)
    owner = app.state.accounts.create("owner", PASSWORD, claim_existing=True)
    assert app.state.accounts.store(owner).item(item["id"])["id"] == item["id"]
    with pytest.raises(ValueError):
        app.state.accounts.create("another", PASSWORD, claim_existing=True)
    c = client(app)
    sign_up(c, "visitor")
    assert c.get("/api/items").json() == []


def test_invalid_public_configuration_fails_closed(tmp_path):
    for origin in [
        "",
        "http://public.example",
        "https://user:pass@public.example",
        "https://public.example/path",
    ]:
        with pytest.raises(ValueError):
            create_app(
                tmp_path / "db.sqlite3", FakeDiscoverer(), CONFIG | {"origin": origin}
            )
    with pytest.raises(ValueError):
        create_app(
            tmp_path / "db.sqlite3", FakeDiscoverer(), CONFIG | {"signup_code": "short"}
        )


def test_password_validation_and_unique_usernames(tmp_path):
    a = Accounts(tmp_path / "db.sqlite3")
    with pytest.raises(ValueError):
        a.create("../escape", PASSWORD)
    with pytest.raises(ValueError):
        a.create("alice", "short")
    a.create("Alice", PASSWORD)
    with pytest.raises(ValueError):
        a.create("alice", PASSWORD)
    with sqlite3.connect(a.path) as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_invalid_credentials_never_echo_password_values(app):
    c = client(app)
    password = "not-a-real-secret" * 20
    r = c.post("/api/auth/login", json={"username": "alice", "password": password})
    assert r.status_code == 422 and password not in r.text


def test_local_account_cookie_works_without_https(tmp_path):
    origin = "http://127.0.0.1:8000"
    local = create_app(
        tmp_path / "local.sqlite3", FakeDiscoverer(), CONFIG | {"origin": origin}
    )
    c = TestClient(local, base_url=origin, headers={"Origin": origin})
    r = sign_up(c, "alice")
    assert (
        "catchup_session=" in r.headers["set-cookie"]
        and "Secure" not in r.headers["set-cookie"]
    )
    assert c.get("/api/items").status_code == 200
