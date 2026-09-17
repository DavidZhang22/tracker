"""Opt-in bounded public listing collection for model review; never crawl details."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tracker.cache import FetchCache
from app.tracker.urls import SafeFetcher

# Splits are assigned before collection or model inspection. Labels are reviewed
# from saved HTML separately, without looking at classifier predictions.
SOURCES = [
    ("numpy", "https://numpy.org/news/", "train", "software"),
    ("postgresql", "https://www.postgresql.org/about/news/", "train", "software"),
    ("julia", "https://julialang.org/blog/", "train", "blog"),
    ("cern", "https://home.cern/news", "train", "blog"),
    ("cnrs", "https://www.cnrs.fr/fr/actualites", "train", "blog"),
    ("aclanthology", "https://aclanthology.org/events/", "train", "research"),
    ("gentoo", "https://www.gentoo.org/news/", "validation", "blog"),
    ("kde", "https://kde.org/announcements/", "validation", "software"),
    (
        "publicdomainreview",
        "https://publicdomainreview.org/essays/",
        "validation",
        "blog",
    ),
    ("godot", "https://godotengine.org/news/", "test", "software"),
    ("signal", "https://signal.org/blog/", "test", "blog"),
    ("projecteuler", "https://projecteuler.net/archives", "test", "course"),
]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    directory = ROOT / "data/model-review"
    directory.mkdir(parents=True, exist_ok=True)
    fetcher = SafeFetcher(cache=FetchCache(directory / "cache.sqlite3"))
    report = []
    for name, url, split, kind in SOURCES:
        file = directory / (name + ".html")
        row = dict(id="review-" + name, url=url, split=split, family=kind)
        try:
            if not file.exists() and args.fetch:
                final, html = await fetcher.get(url)
                file.write_text(html, encoding="utf8")
                row["final_url"] = final
            raw = file.read_bytes()
            row.update(
                file=str(file.relative_to(ROOT)).replace("\\", "/"),
                sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw),
                status="captured",
            )
        except Exception as exc:
            row.update(status="unavailable", error=str(exc)[:250])
        report.append(row)
        print(name, row["status"], row.get("bytes", row.get("error")), flush=True)
    (directory / "capture-report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
