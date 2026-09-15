"""Offline production-image check; synthetic metadata is not a live Enma result."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.discovery import Discoverer
from app.tracker.enma import API
from app.tracker.urls import DiscoveryError

SLUG = "grand-blue-dreaming-season-3-199111"
SOURCE = "https://www.enma.lol/watch/" + SLUG + "?ep=800"


class Listing:
    def __init__(self):
        self.calls = 0
        self.blocked = False

    async def get(self, url, **kwargs):
        assert url == API + SLUG and not kwargs
        self.calls += 1
        if self.blocked:
            raise DiscoveryError("The source refused access (HTTP 403).")
        return url, json.dumps(
            {
                "results": {
                    "totalEpisodes": 3,
                    "episodes": [
                        {
                            "id": f"{SLUG}?ep={eid}",
                            "episode_no": n,
                            "title": f"Title {n}",
                        }
                        for n, eid in ((10, 7), (1, 900), (2, 800))
                    ],
                }
            }
        )


def main():
    fetcher = Listing()
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(Path(directory) / "tracker.sqlite3", Discoverer(fetcher))
        with TestClient(app) as client:
            assert (
                client.post("/api/source-method/detect", json={"url": SOURCE}).json()[
                    "source_method"
                ]
                == "enma"
            )
            assert fetcher.calls == 0
            scan = client.post("/api/scans", json={"url": SOURCE}).json()
            assert [e["number"] for e in scan["entries"]] == [1, 2, 10]
            assert len({e["url"] for e in scan["entries"]}) == 3
            assert SOURCE in {e["url"] for e in scan["entries"]}
            assert not any(e["published_at"] for e in scan["entries"])
            item = client.post(
                "/api/items", json={"scan_id": scan["scan_id"], "mark_read": True}
            ).json()
            assert item["source_method"] == "enma"
            for deep in ("false", "true"):
                before = fetcher.calls
                result = client.post(
                    f"/api/items/{item['id']}/refresh?deep={deep}"
                ).json()
                assert result["ok"] and result["new_count"] == 0
                assert fetcher.calls == before + 1
            before = app.state.store.links(item["id"])
            fetcher.blocked = True
            result = client.post(f"/api/items/{item['id']}/refresh?deep=true").json()
            assert not result["ok"] and "Enma blocked" in result["error"]
            assert app.state.store.links(item["id"]) == before
    print(
        json.dumps(
            {
                "contract_records": 3,
                "listing_requests_per_scan": 1,
                "player_requests": 0,
                "refusal_preserved_library": True,
                "external_requests": 0,
                "live_extraction_verified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
