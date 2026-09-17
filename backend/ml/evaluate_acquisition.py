"""Replay public listing captures. Fetching is opt-in, bounded, and cached.

Run from the repository root. The selectors below are evaluation labels only;
production extraction never imports this module or branches on these hosts.
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--capture", action="store_true")
parser.add_argument(
    "--directory", type=Path, default=Path("backend/data/source-diversity")
)
parser.add_argument("--app-root", type=Path, default=Path("backend"))
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument("--output", type=Path)
parser.add_argument("--check-recipes", action="store_true")
args = parser.parse_args()
sys.path.insert(0, str(args.app_root.resolve()))
os.environ["TRACKER_LINK_MODEL"] = "cascade"

from bs4 import BeautifulSoup

from app.tracker.cache import FetchCache
from app.tracker.parser import parse_page
from app.tracker.urls import RequestBudget, SafeFetcher, canonical_url, request_budget

SOURCES = [
    ("arxiv", "https://arxiv.org/list/cs.AI/recent", "dl dt a[title=Abstract]"),
    (
        "python",
        "https://www.python.org/downloads/",
        ".download-list-widget .release-number a",
    ),
    ("django", "https://www.djangoproject.com/weblog/", ".list-news h2 a"),
    (
        "nasa",
        "https://www.nasa.gov/news/recently-published/",
        ".hds-content-item-heading",
    ),
    (
        "huggingface",
        "https://huggingface.co/papers",
        'main article a[href^="/papers/"]',
    ),
    (
        "gutenberg",
        "https://www.gutenberg.org/ebooks/search/?sort_order=release_date",
        "li.booklink a.link",
    ),
    ("rust", "https://blog.rust-lang.org/", "table a"),
    ("smashing", "https://www.smashingmagazine.com/articles/", "article h2 a"),
]


async def capture():
    args.directory.mkdir(parents=True, exist_ok=True)
    fetcher = SafeFetcher(cache=FetchCache(args.directory / "cache.sqlite3"))
    slots = asyncio.Semaphore(2)

    async def one(name, url, _selector):
        async with slots:
            token = request_budget.set(RequestBudget(limit=4))
            try:
                final, markup = await fetcher.get(url)
                (args.directory / f"{name}.html").write_text(markup, encoding="utf8")
                return name, final
            finally:
                request_budget.reset(token)

    locations = dict(await asyncio.gather(*(one(*row) for row in SOURCES)))
    (args.directory / "locations.json").write_text(
        json.dumps(locations), encoding="utf8"
    )


def evaluate():
    locations_path = args.directory / "locations.json"
    locations = (
        json.loads(locations_path.read_text()) if locations_path.exists() else {}
    )
    results = []
    for name, url, selector in SOURCES:
        raw = (args.directory / f"{name}.html").read_bytes()
        markup = raw.decode("utf8")
        url = locations.get(name, url)
        soup = BeautifulSoup(markup, "html.parser")
        expected = {canonical_url(a["href"], url) for a in soup.select(selector)}
        if not expected:
            raise ValueError(f"Evaluation labels need review for {name}")
        timings = []
        parse_page(markup, url)
        for _ in range(max(1, min(args.repeats, 20))):
            start = time.perf_counter()
            scan, pages, _ = parse_page(markup, url)
            timings.append(time.perf_counter() - start)
        actual = {e.url for e in scan.entries}
        record = {
            "name": name,
            "source": url,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "expected": len(expected),
            "entries": len(actual),
            "correct": len(actual & expected),
            "missed": len(expected - actual),
            "unrelated": len(actual - expected),
            "dated_relevant": sum(
                bool(e.published_at) for e in scan.entries if e.url in expected
            ),
            "median_ms": round(statistics.median(timings) * 1000, 2),
            "next_pages": pages,
            "warnings": scan.warnings,
        }
        if args.check_recipes:
            from app.tracker.recipes import analyze

            expected_scan, recipe, _ = analyze(markup, url)
            actual_scan, _, used = analyze(markup, url, recipe=recipe)
            assert actual_scan == expected_scan, f"Light refresh changed {name}"
            record["recipe_used"] = used
        try:
            import resource

            record["peak_rss_mib"] = round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
            )
        except ImportError:
            pass
        results.append(record)
        print(json.dumps(record), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    if args.capture:
        asyncio.run(capture())
    evaluate()
