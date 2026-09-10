"""SQLite repository. Transactions preserve user state across every scan."""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from math import ceil
from pathlib import Path
from time import time

from .limits import (
    ITEM_ADD_INTERVAL_SECONDS,
    MAX_ITEMS,
    MAX_LIBRARY_LINKS,
    MAX_LINKS,
    MAX_PREVIEW_BYTES,
    MAX_PREVIEWS,
    bounded_scan,
)
from .models import date_rank, utcnow
from .urls import DiscoveryError, canonical_url, content_key


def identity(url):
    return content_key(url)


class ItemAdditionCooldown(Exception):
    def __init__(self, remaining):
        self.retry_after = max(1, ceil(remaining))
        super().__init__(
            f"You can add one item every {ITEM_ADD_INTERVAL_SECONDS} seconds. Your scan is ready to save."
        )


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        marker = Path(str(self.path) + ".initialized")
        if marker.exists() and not Path(self.path).is_file():
            raise sqlite3.OperationalError("Previously initialized database is missing")
        with self.connection(create=True) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS items (
              id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL, kind TEXT NOT NULL,
              favorite INTEGER NOT NULL DEFAULT 0, ignored INTEGER NOT NULL DEFAULT 0,
              auto_read INTEGER NOT NULL DEFAULT 1, selector TEXT NOT NULL DEFAULT '', include_path TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL, last_checked_at TEXT, last_attempt_at TEXT, error TEXT,
              warnings TEXT NOT NULL DEFAULT '[]', methods TEXT NOT NULL DEFAULT '[]', pages_scanned INTEGER NOT NULL DEFAULT 0,
              expected_count INTEGER
            );
            CREATE TABLE IF NOT EXISTS links (
              id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
              identity TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, published_at TEXT,
              number REAL, position INTEGER NOT NULL, method TEXT NOT NULL,
              discovered_at TEXT NOT NULL, is_new INTEGER NOT NULL DEFAULT 0,
              read INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0, ignored INTEGER NOT NULL DEFAULT 0,
              UNIQUE(item_id,identity)
            );
            CREATE INDEX IF NOT EXISTS idx_links_item_published ON links(item_id,published_at);
            CREATE INDEX IF NOT EXISTS idx_links_item_unread ON links(item_id,read,ignored);
            CREATE TABLE IF NOT EXISTS scans (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS addition_cooldown (
              id INTEGER PRIMARY KEY CHECK(id=1), completed_at REAL NOT NULL
            );
            """)
            # Additive migration: existing libraries, IDs and progress stay intact.
            additions = {
                "items": {
                    "deleted": "INTEGER NOT NULL DEFAULT 0",
                    "requests_made": "INTEGER NOT NULL DEFAULT 0",
                    "cache_hits": "INTEGER NOT NULL DEFAULT 0",
                    "coverage": "TEXT NOT NULL DEFAULT 'unknown'",
                    "order_hint": "TEXT NOT NULL DEFAULT ''",
                    "keywords": "TEXT NOT NULL DEFAULT ''",
                },
                "links": {
                    "deleted": "INTEGER NOT NULL DEFAULT 0",
                    "date_kind": "TEXT NOT NULL DEFAULT 'published'",
                    "date_source": "TEXT NOT NULL DEFAULT ''",
                    "date_precision": "TEXT NOT NULL DEFAULT 'day'",
                    "summary": "TEXT NOT NULL DEFAULT ''",
                    "availability": "TEXT NOT NULL DEFAULT ''",
                    "context": "TEXT NOT NULL DEFAULT ''",
                    "language": "TEXT NOT NULL DEFAULT ''",
                },
            }
            for table, columns in additions.items():
                existing = {
                    row["name"] for row in db.execute(f"PRAGMA table_info({table})")
                }
                for name, definition in columns.items():
                    if name not in existing:
                        db.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                        )
            db.execute("PRAGMA user_version=5")
        marker.touch(exist_ok=True)

    @contextmanager
    def connection(self, create=False):
        target = Path(self.path).resolve().as_uri() + (
            "?mode=rwc" if create else "?mode=rw"
        )
        db = sqlite3.connect(target, uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        r = dict(row)
        for k in ("favorite", "ignored", "auto_read", "read", "is_new", "deleted"):
            if k in r:
                r[k] = bool(r[k])
        for k in ("warnings", "methods"):
            if k in r:
                r[k] = json.loads(r[k])
        return r

    def items(self, item_id=None, trash=False):
        with self.connection() as db:
            rows = db.execute(
                """SELECT i.*, count(l.id) total_count,
              coalesce(sum(l.ignored),0) ignored_count,
              coalesce(sum(l.read=1 AND l.ignored=0),0) read_count,
              coalesce(sum(l.read=0 AND l.ignored=0),0) unread_count,
              coalesce(sum(l.is_new=1 AND l.ignored=0 AND l.read=0),0) new_count,
              coalesce(sum(l.published_at IS NOT NULL),0) dated_count,
              coalesce(sum(l.ignored=0),0) active_count,
              coalesce(sum(l.number IS NOT NULL AND l.ignored=0),0) active_numbered,
              coalesce(sum(l.published_at IS NOT NULL AND l.ignored=0),0) active_dated,
              coalesce(sum(l.date_kind='scheduled' AND l.published_at > strftime('%Y-%m-%dT%H:%M:%S','now') AND l.ignored=0),0) upcoming_count,
              max(l.discovered_at) latest_discovered_at
              FROM items i LEFT JOIN links l ON l.item_id=i.id AND l.deleted=0 """
                + ("WHERE i.id=? " if item_id else "WHERE i.deleted=? ")
                + "GROUP BY i.id ORDER BY i.created_at DESC",
                (item_id,) if item_id else (trash,),
            ).fetchall()
            items = [self.decode(r) for r in rows]
            for item in items:
                count = item.pop("active_count")
                numbered = item.pop("active_numbered")
                dated = item.pop("active_dated")
                order = (
                    "position"
                    if item["order_hint"] == "source"
                    else "number"
                    if count == numbered
                    else "published_at"
                    if count == dated
                    else "position"
                )
                latest = (
                    None
                    if item["deleted"] or not count
                    else db.execute(
                        f"SELECT id,url,title,read,number,published_at FROM links WHERE item_id=? AND ignored=0 AND deleted=0 ORDER BY {order} DESC,position DESC,id DESC LIMIT 1",
                        (item["id"],),
                    ).fetchone()
                )
                item["latest_link"] = self.decode(latest)
        return items

    def item(self, item_id):
        rows = self.items(item_id)
        if not rows:
            raise KeyError("Item not found.")
        return rows[0]

    def save_scan(self, payload):
        payload = bounded_scan(payload)
        encoded = json.dumps(payload)
        if len(encoded.encode()) > MAX_PREVIEW_BYTES:
            raise ValueError(
                "This scan is too large to save. Use a narrower source or link selector."
            )
        sid = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM scans WHERE created_at < strftime('%Y-%m-%dT%H:%M:%S', 'now', '-1 hour')"
            )
            db.execute("INSERT INTO scans VALUES (?,?,?)", (sid, utcnow(), encoded))
            db.execute(
                "DELETE FROM scans WHERE id NOT IN (SELECT id FROM scans ORDER BY created_at DESC,id DESC LIMIT ?)",
                (MAX_PREVIEWS,),
            )
        return sid

    def create(self, scan_id, title=None, mark_read=False, auto_read=True):
        iid = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM items").fetchone()[0] >= MAX_ITEMS:
                raise ValueError(
                    f"This library has reached its {MAX_ITEMS}-item limit, including Trash."
                )
            row = db.execute(
                "SELECT payload FROM scans WHERE id=? AND created_at > strftime('%Y-%m-%dT%H:%M:%S','now','-1 hour')",
                (scan_id,),
            ).fetchone()
            if not row:
                raise ValueError("This scan expired. Scan the source again.")
            scan = json.loads(row["payload"])
            if any(
                canonical_url(existing["url"]) == canonical_url(scan["url"])
                for existing in db.execute("SELECT url FROM items")
            ):
                raise sqlite3.IntegrityError("Source already exists")
            # The account's database serializes additions across tabs and workers.
            # Persist separately from items so deleting them cannot reset the limit.
            previous = db.execute(
                "SELECT completed_at FROM addition_cooldown WHERE id=1"
            ).fetchone()
            if previous:
                elapsed = max(0, time() - previous["completed_at"])
                if elapsed < ITEM_ADD_INTERVAL_SECONDS:
                    raise ItemAdditionCooldown(ITEM_ADD_INTERVAL_SECONDS - elapsed)
            db.execute(
                """INSERT INTO items(id,url,title,kind,auto_read,selector,include_path,created_at,keywords)
              VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    iid,
                    scan["url"],
                    title or scan["title"],
                    scan["kind"],
                    auto_read,
                    scan.get("selector", ""),
                    scan.get("include_path", ""),
                    utcnow(),
                    scan.get("keywords", ""),
                ),
            )
            self._merge(db, iid, scan, initial=True, mark_read=mark_read)
            db.execute("DELETE FROM scans WHERE id=?", (scan_id,))
            # Only a committed addition consumes the cooldown; errors roll it back.
            db.execute(
                "INSERT INTO addition_cooldown(id,completed_at) VALUES (1,?) "
                "ON CONFLICT(id) DO UPDATE SET completed_at=excluded.completed_at",
                (time(),),
            )
        return self.item(iid)

    def _merge(self, db, iid, scan, initial=False, mark_read=False):
        scan = bounded_scan(scan)
        if len(json.dumps(scan).encode()) > MAX_PREVIEW_BYTES:
            raise DiscoveryError(
                "This scan is too large to save. Use a narrower source or link selector. Saved links were kept."
            )
        now = utcnow()
        added = 0
        previous = [
            r["identity"]
            for r in db.execute(
                "SELECT identity FROM links WHERE item_id=? ORDER BY position,id",
                (iid,),
            )
        ]
        known = set(previous)
        remaining = max(
            0,
            min(
                MAX_LINKS - len(previous),
                MAX_LIBRARY_LINKS
                - db.execute("SELECT count(*) FROM links").fetchone()[0],
            ),
        )
        accepted = []
        capped = False
        for entry in scan["entries"]:
            key = identity(entry["url"])
            if key not in known:
                if not remaining:
                    capped = True
                    continue
                remaining -= 1
                known.add(key)
            accepted.append(entry)
        scan = scan | {"entries": accepted}
        if capped:
            scan = scan | {
                "coverage": "partial",
                "warnings": [
                    *scan["warnings"],
                    f"Storage limit reached ({MAX_LINKS:,} links per item; {MAX_LIBRARY_LINKS:,} per library, including Trash). Saved links and progress were kept; additional links were skipped.",
                ],
            }
        incoming = list(dict.fromkeys(identity(e["url"]) for e in scan["entries"]))
        incoming_set = set(incoming)
        # Retain disappeared entries next to their surviving source neighbors.
        before = {}
        next_survivor = None
        for key in reversed(previous):
            if key in incoming_set:
                next_survivor = key
            else:
                before.setdefault(next_survivor, []).append(key)
        order = []
        for key in incoming:
            order.extend(reversed(before.get(key, [])))
            order.append(key)
        order.extend(reversed(before.get(None, [])))
        for entry in scan["entries"]:
            key = identity(entry["url"])
            old = db.execute(
                "SELECT * FROM links WHERE item_id=? AND identity=?", (iid, key)
            ).fetchone()
            if old:
                if date_rank(old) > date_rank(entry):
                    entry = dict(entry)
                    for field in (
                        "published_at",
                        "date_kind",
                        "date_source",
                        "date_precision",
                    ):
                        entry[field] = old[field]
                db.execute(
                    """UPDATE links SET url=?,title=?,published_at=coalesce(?,published_at),
                    number=coalesce(?,number),position=?,method=? WHERE id=?""",
                    (
                        entry["url"],
                        entry["title"],
                        entry.get("published_at"),
                        entry["number"],
                        entry["position"],
                        entry["method"],
                        old["id"],
                    ),
                )
            else:
                db.execute(
                    """INSERT INTO links(id,item_id,identity,url,title,published_at,number,position,method,discovered_at,is_new,read)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        iid,
                        key,
                        entry["url"],
                        entry["title"],
                        entry["published_at"],
                        entry["number"],
                        entry["position"],
                        entry["method"],
                        now,
                        not initial,
                        mark_read,
                    ),
                )
                added += 1
            metadata = {
                k: entry.get(k, "")
                for k in ("summary", "availability", "context", "language")
            }
            if entry.get("published_at"):
                metadata.update(
                    {
                        k: entry.get(k, default)
                        for k, default in (
                            ("date_kind", "published"),
                            ("date_source", ""),
                            ("date_precision", "day"),
                        )
                    }
                )
            db.execute(
                "UPDATE links SET "
                + ",".join(k + "=?" for k in metadata)
                + " WHERE item_id=? AND identity=?",
                (*metadata.values(), iid, key),
            )
        db.executemany(
            "UPDATE links SET position=? WHERE item_id=? AND identity=?",
            ((i, iid, key) for i, key in enumerate(order)),
        )
        db.execute(
            """UPDATE items SET last_checked_at=?,last_attempt_at=?,error=NULL,warnings=?,methods=?,pages_scanned=?,expected_count=?,requests_made=?,cache_hits=?,coverage=?,order_hint=? WHERE id=?""",
            (
                scan.get("checked_at") or now,
                now,
                json.dumps(scan["warnings"]),
                json.dumps(scan["methods"]),
                scan["pages_scanned"],
                scan["expected_count"],
                scan.get("requests_made", 0),
                scan.get("cache_hits", 0),
                scan.get("coverage", "unknown"),
                "source" if scan.get("order_hint") == "source" else "",
                iid,
            ),
        )
        return added

    def merge(self, iid, scan):
        with self.connection() as db:
            # Serialize quota checks with inserts, including concurrent refreshes.
            db.execute("BEGIN IMMEDIATE")
            return self._merge(db, iid, scan)

    def failure(self, iid, message):
        with self.connection() as db:
            db.execute(
                "UPDATE items SET error=?,last_attempt_at=? WHERE id=?",
                (message, utcnow(), iid),
            )

    def update(self, table, row_id, values):
        allowed = {
            "items": {
                "favorite",
                "ignored",
                "auto_read",
                "title",
                "selector",
                "include_path",
                "keywords",
            },
            "links": {"favorite", "ignored", "read"},
        }
        if table not in allowed or set(values) - allowed[table]:
            raise ValueError("Invalid update.")
        if values:
            with self.connection() as db:
                sql = ",".join(k + "=?" for k in values)
                if table == "links" and values.get("read"):
                    sql += ",is_new=0"
                cur = db.execute(
                    f"UPDATE {table} SET {sql} WHERE id=?", (*values.values(), row_id)
                )
                if not cur.rowcount:
                    raise KeyError("Record not found.")

    def bulk(self, iid, action):
        self.item(iid)
        with self.connection() as db:
            if action == "read":
                db.execute(
                    "UPDATE links SET read=1,is_new=0 WHERE item_id=? AND ignored=0 AND deleted=0",
                    (iid,),
                )
            elif action == "acknowledge":
                db.execute(
                    "UPDATE links SET is_new=0 WHERE item_id=? AND deleted=0", (iid,)
                )

    def bulk_selected(self, table, ids, action, item_id=None):
        actions = {
            "favorite": "favorite=1",
            "unfavorite": "favorite=0",
            "ignore": "ignored=1",
            "unignore": "ignored=0",
            "delete": "deleted=1",
            "restore": "deleted=0",
        }
        if table == "links":
            actions.update(read="read=1,is_new=0", unread="read=0")
        if (
            table not in {"items", "links"}
            or action not in actions
            or not ids
            or len(ids) > (MAX_LINKS if table == "links" else 1000)
        ):
            raise ValueError("Invalid bulk action or selection size.")
        ids = list(dict.fromkeys(ids))
        where = "id IN (" + ",".join("?" for _ in ids) + ")"
        args = list(ids)
        if table == "links":
            if not item_id:
                raise ValueError("Select an item for link actions.")
            where += " AND item_id=?"
            args.append(item_id)
        with self.connection() as db:
            count = db.execute(
                f"SELECT count(*) FROM {table} WHERE {where}", args
            ).fetchone()[0]
            if count != len(ids):
                raise KeyError(
                    "Some selected records no longer exist in this collection. Nothing was changed."
                )
            db.execute(f"UPDATE {table} SET {actions[action]} WHERE {where}", args)
        return {"updated": len(ids), "action": action}

    def _link_query(self, db, iid, filter, search, sort, direction):
        item = db.execute("SELECT order_hint FROM items WHERE id=?", (iid,)).fetchone()
        if item is None:
            raise KeyError("Item not found.")
        clauses = ["item_id=?"]
        args = [iid]
        clauses.append("deleted=1" if filter == "trash" else "deleted=0")
        if filter != "trash":
            clauses.append("ignored=1" if filter == "ignored" else "ignored=0")
        if filter == "upcoming":
            clauses.append(
                "date_kind='scheduled' AND published_at > strftime('%Y-%m-%dT%H:%M:%S','now')"
            )
        if filter == "unread":
            clauses.append("read=0")
        if filter == "read":
            clauses.append("read=1")
        if filter == "new":
            clauses.extend(["is_new=1", "read=0"])
        if filter == "favorites":
            clauses.append("favorite=1")
        if search:
            clauses.append("title LIKE ? ESCAPE '\\'")
            args.append(
                "%"
                + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
        stats = db.execute(
            "SELECT count(*) n,count(published_at) dated,count(number) numbered FROM links WHERE item_id=? AND deleted=0",
            (iid,),
        ).fetchone()
        if sort == "auto":
            sort = (
                "source"
                if item["order_hint"] == "source"
                else "number"
                if stats["n"] == stats["numbered"]
                else "date"
                if stats["n"] == stats["dated"]
                else "source"
            )
        field = {
            "date": "published_at",
            "number": "number",
            "source": "position",
            "discovered": "discovered_at",
            "title": "title COLLATE NOCASE",
        }.get(sort, "position")
        order = "DESC" if direction == "desc" else "ASC"
        where = " AND ".join(clauses)
        return where, args, field, order, sort

    def links(
        self,
        iid,
        filter="all",
        search="",
        sort="auto",
        direction="asc",
        offset=0,
        limit=50,
    ):
        with self.connection() as db:
            where, args, field, order, sort = self._link_query(
                db, iid, filter, search, sort, direction
            )
            total = db.execute(
                "SELECT count(*) FROM links WHERE " + where, args
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM links WHERE {where} ORDER BY {field} IS NULL,{field} {order},position {order},id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return {
            "links": [self.decode(r) for r in rows],
            "total": total,
            "sort_used": sort,
            "offset": offset,
            "limit": limit,
        }

    def _view_ids(self, db, iid, filter, search, sort, direction):
        where, args, field, order, sort = self._link_query(
            db, iid, filter, search, sort, direction
        )
        rows = db.execute(
            f"SELECT id FROM links WHERE {where} ORDER BY {field} IS NULL,{field} {order},position {order},id LIMIT ?",
            (*args, MAX_LINKS),
        ).fetchall()
        return [r["id"] for r in rows], sort

    def link_selection(
        self, iid, filter="all", search="", sort="auto", direction="asc"
    ):
        with self.connection() as db:
            ids, sort = self._view_ids(db, iid, filter, search, sort, direction)
        return {"ids": ids, "total": len(ids), "sort_used": sort}

    def read_range(
        self,
        iid,
        anchor_id,
        side,
        filter="all",
        search="",
        sort="auto",
        direction="asc",
    ):
        if side not in {"before", "after"} or filter in {"trash", "ignored"}:
            raise ValueError("Choose an active link and a valid read direction.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            ids, sort = self._view_ids(db, iid, filter, search, sort, direction)
            if anchor_id not in ids:
                raise ValueError(
                    "The selected link is no longer in this view. Reload and select it again."
                )
            index = ids.index(anchor_id)
            chosen = ids[:index] if side == "before" else ids[index + 1 :]
            db.executemany(
                "UPDATE links SET read=1,is_new=0 WHERE id=? AND item_id=? AND deleted=0 AND ignored=0",
                ((lid, iid) for lid in chosen),
            )
        return {"updated": len(chosen), "sort_used": sort}
