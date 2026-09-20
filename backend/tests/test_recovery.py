import importlib.util
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import create_app
from app.tracker.accounts import digest
from app.tracker.recovery import Mailer, email_address
from tests.test_accounts import CONFIG, PASSWORD, client, sign_up
from tests.test_store_api import FakeDiscoverer

EMAIL = "alice@example.com"
NEW = "new-password-strong"


class FakeMailer:
    available = True

    def __init__(self):
        self.messages = []

    def send(self, recipient, subject, body):
        self.messages.append((recipient, subject, body))
        return True

    def token(self):
        return re.search(r"#token=([A-Za-z0-9_-]{43})", self.messages[-1][2])[1]


@pytest.fixture
def app(tmp_path):
    return create_app(
        tmp_path / "tracker.sqlite3",
        FakeDiscoverer(),
        CONFIG | {"mailer": FakeMailer()},
    )


def attach(app):
    c = client(app)
    sign_up(c, "alice")
    response = c.post(
        "/api/auth/email", json={"email": EMAIL, "current_password": PASSWORD}
    )
    assert response.status_code == 202, response.text
    token = app.state.recovery.mailer.token()
    assert c.post("/api/auth/email/verify", json={"token": token}).status_code == 200
    return c


def request(app, c=None):
    c = c or client(app)
    response = c.post("/api/auth/recovery/request", json={"email": EMAIL})
    assert response.status_code == 202, response.text
    return app.state.recovery.mailer.token()


def test_email_requires_password_and_verification_without_account_disclosure(app):
    c, stranger = client(app), client(app)
    sign_up(c, "alice")
    assert stranger.get("/api/auth/email").status_code == 401
    assert (
        c.post(
            "/api/auth/email", json={"email": EMAIL, "current_password": "wrong"}
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/api/auth/email",
            json={"email": " alice@Example.COM ", "current_password": PASSWORD},
        ).status_code
        == 202
    )
    assert c.get("/api/auth/email").json() == {
        "email": None,
        "pending_email": EMAIL,
        "available": True,
    }
    token = app.state.recovery.mailer.token()
    assert (
        "https://tracker.example.com/account/verify-email#token="
        in app.state.recovery.mailer.messages[-1][2]
    )
    assert (
        stranger.post("/api/auth/recovery/request", json={"email": EMAIL}).status_code
        == 202
    )
    assert len(app.state.recovery.mailer.messages) == 1
    with app.state.accounts.connection() as db:
        row = dict(db.execute("SELECT * FROM recovery_tokens").fetchone())
        assert token not in str(row)
        assert row["token_hash"] == digest(token)
    assert stranger.get(
        "/api/auth/email/verify", params={"token": token}
    ).status_code in {404, 405}
    assert (
        stranger.post("/api/auth/email/verify", json={"token": token}).status_code
        == 200
    )
    assert (
        stranger.post("/api/auth/email/verify", json={"token": token}).status_code
        == 400
    )
    assert c.get("/api/auth/email").json()["email"] == EMAIL
    assert stranger.get("/api/auth/status").json()["user"] is None


def test_recovery_is_restricted_and_reset_revokes_every_session_and_link(app):
    original = attach(app)
    other = client(app)
    other.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    recovering = client(app)
    token = request(app, recovering)
    response = recovering.post("/api/auth/recovery/exchange", json={"token": token})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=900"):
        assert attribute in cookie
    assert recovering.get("/api/auth/recovery/status").json() == {
        "active": True,
        "username": "alice",
    }
    assert recovering.get("/api/items").status_code == 401
    assert recovering.get("/api/auth/email").status_code == 401
    assert (
        recovering.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": NEW},
        ).status_code
        == 401
    )
    assert (
        recovering.post(
            "/api/auth/recovery/password", json={"new_password": "short"}
        ).status_code
        == 422
    )
    assert (
        recovering.post(
            "/api/auth/recovery/password", json={"new_password": NEW}
        ).status_code
        == 200
    )
    assert recovering.get("/api/auth/recovery/status").json() == {"active": False}
    assert recovering.get("/api/auth/status").json()["user"] is None
    assert (
        original.get("/api/items").status_code
        == other.get("/api/items").status_code
        == 401
    )
    assert (
        recovering.post(
            "/api/auth/recovery/exchange", json={"token": token}
        ).status_code
        == 400
    )
    assert (
        recovering.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        recovering.post(
            "/api/auth/login", json={"username": "alice", "password": NEW}
        ).status_code
        == 200
    )
    assert "password changed" in app.state.recovery.mailer.messages[-1][1]
    assert NEW not in app.state.recovery.mailer.messages[-1][2]
    with app.state.accounts.connection() as db:
        assert db.execute("SELECT count(*) FROM recovery_sessions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM recovery_tokens").fetchone()[0] == 0


