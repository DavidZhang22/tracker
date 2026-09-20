"""Scrub a STAGED restore using the current deletion ledger.

Usage: python3 deploy/prepare-restore.py LIVE_DATA STAGED_COPY.
The live directory is read-only. Staged sessions are all revoked.
"""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.tracker.erasure import apply_erasure, ledger


def prepare(live, staged):
    live, staged = Path(live).resolve(strict=True), Path(staged).resolve(strict=True)
    if live == staged or staged.is_relative_to(live) or live.is_relative_to(staged):
        raise ValueError("Use a separate staged restore directory")
    records = ledger(live / "accounts.sqlite3")
    apply_erasure(staged, staged / "tracker.sqlite3", records)
    with closing(
        sqlite3.connect((live / "accounts.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute("BEGIN")
        credentials = db.execute("SELECT password_hash,id FROM users").fetchall()
        emails = (
            db.execute("SELECT user_id,email,verified FROM recovery_emails").fetchall()
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_emails'"
            ).fetchone()
            else []
        )
    with closing(sqlite3.connect(staged / "accounts.sqlite3")) as db, db:
        db.execute("PRAGMA secure_delete=ON")
        db.executemany("UPDATE users SET password_hash=? WHERE id=?", credentials)
        for table in ("sessions", "recovery_tokens", "recovery_sessions"):
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                db.execute(f"DELETE FROM {table}")
        db.execute(
            "CREATE TABLE IF NOT EXISTS recovery_emails (user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, email TEXT COLLATE NOCASE UNIQUE NOT NULL, verified REAL NOT NULL)"
        )
        # Restoring an old address could give a former mailbox owner access.
        db.execute("DELETE FROM recovery_emails")
        for uid, email, verified in emails:
            db.execute(
                "INSERT INTO recovery_emails SELECT id,?,? FROM users WHERE id=?",
                (email, verified, uid),
            )
        db.execute(
            "CREATE TABLE IF NOT EXISTS erasures (user_id TEXT PRIMARY KEY, legacy_library INTEGER NOT NULL, requested REAL NOT NULL, completed REAL, cache_cleared INTEGER NOT NULL DEFAULT 0)"
        )
        for row in records:
            db.execute(
                "INSERT OR REPLACE INTO erasures VALUES (?,?,?,?,?)",
                (
                    row["user_id"],
                    row["legacy_library"],
                    row["requested"],
                    row["completed"],
                    row.get("cache_cleared", 0),
                ),
            )
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print(
        "Staged restore scrubbed; sessions revoked. Keep the current deletion ledger with all restores."
    )


if __name__ == "__main__":
    prepare(*sys.argv[1:])
