"""Offline comparison of full-model parsing and validated learned extraction."""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from benchmark_parallel import cards, output_hash

from app.tracker.analysis_pool import initialize_models
from app.tracker.github import github_readme
from app.tracker.parser import parse_page
from app.tracker.recipes import analyze


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--asura", type=Path)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests/fixtures",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [("nested-1000", cards(1000), "https://example.org/series/book")]
    for name, source in [
        ("asura", "https://asurascans.com/comics/the-nebulas-civilization-53fc8424"),
        ("royalroad", "https://www.royalroad.com/fiction/21220/mother-of-learning"),
        ("hn", "https://news.ycombinator.com/"),
        ("xkcd", "https://xkcd.com/archive/"),
    ]:
        cases.append(
            (name, (args.fixtures / f"{name}.html").read_text(encoding="utf8"), source)
        )
    if args.asura:
        cases.append(
            (
                "asura-current",
                args.asura.read_text(encoding="utf8"),
                "https://asurascans.com/comics/dungeon-odyssey-53fc8424",
            )
        )
    if args.capture:
        data = json.loads(args.capture.read_text(encoding="utf8"))

        class Fetcher:
            async def get(self, url):
                return data[url]

        source = "https://github.com/SimplifyJobs/New-Grad-Positions"
        html, _ = await github_readme(Fetcher(), source, data[source][1])
        cases.append(("jobs-911", html, source))
        source = "https://novelshaven.com/series/the-galgame-martial-saint"
        cases.append(("novelshaven-structured", data[source][1], source))
    initialize_models()
    report = {
        "date": "2026-09-13",
        "baseline_revision": "2a09baa",
        "source_requests": 0,
        "cases": [],
    }
    for name, html, source in cases:
        start = time.perf_counter()
        original, recipe, _ = analyze(html, source)
        initial = time.perf_counter() - start
        timings = {"deep": [], "light": []}
        for _ in range(args.repeats):
            start = time.perf_counter()
            full = parse_page(html, source)
            timings["deep"].append(time.perf_counter() - start)
            start = time.perf_counter()
            quick, _, used = analyze(html, source, recipe=recipe)
            timings["light"].append(time.perf_counter() - start)
            assert full == quick == original, name
        row = {
            "name": name,
            "links": len(full[0].entries),
            "recipe_used": used,
            "initial_scan_and_validation_s": initial,
            "samples_s": timings,
            "deep_median_s": statistics.median(timings["deep"]),
            "light_median_s": statistics.median(timings["light"]),
            "speedup": statistics.median(timings["deep"])
            / statistics.median(timings["light"]),
            "recipe_bytes": len(json.dumps(recipe).encode()) if recipe else 0,
            "output_hash": output_hash(full),
            "all_fields_match": True,
        }
        report["cases"].append(row)
        print(json.dumps(row), flush=True)
    peak = Path("/sys/fs/cgroup/memory.peak")
    if peak.exists():
        report["container_peak_mib"] = int(peak.read_text()) / 1024**2
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    asyncio.run(main())
