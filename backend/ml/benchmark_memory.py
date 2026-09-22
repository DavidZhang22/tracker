"""Offline production-path refresh benchmark; no source requests or user data.

Run in a fresh container with --memory 1g --cpus 2 --network none on each revision.
The same script works on the pre-compression revision for a fair comparison.
"""

import argparse
import asyncio
import cProfile
import gc
import gzip
import hashlib
import io
import json
import pstats
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.main import create_app
from app.tracker.analysis_pool import PageAnalyzer, initialize_models
from app.tracker.api import refresh_events
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.github import github_readme
from app.tracker.link_context import context_candidates
from app.tracker.models import Entry, Scan
from app.tracker.parser import parse_page
from app.tracker.store import Store
from app.tracker.urls import SafeFetcher
from ml.artifacts import write_text


def cards(count):
    return (
        "<title>Chapters</title><main>"
        + "".join(
            f'<article><h2><a href="/chapter/{i}">Chapter {i}</a></h2>'
            '<div><span lang="en">English</span>Official <time datetime="2026-09-10">September 10</time></div></article>'
            for i in range(count)
        )
        + "</main>"
    )


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def memory():
    root = Path("/sys/fs/cgroup")
    if not (root / "memory.peak").exists():
        return {}
    events = dict(
        line.split() for line in (root / "memory.events").read_text().splitlines()
    )
    assert events["oom"] == events["oom_kill"] == "0"
    return {
        "peak_memory_mib": int((root / "memory.peak").read_text()) / 1024**2,
        "oom_events": events,
    }


