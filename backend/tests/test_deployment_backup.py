import importlib.util
import sqlite3
from pathlib import Path

import pytest

from app.tracker.accounts import Accounts
from app.tracker.store import Store

spec = importlib.util.spec_from_file_location(
    "deployment_backup", Path(__file__).resolve().parents[2] / "deploy" / "backup.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_online_backup_preserves_account_and_library_and_rotates(tmp_path):
    data = tmp_path / "live"
    store = Store(data / "tracker.sqlite3")
    accounts = Accounts(store.path)
    accounts.create("owner", "a-unique-test-password", claim_existing=True)
    with store.connection() as db:
        db.execute("INSERT INTO scans VALUES ('saved', '2026-09-09', '{}')")
    snapshots = tmp_path / "backups"
    for _ in range(8):
        latest = module.backup(data, snapshots)
    assert len(list(snapshots.iterdir())) == 7
    recovered = Accounts(latest / "tracker.sqlite3")
    assert recovered.login("owner", "a-unique-test-password")["username"] == "owner"
    with sqlite3.connect(latest / "tracker.sqlite3") as db:
        assert db.execute("SELECT id FROM scans").fetchone()[0] == "saved"


def test_failed_backup_keeps_previous_snapshot(tmp_path):
    data = tmp_path / "live"
    Store(data / "tracker.sqlite3")
    Accounts(data / "tracker.sqlite3")
    snapshots = tmp_path / "backups"
    previous = module.backup(data, snapshots)
    (data / "tracker.sqlite3").write_bytes(b"broken database")
    with pytest.raises(sqlite3.DatabaseError):
        module.backup(data, snapshots)
    assert list(snapshots.iterdir()) == [previous]
