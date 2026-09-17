import asyncio
import gzip
import importlib.util
import socket
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.main import create_app
from app.tracker.accounts import COOKIE, Accounts
from app.tracker.cache import FetchCache, cache_epochs
from app.tracker.discovery import youtube_archive
from app.tracker.erasure import apply_erasure, ledger
from app.tracker.models import Entry, Scan
from app.tracker.semantic_search import SemanticSearch
from app.tracker.store import LibraryErased, Store
from app.tracker.urls import (
    DiscoveryError,
    SafeFetcher,
    canonical_url,
    public_addresses,
)
from tests.test_accounts import CONFIG, PASSWORD, add, client, sign_up
from tests.test_semantic_search import FakeEncoder
from tests.test_store_api import FakeDiscoverer


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "tracker.sqlite3", FakeDiscoverer(), CONFIG)


def test_export_is_reauthenticated_scoped_complete_and_rate_limited(app):
    alice, bob = client(app), client(app)
    sign_up(alice, "alice")
    sign_up(bob, "bob")
    item = add(alice)
    store = app.state.accounts.store(app.state.accounts.user(alice.cookies.get(COOKIE)))
    store.update("items", item["id"], {"description_override": "Private description"})
    SemanticSearch(FakeEncoder).enrich(store)
    assert store.semantic_records()[0]["semantic_vector"]
    assert (
        bob.post("/api/search", json={"query": "Private description"}).json()["scores"]
        == []
    )
    assert (
        alice.post("/api/auth/export", json={"current_password": "wrong"}).status_code
        == 401
    )
    response = alice.post("/api/auth/export", json={"current_password": PASSWORD})
    assert response.status_code == 200
    data = response.json()
    assert (
        data["account"]["username"] == "alice" and data["items"][0]["id"] == item["id"]
    )
    assert "password_hash" not in response.text and "token_hash" not in response.text
    assert data["items"][0]["description_override"] == "Private description"
    assert "semantic_vector" not in data["items"][0]
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert (
        bob.post("/api/auth/export", json={"current_password": PASSWORD}).json()[
            "items"
        ]
        == []
    )
    for _ in range(2):
        assert (
            alice.post(
                "/api/auth/export", json={"current_password": PASSWORD}
            ).status_code
            == 200
        )
    assert (
        alice.post("/api/auth/export", json={"current_password": PASSWORD}).status_code
        == 429
    )


