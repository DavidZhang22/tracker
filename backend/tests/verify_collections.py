"""Opt-in source checks. Default replays saved observations; --live fetches indexes.

Uses a temporary library. Generic news/releases checks deliberately inspect one
index page; the chapter API walks its bounded metadata pagination.
"""

import argparse
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.urls import DiscoveryError, SafeFetcher, canonical_url

HERE = Path(__file__).parent
WT = "https://wetriedtls.com/series/climbing-the-tower-with-time-stop-ability"
SOURCES = {
    "wetried": (WT, 40),
    "codeforces": ("https://codeforces.com/contests", 1),
    "xkcd": ("https://xkcd.com/archive/", 1),
    "hacker_news": ("https://news.ycombinator.com/", 1),
    "github_releases": ("https://github.com/astral-sh/ruff/releases", 1),
}


class RecordedFetcher:
    def __init__(self):
        files = {
            WT: "wetried.html",
            "https://codeforces.com/api/contest.list?gym=false": "cf_api.html",
            "https://xkcd.com/archive/": "xkcd.html",
            "https://news.ycombinator.com/": "hn.html",
            "https://github.com/astral-sh/ruff/releases": "github.html",
            "https://api.wetriedtls.com/chapters/82/paid?query=&order=desc": "wetried-paid.json",
        }
        for page in range(1, 7):
            files[
                f"https://api.wetriedtls.com/chapters/82?page={page}&perPage=30&query=&order=desc"
            ] = (
                "wetried-chapters.json"
                if page == 1
                else f"wetried-chapters-{page}.json"
            )
        self.files = {canonical_url(u): name for u, name in files.items()}
        self.calls = []

    async def get(self, url):
        self.calls.append(url)
        name = self.files.get(canonical_url(url))
        if not name:
            raise DiscoveryError("This verification only replays the recorded indexes.")
        return url, (HERE / "live" / name).read_text(encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch public source metadata with the normal cache, pacing and request budget.",
    )
    args = parser.parse_args()
    report = {
        "mode": "live" if args.live else "replayed public HTTP observations",
        "sources": {},
    }
    fetcher = (
        SafeFetcher(FetchCache(HERE / "live" / "verification-cache.sqlite3"))
        if args.live
        else RecordedFetcher()
    )
    with tempfile.TemporaryDirectory() as folder:
        for name, (url, budget) in SOURCES.items():
            before = len(getattr(fetcher, "calls", []))
            with TestClient(
                create_app(
                    Path(folder) / (name + ".sqlite3"),
                    Discoverer(fetcher, max_pages=budget),
                )
            ) as client:
                response = client.post("/api/scans", json={"url": url})
                assert response.status_code == 200, response.text
                scan = response.json()
                if not args.live:
                    assert (
                        len(scan["entries"])
                        == {
                            "wetried": 169,
                            "codeforces": 2146,
                            "xkcd": 3276,
                            "hacker_news": 30,
                            "github_releases": 10,
                        }[name]
                    ), name
                assert scan["entries"] and all(
                    e["published_at"] for e in scan["entries"]
                ), name
                if name == "wetried":
                    assert len(scan["entries"]) == scan["expected_count"]
                    assert any(e["availability"] == "paid" for e in scan["entries"])
                item_response = client.post(
                    "/api/items", json={"scan_id": scan["scan_id"]}
                )
                assert item_response.status_code == 201, item_response.text
                item = item_response.json()
                all_links = []
                for offset in range(0, item["total_count"], 200):
                    rows = client.get(
                        f"/api/items/{item['id']}/links",
                        params={
                            "sort": "date",
                            "direction": "asc",
                            "limit": 200,
                            "offset": offset,
                        },
                    ).json()
                    all_links += rows["links"]
                dates = [e["published_at"] for e in all_links]
                assert dates == sorted(dates) and len(all_links) == len(scan["entries"])
                report["sources"][name] = {
                    "url": url,
                    "links": len(all_links),
                    "dated": len(dates),
                    "reported_count": scan["expected_count"],
                    "index_responses": scan["pages_scanned"],
                    "requests_made": scan["requests_made"]
                    if args.live
                    else len(fetcher.calls) - before,
                    "coverage": scan["coverage"],
                    "scope": "public API collection"
                    if name in {"wetried", "codeforces"}
                    else "one archive/index page",
                    "first_date": dates[0],
                    "last_date": dates[-1],
                    "warnings": scan["warnings"],
                }
                print(
                    name,
                    len(all_links),
                    "links; all dated; date ordering verified",
                    flush=True,
                )
    (HERE / "collections-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf8"
    )


if __name__ == "__main__":
    main()
