"""Resumable, opt-in audit of one public listing per ranked source."""

import argparse
import asyncio
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.tracker.cache import FetchCache
from ml.collect_breadth import collect, write_manifest

ROOT = Path(__file__).resolve().parents[2]


def classify(row):
    status = row.get("status", "pending")
    error = row.get("error", "").lower()
    if status == "captured":
        return "accessible"
    if status == "robots-disallowed":
        return "robots_disallowed"
    if "cross-host redirect" in error:
        return "unreviewed_redirect"
    if status == "robots-rate-limit":
        return "deferred_crawl_delay"
    if status == "robots-unavailable":
        return "robots_unavailable"
    if any(
        value in error
        for value in (
            "http 401",
            "http 403",
            "http 406",
            "http 451",
            "browser check",
            "access denied",
            "forbidden",
        )
    ):
        return "access_blocked"
    return "temporarily_unavailable"


async def run(manifest, directory, *, fetch=False, egress="unspecified", concurrency=4):
    rows = json.loads(manifest.read_text(encoding="utf8"))
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError("Expected 1 to 200 reviewed source URLs.")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate source IDs.")
    if any(not re.fullmatch(r"media200-[a-z0-9-]+", row["id"]) for row in rows):
        raise ValueError("Source IDs must use media200-* identifiers.")
    if not directory.resolve().is_relative_to((ROOT / "data").resolve()):
        raise ValueError("Captures must stay under backend/data.")
    if not fetch:
        print(
            json.dumps(
                dict(
                    sources=len(rows),
                    pending=sum(r.get("status") == "pending" for r in rows),
                )
            )
        )
        return
    directory.mkdir(parents=True, exist_ok=True)
    cache = FetchCache(directory / "cache.sqlite3")
    gate = asyncio.Semaphore(max(1, min(4, concurrency)))
    started = time.perf_counter()

    async def one(row):
        if row.get("status") not in (None, "pending"):
            return
        async with gate:
            row.update(
                status="attempt-started",
                captured_at=datetime.now(timezone.utc).isoformat(),
                egress=egress,
            )
            write_manifest(manifest, rows)
            try:
                async with asyncio.timeout(90):
                    await collect(row, directory, cache)
            except TimeoutError:
                row.update(
                    status="timeout",
                    error="Collection exceeded its 90-second budget; no retry.",
                )
            row["access_status"] = classify(row)
            write_manifest(manifest, rows)
            print(
                row["id"],
                row["access_status"],
                row.get("bytes", row.get("error", "")),
                flush=True,
            )

    await asyncio.gather(*(one(row) for row in rows))
    print(
        json.dumps(
            dict(
                sources=len(rows),
                statuses=Counter(r.get("access_status", r["status"]) for r in rows),
                network_requests=sum(r.get("network_requests", 0) for r in rows),
                bytes_received=sum(r.get("bytes_received", 0) for r in rows),
                elapsed_seconds=round(time.perf_counter() - started, 3),
            )
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--egress", default="unspecified")
    args = parser.parse_args()
    asyncio.run(
        run(
            args.manifest.resolve(),
            args.directory.resolve(),
            fetch=args.fetch,
            egress=args.egress,
        )
    )


if __name__ == "__main__":
    main()
