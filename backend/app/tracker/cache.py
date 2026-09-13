"""Bounded disk cache, shared by HTTP responses and complete scan previews."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from threading import RLock

MAX_BYTES = 64_000_000


class FetchCache:
    def __init__(self, path=None):
        self.path = str(path) if path else None
        self.memory = {}
        self.sizes = {}
        self.total_bytes = 0
        self.lock = RLock()
        if self.path:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL, checked REAL NOT NULL, size INTEGER NOT NULL DEFAULT 0)"
                )
                if "size" not in {r[1] for r in db.execute("PRAGMA table_info(cache)")}:
                    db.execute(
                        "ALTER TABLE cache ADD COLUMN size INTEGER NOT NULL DEFAULT 0"
                    )
                    db.execute("UPDATE cache SET size=length(CAST(payload AS BLOB))")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS cache_eviction ON cache(checked DESC,key,size)"
                )

    def get(self, key):
        if not self.path:
            with self.lock:
                return self.memory.get(key)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT payload FROM cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        value = dict(value)
        value.setdefault("checked", time.time())
        payload = json.dumps(value)
        size = len(payload.encode())
        if not self.path:
            with self.lock:
                self.total_bytes += size - self.sizes.get(key, 0)
                self.memory[key], self.sizes[key] = value, size
                while len(self.memory) > 128 or self.total_bytes > MAX_BYTES:
                    oldest = next(iter(self.memory))
                    self.total_bytes -= self.sizes.pop(oldest)
                    del self.memory[oldest]
            return
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO cache(key,payload,checked,size) VALUES (?,?,?,?)",
                (key, payload, value["checked"], size),
            )
            db.execute("DELETE FROM cache WHERE checked<?", (time.time() - 7 * 86400,))
            # Prune by byte budget as well as age; VACUUM is unnecessary for reuse.
            rows = db.execute(
                "SELECT key,size FROM cache ORDER BY checked DESC,key"
            ).fetchall()
            total = 0
            for index, (old_key, size) in enumerate(rows):
                total += size
                if total > MAX_BYTES or index >= 1000:
                    db.execute("DELETE FROM cache WHERE key=?", (old_key,))
