"""Offline before/after scan-cache benchmark with real SQLite and mocked HTTP."""

import argparse
import asyncio
import cProfile
import gc
import hashlib
import json
import os
import pstats
import statistics
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch


async def measure(args):
    sys.path.insert(0, str(args.app_root))
    import httpx

    from app.tracker.cache import FetchCache
    from app.tracker.discovery import Discoverer
    from app.tracker.models import Entry, Scan
    from app.tracker.native_model import kernel
    from app.tracker.urls import SafeFetcher

    source = "https://cache.example/series/book"
    html = (
        "<main>"
        + "".join(
            f'<article><h2><a href="/chapter/{i}">Chapter {i}</a></h2><time datetime="2026-09-16"></time></article>'
            for i in range(250)
        )
        + "</main>"
    )
    requests = []
    client = httpx.AsyncClient

    async def respond(request):
        requests.append(request.url.path)
        await asyncio.sleep(0.01)
        if request.url.path.startswith("/alias-"):
            return httpx.Response(301, headers={"Location": source})
        return httpx.Response(200, text=html)

    async def addresses(_):
        return ["93.184.216.34"]

    class Scanner(Discoverer):
        analyzed = 0

        async def _analyze_page(self, *values):
            type(self).analyzed += 1
            return await super()._analyze_page(*values)

    def digest(scan):
        return hashlib.sha256(
            json.dumps([asdict(e) for e in scan.entries], sort_keys=True).encode()
        ).hexdigest()

    report = {
        "native_inference": kernel() is not None
        and os.environ.get("TRACKER_NATIVE_MODEL") != "off",
        "python": sys.version,
        "network": "mocked; 10 ms request delay, no external requests",
        "rows": 250,
    }
    with (
        tempfile.TemporaryDirectory() as temporary,
        patch("app.tracker.urls.public_addresses", addresses),
        patch(
            "app.tracker.urls.httpx.AsyncClient",
            lambda **kw: client(transport=httpx.MockTransport(respond), **kw),
        ),
    ):
        directory = Path(temporary)
        scanner = Scanner(SafeFetcher(FetchCache(directory / "repeat.db"), interval=0))
        start = time.perf_counter()
        first = await scanner.scan(source, deep=True)
        report["cold_seconds"] = time.perf_counter() - start
        elapsed, hashes = [], []
        for _ in range(4):
            start = time.perf_counter()
            result = await scanner.scan(source, deep=True)
            elapsed.append(time.perf_counter() - start)
            hashes.append(digest(result))
        report["repeat_full_refresh"] = dict(
            seconds=elapsed,
            median_seconds=statistics.median(elapsed),
            requests=len(requests),
            analyses=Scanner.analyzed,
            identical=all(h == digest(first) for h in hashes),
        )

        requests.clear()
        Scanner.analyzed = 0
        scanner = Scanner(SafeFetcher(FetchCache(directory / "aliases.db"), interval=0))
        await scanner.scan(source, deep=True)
        start = time.perf_counter()
        results = [
            await scanner.scan(f"https://cache.example/alias-{i}", deep=True)
            for i in range(3)
        ]
        results += [
            await scanner.scan(f"https://cache.example/alias-{i}", deep=True)
            for i in range(3)
        ]
        report["aliases"] = dict(
            seconds=time.perf_counter() - start,
            requests=len(requests),
            destination_requests=requests.count("/series/book"),
            analyses=Scanner.analyzed,
            identical=all(digest(s) == digest(first) for s in results),
        )

        requests.clear()
        Scanner.analyzed = 0
        scanners = [
            Scanner(SafeFetcher(FetchCache(directory / "parallel.db"), interval=0))
            for _ in range(4)
        ]
        start = time.perf_counter()
        results = await asyncio.gather(*(s.scan(source, deep=True) for s in scanners))
        report["concurrent_libraries"] = dict(
            seconds=time.perf_counter() - start,
            requests=len(requests),
            analyses=Scanner.analyzed,
            identical=all(digest(s) == digest(first) for s in results),
        )

        class CachedScanner(Discoverer):
            async def _scan(self, url, *_):
                return Scan(
                    url,
                    entries=[
                        Entry(
                            f"https://cache.example/chapter/{i}",
                            f"Chapter {i}",
                            number=i,
                        )
                        for i in range(4999)
                    ],
                )

        scanner = CachedScanner(
            SafeFetcher(FetchCache(directory / "large.db"), interval=0)
        )
        await scanner.scan(source)
        elapsed = []
        for _ in range(15):
            start = time.perf_counter()
            result = await scanner.scan(source)
            elapsed.append(time.perf_counter() - start)
            assert result.cached and len(result.entries) == 4999
        report["warm_4999_links"] = dict(
            seconds=elapsed,
            median_seconds=statistics.median(elapsed),
            p95_seconds=sorted(elapsed)[-1],
        )
        if args.profile:
            profile = cProfile.Profile()
            profile.enable()
            for _ in range(5):
                await scanner.scan(source)
            profile.disable()
            stats = pstats.Stats(profile)
            report["warm_profile"] = [
                dict(
                    function=f"{Path(key[0]).name}:{key[1]}:{key[2]}",
                    calls=value[1],
                    self_seconds=value[2],
                    cumulative_seconds=value[3],
                )
                for key, value in sorted(
                    stats.stats.items(), key=lambda pair: pair[1][3], reverse=True
                )[:30]
            ]
        report["cache_file_bytes"] = sum(p.stat().st_size for p in directory.iterdir())
    gc.collect()
    peak = Path("/sys/fs/cgroup/memory.peak")
    report["container_peak_bytes"] = int(peak.read_text()) if peak.exists() else None
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(measure(args))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    print(
        json.dumps({k: v for k, v in result.items() if k != "warm_profile"}, indent=2)
    )


if __name__ == "__main__":
    main()
