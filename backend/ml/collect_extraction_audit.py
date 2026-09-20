"""Collect one bounded public listing per new evaluation source; no detail crawl."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tracker.cache import FetchCache
from app.tracker.urls import RequestBudget, SafeFetcher, request_budget

SOURCES = [
    ("mit-course", "https://missing.csail.mit.edu/2020/", "course schedule"),
    ("cses", "https://cses.fi/problemset/", "grouped problem lists"),
    ("syntax", "https://syntax.fm/", "podcast cards"),
    ("lexfridman", "https://lexfridman.com/podcast/", "podcast archive"),
    ("standardebooks", "https://standardebooks.org/ebooks/", "book catalog"),
    ("zig", "https://ziglang.org/news/", "software news"),
    ("unsong", "https://unsongbook.com/", "serial table of contents"),
    ("inria", "https://www.inria.fr/fr/actualites", "French news cards"),
]


def status_code(exc):
    while exc is not None:
        response = getattr(exc, "response", None)
        if response is not None:
            return response.status_code
        exc = exc.__cause__
    return None


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument(
        "--directory", type=Path, default=ROOT / "data/extraction-audit"
    )
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    fetcher = SafeFetcher(FetchCache(args.directory / "cache.sqlite3"))
    budget = RequestBudget(limit=24, byte_limit=16_000_000)
    token = request_budget.set(budget)
    report = []
    try:
        for name, url, layout in SOURCES:
            row = dict(id="audit-" + name, url=url, layout=layout, split="test")
            file = args.directory / (name + ".html")
            try:
                if not file.exists() and args.fetch:
                    parsed = urlsplit(url)
                    robots_url = parsed.scheme + "://" + parsed.netloc + "/robots.txt"
                    try:
                        _, robots = await fetcher.get(robots_url)
                        rules = RobotFileParser()
                        rules.parse(robots.splitlines())
                        if not rules.can_fetch("MediaTracker", url):
                            row.update(
                                status="robots-disallowed", robots_url=robots_url
                            )
                            report.append(row)
                            print(name, row["status"], flush=True)
                            continue
                        row["robots"] = "allowed"
                    except Exception as exc:
                        if status_code(exc) != 404:
                            row.update(
                                status="robots-unavailable", error=str(exc)[:200]
                            )
                            report.append(row)
                            print(name, row["status"], flush=True)
                            continue
                        row["robots"] = "not-found"
                    final, html = await fetcher.get(url)
                    file.write_text(html, encoding="utf8")
                    row["final_url"] = final
                data = file.read_bytes()
                row.update(
                    status="captured",
                    file=str(file),
                    bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            except Exception as exc:
                row.update(status="unavailable", error=str(exc)[:200])
            report.append(row)
            print(
                name, row["status"], row.get("bytes", row.get("error", "")), flush=True
            )
    finally:
        request_budget.reset(token)
    result = dict(
        sources=report,
        network_requests=budget.requests,
        cache_hits=budget.hits,
        bytes_received=budget.received,
    )
    (args.directory / "capture-report.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf8"
    )


if __name__ == "__main__":
    asyncio.run(main())
