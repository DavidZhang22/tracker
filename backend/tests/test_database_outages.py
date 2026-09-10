import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.store import Store


def test_missing_database_returns_503_and_recovers_without_restart(tmp_path):
    path = tmp_path / "library.sqlite3"
    app = create_app(path)
    with TestClient(app) as client:
        assert client.get("/api/ready").status_code == 200
        backup = path.with_suffix(".backup")
        path.rename(backup)
        response = client.get("/api/items")
        assert response.status_code == 503
        assert response.json()["code"] == "DATABASE_UNAVAILABLE"
        assert response.headers["retry-after"] == "5"
        assert response.headers["cache-control"] == "no-store"
        assert not path.exists()
        assert client.get("/api/ready").status_code == 503
        assert client.get("/api/health").status_code == 200
        backup.rename(path)
        assert client.get("/api/items").json() == []


def test_missing_database_is_not_reinitialized(tmp_path):
    path = tmp_path / "library.sqlite3"
    Store(path)
    path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    assert not path.exists()


def test_transaction_rolls_back_on_storage_failure(tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    with pytest.raises(sqlite3.OperationalError):
        with store.connection() as db:
            db.execute("INSERT INTO scans VALUES ('test', '2026-01-01', '{}')")
            raise sqlite3.OperationalError("disk full")
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM scans").fetchone()[0] == 0


def test_account_outage_does_not_bypass_authentication(tmp_path, monkeypatch):
    app = create_app(
        tmp_path / "library.sqlite3",
        auth_config={"required": True, "origin": "https://tracker.example.com"},
    )

    def unavailable(*args):
        raise sqlite3.OperationalError("sensitive internal path")

    monkeypatch.setattr(app.state.accounts, "user", unavailable)
    with TestClient(app, base_url="https://tracker.example.com") as client:
        response = client.get("/api/items")
        assert response.status_code == 503
        assert "sensitive" not in response.text
        assert "set-cookie" not in response.headers


def test_scan_does_not_fetch_sources_when_storage_is_down(tmp_path):
    app = create_app(tmp_path / "library.sqlite3")
    (tmp_path / "library.sqlite3").unlink()
    with TestClient(app) as client:
        response = client.post("/api/scans", json={"url": "https://example.com"})
        assert response.status_code == 503
