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
    with closing(sqlite3.connect(staged / "accounts.sqlite3")) as db, db:
        db.execute("PRAGMA secure_delete=ON")
        db.execute("DELETE FROM sessions")
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
