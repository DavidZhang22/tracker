"""Offline full-page throughput and output checks for threads versus processes."""

import argparse
import asyncio
import hashlib
import json
import math
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tracker import record_context
from app.tracker.analysis_pool import PageAnalyzer, initialize_models
from app.tracker.github import github_readme
from app.tracker.link_model import FEATURES, LinkModel


def reference_network(layers, values, sparse=False):
    for i, layer in enumerate(layers):
        if sparse:
            nonzero = [(j, v) for j, v in enumerate(values) if v]
            values = [
                b + sum(w[j] * v for j, v in nonzero)
                for w, b in zip(layer["weights"], layer["bias"], strict=True)
            ]
        else:
            values = [
                b + sum(w * v for w, v in zip(weights, values, strict=True))
                for weights, b in zip(layer["weights"], layer["bias"], strict=True)
            ]
        if i + 1 < len(layers):
            values = [max(0, v) for v in values]
    return 1 / (1 + math.exp(-max(-60, min(60, values[0]))))


def reference_record(model, features):
    return reference_network(model["layers"], features, sparse=True)


def reference_link(self, features):
    if len(features) != len(FEATURES) or not all(
        math.isfinite(x) and 0 <= x <= 1 for x in features
    ):
        raise ValueError("Invalid link feature vector")
    return reference_network(self.layers, features)


def cards(count):
    return (
        "<main>"
        + "".join(
            f'<article><h2><a href="/chapter/{i}">Chapter {i}</a></h2>'
            '<div><span lang="en">English</span>Official <time datetime="2026-09-10">September 10</time></div></article>'
            for i in range(count)
        )
        + "</main>"
    )


def output_hash(result):
    scan, pages, feeds = result
    return hashlib.sha256(
        json.dumps([scan.to_dict(), pages, feeds], sort_keys=True).encode()
    ).hexdigest()


async def benchmark(mode, cases, repeats):
    analyzer = PageAnalyzer(2 if mode == "processes" else 0)
    previous = record_context.predict, LinkModel.score
    if mode == "threads-reference":
        record_context.predict, LinkModel.score = reference_record, reference_link
    gaps, stop = [], asyncio.Event()

    async def heartbeat():
        while not stop.is_set():
            t = time.monotonic()
            await asyncio.sleep(0.01)
            gaps.append(max(0, time.monotonic() - t - 0.01))

    initialize_models()
    started = time.monotonic()
    await analyzer.start()
    startup = time.monotonic() - started
    task = asyncio.create_task(heartbeat())
    samples, jobs, hashes = [], [], None
    try:
        for _ in range(repeats):
            analyzer.last_jobs.clear()
            t = time.monotonic()
            results = await asyncio.gather(
                *(analyzer.analyze(html, url) for _, html, url in cases)
            )
            samples.append(time.monotonic() - t)
            current = {
                name: output_hash(result)
                for (name, _, _), result in zip(cases, results, strict=True)
            }
            assert hashes is None or hashes == current
            hashes = current
            jobs.append(list(analyzer.last_jobs))
    finally:
        stop.set()
        await task
        await analyzer.aclose()
        record_context.predict, LinkModel.score = previous
    return dict(
        mode=mode,
        startup_s=startup,
        median_s=statistics.median(samples),
        samples_s=samples,
        max_event_loop_lag_s=max(gaps, default=0),
        jobs=jobs,
        output_hashes=hashes,
        cpu_wall_ratios=[
            sum(j["cpu_seconds"] for j in group) / duration
            for group, duration in zip(jobs, samples, strict=True)
        ],
    )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["all", "threads-reference", "threads", "processes"],
        default="all",
    )
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--links", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [
        (f"chapters-{i}", cards(args.links), f"https://example.org/series/book-{i}")
        for i in range(2)
    ]
    if args.capture:
        data = json.loads(args.capture.read_text(encoding="utf8"))

        class Fetcher:
            async def get(self, url):
                return data[url]

        url = "https://github.com/SimplifyJobs/New-Grad-Positions"
        html, _ = await github_readme(Fetcher(), url, data[url][1])
        if not isinstance(html, str):
            html = html.text()
        cases += [("jobs", html, url)]
        url = "https://novelshaven.com/series/the-galgame-martial-saint"
        cases += [("novels", data[url][1], url)]
    report = dict(
        python=sys.version,
        platform=platform.platform(),
        gil_enabled=getattr(sys, "_is_gil_enabled", lambda: True)(),
        cases=[dict(name=name, bytes=len(html.encode())) for name, html, _ in cases],
        modes=[],
    )
    for mode in (
        ["threads-reference", "threads", "processes"]
        if args.mode == "all"
        else [args.mode]
    ):
        result = await benchmark(mode, cases, args.repeats)
        if report["modes"]:
            assert result["output_hashes"] == report["modes"][0]["output_hashes"]
        report["modes"].append(result)
        print(
            json.dumps(
                {k: v for k, v in result.items() if k not in {"jobs", "output_hashes"}}
            ),
            flush=True,
        )
    report["training_packages_loaded"] = any(
        m in sys.modules for m in ("numpy", "sklearn", "torch")
    )
    peak = Path("/sys/fs/cgroup/memory.peak")
    if peak.exists():
        report["container_peak_mib"] = int(peak.read_text()) / 1024**2
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    asyncio.run(main())
