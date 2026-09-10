import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.models import Entry, Scan
from app.tracker.store import ItemAdditionCooldown, Store
from tests.test_accounts import CONFIG, sign_up
from tests.test_accounts import client as account_client
from tests.test_store_api import FakeDiscoverer


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("app.tracker.store.time", lambda: now[0])
    return now


def preview(store, name):
    url = f"https://example.com/{name}"
    return store.save_scan(
        Scan(url, name, entries=[Entry(url + "/1", "Chapter 1")]).to_dict()
    )


def test_api_cooldown_boundary_keeps_preview_and_does_not_extend_on_retry(
    tmp_path, clock
):
    app = create_app(tmp_path / "library.sqlite3")
    store = app.state.store
    with TestClient(app) as client:
        first = client.post("/api/items", json={"scan_id": preview(store, "first")})
        assert first.status_code == 201
        body = {
            "scan_id": preview(store, "second"),
            "title": "My title",
            "mark_read": True,
        }
        for elapsed, retry in [(0, "8"), (7.9, "1")]:
            clock[0] = 1000 + elapsed
            response = client.post("/api/items", json=body)
            assert response.status_code == 429
            assert response.headers["retry-after"] == retry
            assert "no-store" in response.headers["cache-control"]
            assert "every 8 seconds" in response.json()["detail"]
            assert len(store.items()) == 1
        clock[0] = 1008
        saved = client.post("/api/items", json=body)
        assert saved.status_code == 201, saved.text
        assert saved.json()["title"] == "My title" and saved.json()["read_count"] == 1
        assert len(store.items()) == 2
        third = client.post("/api/items", json={"scan_id": preview(store, "third")})
        assert third.status_code == 429 and third.headers["retry-after"] == "8"


def test_storage_failure_does_not_consume_preview_or_cooldown(
    tmp_path, clock, monkeypatch
):
    app = create_app(tmp_path / "library.sqlite3")
    store = app.state.store
    store.create(preview(store, "first"))
    clock[0] += 8
    sid = preview(store, "second")

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    with TestClient(app) as client:
        with monkeypatch.context() as patch:
            patch.setattr(store, "_merge", fail)
            failed = client.post("/api/items", json={"scan_id": sid})
        assert failed.status_code == 503
        assert len(store.items()) == 1
        with store.connection() as db:
            assert (
                db.execute("SELECT completed_at FROM addition_cooldown").fetchone()[0]
                == 1000
            )
        assert client.post("/api/items", json={"scan_id": sid}).status_code == 201


@pytest.mark.parametrize("permanent", [False, True])
def test_restart_and_deletion_do_not_reset_cooldown(tmp_path, clock, permanent):
    path = tmp_path / "library.sqlite3"
    store = Store(path)
    first = store.create(preview(store, "first"))
    if permanent:
        with store.connection() as db:
            db.execute("DELETE FROM items WHERE id=?", (first["id"],))
    else:
        store.bulk_selected("items", [first["id"]], "delete")
    reopened = Store(path)
    sid = preview(reopened, "second")
    with pytest.raises(ItemAdditionCooldown) as blocked:
        reopened.create(sid)
    assert blocked.value.retry_after == 8
    clock[0] += 8
    assert reopened.create(sid)["title"] == "second"


def test_concurrent_additions_across_store_instances_commit_only_once(tmp_path, clock):
    stores = [Store(tmp_path / "library.sqlite3") for _ in range(2)]
    ids = [preview(store, str(i)) for i, store in enumerate(stores)]
    start = Barrier(2)

    def save(i):
        start.wait(timeout=5)
        try:
            stores[i].create(ids[i])
            return 201
        except ItemAdditionCooldown:
            return 429

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [201, 429]
    with stores[0].connection() as db:
        for table in ("items", "links", "scans"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_accounts_have_independent_cooldowns_on_the_same_ip(tmp_path, clock):
    fake = FakeDiscoverer()
    app = create_app(tmp_path / "library.sqlite3", fake, CONFIG)
    with account_client(app) as alice, account_client(app) as bob:
        sign_up(alice, "alice")
        sign_up(bob, "bob")
        for client in (alice, bob):
            scan = client.post("/api/scans", json={"url": fake.result.url}).json()
            assert (
                client.post("/api/items", json={"scan_id": scan["scan_id"]}).status_code
                == 201
            )
        fake.result.url = "https://blog.example/second"
        for client in (alice, bob):
            scan = client.post("/api/scans", json={"url": fake.result.url}).json()
            assert (
                client.post("/api/items", json={"scan_id": scan["scan_id"]}).status_code
                == 429
            )