def test_deletion_requires_password_confirmation_and_same_origin(app):
    c = client(app)
    sign_up(c, "alice")
    add(c)
    body = {"current_password": PASSWORD, "confirmation": "DELETE"}
    for bad, status in (
        ({"current_password": "wrong"}, 401),
        ({"confirmation": "delete"}, 422),
    ):
        assert c.post("/api/auth/delete", json=body | bad).status_code == status
    assert (
        c.post(
            "/api/auth/delete", json=body, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert len(c.get("/api/items").json()) == 1


def test_delete_revokes_sessions_wipes_all_tables_and_preserves_other_accounts(app):
    c, other_session, bob = client(app), client(app), client(app)
    sign_up(c, "alice")
    token = c.cookies.get(COOKIE)
    other_session.cookies.set(COOKIE, token)
    sign_up(bob, "bob")
    alice_item, bob_item = add(c), add(bob)
    user = app.state.accounts.user(token)
    old_store = app.state.accounts.store(user)
    with old_store.connection() as db:
        db.execute("INSERT INTO preferences VALUES (1, '{}')")
    r = c.post(
        "/api/auth/delete",
        json={"current_password": PASSWORD, "confirmation": "DELETE"},
    )
    assert r.status_code == 200 and not r.json()["cleanup_pending"]
    assert '"storage"' in r.headers["clear-site-data"]
    assert other_session.get("/api/items").status_code == 401
    assert app.state.accounts.user(token) is None
    assert bob.get("/api/items").json()[0]["id"] == bob_item["id"]
    with pytest.raises(LibraryErased):
        old_store.merge(
            alice_item["id"], Scan("https://example.org", "Late", entries=[]).to_dict()
        )
    with closing(sqlite3.connect(old_store.path)) as db:
        for table in (
            "items",
            "links",
            "scans",
            "preferences",
            "suggestions",
            "suggestion_sources",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    with app.state.accounts.connection() as db:
        assert (
            db.execute("SELECT 1 FROM users WHERE id=?", (user["id"],)).fetchone()
            is None
        )
        assert (
            db.execute(
                "SELECT 1 FROM sessions WHERE user_id=?", (user["id"],)
            ).fetchone()
            is None
        )
        record = dict(db.execute("SELECT * FROM erasures").fetchone())
        assert record["completed"] and "username" not in record


def test_deletion_is_queued_on_io_failure_and_retries_after_restart(app):
    c = client(app)
    sign_up(c, "alice")
    item = add(c)
    user = app.state.accounts.user(c.cookies.get(COOKIE))
    path = app.state.accounts.store(user).path
    with patch("app.tracker.accounts.wipe_library", side_effect=OSError("test-only")):
        result = c.post(
            "/api/auth/delete",
            json={"current_password": PASSWORD, "confirmation": "DELETE"},
        )
    assert result.status_code == 202 and result.json()["cleanup_pending"]
    assert c.get("/api/items").status_code == 401
    restarted = Accounts(app.state.store.path)
    restarted.maintenance()
    assert ledger(restarted.path)[0]["completed"]
    with closing(sqlite3.connect(path)) as db:
        assert not db.execute(
            "SELECT 1 FROM items WHERE id=?", (item["id"],)
        ).fetchone()


def test_legacy_owner_deletion_keeps_readiness_and_cannot_be_reclaimed(app):
    auth = app.state.accounts
    auth.create("owner", PASSWORD, True)
    c = client(app)
    assert (
        c.post(
            "/api/auth/login", json={"username": "owner", "password": PASSWORD}
        ).status_code
        == 200
    )
    add(c)
    assert (
        c.post(
            "/api/auth/delete",
            json={"current_password": PASSWORD, "confirmation": "DELETE"},
        ).status_code
        == 200
    )
    restarted = create_app(app.state.store.path, FakeDiscoverer(), CONFIG)
    assert client(restarted).get("/api/ready").status_code == 200
    with pytest.raises(ValueError, match="erased"):
        restarted.state.accounts.create("next-owner", PASSWORD, True)


def test_already_open_transaction_cannot_commit_after_erasure(app):
    user = app.state.accounts.create("alice", PASSWORD)
    store = app.state.accounts.store(user)
    with pytest.raises(LibraryErased), store.connection() as db:
        db.execute("INSERT INTO scans VALUES ('late','2026-09-14','{}')")
        store.erased_marker.touch()
    with closing(sqlite3.connect(store.path)) as db:
        assert not db.execute("SELECT 1 FROM scans").fetchone()


def test_logout_all_and_account_controls_work_when_library_is_down(app):
    c = client(app)
    sign_up(c, "alice")
    user = app.state.accounts.user(c.cookies.get(COOKIE))
    app.state.accounts.store(user)  # initialize once
    with patch.object(
        app.state.accounts, "store", side_effect=sqlite3.OperationalError("offline")
    ):
        assert (
            c.post(
                "/api/auth/logout-all", json={"current_password": PASSWORD}
            ).status_code
            == 200
        )
    assert c.get("/api/items").status_code == 401


def test_cache_erasure_discards_writes_from_inflight_scans(tmp_path):
    for cache in (FetchCache(), FetchCache(tmp_path / "cache.db")):
        cache.put("private-query", {"body": "test-only"})
        token = cache_epochs.set({id(cache): cache.generation})
        try:
            cache.clear()
            cache.put("late", {"body": "old request"})
            assert cache.get("private-query") is cache.get("late") is None
        finally:
            cache_epochs.reset(token)
        cache.put("fresh", {"body": "new public response"})
        assert cache.get("fresh")


@pytest.mark.parametrize(
    "encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)]
)
async def test_http_compression_is_bounded_before_decoding(encoding, compress):
    actual_client = httpx.AsyncClient
    payload = compress(b"a safe listing")

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload

    with (
        patch("app.tracker.urls.public_addresses", return_value=["93.184.216.34"]),
        patch(
            "app.tracker.urls.httpx.AsyncClient",
            side_effect=lambda **kw: actual_client(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(
                        200, stream=Body(), headers={"Content-Encoding": encoding}
                    )
                ),
                **kw,
            ),
        ),
    ):
        fetcher = SafeFetcher(interval=0)
        assert (await fetcher.get("https://example.org/normal"))[1] == "a safe listing"
        payload = compress(b"x" * 9_000_000)
        with pytest.raises(DiscoveryError, match="8 MB"):
            await fetcher.get("https://example.org/bomb")


@pytest.mark.parametrize(
    "address",
    [
        "168.63.129.16",
        "169.254.169.254",
        "127.0.0.1",
        "10.1.2.3",
        "::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "ff02::1",
        "2002:7f00:1::",
    ],
)
async def test_cloud_metadata_and_ip_tunnels_are_blocked(address):
    with patch.object(
        asyncio.get_running_loop(),
        "getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    ):
        with pytest.raises(DiscoveryError):
            await public_addresses("malicious.example")


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "http://user:password@public.example",
        "http://public.example:22/",
        "https://public.example/\r\nX-Evil:yes",
        "https://public.example/\\evil",
        "https://public.example/" + "x" * 4096,
    ],
)
def test_unsafe_urls_rejected(value):
    with pytest.raises(DiscoveryError):
        canonical_url(value)


def test_external_extractor_off_and_public_privacy_contact_available(app, monkeypatch):
    monkeypatch.setenv("TRACKER_PRIVACY_EMAIL", "privacy@example.com")
    monkeypatch.delenv("TRACKER_YOUTUBE_ARCHIVE", raising=False)
    with patch("app.tracker.discovery.subprocess.run") as execute:
        with pytest.raises(DiscoveryError, match="disabled"):
            youtube_archive("https://www.youtube.com/@example")
        execute.assert_not_called()
    c = client(app)
    assert c.get("/api/privacy").json()["contact"] == "privacy@example.com"
    assert "mailto:privacy@example.com" in c.get("/.well-known/security.txt").text


def test_backups_and_staged_restores_apply_current_erasure_ledger(tmp_path):
    from tests.test_deployment_backup import module as backups

    live = tmp_path / "live"
    store = Store(live / "tracker.sqlite3")
    auth = Accounts(store.path)
    user = auth.create("alice", PASSWORD)
    bob = auth.create("bob", PASSWORD)
    library = auth.store(user)
    library.create(
        library.save_scan(
            Scan(
                "https://example.org",
                "Private",
                entries=[Entry("https://example.org/one", "One")],
            ).to_dict()
        )
    )
    saved = backups.backup(live, tmp_path / "backups")
    auth.delete(user)
    # Current ledger also protects a pre-deletion snapshot before restoration.
    spec = importlib.util.spec_from_file_location(
        "prepare_restore",
        Path(__file__).resolve().parents[2] / "deploy" / "prepare-restore.py",
    )
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    prepare.prepare(live, saved)
    with closing(sqlite3.connect(saved / "accounts.sqlite3")) as db:
        assert db.execute("SELECT id FROM users").fetchall() == [(bob["id"],)]
    backups.backup(live, tmp_path / "backups")
    for snapshot in (tmp_path / "backups").iterdir():
        with closing(
            sqlite3.connect(snapshot / "users" / user["id"] / "tracker.sqlite3")
        ) as db:
            assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    with pytest.raises(ValueError):
        apply_erasure(
            tmp_path,
            tmp_path / "tracker.sqlite3",
            [{"user_id": "../../outside", "legacy_library": 0}],
        )
