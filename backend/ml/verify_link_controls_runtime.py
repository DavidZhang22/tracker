"""Offline production-image checks, including a maximum-size selection."""

import json
import os
import sys
import tempfile
from pathlib import Path
from time import perf_counter


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.environ["TRACKER_DB"] = str(Path(directory) / "tracker.sqlite3")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from fastapi.testclient import TestClient

        from app.main import create_app
        from app.tracker.models import Entry, Scan

        class Listing:
            async def scan(self, url, *args, **kwargs):
                return Scan(
                    url,
                    "Offline library",
                    entries=[
                        Entry(
                            f"{url}/part/{i}",
                            f"Chapter {i}",
                            number=i,
                            summary="English" if i % 2 else "French",
                        )
                        for i in range(1, 5000)
                    ],
                )

        origin = "https://tracker.example"
        app = create_app(
            os.environ["TRACKER_DB"],
            Listing(),
            {"required": True, "origin": origin, "public_signup": True},
        )
        with TestClient(app, base_url=origin, headers={"Origin": origin}) as client:
            assert (
                client.post(
                    "/api/auth/register",
                    json={"username": "fixture", "password": "test-only-password"},
                ).status_code
                == 201
            )
            preview = client.post(
                "/api/scans", json={"url": "https://example.org"}
            ).json()
            item = client.post(
                "/api/items", json={"scan_id": preview["scan_id"]}
            ).json()
            path = f"/api/items/{item['id']}"
            view = {"sort": "number", "direction": "asc"}
            started = perf_counter()
            response = client.get(path + "/links", params=view | {"search": "French"})
            assert response.status_code == 200 and response.json()["total"] == 2499
            search_seconds = perf_counter() - started
            ids = client.post(path + "/link-selection", json=view).json()["ids"]
            assert len(ids) == 4999
            started = perf_counter()
            merged = client.post(
                path + "/link-groups", json=view | {"ids": ids, "action": "merge"}
            )
            assert merged.status_code == 200, merged.text
            assert merged.json() == {"updated": 4998, "skipped": 1}
            merge_seconds = perf_counter() - started
            grouped = client.get(path + "/links").json()
            assert grouped["total"] == 1 and grouped["links"][0]["link_count"] == 4999
            assert len({r["url"] for r in grouped["links"][0]["members"]}) == 4999
            assert (
                client.get(path + "/links", params={"search": "part/4999"}).json()[
                    "total"
                ]
                == 1
            )
            assert client.post(path + "/refresh").status_code == 200
            assert client.get(path + "/links").json()["total"] == 1
            assert (
                client.post(
                    path + "/link-groups",
                    json=view | {"ids": [ids[0]], "action": "separate"},
                ).json()["updated"]
                == 4998
            )
            assert client.get(path + "/links").json()["total"] == 4999
            print(
                json.dumps(
                    {
                        "links": 4999,
                        "search_seconds": round(search_seconds, 3),
                        "merge_seconds": round(merge_seconds, 3),
                        "large_selection": "passed",
                        "refresh": "passed",
                        "separation": "passed",
                        "source_requests": 0,
                    }
                )
            )


if __name__ == "__main__":
    main()
