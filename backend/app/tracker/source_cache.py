"""Small durable leases and verified redirect aliases, separate from evictable bodies."""

import asyncio
import hashlib
import sqlite3
import time
import uuid
from contextlib import closing

from .cache import cache_epochs
from .errors import DiscoveryError
from .workers import run_blocking

REUSE_SECONDS = 300
MAX_RECORDS = 10_000
MAX_NEW_SOURCES = 20


class SourceRedirect(Exception):
    def __init__(self, url, document):
        self.url, self.document = url, document


def cacheable():
    from .urls import request_budget

    budget = request_budget.get()
    return budget is None or budget.cacheable


def identity(value):
    return hashlib.sha256(value.encode()).hexdigest()


class SourceCoordinator:
    def __init__(self, cache):
        self.cache = cache
        self.gates, self.aliases = {}, {}
        self.probes = []
        if cache.path:
            with closing(self.connect()) as db, db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS source_gates (
                      key TEXT PRIMARY KEY, owner TEXT NOT NULL, lease REAL NOT NULL,
                      until REAL NOT NULL, error TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS source_gate_expiry ON source_gates(until,lease);
                    CREATE TABLE IF NOT EXISTS source_aliases (
                      key TEXT PRIMARY KEY, target TEXT NOT NULL, expires REAL NOT NULL);
                    CREATE INDEX IF NOT EXISTS source_alias_expiry ON source_aliases(expires);
                    CREATE TABLE IF NOT EXISTS source_probes (host TEXT NOT NULL, expires REAL NOT NULL);
                    CREATE INDEX IF NOT EXISTS source_probe_host ON source_probes(host,expires);
                    CREATE INDEX IF NOT EXISTS source_probe_expiry ON source_probes(expires);
                """)

    def connect(self):
        return sqlite3.connect(self.cache.path, timeout=1)

    def writable(self):
        return (cache_epochs.get() or {}).get(
            id(self.cache), self.cache.generation
        ) == self.cache.generation

    def resolve(self, url):
        now, seen = time.time(), set()

        def follow(get):
            current = url
            for _ in range(7):
                if current in seen:
                    raise DiscoveryError("The source has a redirect loop.")
                seen.add(current)
                row = get(identity(current))
                if not row or row[1] <= now:
                    return current
                current = row[0]
            raise DiscoveryError("The source redirected too many times.")

        if not self.cache.path:
            with self.cache.lock:
                return follow(self.aliases.get)
        with closing(self.connect()) as db:
            return follow(
                lambda key: db.execute(
                    "SELECT target,expires FROM source_aliases WHERE key=?", (key,)
                ).fetchone()
            )

    def remember(self, source, target, seconds):
        if source == target:
            return
        with self.cache.lock:
            if not self.writable():
                return
            now, key = time.time(), identity(source)
            expires = now + min(seconds, 86400)
            if not self.cache.path:
                self.aliases = {k: v for k, v in self.aliases.items() if v[1] > now}
                if key in self.aliases or len(self.aliases) < MAX_RECORDS:
                    self.aliases[key] = (target, expires)
                return
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM source_aliases WHERE expires<=?", (now,))
                if (
                    db.execute(
                        "SELECT 1 FROM source_aliases WHERE key=?", (key,)
                    ).fetchone()
                    or db.execute("SELECT count(*) FROM source_aliases").fetchone()[0]
                    < MAX_RECORDS
                ):
                    db.execute(
                        "INSERT OR REPLACE INTO source_aliases VALUES(?,?,?)",
                        (key, target, expires),
                    )

    def claim(self, key, owner, lease=210):
        now = time.time()

        def state(row):
            if row:
                if row[0] and row[1] > now:
                    return "busy", row[1], ""
                if row[2] > now:
                    return "recent", row[2], row[3]
            return None

        with self.cache.lock:
            if not self.writable():
                raise DiscoveryError(
                    "The scan was cancelled by a cache reset. Retry shortly."
                )
            if not self.cache.path:
                if result := state(self.gates.get(key)):
                    return result
                self.gates = {k: v for k, v in self.gates.items() if max(v[1:3]) > now}
                if len(self.gates) >= MAX_RECORDS and key not in self.gates:
                    raise DiscoveryError(
                        "The shared source cache is busy. Retry shortly."
                    )
                self.gates[key] = (owner, now + lease, now + REUSE_SECONDS, "")
                return "owner", 0, ""
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT owner,lease,until,error FROM source_gates WHERE key=?",
                    (key,),
                ).fetchone()
                if result := state(row):
                    return result
                db.execute(
                    "DELETE FROM source_gates WHERE until<=? AND lease<=?", (now, now)
                )
                if (
                    not row
                    and db.execute("SELECT count(*) FROM source_gates").fetchone()[0]
                    >= MAX_RECORDS
                ):
                    raise DiscoveryError(
                        "The shared source cache is busy. Retry shortly."
                    )
                db.execute(
                    "INSERT OR REPLACE INTO source_gates VALUES(?,?,?,?,?)",
                    (key, owner, now + lease, now + REUSE_SECONDS, ""),
                )
                return "owner", 0, ""

    def finish(self, key, owner, seconds=0, error=""):
        until = time.time() + seconds if seconds else 0
        with self.cache.lock:
            if not self.writable():
                error = ""
            if not self.cache.path:
                row = self.gates.get(key)
                if row and row[0] == owner:
                    self.gates[key] = ("", 0, until, error[:600])
                return
            with closing(self.connect()) as db, db:
                db.execute(
                    "UPDATE source_gates SET owner='',lease=0,until=?,error=? WHERE key=? AND owner=?",
                    (until, error[:600], key, owner),
                )

    def clear(self):
        with self.cache.lock:
            self.gates = {
                key: (*value[:3], "")
                for key, value in self.gates.items()
                if key.startswith("fetch:")
            }
            self.aliases.clear()
            if self.cache.path:
                with closing(self.connect()) as db, db:
                    db.execute("PRAGMA secure_delete=ON")
                    db.execute("DELETE FROM source_gates WHERE key NOT LIKE 'fetch:%'")
                    db.execute(
                        "UPDATE source_gates SET error='' WHERE key LIKE 'fetch:%'"
                    )
                    db.execute("DELETE FROM source_aliases")

    def prune(self):
        now = time.time()
        with self.cache.lock:
            if not self.cache.path:
                self.gates = {k: v for k, v in self.gates.items() if max(v[1:3]) > now}
                self.aliases = {k: v for k, v in self.aliases.items() if v[1] > now}
                self.probes = [(h, t) for h, t in self.probes if t > now]
                return
            with closing(self.connect()) as db, db:
                db.execute("PRAGMA secure_delete=ON")
                db.execute(
                    "DELETE FROM source_gates WHERE until<=? AND lease<=?", (now, now)
                )
                db.execute("DELETE FROM source_aliases WHERE expires<=?", (now,))
                db.execute("DELETE FROM source_probes WHERE expires<=?", (now,))

    def admit_source(self, host):
        """Aggregate host limits survive URL mutations and contain no account/URL data."""
        host, now = identity(host.removeprefix("www.")), time.time()

        def check(rows):
            if len(rows) >= MAX_NEW_SOURCES:
                delay = max(1, int(min(rows) - now) + 1)
                raise DiscoveryError(
                    f"Too many new source URLs for this site. Retry in {delay} seconds."
                )

        with self.cache.lock:
            if not self.writable():
                raise DiscoveryError(
                    "The scan was cancelled by a cache reset. Retry shortly."
                )
            if not self.cache.path:
                self.probes = [(h, t) for h, t in self.probes if t > now]
                check([t for h, t in self.probes if h == host])
                if len(self.probes) >= MAX_RECORDS:
                    raise DiscoveryError(
                        "The shared source cache is busy. Retry shortly."
                    )
                self.probes.append((host, now + REUSE_SECONDS))
                return
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM source_probes WHERE expires<=?", (now,))
                check(
                    [
                        r[0]
                        for r in db.execute(
                            "SELECT expires FROM source_probes WHERE host=?", (host,)
                        )
                    ]
                )
                if (
                    db.execute("SELECT count(*) FROM source_probes").fetchone()[0]
                    >= MAX_RECORDS
                ):
                    raise DiscoveryError(
                        "The shared source cache is busy. Retry shortly."
                    )
                db.execute(
                    "INSERT INTO source_probes VALUES(?,?)", (host, now + REUSE_SECONDS)
                )


class SharedWork:
    def __init__(self, coordinator, key):
        self.coordinator, self.key = coordinator, key
        self.owner = uuid.uuid4().hex
        self.seconds, self.error = 0, ""

    async def __aenter__(self):
        try:
            async with asyncio.timeout(185):
                while True:
                    result = await run_blocking(
                        self.coordinator.claim, self.key, self.owner
                    )
                    if result[0] != "busy":
                        self.state = result
                        return self
                    await asyncio.sleep(0.1)
        except TimeoutError as exc:
            await self.__aexit__(None, None, None)
            raise DiscoveryError(
                "This source is still being checked. Retry shortly."
            ) from exc
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_):
        await run_blocking(
            self.coordinator.finish, self.key, self.owner, self.seconds, self.error
        )

    def require_owner(self):
        if self.state[0] == "recent":
            if self.state[2]:
                raise DiscoveryError(self.state[2])
            remaining = max(1, int(self.state[1] - time.time()) + 1)
            raise DiscoveryError(
                f"This source was checked recently. Its cached result is unavailable; retry in {remaining} seconds."
            )
