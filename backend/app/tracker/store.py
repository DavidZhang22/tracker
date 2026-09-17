"""SQLite repository. Transactions preserve user state across every scan."""

import json
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from math import ceil
from pathlib import Path
from time import time

from . import link_groups
from .entry_identity import entry_key
from .limits import (
    ITEM_ADD_INTERVAL_SECONDS,
    MAX_ITEMS,
    MAX_LIBRARY_LINKS,
    MAX_LINKS,
    MAX_PREVIEW_BYTES,
    MAX_PREVIEWS,
    bounded_scan,
)
from .media_metadata import MEDIA_TYPES, annotate
from .media_metadata import VERSION as MEDIA_VERSION
from .models import date_rank, utcnow
from .preferences import Preferences
from .semantic_profile import fingerprint
from .suggestions import save_observations
from .urls import DiscoveryError, canonical_url, content_key


def identity(url):
    return content_key(url)


class LibraryErased(Exception):
    pass


class ItemAdditionCooldown(Exception):
    def __init__(self, remaining):
        self.retry_after = max(1, ceil(remaining))
        super().__init__(
            f"You can add one item every {ITEM_ADD_INTERVAL_SECONDS} seconds. Your scan is ready to save."
        )


class Store:
    def __init__(self, path, *, allow_erased=False):
        self.path = str(path)
        self.allow_erased = allow_erased
        self.erased_marker = Path(str(self.path) + ".erased")
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
            CREATE TABLE IF NOT EXISTS preferences (
              id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS suggestions (
              id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
              summary TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'website',
              found_at TEXT NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS suggestion_sources (
              suggestion_id TEXT NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
              item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
              PRIMARY KEY(suggestion_id,item_id)
            );
            """)
            db.execute("BEGIN IMMEDIATE")
            # Additive migration: existing libraries, IDs and progress stay intact.
            additions = {
                "items": {
                    "deleted": "INTEGER NOT NULL DEFAULT 0",
                    "requests_made": "INTEGER NOT NULL DEFAULT 0",
                    "cache_hits": "INTEGER NOT NULL DEFAULT 0",
                    "coverage": "TEXT NOT NULL DEFAULT 'unknown'",
                    "order_hint": "TEXT NOT NULL DEFAULT ''",
                    "keywords": "TEXT NOT NULL DEFAULT ''",
                    "suggestions_checked": "REAL NOT NULL DEFAULT 0",
                    "source_method": "TEXT NOT NULL DEFAULT 'auto'",
                    "source_type": "TEXT NOT NULL DEFAULT 'web'",
                    "source_name": "TEXT NOT NULL DEFAULT ''",
                    "detected_kind": "TEXT NOT NULL DEFAULT ''",
                    "kind_override": "TEXT NOT NULL DEFAULT ''",
                    "search_tags": "TEXT NOT NULL DEFAULT '[]'",
                    "source_summary": "TEXT NOT NULL DEFAULT ''",
                    "media_version": "INTEGER NOT NULL DEFAULT 0",
                    "description_auto": "TEXT NOT NULL DEFAULT ''",
                    "description_override": "TEXT",
                    "description_method": "TEXT NOT NULL DEFAULT ''",
                    "semantic_key": "TEXT NOT NULL DEFAULT ''",
                    "semantic_vector": "BLOB",
                },
                "links": {
                    "merged_into": "TEXT",
                    "merge_order": "INTEGER NOT NULL DEFAULT 0",
                    "deleted": "INTEGER NOT NULL DEFAULT 0",
                    "date_kind": "TEXT NOT NULL DEFAULT 'published'",
                    "date_source": "TEXT NOT NULL DEFAULT ''",
                    "date_precision": "TEXT NOT NULL DEFAULT 'day'",
                    "summary": "TEXT NOT NULL DEFAULT ''",
                    "availability": "TEXT NOT NULL DEFAULT ''",
                    "context": "TEXT NOT NULL DEFAULT ''",
                    "language": "TEXT NOT NULL DEFAULT ''",
                    "source_id": "TEXT NOT NULL DEFAULT ''",
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
            if db.execute("PRAGMA user_version").fetchone()[0] < 8:
                self._migrate_link_identities(db)
            link_groups.install_view(db)
            for item in db.execute(
                "SELECT id FROM items WHERE media_version<?", (MEDIA_VERSION,)
            ).fetchall():
                self._refresh_media(db, item["id"])
            db.execute("PRAGMA user_version=12")
        marker.touch(exist_ok=True)

    @staticmethod
    def _refresh_media(db, iid, scan=None):
        item = dict(db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone())
        evidence = item | {
            "kind": item["detected_kind"] or item["kind"],
            "detected_kind": "",
        }
        if scan is not None:
            evidence.update(scan)
            evidence["source_summary"] = (
                scan.get("source_summary") or item["source_summary"]
            )
        else:
            evidence["entries"] = [
                dict(row)
                for row in db.execute(
                    "SELECT title,url,language FROM links WHERE item_id=? AND deleted=0 ORDER BY position LIMIT 32",
                    (iid,),
                )
            ]
        metadata = annotate(evidence, item["kind_override"])
        db.execute(
            "UPDATE items SET kind=?,detected_kind=?,search_tags=?,source_summary=?,media_version=? WHERE id=?",
            (
                metadata["kind"],
                metadata["detected_kind"],
                json.dumps(metadata["search_tags"]),
                metadata["source_summary"],
                MEDIA_VERSION,
                iid,
            ),
        )

    @staticmethod
    def _migrate_link_identities(db):
        """Coalesce old URL aliases atomically, retaining progress and original IDs."""
        for item in db.execute("SELECT id FROM items").fetchall():
            groups = {}
            for row in db.execute(
                "SELECT * FROM links WHERE item_id=? ORDER BY discovered_at,id",
                (item["id"],),
            ):
                groups.setdefault(identity(row["url"]), []).append(dict(row))
            for key, rows in groups.items():
                original = rows[0]
                if len(rows) == 1 and original["identity"] == key:
                    continue
                Store._combine_link_rows(db, rows, key)

    @staticmethod
    def _combine_link_rows(db, rows, key):
        rows = sorted(rows, key=lambda r: (r["discovered_at"], r["id"]))
        original = link_groups.coalesce_groups(db, rows, rows[0]["id"])
        # Newest discovered URL/metadata wins; precise dates and explicit
        # saved flags survive even when only an older alias has them.
        merged = dict(rows[-1])
        merged["number"] = next(
            (r["number"] for r in reversed(rows) if r["number"] is not None),
            None,
        )
        for field in ("summary", "availability", "context", "language", "source_id"):
            merged[field] = next((r[field] for r in reversed(rows) if r[field]), "")
        best_date = max(reversed(rows), key=date_rank)
        for field in (
            "published_at",
            "date_kind",
            "date_source",
            "date_precision",
        ):
            merged[field] = best_date[field]
        for field in ("read", "favorite", "ignored", "deleted"):
            merged[field] = any(r[field] for r in rows)
        merged.update(
            identity=key,
            position=original["position"],
            is_new=bool(original["is_new"] and not merged["read"]),
        )
        # Delete redundant copies before changing the surviving key so
        # the existing per-item UNIQUE constraint remains enforced.
        db.executemany("DELETE FROM links WHERE id=?", ((r["id"],) for r in rows[1:]))
        changes = {
            k: v
            for k, v in merged.items()
            if k not in {"id", "item_id", "discovered_at", "merged_into", "merge_order"}
            and original[k] != v
        }
        if changes:
            db.execute(
                "UPDATE links SET "
                + ",".join(k + "=?" for k in changes)
                + " WHERE id=?",
                (*changes.values(), original["id"]),
            )
        return original | changes

    @contextmanager
    def connection(self, create=False):
        self.check_active()
        target = Path(self.path).resolve().as_uri() + (
            "?mode=rwc" if create else "?mode=rw"
        )
        db = sqlite3.connect(target, uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        db.create_function(
            "fold",
            1,
            lambda value: unicodedata.normalize("NFKC", str(value or "")).casefold(),
            deterministic=True,
        )
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
                # An already-running refresh must not restore erased records.
                self.check_active()
        finally:
            db.close()

    def check_active(self):
        if not self.allow_erased and self.erased_marker.exists():
            raise LibraryErased("This account's library has been deleted.")

    @staticmethod
    def decode(row):
        if row is None:
            return None
        r = dict(row)
        r.pop("semantic_vector", None)
        r.pop("semantic_key", None)
        if "description_auto" in r:
            r["description"] = (
                r["description_override"]
                if r["description_override"] is not None
                else r["description_auto"]
            )
        for k in ("favorite", "ignored", "auto_read", "read", "is_new", "deleted"):
            if k in r:
                r[k] = bool(r[k])
        for k in ("warnings", "methods", "search_tags"):
            if k in r:
                r[k] = json.loads(r[k])
        return r

    def semantic_records(self, ids=None, trash=False):
        with self.connection() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM items WHERE deleted=? ORDER BY id LIMIT ?",
                    (bool(trash), MAX_ITEMS),
                )
            ]
        return rows if ids is None else [row for row in rows if row["id"] in ids]

    def save_semantic(self, iid, signature, key, description, method, vector):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM items WHERE id=? AND deleted=0", (iid,)
            ).fetchone()
            if row is None or fingerprint(dict(row)) != signature:
                return False
            db.execute(
                "UPDATE items SET description_auto=?,description_method=?,semantic_key=?,semantic_vector=? WHERE id=?",
                (description, method, key, vector, iid),
            )
            return True

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
              FROM items i LEFT JOIN link_entries l ON l.item_id=i.id AND l.deleted=0 """
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
                        f"SELECT id,url,title,read,number,published_at FROM link_entries WHERE item_id=? AND ignored=0 AND deleted=0 ORDER BY {order} DESC,position DESC,id DESC LIMIT 1",
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

    def refresh_source(self, item_id):
        """Preferences and current flags without aggregating thousands of links."""
        with self.connection() as db:
            row = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError("Item not found.")
        return self.decode(row)

    def refresh_sources(self):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,url FROM items WHERE ignored=0 AND deleted=0 AND source_type NOT IN ('csv','document') ORDER BY created_at DESC"
                )
            ]

    def save_scan(self, payload):
        payload = annotate(bounded_scan(payload))
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

    @staticmethod
    def _settings(db):
        row = db.execute("SELECT payload FROM preferences WHERE id=1").fetchone()
        return Preferences(**(json.loads(row[0]) if row else {})).model_dump()

    def settings(self):
        with self.connection() as db:
            return self._settings(db)

    def update_settings(self, values, apply_auto_read=False):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            saved = Preferences(**(self._settings(db) | values)).model_dump()
            if apply_auto_read and "auto_read" not in values:
                raise ValueError(
                    "Choose read-on-open behavior before applying it to existing items."
                )
            db.execute(
                "INSERT INTO preferences(id,payload) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (json.dumps(saved),),
            )
            if apply_auto_read:
                db.execute(
                    "UPDATE items SET auto_read=? WHERE deleted=0",
                    (saved["auto_read"],),
                )
            return saved

    def create(
        self,
        scan_id,
        title=None,
        mark_read=False,
        auto_read=None,
        read_indices=None,
        kind_override="",
    ):
        if kind_override and kind_override not in MEDIA_TYPES:
            raise ValueError("Choose a supported media type.")
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
            if scan.get("import_item_id"):
                raise ValueError("This preview updates an existing imported item.")
            if scan.get("source_type") in {"csv", "document"} and not scan["entries"]:
                raise ValueError(
                    "There are no links to import. Check the input and import options."
                )
            selected = set(read_indices or [])
            if len(selected) > MAX_LINKS or any(
                type(i) is not int or not 0 <= i < len(scan["entries"])
                for i in selected
            ):
                raise ValueError(
                    "Some selected links are not in this scan. Review the preview and try again."
                )
            if selected and mark_read:
                raise ValueError(
                    "Choose individual read links or mark all read, not both."
                )
            read_identities = {entry_key(scan["entries"][i]) for i in selected}
            if auto_read is None:
                auto_read = self._settings(db)["auto_read"]
            if any(
                (
                    existing["url"]
                    if existing["source_type"] in {"csv", "document"}
                    else canonical_url(existing["url"])
                )
                == (
                    scan["url"]
                    if scan.get("source_type") in {"csv", "document"}
                    else canonical_url(scan["url"])
                )
                for existing in db.execute("SELECT url,source_type FROM items")
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
                """INSERT INTO items(id,url,title,kind,auto_read,selector,include_path,created_at,keywords,source_method,source_type,source_name)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    scan.get("source_method", "auto"),
                    scan.get("source_type", "web"),
                    scan.get("source_name", ""),
                ),
            )
            db.execute(
                "UPDATE items SET kind_override=? WHERE id=?", (kind_override, iid)
            )
            self._merge(
                db,
                iid,
                scan,
                initial=True,
                mark_read=mark_read,
                read_identities=read_identities,
            )
            db.execute("DELETE FROM scans WHERE id=?", (scan_id,))
            # Only a committed addition consumes the cooldown; errors roll it back.
            db.execute(
                "INSERT INTO addition_cooldown(id,completed_at) VALUES (1,?) "
                "ON CONFLICT(id) DO UPDATE SET completed_at=excluded.completed_at",
                (time(),),
            )
        return self.item(iid)

    def import_csv(self, iid, scan_id, title=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute(
                "SELECT source_type,deleted FROM items WHERE id=?", (iid,)
            ).fetchone()
            if not item:
                raise KeyError("Item not found.")
            if item["deleted"] or item["source_type"] not in {"csv", "document"}:
                raise ValueError("Choose an imported item outside Trash to update.")
            row = db.execute(
                "SELECT payload FROM scans WHERE id=? AND created_at > strftime('%Y-%m-%dT%H:%M:%S','now','-1 hour')",
                (scan_id,),
            ).fetchone()
            if not row:
                raise ValueError("This preview expired. Import the file or text again.")
            scan = json.loads(row["payload"])
            if (
                scan.get("source_type") not in {"csv", "document"}
                or scan.get("import_item_id") != iid
                or not scan["entries"]
            ):
                raise ValueError(
                    "Import a file or text for this item and review its links first."
                )
            self._merge(db, iid, scan)
            db.execute(
                "UPDATE items SET source_name=?,keywords=?,title=coalesce(?,title) WHERE id=?",
                (scan["source_name"], scan.get("keywords", ""), title, iid),
            )
            db.execute("DELETE FROM scans WHERE id=?", (scan_id,))
        return self.item(iid)

    def _merge(
        self, db, iid, scan, initial=False, mark_read=False, read_identities=None
    ):
        read_identities = read_identities or set()
        scan = bounded_scan(scan)
        if len(json.dumps(scan).encode()) > MAX_PREVIEW_BYTES:
            raise DiscoveryError(
                "This scan is too large to save. Use a narrower source or link selector. Saved links were kept."
            )
        now = utcnow()
        added = 0
        previous_rows = {
            r["identity"]: dict(r)
            for r in db.execute(
                "SELECT * FROM links WHERE item_id=? ORDER BY position,id",
                (iid,),
            )
        }
        # API IDs can prove that a renamed URL and a previously saved sitemap
        # URL refer to the same content. Coalesce that evidence before quotas.
        indexed_urls = {entry_key(r): r for r in previous_rows.values()}
        indexed_sources = {
            r["source_id"]: r for r in previous_rows.values() if r["source_id"]
        }
        for entry in scan["entries"]:
            source_id = entry.get("source_id")
            a = indexed_sources.get(source_id) if source_id else None
            b = indexed_urls.get(entry_key(entry))
            if a and b and a["id"] != b["id"]:
                original = min((a, b), key=lambda r: (r["discovered_at"], r["id"]))
                merged = self._combine_link_rows(db, [a, b], original["identity"])
                previous_rows.pop(a["identity"], None)
                previous_rows.pop(b["identity"], None)
                previous_rows[merged["identity"]] = merged
                indexed_sources[source_id] = merged
                for row in (a, b):
                    indexed_urls[entry_key(row)] = merged
        previous_rows = dict(
            sorted(
                previous_rows.items(), key=lambda kv: (kv[1]["position"], kv[1]["id"])
            )
        )
        previous = list(previous_rows)
        by_url = {entry_key(row): key for key, row in previous_rows.items()}
        by_source = {
            row["source_id"]: key
            for key, row in previous_rows.items()
            if row["source_id"]
        }
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
            url_key = entry_key(entry)
            source_id = entry.get("source_id", "")
            key = by_source.get(source_id) or by_url.get(url_key, url_key)
            if key not in known:
                if not remaining:
                    capped = True
                    continue
                remaining -= 1
                known.add(key)
            by_url[url_key] = key
            if source_id:
                by_source[source_id] = key
            accepted.append((key, entry))
        scan = scan | {"entries": [entry for _, entry in accepted]}
        if capped:
            scan = scan | {
                "coverage": "partial",
                "warnings": [
                    *scan["warnings"],
                    f"Storage limit reached ({MAX_LINKS:,} links per item; {MAX_LIBRARY_LINKS:,} per library, including Trash). Saved links and progress were kept; additional links were skipped.",
                ],
            }
        incoming = list(dict.fromkeys(key for key, _ in accepted))
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
        positions = {key: i for i, key in enumerate(order)}
        for key, entry in accepted:
            old = previous_rows.get(key)
            if old:
                if entry.get("method") == "sitemap":
                    # URL-derived labels and absent sitemap context should not
                    # replace an existing title or richer API/page metadata.
                    entry = dict(entry)
                    entry["title"] = old["title"]
                    for field in ("summary", "availability", "context", "language"):
                        entry[field] = entry.get(field) or old.get(field, "")
                if date_rank(old) > date_rank(entry):
                    entry = dict(entry)
                    for field in (
                        "published_at",
                        "date_kind",
                        "date_source",
                        "date_precision",
                    ):
                        entry[field] = old[field]
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
                        positions[key],
                        entry["method"],
                        now,
                        not initial,
                        mark_read or key in read_identities,
                    ),
                )
                added += 1
                old = {"id": None}  # New rows need their extended metadata below.
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
            metadata.update(
                url=entry["url"] or old.get("url", ""),
                title=entry["title"],
                method=entry["method"],
                position=positions[key],
            )
            if entry.get("source_id"):
                metadata["source_id"] = entry["source_id"]
            if entry.get("published_at") is not None:
                metadata["published_at"] = entry["published_at"]
            if entry.get("number") is not None:
                metadata["number"] = entry["number"]
            changed = {k: v for k, v in metadata.items() if old.get(k) != v}
            if changed:
                db.execute(
                    "UPDATE links SET "
                    + ",".join(k + "=?" for k in changed)
                    + " WHERE item_id=? AND identity=?",
                    (*changed.values(), iid, key),
                )
            # Preserve the original sequential behavior for duplicate identities.
            previous_rows[key] = old | metadata
        db.executemany(
            "UPDATE links SET position=? WHERE item_id=? AND identity=?",
            (
                (i, iid, key)
                for i, key in enumerate(order)
                if key not in incoming_set and previous_rows[key]["position"] != i
            ),
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
        save_observations(db, iid, scan.get("suggestions"))
        self._refresh_media(db, iid, scan)
        return added

    def merge(self, iid, scan):
        with self.connection() as db:
            # Serialize quota checks with inserts, including concurrent refreshes.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT deleted FROM items WHERE id=?", (iid,)).fetchone()
            if not row:
                raise KeyError("Item not found.")
            if row["deleted"]:
                raise DiscoveryError(
                    "Restore this item from Trash before refreshing it."
                )
            return self._merge(db, iid, scan)

    def failure(self, iid, message):
        with self.connection() as db:
            db.execute(
                "UPDATE items SET error=?,last_attempt_at=? WHERE id=? AND deleted=0",
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
                "source_method",
                "kind_override",
                "description_override",
            },
            "links": {"favorite", "ignored", "read"},
        }
        if table not in allowed or set(values) - allowed[table]:
            raise ValueError("Invalid update.")
        if values:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                sql = ",".join(k + "=?" for k in values)
                if table == "links" and values.get("read"):
                    sql += ",is_new=0"
                ids = (
                    link_groups.expand_ids(db, [row_id])
                    if table == "links"
                    else [row_id]
                )
                cur = db.execute(
                    f"UPDATE {table} SET {sql} WHERE id IN ({','.join('?' for _ in ids)})",
                    (*values.values(), *ids),
                )
                if not cur.rowcount:
                    raise KeyError("Record not found.")
                if (
                    table == "items"
                    and {"kind_override", "title", "keywords"} & values.keys()
                ):
                    self._refresh_media(db, row_id)

    def bulk(self, iid, action):
        self.item(iid)
        with self.connection() as db:
            if action == "read":
                db.execute(
                    "UPDATE links SET read=1,is_new=0 WHERE coalesce(merged_into,id) IN (SELECT id FROM link_entries WHERE item_id=? AND ignored=0 AND deleted=0)",
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
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                f"SELECT count(*) FROM {table} WHERE {where}", args
            ).fetchone()[0]
            if count != len(ids):
                raise KeyError(
                    "Some selected records no longer exist in this collection. Nothing was changed."
                )
            if table == "links":
                expanded = link_groups.expand_ids(db, ids, item_id)
                where = "id IN (" + ",".join("?" for _ in expanded) + ")"
                args = expanded
            db.execute(f"UPDATE {table} SET {actions[action]} WHERE {where}", args)
        return {"updated": len(ids), "action": action}

    def _link_query(self, db, iid, filter, search, sort, direction):
        if sort is None or direction is None:
            preferences = self._settings(db)
            sort = sort or preferences["link_sort"]
            direction = direction or preferences["link_direction"]
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
        if search.strip():
            clauses.append(
                "EXISTS (SELECT 1 FROM links candidate WHERE candidate.item_id=link_entries.item_id AND coalesce(candidate.merged_into,candidate.id)=+link_entries.id AND instr(fold(candidate.title || ' ' || candidate.url || ' ' || candidate.summary || ' ' || candidate.context || ' ' || candidate.language || ' ' || coalesce(candidate.number,'') || ' ' || coalesce(candidate.published_at,'')),fold(?))>0)"
            )
            args.append(search.strip())
        stats = db.execute(
            "SELECT count(*) n,count(published_at) dated,count(number) numbered FROM link_entries WHERE item_id=? AND deleted=0",
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
        sort=None,
        direction=None,
        offset=0,
        limit=50,
    ):
        with self.connection() as db:
            where, args, field, order, sort = self._link_query(
                db, iid, filter, search, sort, direction
            )
            total = db.execute(
                "SELECT count(*) FROM link_entries WHERE " + where, args
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM link_entries WHERE {where} ORDER BY {field} IS NULL,{field} {order},position {order},id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
            entries = [self.decode(r) for r in rows]
            for entry in entries:
                entry["members"] = (
                    [self.decode(r) for r in link_groups.members(db, entry["id"])]
                    if entry["link_count"] > 1
                    else []
                )
        return {
            "links": entries,
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
            f"SELECT id FROM link_entries WHERE {where} ORDER BY {field} IS NULL,{field} {order},position {order},id LIMIT ?",
            (*args, MAX_LINKS),
        ).fetchall()
        return [r["id"] for r in rows], sort

    def link_selection(
        self, iid, filter="all", search="", sort=None, direction=None, pattern=None
    ):
        with self.connection() as db:
            ids, sort = self._view_ids(db, iid, filter, search, sort, direction)
        total = len(ids)
        if pattern:
            ids = link_groups.pattern_ids(ids, **pattern)
        return {"ids": ids, "total": total, "sort_used": sort}

    def group_links(
        self, iid, ids, action, filter="all", search="", sort=None, direction=None
    ):
        if not ids or len(ids) > MAX_LINKS or action not in {"merge", "separate"}:
            raise ValueError("Choose links and a valid grouping action.")
        if filter == "trash":
            raise ValueError("Restore links from Trash before merging them.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self_item = db.execute(
                "SELECT deleted FROM items WHERE id=?", (iid,)
            ).fetchone()
            if not self_item:
                raise KeyError("Item not found.")
            if self_item["deleted"]:
                raise ValueError("Restore this item from Trash before merging links.")
            ordered, _ = self._view_ids(db, iid, filter, search, sort, direction)
            if not set(ids).issubset(ordered):
                raise KeyError(
                    "Some selected links are no longer in this view. Nothing was changed."
                )
            return (
                link_groups.merge_above(db, ordered, ids)
                if action == "merge"
                else link_groups.separate(db, ids, iid)
            )

    def read_range(
        self,
        iid,
        anchor_id,
        side,
        filter="all",
        search="",
        sort=None,
        direction=None,
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
            expanded = link_groups.expand_ids(db, chosen, iid) if chosen else []
            db.executemany(
                "UPDATE links SET read=1,is_new=0 WHERE id=? AND item_id=? AND deleted=0",
                ((lid, iid) for lid in expanded),
            )
        return {"updated": len(chosen), "sort_used": sort}
