"""Profile explicit public sources once; replay captures without more scraping.

Capture HTML stays local and untracked. No account data is read or changed.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tracker import discovery
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.urls import SafeFetcher


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", type=Path)
    mode.add_argument("--replay", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    captured = json.loads(args.replay.read_text(encoding="utf8")) if args.replay else {}
    report = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        fetcher = SafeFetcher(FetchCache(Path(tmp) / "cache.db"))
        scanner = Discoverer(fetcher)
        get, parse_page = fetcher.get, discovery.parse_page
        times = {"fetch_s": 0, "parse_s": 0, "calls": 0}

        async def fetch(url):
            started = time.perf_counter()
            times["calls"] += 1
            try:
                if args.replay:
                    await asyncio.sleep(0)
                    result = captured[url]
                else:
                    result = await get(url)
                    captured[url] = result
                return result
            finally:
                times["fetch_s"] += time.perf_counter() - started

        def parse(*values, **kwargs):
            started = time.perf_counter()
            try:
                return parse_page(*values, **kwargs)
            finally:
                times["parse_s"] += time.perf_counter() - started

        fetcher.get = fetch
        discovery.parse_page = parse
        for url in args.urls:
            times.update(fetch_s=0, parse_s=0, calls=0)
            started = time.perf_counter()
            try:
                scan = await scanner.scan(url)
                stable = scan.to_dict()
                for key in ("checked_at", "cached", "requests_made", "cache_hits"):
                    stable.pop(key, None)
                row = dict(
                    source=url,
                    seconds=time.perf_counter() - started,
                    **times,
                    links=len(scan.entries),
                    requests=scan.requests_made,
                    output_sha256=hashlib.sha256(
                        json.dumps(stable, sort_keys=True).encode()
                    ).hexdigest(),
                )
                assert (await scanner.scan(url)).cached
            except Exception as exc:
                row = dict(
                    source=url,
                    seconds=time.perf_counter() - started,
                    **times,
                    error=str(exc),
                )
            report.append(row)
            print(json.dumps(row), flush=True)
        discovery.parse_page = parse_page
    if args.capture:
        args.capture.write_text(json.dumps(captured), encoding="utf8")
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    asyncio.run(main())