def test_unknown_and_known_requests_have_identical_responses_and_headers(app):
    attach(app)
    c = client(app)
    known = c.post("/api/auth/recovery/request", json={"email": EMAIL})
    unknown = c.post(
        "/api/auth/recovery/request", json={"email": "missing@example.com"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    for response in (known, unknown):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
    assert len(app.state.recovery.mailer.messages) == 2


def test_expiry_purpose_and_csrf_are_enforced(app):
    c = attach(app)
    token = request(app)
    outsider = client(app)
    assert (
        outsider.post("/api/auth/email/verify", json={"token": token}).status_code
        == 400
    )
    assert (
        outsider.post(
            "/api/auth/recovery/exchange",
            json={"token": token},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    with app.state.accounts.connection() as db:
        db.execute("UPDATE recovery_tokens SET expires=?", (time.time() - 1,))
    assert (
        outsider.post("/api/auth/recovery/exchange", json={"token": token}).status_code
        == 400
    )
    assert c.get("/api/items").status_code == 200


def test_email_removal_and_password_change_invalidate_pending_recovery(app):
    c = attach(app)
    token = request(app)
    outsider = client(app)
    outsider.post("/api/auth/recovery/exchange", json={"token": token})
    assert (
        c.request(
            "DELETE", "/api/auth/email", json={"current_password": PASSWORD}
        ).status_code
        == 200
    )
    assert outsider.get("/api/auth/recovery/status").json() == {"active": False}
    assert c.get("/api/auth/email").json()["email"] is None
    assert (
        outsider.post("/api/auth/recovery/exchange", json={"token": token}).status_code
        == 400
    )
    with app.state.accounts.connection() as db:
        uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        token = app.state.recovery.issue(db, uid, "verify", EMAIL)
    assert (
        c.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": NEW},
        ).status_code
        == 200
    )
    assert (
        outsider.post("/api/auth/email/verify", json={"token": token}).status_code
        == 400
    )


def test_duplicate_email_not_disclosed_before_ownership_is_proven(app):
    attach(app)
    bob = client(app)
    sign_up(bob, "bob")
    response = bob.post(
        "/api/auth/email", json={"email": EMAIL, "current_password": PASSWORD}
    )
    assert response.status_code == 202
    assert bob.get("/api/auth/email").json()["pending_email"] == EMAIL
    token = app.state.recovery.mailer.token()
    assert bob.post("/api/auth/email/verify", json={"token": token}).status_code == 400
    assert bob.get("/api/auth/email").json()["email"] is None
    assert bob.get("/api/auth/email").json()["pending_email"] is None


def test_request_throttling_persists_across_restart_without_user_lookup(app):
    c = client(app)
    for _ in range(3):
        assert (
            c.post("/api/auth/recovery/request", json={"email": EMAIL}).status_code
            == 202
        )
    restarted = create_app(
        app.state.store.path, FakeDiscoverer(), CONFIG | {"mailer": FakeMailer()}
    )
    response = client(restarted).post(
        "/api/auth/recovery/request", json={"email": EMAIL}
    )
    assert response.status_code == 429 and response.headers["retry-after"] == "3600"
    with app.state.accounts.connection() as db:
        assert EMAIL not in str(
            [tuple(row) for row in db.execute("SELECT * FROM recovery_limits")]
        )


def test_token_exchange_is_atomic_under_concurrency(app):
    attach(app)
    token = request(app)
    barrier = threading.Barrier(4)

    def exchange():
        barrier.wait()
        try:
            return app.state.recovery.exchange(token)
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(4) as pool:
        result = list(pool.map(lambda _: exchange(), range(4)))
    assert sum(isinstance(value, str) for value in result) == 1
    assert result.count(400) == 3


def test_concurrent_recovery_passwords_cannot_overwrite_newer_credentials(
    app, monkeypatch
):
    from app.tracker import recovery

    attach(app)
    grant = app.state.recovery.exchange(request(app))
    original = recovery.hash_password

    def race(value):
        if value == "superseded-password":
            app.state.recovery.password(grant, NEW)
        return original(value)

    monkeypatch.setattr(recovery, "hash_password", race)
    with pytest.raises(HTTPException) as caught:
        app.state.recovery.password(grant, "superseded-password")
    assert caught.value.status_code == 401
    assert app.state.accounts.login("alice", NEW)


def test_email_update_fenced_against_password_change(app):
    c = attach(app)
    verified = app.state.accounts._credentials("alice", PASSWORD)
    app.state.accounts.password(verified["id"], NEW)
    with pytest.raises(HTTPException):
        app.state.recovery.prepare_email(verified, "new@example.com")
    with pytest.raises(HTTPException):
        app.state.recovery.remove_email(verified)
    assert c.get("/api/items").status_code == 401


def test_mail_disabled_and_delivery_errors_do_not_expose_secrets(app, caplog):
    c = client(app)
    sign_up(c, "alice")
    app.state.recovery.mailer.available = False
    assert not c.get("/api/auth/status").json()["recovery_available"]
    assert (
        c.post(
            "/api/auth/email", json={"email": EMAIL, "current_password": PASSWORD}
        ).status_code
        == 503
    )
    assert (
        c.post("/api/auth/recovery/request", json={"email": EMAIL}).status_code == 202
    )
    assert not app.state.recovery.mailer.messages
    app.state.recovery.mailer.available = True

    def fail(*args):
        raise OSError("private-provider-credential")

    app.state.recovery.mailer.send = fail
    response = c.post(
        "/api/auth/email", json={"email": EMAIL, "current_password": PASSWORD}
    )
    assert response.status_code == 202
    assert "private-provider-credential" not in caplog.text
    assert EMAIL not in caplog.text
    assert c.get("/api/auth/email").json()["pending_email"] is None


def test_delete_and_export_include_recovery_data(app):
    c = attach(app)
    token = request(app)
    app.state.recovery.exchange(token)
    export = c.post("/api/auth/export", json={"current_password": PASSWORD}).json()
    assert export["account"]["recovery_email"] == EMAIL
    assert token not in str(export)
    assert (
        c.post(
            "/api/auth/delete",
            json={"current_password": PASSWORD, "confirmation": "DELETE"},
        ).status_code
        == 200
    )
    with app.state.accounts.connection() as db:
        for table in ("recovery_emails", "recovery_tokens", "recovery_sessions"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_restore_clears_recovery_tokens_and_sessions(app, tmp_path):
    attach(app)
    app.state.recovery.exchange(request(app))
    script = Path(__file__).resolve().parents[2] / "deploy" / "prepare-restore.py"
    spec = importlib.util.spec_from_file_location("prepare_restore", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    staged = tmp_path.with_name(tmp_path.name + "-staged")
    staged.mkdir()
    with app.state.accounts.connection() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(app.state.accounts.path, staged / "accounts.sqlite3")
    with app.state.accounts.connection() as db:
        db.execute("UPDATE recovery_emails SET email='replacement@example.com'")
        uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    updated_hash = app.state.accounts.password(uid, NEW)
    module.prepare(app.state.accounts.root, staged)
    import sqlite3

    with sqlite3.connect(staged / "accounts.sqlite3") as db:
        assert (
            db.execute("SELECT password_hash FROM users").fetchone()[0] == updated_hash
        )
        assert db.execute("SELECT count(*) FROM recovery_sessions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM recovery_tokens").fetchone()[0] == 0
        assert (
            db.execute("SELECT email FROM recovery_emails").fetchone()[0]
            == "replacement@example.com"
        )


@pytest.mark.parametrize(
    "value",
    [
        "x\r\nBcc: victim@example.com",
        "a@example.com\n.evil",
        "x@y",
        "a..b@example.com",
        "x@-example.com",
    ],
)
def test_email_header_and_address_validation(value):
    with pytest.raises(ValueError):
        email_address(value)


def test_smtp_requires_encryption(monkeypatch):
    monkeypatch.setenv("TRACKER_SMTP_HOST", "mail.example.com")
    monkeypatch.setenv("TRACKER_SMTP_FROM", "recovery@example.com")
    monkeypatch.setenv("TRACKER_SMTP_TLS", "none")
    with pytest.raises(ValueError):
        Mailer()


@pytest.mark.parametrize("route", ["/api/auth/delete", "/api/auth/logout-all"])
def test_stale_password_confirmation_cannot_delete_or_revoke_after_recovery(
    app, monkeypatch, route
):
    from app.tracker import accounts

    c = attach(app)
    original = accounts.reauthenticate

    def reset_after_confirmation(request, password):
        auth, user = original(request, password)
        auth.password(user["id"], NEW)
        return auth, user

    monkeypatch.setattr(accounts, "reauthenticate", reset_after_confirmation)
    body = {"current_password": PASSWORD, "confirmation": "DELETE"}
    assert c.post(route, json=body).status_code == 401
    assert app.state.accounts.login("alice", NEW)
    with app.state.accounts.connection() as db:
        assert db.execute("SELECT count(*) FROM erasures").fetchone()[0] == 0


def test_recovery_grant_expires_and_logout_all_revokes_it(app):
    c = attach(app)
    recovery = app.state.recovery
    grant = recovery.exchange(request(app))
    with app.state.accounts.connection() as db:
        db.execute("UPDATE recovery_sessions SET expires=?", (time.time() - 1,))
    assert recovery.session(grant) is None
    with pytest.raises(HTTPException):
        recovery.password(grant, NEW)
    with app.state.accounts.connection() as db:
        uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        second = recovery.issue(db, uid, "recover", EMAIL)
    second_grant = recovery.exchange(second)
    assert (
        c.post("/api/auth/logout-all", json={"current_password": PASSWORD}).status_code
        == 200
    )
    assert recovery.session(second_grant) is None


def test_email_lookup_ignores_case_but_delivery_preserves_verified_recipient(app):
    c = client(app)
    sign_up(c, "alice")
    verified_address = "Alice@Example.com"
    assert email_address(verified_address) == "Alice@example.com"
    assert (
        c.post(
            "/api/auth/email",
            json={"email": verified_address, "current_password": PASSWORD},
        ).status_code
        == 202
    )
    mailer = app.state.recovery.mailer
    assert mailer.messages[-1][0] == "Alice@example.com"
    assert (
        c.post("/api/auth/email/verify", json={"token": mailer.token()}).status_code
        == 200
    )
    assert (
        c.post(
            "/api/auth/recovery/request", json={"email": "ALICE@EXAMPLE.COM"}
        ).status_code
        == 202
    )
    assert mailer.messages[-1][0] == "Alice@example.com"
    assert (
        c.post(
            "/api/auth/recovery/request", json={"email": "alice@example.com"}
        ).status_code
        == 202
    )
    assert (
        c.post(
            "/api/auth/recovery/request", json={"email": "aLiCe@example.com"}
        ).status_code
        == 429
    )
