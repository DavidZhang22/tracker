"""Verify migration on private copies of a backup, without printing user data."""

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tracker.store import Store


def contents(path, schemas=None):
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        if schemas is None:
            tables = [
                r[0]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            schemas = {
                table: [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
                for table in tables
            }
        digest = hashlib.sha256()
        for table, columns in sorted(schemas.items()):
            selected = ",".join(f'"{column}"' for column in columns)
            rows = db.execute(f'SELECT {selected} FROM "{table}" ORDER BY 1').fetchall()
            digest.update(json.dumps([table, rows], sort_keys=True).encode())
        return schemas, digest.hexdigest()


def main(source):
    count = 0
    with tempfile.TemporaryDirectory() as directory:
        for original in Path(source).rglob("tracker.sqlite3"):
            target = Path(directory) / str(count) / "tracker.sqlite3"
            target.parent.mkdir()
            shutil.copyfile(original, target)
            schemas, before = contents(target)
            store = Store(target, allow_erased=True)
            assert contents(target, schemas)[1] == before, (
                "Migration changed existing records"
            )
            for item in store.items():
                store.links(item["id"], limit=1)
            count += 1
    assert count, "No library backups found"
    print(
        json.dumps(
            {
                "libraries_verified": count,
                "existing_records": "unchanged",
                "integrity": "passed",
            }
        )
    )


if __name__ == "__main__":
    main(sys.argv[1])
