"""Bounded disk cache, shared by HTTP responses and complete scan previews."""

import json
import sqlite3
import time
from pathlib import Path


class FetchCache:
    def __init__(self, path=None):
        self.path = str(path) if path else None
        self.memory = {}
        if self.path:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL, checked REAL NOT NULL)"
                )

    def get(self, key):
        if not self.path:
            return self.memory.get(key)
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        value = dict(value)
        value.setdefault("checked", time.time())
        if not self.path:
            self.memory[key] = value
            while (
                len(self.memory) > 128
                or sum(len(str(v)) for v in self.memory.values()) > 64_000_000
            ):
                del self.memory[next(iter(self.memory))]
            return
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?)",
                (key, json.dumps(value), value["checked"]),
            )
            db.execute("DELETE FROM cache WHERE checked<?", (time.time() - 7 * 86400,))
            # Prune by byte budget as well as age; VACUUM is unnecessary for reuse.
            rows = db.execute(
                "SELECT key,length(CAST(payload AS BLOB)) size FROM cache ORDER BY checked DESC"
            ).fetchall()
            total = 0
            for index, (old_key, size) in enumerate(rows):
                total += size
                if total > 64_000_000 or index >= 1000:
                    db.execute("DELETE FROM cache WHERE key=?", (old_key,))
