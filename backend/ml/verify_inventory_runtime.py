"""Verify inventory scanning and schema migration without network requests.

With --database, SQLite backup opens the source read-only and migration writes
only to a temporary copy. Existing rows and user progress must be unchanged.
"""

import argparse
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient


class Listings:
    def __init__(self):
        from app.tracker.cache import FetchCache

        self.cache = FetchCache()
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        parsed = urlsplit(url)
        if parsed.hostname == "public-api.wordpress.com":
            return url, json.dumps(
                {
                    "found": 1,
                    "posts": [
                        {
                            "ID": 7,
                            "URL": "https://example.wordpress.com/post",
                            "title": "A real title",
                            "date": "2026-09-01T00:00:00Z",
                            "tags": {"English": {}},
                        }
                    ],
                }
            )
        if parsed.path == "/robots.txt":
            return url, "Sitemap: https://example.wordpress.com/sitemap.xml"
        assert parsed.path == "/sitemap.xml", "Content pages must never be requested"
        return url, (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.wordpress.com/post</loc>"
            "<lastmod>2026-09-14</lastmod></url></urlset>"
        )


def verify_migration(path):
    from app.tracker.store import Store

    with closing(sqlite3.connect(path)) as db:
        prior = {}
        for table in ("items", "links", "preferences"):
            columns = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
            if columns:
                prior[table] = (
                    columns,
                    list(db.execute(f"SELECT * FROM {table} ORDER BY id")),
                )
    store = Store(path)
    with store.connection() as db:
        for table, (columns, rows) in prior.items():
            selected = ",".join(f'"{c}"' for c in columns)
            assert rows == [
                tuple(r)
                for r in db.execute(f"SELECT {selected} FROM {table} ORDER BY id")
            ], f"Existing {table} changed during migration"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        counts = {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("items", "links")
        }
    return counts


def verify_routes(path):
    from app.main import create_app
    from app.tracker.discovery import Discoverer

    fetcher = Listings()
    app = create_app(path, Discoverer(fetcher), auth_config={"required": False})
    with TestClient(app) as client:
        detected = client.post(
            "/api/source-method/detect", json={"url": "https://example.wordpress.com/"}
        )
        assert detected.json()["source_method"] == "wordpress_com"
        assert not fetcher.calls
        response = client.post(
            "/api/scans",
            json={
                "url": "https://example.wordpress.com/",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["source_method"] == "wordpress_com"
        item = client.post(
            "/api/items",
            json={"scan_id": response.json()["scan_id"], "mark_read": True},
        ).json()
        assert item["source_method"] == "wordpress_com" and item["read_count"] == 1
        iid = item["id"]
        original = client.get(f"/api/items/{iid}/links").json()["links"][0]
        assert original["source_id"] and original["date_kind"] == "published"
        assert client.post(f"/api/items/{iid}/refresh?deep=true").status_code == 200
        assert all(
            urlsplit(url).hostname == "public-api.wordpress.com"
            for url in fetcher.calls
        )
        assert (
            client.patch(
                f"/api/items/{iid}", json={"source_method": "sitemap"}
            ).status_code
            == 200
        )
        assert client.post(f"/api/items/{iid}/refresh").status_code == 200
        assert client.post(f"/api/items/{iid}/refresh?deep=true").status_code == 200
        rows = client.get(f"/api/items/{iid}/links").json()["links"]
        assert len(rows) == 1 and rows[0]["id"] == original["id"]
        for field in ("read", "title", "published_at", "date_kind", "source_id"):
            assert rows[0][field] == original[field], field
        assert (
            client.patch("/api/settings", json={"source_method": "sitemap"}).status_code
            == 200
        )
        assert client.get("/api/settings").json()["source_method"] == "sitemap"
        assert client.get("/api/ready").status_code == 200
    return {
        "api_detection": "passed",
        "api_and_sitemap": "passed",
        "method_switch_preserves_progress": "passed",
    }


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "library.sqlite3"
        if args.database:
            with (
                closing(
                    sqlite3.connect(
                        args.database.resolve().as_uri() + "?mode=ro", uri=True
                    )
                ) as source,
                closing(sqlite3.connect(path)) as target,
            ):
                source.backup(target)
            result = verify_migration(path)
        else:
            result = verify_routes(path)
            # Reproduce the preceding production schema, then migrate it.
            with closing(sqlite3.connect(path)) as db:
                db.execute("ALTER TABLE items DROP COLUMN source_method")
                db.execute("ALTER TABLE links DROP COLUMN source_id")
                db.execute("PRAGMA user_version=8")
                db.commit()
            result.update(verify_migration(path))
        print(json.dumps(result | {"migration": "passed", "source_requests": 0}))


if __name__ == "__main__":
    main()