async def pipeline(args):
    html = (
        cards(args.links)
        + '<script type="application/json">{"unused":"'
        + "x" * args.padding
        + '"}</script>'
    )
    wire = gzip.compress(html.encode())
    del html
    metrics = {"active": 0, "fetch_peak": 0, "external_requests": 0}
    starts, hashes, finished = defaultdict(list), {}, {}
    began = time.monotonic()
    hosts = ["slow.example"] * 4 + [f"fast{i}.example" for i in range(4)]

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(0, len(wire), 16384):
                yield wire[index : index + 16384]

    async def response(request):
        host = request.headers["host"]
        starts[host].append(time.monotonic() - began)
        metrics["active"] += 1
        metrics["fetch_peak"] = max(metrics["fetch_peak"], metrics["active"])
        try:
            await asyncio.sleep(args.delay)
            if host == "slow.example" and not request.url.path.endswith("/"):
                return httpx.Response(301, headers={"location": request.url.path + "/"})
            return httpx.Response(
                200,
                stream=Body(),
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "content-encoding": "gzip",
                },
            )
        finally:
            metrics["active"] -= 1

    client_type = httpx.AsyncClient
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Path(tmp) / "library.db", auth_config={"required": False})
        analyzer = PageAnalyzer(2)
        cache = FetchCache(Path(tmp) / "cache.db")
        scanner = app.state.discoverer = Discoverer(
            SafeFetcher(cache, interval=args.interval), analyzer=analyzer
        )
        rows = []
        stores = [app.state.store]
        if args.libraries == 2:
            stores.append(Store(Path(tmp) / "second-library.db"))
        ticks = iter(range(1000, 100000, 8))
        with patch("app.tracker.store.time", lambda: next(ticks)):
            for i, host in enumerate(hosts):
                url = f"https://{host}/series/book-{i}"
                scan = Scan(url, str(i), entries=[Entry(url + "/seed", "Seed")])
                store = stores[i // 4] if args.libraries == 2 else stores[0]
                item = store.create(store.save_scan(scan.to_dict()))
                rows.append({"id": item["id"], "url": url})
        ids = {row["id"]: i for i, row in enumerate(rows)}
        host_slots = {host: i for i, host in enumerate(dict.fromkeys(hosts))}
        original_scan = scanner.scan

        async def scan(url, *values, **kwargs):
            result = await original_scan(url, *values, **kwargs)
            assert len(result.entries) == args.links, (url, len(result.entries))
            hashes[url] = digest(
                [
                    entry.to_dict() if hasattr(entry, "to_dict") else vars(entry)
                    for entry in result.entries
                ]
            )
            return result

        scanner.scan = scan
        await analyzer.start()
        began = time.monotonic()
        try:
            with (
                patch(
                    "app.tracker.urls.public_addresses",
                    AsyncMock(return_value=["93.184.216.34"]),
                ),
                patch(
                    "app.tracker.urls.httpx.AsyncClient",
                    lambda **kw: client_type(
                        transport=httpx.MockTransport(response), **kw
                    ),
                ),
                patch(
                    "app.tracker.urls.hash", lambda host: host_slots[host], create=True
                ),
                patch("app.tracker.api.hash", lambda iid: ids[iid], create=True),
            ):

                async def refresh(subset, store):
                    async for event in refresh_events(subset, app, store, deep=True):
                        if event["type"] == "item":
                            assert event["result"]["ok"], event
                            finished[ids[event["item"]["id"]]] = (
                                time.monotonic() - began
                            )
                        elif event["type"] == "complete":
                            assert (
                                event["checked"] == len(subset) and not event["failed"]
                            )

                await asyncio.gather(
                    *(
                        refresh(
                            rows[i * 4 : (i + 1) * 4] if args.libraries == 2 else rows,
                            store,
                        )
                        for i, store in enumerate(stores)
                    )
                )
            elapsed = time.monotonic() - began
            jobs = list(analyzer.last_jobs)
        finally:
            await analyzer.aclose()
        assert sum(map(len, starts.values())) == 12
        for values in starts.values():
            assert all(
                b - a >= args.interval - 0.02
                for a, b in zip(values, values[1:], strict=False)
            )
        return dict(
            seconds=elapsed,
            first_other_host_s=min(t for i, t in finished.items() if i >= 4),
            links_per_item=args.links,
            padding_bytes=args.padding,
            libraries=args.libraries,
            fetch_peak=metrics["fetch_peak"],
            requests=12,
            external_requests=0,
            hashes=hashes,
            jobs=jobs,
            **memory(),
        )


async def profile(args):
    from dataclasses import asdict

    cases = [("nested", cards(args.links), "https://example.org/series/story")]
    if args.capture:
        data = json.loads(args.capture.read_text(encoding="utf-8"))

        class Replay:
            async def get(self, url):
                return data[url]

        source = "https://github.com/SimplifyJobs/New-Grad-Positions"
        html, _ = await github_readme(Replay(), source, data[source][1])
        cases.append(("jobs", html.text() if hasattr(html, "text") else html, source))
        source = "https://novelshaven.com/series/the-galgame-martial-saint"
        cases.append(("novelshaven", data[source][1], source))
    initialize_models()
    rows = []
    for name, html, source in cases:
        gc.collect()
        samples = []
        for _ in range(3):
            started = time.perf_counter()
            result = parse_page(html, source)
            samples.append(time.perf_counter() - started)
        profiler = cProfile.Profile()
        profiler.runcall(parse_page, html, source)
        output = io.StringIO()
        pstats.Stats(profiler, stream=output).strip_dirs().sort_stats(
            "cumtime"
        ).print_stats(25)
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        features = digest(
            [
                {k: row[k] for k in ("url", "label", "features", "tokens")}
                for row in context_candidates(soup, source)
            ]
        )
        soup.clear(decompose=True)
        soup.decompose()
        rows.append(
            dict(
                name=name,
                feature_hash=features,
                input_bytes=len(html.encode()),
                output_hash=digest([asdict(result[0]), result[1], result[2]]),
                links=len(result[0].entries),
                samples_s=samples,
                profile=output.getvalue(),
            )
        )
    return dict(cases=rows, external_requests=0, **memory())


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["pipeline", "profile"], default="pipeline")
    parser.add_argument("--links", type=int, default=500)
    parser.add_argument("--padding", type=int, default=0)
    parser.add_argument("--libraries", type=int, choices=[1, 2], default=1)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    assert 1 <= args.links <= 4999 and 0 <= args.padding <= 6_000_000
    result = await (pipeline(args) if args.mode == "pipeline" else profile(args))
    if args.output:
        write_text(args.output, json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
