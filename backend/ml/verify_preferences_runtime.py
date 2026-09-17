"""Verify settings migration and initial read selection in an isolated library."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class Listing:
    async def scan(self, url, *args, **kwargs):
        from app.tracker.models import Entry, Scan

        return Scan(
            url,
            "Example",
            entries=[
                Entry(f"{url}/{i}", f"Chapter {i}", number=i) for i in range(1, 32)
            ],
        )


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.main import create_app
    from app.tracker.models import Entry, Scan
    from app.tracker.store import Store

    with (
        tempfile.TemporaryDirectory() as tmp,
        patch("app.tracker.store.time", side_effect=range(1000, 100000, 8)),
    ):
        path = Path(tmp) / "library.db"
        legacy = Store(path)
        prior = legacy.create(
            legacy.save_scan(
                Scan(
                    "https://example.org/old",
                    "Existing",
                    entries=[Entry("https://example.org/old/1", "One")],
                ).to_dict()
            ),
            mark_read=True,
        )
        with legacy.connection() as db:
            db.execute("DROP TABLE preferences")
            db.execute("PRAGMA user_version=6")
        app = create_app(path, Listing(), auth_config={"required": False})
        with TestClient(app) as client:
            assert client.get(f"/api/items/{prior['id']}").json() == prior
            assert client.get("/api/settings").json()["link_direction"] == "desc"
            settings = client.patch(
                "/api/settings", json={"auto_read": False, "library_sort": "title"}
            ).json()
            assert settings["auto_read"] is False
            scan = client.post(
                "/api/scans", json={"url": "https://example.org/new"}
            ).json()
            item = client.post(
                "/api/items",
                json={"scan_id": scan["scan_id"], "read_indices": [0, 25, 30]},
            ).json()
            assert item["read_count"] == 3 and not item["auto_read"]
            links = client.get(f"/api/items/{item['id']}/links").json()["links"]
            assert links[0]["number"] == 31 and {
                e["number"] for e in links if e["read"]
            } == {1, 26, 31}
            assert (
                client.patch(
                    "/api/settings", json={"link_direction": "asc"}
                ).status_code
                == 200
            )
            assert (
                client.get(f"/api/items/{item['id']}/links").json()["links"][0][
                    "number"
                ]
                == 1
            )
            assert Store(path).settings()["link_direction"] == "asc"
            assert client.get("/api/ready").status_code == 200
        print(
            json.dumps(
                dict(
                    migration="passed",
                    saved_progress="passed",
                    initial_read_selection="passed",
                    newest_default="passed",
                    oldest_preference="passed",
                    settings_persistence="passed",
                    source_requests=0,
                )
            )
        )


if __name__ == "__main__":
    main()
