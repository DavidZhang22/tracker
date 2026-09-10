"""Opt-in live checks through the real API; never modifies the personal library."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

sources = {
    "asura": "https://asurascans.com/comics/the-nebulas-civilization-53fc8424",
    "royalroad": "https://www.royalroad.com/fiction/21220/mother-of-learning",
    "youtube": "https://www.youtube.com/channel/UCRIgIJQWuBJ0Cv_VlU3USNA",
}
report = {}
with tempfile.TemporaryDirectory() as temp:
    with TestClient(create_app(Path(temp) / "test.sqlite3")) as client:
        for name, url in sources.items():
            response = client.post("/api/scans", json={"url": url})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["entries"], name + " returned no entries"
            item = client.post("/api/items", json={"scan_id": result["scan_id"]}).json()
            links = client.get(f"/api/items/{item['id']}/links?limit=200").json()
            assert item["total_count"] == len(result["entries"])
            assert (
                item["new_count"] == 0 and item["unread_count"] == item["total_count"]
            )
            if name == "asura":
                assert [e["number"] for e in links["links"]] == list(
                    range(1, item["total_count"] + 1)
                )
            if name == "royalroad":
                assert any(
                    e["title"] == "Epilogue" and e["number"] is None
                    for e in links["links"]
                )
                assert links["sort_used"] == "date"
            report[name] = {
                "url": url,
                "count": item["total_count"],
                "reported_count": result["expected_count"],
                "pages": result["pages_scanned"],
                "methods": result["methods"],
                "warnings": result["warnings"],
                "order": links["sort_used"],
                "first_url": links["links"][0]["url"],
                "last_url": links["links"][-1]["url"],
            }
            print(name, json.dumps(report[name], ensure_ascii=True), flush=True)
Path("tests/live-report.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8"
)
