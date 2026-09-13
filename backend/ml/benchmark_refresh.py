"""Offline refresh profile; synthetic/public fixtures and disposable storage only.

Run: python ml/benchmark_refresh.py --output data/refresh-before.json --profile
Repeat the same command on the candidate revision for an output-equivalence check.
No source requests, accounts, training packages, or production database access.
"""

import argparse
import asyncio
import cProfile
import gc
import hashlib
import io
import json
import platform
import pstats
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.models import Entry, Scan
from app.tracker.parser import parse_page
from app.tracker.store import Store


def cards(count):
    return (
        "<title>Story chapters</title><main>"
        + "".join(
            f'<article><div><a href="/chapter/{i}">Chapter {i}</a></div>'
            '<div><span lang="en">English</span>Official '
            '<time datetime="2026-09-10">Sep 10</time></div></article>'
            for i in range(count)
        )
        + "</main>"
    )


def measured(fn, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        samples.append(time.perf_counter() - start)
    return result, {
        "median_s": round(statistics.median(samples), 6),
        "samples_s": samples,
    }


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ReplayFetcher:
    def __init__(self, html, cache):
        self.html, self.cache = html, cache
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        await asyncio.sleep(0)
        return url, self.html


async def responsiveness(html, cache):
    fetcher = ReplayFetcher(html, cache)
    scanner = Discoverer(fetcher)
    gaps = []

    async def heartbeat():
        while True:
            started = time.perf_counter()
            await asyncio.sleep(0.01)
            gaps.append(max(0, time.perf_counter() - started - 0.01))

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    started = time.perf_counter()
    cold = await scanner.scan("https://benchmark.example/series/story")
    cold_seconds = time.perf_counter() - started
    await asyncio.sleep(0.02)
    started = time.perf_counter()
    warm = await scanner.scan("https://benchmark.example/series/story")
    warm_seconds = time.perf_counter() - started
    original_get = cache.get
    # Simulate an expired complete scan, leaving HTTP/parsed-page caches intact.
    cache.get = lambda key: None if key.startswith("scan:") else original_get(key)
    started = time.perf_counter()
    revalidated = await scanner.scan("https://benchmark.example/series/story")
    unchanged_seconds = time.perf_counter() - started
    cache.get = original_get
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert cold.entries == warm.entries == revalidated.entries
    assert warm.cached and not revalidated.cached and fetcher.calls == 2
    return dict(
        cold_s=cold_seconds,
        warm_s=warm_seconds,
        unchanged_revalidated_s=unchanged_seconds,
        max_event_loop_lag_s=max(gaps),
        heartbeat_samples=len(gaps),
        fetch_calls=fetcher.calls,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    html = cards(1000)
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "parsing": {},
    }
    cases = [("nested_1000", html, "https://benchmark.example/series/story")]
    for name, url in [
        ("jobs-table.html", "https://github.com/SimplifyJobs/New-Grad-Positions"),
        (
            "royalroad.html",
            "https://www.royalroad.com/fiction/21220/mother-of-learning",
        ),
        ("hn.html", "https://news.ycombinator.com/"),
        ("xkcd.html", "https://xkcd.com/archive/"),
    ]:
        path = ROOT / "tests" / "fixtures" / name
        if path.exists():
            cases.append((name, path.read_text(encoding="utf8"), url))
    for name, page, url in cases:
        (scan, pages, feeds), timing = measured(
            lambda page=page, url=url: parse_page(page, url), args.repeats
        )
        report["parsing"][name] = timing | dict(
            links=len(scan.entries),
            input_sha256=hashlib.sha256(page.encode()).hexdigest(),
            output_sha256=digest([scan.to_dict(), pages, feeds]),
        )
        print(name, timing["median_s"], len(scan.entries), flush=True)
    if args.profile:
        profiler = cProfile.Profile()
        profiler.runcall(parse_page, html, "https://benchmark.example/series/story")
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).strip_dirs().sort_stats(
            "cumtime"
        ).print_stats(35)
        report["profile"] = output.getvalue()
    # SQLite connection objects in the baseline cache are only closed by GC.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = Store(Path(tmp) / "library.db")
        scan = Scan(
            "https://benchmark.example/book",
            "Book",
            entries=[
                Entry(
                    f"https://benchmark.example/chapter/{i}",
                    f"Chapter {i}",
                    number=i,
                    position=i,
                )
                for i in range(4999)
            ],
        ).to_dict()
        item = store.create(store.save_scan(scan))
        _, report["unchanged_merge_4999"] = measured(
            lambda: store.merge(item["id"], scan), args.repeats
        )
        _, report["item_read_4999"] = measured(
            lambda: store.item(item["id"]), args.repeats
        )
        with store.connection() as db:
            before = db.execute("SELECT * FROM links ORDER BY position").fetchall()
        store.merge(item["id"], scan)
        with store.connection() as db:
            assert (
                before == db.execute("SELECT * FROM links ORDER BY position").fetchall()
            )
        cache = FetchCache(Path(tmp) / "cache.db")
        for i in range(55):
            cache.put(str(i), {"body": "x" * 1_000_000})
        _, report["cache_put_with_55mb"] = measured(
            lambda: cache.put("small", {"body": "x" * 1000}), 10
        )
        gc.collect()
        report["scan_replay"] = asyncio.run(responsiveness(html, cache))
        gc.collect()
    report["training_libraries_loaded"] = any(
        name in sys.modules for name in ("numpy", "sklearn", "torch")
    )
    try:
        import resource

        report["peak_rss_mib"] = (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        )
    except ImportError:
        pass
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps({k: v for k, v in report.items() if k != "profile"}, indent=2))


if __name__ == "__main__":
    main()
