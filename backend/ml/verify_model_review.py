"""Offline production-model replay against frozen public source scopes."""

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--entries", type=Path)
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--inference-only", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("Use 1–10 repetitions")
    sys.path[:0] = [str(args.app_root), str(ROOT / "ml")]
    os.environ["TRACKER_LINK_MODEL"] = "cascade"
    from verify_model_pipeline import expected_urls

    from app.tracker.parser import parse_page

    if args.inference_only:
        from app.tracker.cascade_model import load_cascade_model
        from app.tracker.native_model import kernel

        if kernel() is None:
            parser.error("Build the native kernel before comparing inference")
        rows = [
            json.loads(line)
            for name in (
                "v3-dataset",
                "generalization",
                "cascade-dataset",
                "review-dataset",
            )
            for line in (ROOT / "ml/datasets" / (name + ".jsonl"))
            .read_text(encoding="utf8")
            .splitlines()
        ]
        model = load_cascade_model()
        report = dict(rows=len(rows), model_id=model.model_id, timings={})
        predictions = {}
        for mode in ("off", "on"):
            os.environ["TRACKER_NATIVE_MODEL"] = mode
            elapsed = []
            for _ in range(args.repeats):
                start = time.perf_counter()
                predictions[mode] = model.score_many(rows)
                elapsed.append(time.perf_counter() - start)
            report["timings"][mode] = dict(
                runs=elapsed, median_seconds=statistics.median(elapsed)
            )
        pairs = list(zip(predictions["off"], predictions["on"], strict=True))
        report["maximum_error"] = max(abs(a - b) for a, b in pairs)
        report["decision_changes"] = sum(
            (a >= model.upper) != (b >= model.upper) for a, b in pairs
        )
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
        if report["maximum_error"] > 1e-12 or report["decision_changes"]:
            raise SystemExit("Native/Python parity failed")
        return

    sources = {}
    for name in (
        "sources",
        "v2_sources",
        "generalization-sources",
        "cascade-sources",
        "cascade-final-sources",
        "review-sources",
    ):
        for source in json.loads(
            (ROOT / "ml/datasets" / (name + ".json")).read_text(encoding="utf8")
        ):
            sources[source["id"]] = source
    report = dict(repeats=args.repeats, pages={}, skipped=[])
    entries = {}
    for source in sources.values():
        if args.only and source["id"] not in args.only:
            continue
        if source.get("status", "captured") != "captured":
            continue
        file = ROOT / source["file"]
        if not file.is_file():
            file = ROOT / "data/generalization" / (Path(source["file"]).name + ".html")
        if not file.is_file():
            report["skipped"].append(source["id"])
            continue
        html = file.read_text(encoding="utf8")
        expected = expected_urls(source, html)
        elapsed = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            scan = parse_page(html, source["url"])[0]
            elapsed.append(time.perf_counter() - start)
        actual = {entry.url for entry in scan.entries}
        entries[source["id"]] = {entry.url: asdict(entry) for entry in scan.entries}
        page = dict(
            expected=len(expected),
            correct=len(actual & expected),
            unwanted=len(actual - expected),
            missing=len(expected - actual),
            median_seconds=statistics.median(elapsed),
            capture_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
            output_sha256=hashlib.sha256(
                json.dumps(entries[source["id"]], sort_keys=True).encode()
            ).hexdigest(),
        )
        report["pages"][source["id"]] = page
        print(
            source["id"],
            page["correct"],
            page["unwanted"],
            round(page["median_seconds"], 3),
            flush=True,
        )
    try:
        import psutil

        memory = psutil.Process().memory_info()
        report["process_peak_bytes"] = getattr(memory, "peak_wset", None)
        report["process_final_rss_bytes"] = memory.rss
    except ImportError:
        pass
    peak = Path("/sys/fs/cgroup/memory.peak")
    if peak.exists():
        report["container_peak_bytes"] = int(peak.read_text())
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if args.entries:
        args.entries.write_text(json.dumps(entries, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
