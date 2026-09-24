"""Shared, reviewed source observations. Advisory only; never relax fetch safety."""

import ipaddress
import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SEED = Path(__file__).with_name("source_status.json")
STATUSES = {
    "accessible",
    "access_blocked",
    "robots_disallowed",
    "robots_unavailable",
    "temporarily_unavailable",
    "unreviewed_redirect",
    "deferred_crawl_delay",
    "javascript_required",
    "no_listing",
}
MESSAGES = {
    "access_blocked": "This site's listing refused the last automated check.",
    "robots_disallowed": "This site's crawl rules disallow the checked listing.",
    "robots_unavailable": "The last check could not verify this site's crawl rules.",
    "temporarily_unavailable": "This site's listing was temporarily unavailable at the last check.",
    "unreviewed_redirect": "The checked listing redirected to another host that has not been reviewed.",
    "deferred_crawl_delay": "This site's crawl delay exceeds the audit's request window.",
    "javascript_required": "The checked listing requires JavaScript to display its content.",
    "no_listing": "The checked page did not contain a public content listing.",
}


def public_url(value):
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError("Expected a public HTTP(S) URL.")
    parts = urlsplit(value)
    host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    if (
        parts.scheme not in {"https", "http"}
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 80, 443}
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
        or "." not in host
        or ".." in host
        or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
    ):
        raise ValueError("Expected a public HTTP(S) URL.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(r"[0-9.]+", host):
            raise ValueError("Ambiguous numeric host.") from None
    else:
        if not address.is_global:
            raise ValueError("Private addresses are not source alternatives.")
    netloc = host + (f":{parts.port}" if parts.port is not None else "")
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def host_key(url):
    return urlsplit(public_url(url)).hostname.removeprefix("www.")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Expected an ISO observation timestamp.")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Observation timestamps must include their timezone.")
    return date.timestamp()


def validated(row):
    if not isinstance(row, dict) or not isinstance(row.get("alternatives", []), list):
        raise ValueError("Expected a source observation with an alternatives list.")
    url = public_url(row["checked_url"])
    status = row["status"]
    if status not in STATUSES:
        raise ValueError("Unknown source status.")
    timestamp(row["checked_at"])
    alternatives = []
    for alternate in row.get("alternatives", []):
        if not isinstance(alternate, dict):
            raise ValueError("Expected an alternative record.")
        if len(alternatives) == 5:
            raise ValueError("At most five reviewed alternatives per source.")
        if alternate.get("relationship") != "official" or alternate.get("kind") not in {
            "feed",
            "api",
            "mirror",
        }:
            raise ValueError(
                "Only reviewed official feeds, APIs, or mirrors are supported."
            )
        label = alternate["label"]
        if not isinstance(label, str) or not 1 <= len(label) <= 120:
            raise ValueError("Invalid alternative label.")
        timestamp(alternate["verified_at"])
        alternatives.append(
            {
                "url": public_url(alternate["url"]),
                "label": label,
                "kind": alternate["kind"],
                "relationship": "official",
                "evidence_url": public_url(alternate["evidence_url"]),
                "verified_at": alternate["verified_at"],
            }
        )
    return {
        "host": host_key(url),
        "checked_url": url,
        "status": status,
        "checked_at": row["checked_at"],
        "alternatives": alternatives,
    }


class SourceRegistry:
    """One global SQLite registry, updated only by reviewed seed/admin imports.

    Observations apply to the checked listing, not every path or subdomain.
    Lookups perform no network requests and never prevent a user-requested scan.
    """

    def __init__(self, path, seed=SEED):
        self.path = Path(path).resolve()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=0.25)) as db, db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS sources (host TEXT PRIMARY KEY, checked_at REAL NOT NULL, observation TEXT NOT NULL)"
                )
            if seed is not None and Path(seed).is_file():
                self.import_rows(json.loads(Path(seed).read_text(encoding="utf8")))
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            logging.getLogger(__name__).warning(
                "Source guidance is unavailable; normal scans remain enabled."
            )

    def import_rows(self, rows):
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError(
                "A registry import must contain at most 2,000 reviewed observations."
            )
        values = [validated(row) for row in rows]
        with closing(sqlite3.connect(self.path, timeout=0.25)) as db, db:
            db.executemany(
                "INSERT INTO sources VALUES (?, ?, ?) ON CONFLICT(host) DO UPDATE SET checked_at=excluded.checked_at, observation=excluded.observation WHERE excluded.checked_at >= sources.checked_at",
                [
                    (row["host"], timestamp(row["checked_at"]), json.dumps(row))
                    for row in values
                ],
            )

    def lookup(self, url, *, now=None):
        try:
            host = host_key(url)
            with closing(
                sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
            ) as db:
                result = db.execute(
                    "SELECT observation FROM sources WHERE host=?", (host,)
                ).fetchone()
            if not result:
                return None
            row = validated(json.loads(result[0]))
            if row["status"] == "accessible" or any(
                public_url(url) == alt["url"] for alt in row["alternatives"]
            ):
                return None
            now = time.time() if now is None else now
            age = now - timestamp(row["checked_at"])
            row["stale"] = age > 86400 or age < -3600
            row["message"] = MESSAGES[row["status"]]
            row["alternatives"] = [
                alt
                for alt in row["alternatives"]
                if -3600 <= now - timestamp(alt["verified_at"]) <= 30 * 86400
            ]
            return row
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, UnicodeError):
            return None
