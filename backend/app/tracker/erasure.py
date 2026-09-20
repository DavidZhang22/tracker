"""Idempotent erasure shared by the app and backup/restore tooling."""

import hashlib
import re
import sqlite3
from contextlib import closing
from pathlib import Path


def library_path(root, library, user_id, legacy):
    if not re.fullmatch(r"[0-9a-f]{32}", user_id):
        raise ValueError("Invalid erasure account ID")
    root = Path(root).resolve()
    path = Path(library) if legacy else root / "users" / user_id / "tracker.sqlite3"
    if not path.resolve().is_relative_to(root):
        raise ValueError("Erasure path leaves the data directory")
    return path


def wipe_library(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(str(path) + ".erased").touch(mode=0o600, exist_ok=True)
    if not path.exists():
        return
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
    ) as db:
        db.execute("PRAGMA secure_delete=ON")
        with db:
            db.execute("BEGIN IMMEDIATE")
            # Only application tables, not arbitrary SQL from submitted data.
            for table in (
                "suggestion_sources",
                "suggestions",
                "links",
                "items",
                "scans",
                "preferences",
                "saved_views",
                "addition_cooldown",
            ):
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone():
                    db.execute(f'DELETE FROM "{table}"')
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("VACUUM")


def apply_erasure(root, library, records):
    """Apply a current deletion ledger to a live directory or restored snapshot."""
    root = Path(root).resolve()
    for record in records:
        wipe_library(
            library_path(root, library, record["user_id"], record["legacy_library"])
        )
    accounts = root / "accounts.sqlite3"
    if not accounts.exists():
        return
    with closing(sqlite3.connect(accounts)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        with db:
            for record in records:
                user = db.execute(
                    "SELECT username FROM users WHERE id=?", (record["user_id"],)
                ).fetchone()
                if user:
                    db.execute(
                        "DELETE FROM attempts WHERE key=?",
                        ("user:" + hashlib.sha256(user[0].encode()).hexdigest(),),
                    )
                db.execute("DELETE FROM sessions WHERE user_id=?", (record["user_id"],))
                db.execute("DELETE FROM users WHERE id=?", (record["user_id"],))
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def ledger(path):
    with closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.row_factory = sqlite3.Row
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='erasures'"
        ).fetchone():
            return []
        return [dict(row) for row in db.execute("SELECT * FROM erasures")]
