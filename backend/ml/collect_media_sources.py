"""Collect one public index per source for media format evaluation, never details."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tracker.cache import FetchCache
from app.tracker.media_metadata import sample_entries
from app.tracker.parser import parse_page
from app.tracker.urls import SafeFetcher

# Source labels and partitions are fixed before classification is evaluated.
SOURCES = [
    ("nin", "https://www.nin.com/music/", "music", "train"),
    ("ninjatune", "https://ninjatune.net/releases", "music", "validation"),
    ("monstercat", "https://www.monstercat.com/music", "music", "test"),
    ("pythonjobs", "https://www.python.org/jobs/", "jobs", "train"),
    (
        "weworkremotely",
        "https://weworkremotely.com/categories/remote-programming-jobs",
        "jobs",
        "validation",
    ),
    ("mozillajobs", "https://www.mozilla.org/en-US/careers/listings/", "jobs", "test"),
    ("smbc", "https://www.smbc-comics.com/comic/archive", "comic", "train"),
    (
        "questionablecontent",
        "https://www.questionablecontent.net/archive.php",
        "comic",
        "test",
    ),
    ("standardebooks", "https://standardebooks.org/ebooks", "novel", "validation"),
    ("wanderinginn", "https://wanderinginn.com/table-of-contents/", "novel", "test"),
    ("cs50", "https://cs50.harvard.edu/x/weeks/", "course", "train"),
    ("mitocw", "https://ocw.mit.edu/courses/", "course", "test"),
    ("ccctalks", "https://media.ccc.de/c/38c3", "video", "train"),
    ("20k", "https://www.20k.org/episodes", "podcast", "test"),
    ("jmlr", "https://www.jmlr.org/papers/", "research", "train"),
    ("arxiv", "https://arxiv.org/list/cs.AI/recent", "research", "test"),
    ("pitchfork", "https://pitchfork.com/news/", "blog", "train"),
    ("animenews", "https://www.animenewsnetwork.com/news/", "blog", "validation"),
    ("musicbusiness", "https://www.musicbusinessworldwide.com/", "blog", "test"),
]

FINAL_SOURCES = [
    ("subpop", "https://www.subpop.com/releases", "music", "final"),
    ("darknetdiaries", "https://darknetdiaries.com/episode/", "podcast", "final"),
    (
        "neurips",
        "https://proceedings.neurips.cc/paper_files/paper/2025",
        "research",
        "final",
    ),
    ("metmuseum", "https://www.metmuseum.org/exhibitions", "events", "final"),
    ("stereogum", "https://www.stereogum.com/category/news/", "blog", "final"),
    (
        "practicalguide",
        "https://practicalguidetoevil.wordpress.com/table-of-contents/",
        "novel",
        "final",
    ),
]


async def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--fetch", action="store_true")
    args.add_argument(
        "--final",
        action="store_true",
        help="Separate final source set; never used for model selection.",
    )
    options = args.parse_args()
    directory = ROOT / "data/media-strength"
    directory.mkdir(parents=True, exist_ok=True)
    fetcher = SafeFetcher(cache=FetchCache(directory / "cache.sqlite3"))
    report_path = directory / (
        "final-capture-report.json" if options.final else "capture-report.json"
    )
    previous = (
        {row["source_id"]: row for row in json.loads(report_path.read_text())}
        if report_path.exists()
        else {}
    )
    profiles, report = [], []
    for name, url, label, split in FINAL_SOURCES if options.final else SOURCES:
        file = directory / (name + ".html")
        row = dict(source_id="media-" + name, url=url, label=label, split=split)
        if old := previous.get(row["source_id"]):
            row["final_url"] = old.get("final_url", url)
        if name == "nin":
            row["excluded_reason"] = (
                "Requested music inventory redirected to a single news article; not an inventory observation."
            )
        try:
            if not file.exists() and options.fetch:
                final, html = await fetcher.get(url)
                file.write_text(html, encoding="utf8")
                row["final_url"] = final
            raw = file.read_bytes()
            scan, *_ = parse_page(
                raw.decode("utf8", errors="replace"), row.get("final_url", url)
            )
            payload = scan.to_dict()
            row.update(
                sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw),
                status="captured",
                entries=len(payload["entries"]),
            )
            profiles.append(
                {k: row[k] for k in ("source_id", "url", "label", "split", "sha256")}
                | {
                    "title": payload["title"],
                    "source_summary": payload.get("source_summary", ""),
                    "kind": payload["kind"],
                    "entries": [
                        {field: entry.get(field, "") for field in ("title", "url")}
                        for entry in sample_entries(payload["entries"])
                    ],
                    "provenance": "public listing; bounded deployed parser output; no detail pages fetched",
                }
            )
        except Exception as exc:
            row.update(status="unavailable", error=str(exc)[:250])
        if (
            row.get("excluded_reason")
            and profiles
            and profiles[-1]["source_id"] == row["source_id"]
        ):
            profiles[-1]["excluded_reason"] = row["excluded_reason"]
        report.append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    (
        directory
        / ("final-capture-report.json" if options.final else "capture-report.json")
    ).write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    target = (
        ROOT
        / "ml/datasets"
        / (
            "media-final-profiles.json"
            if options.final
            else "media-public-profiles.json"
        )
    )
    target.write_text(
        json.dumps(profiles, indent=2, ensure_ascii=True) + "\n", encoding="utf8"
    )


if __name__ == "__main__":
    asyncio.run(main())
