"""Replay public HTML through the full parser, comparing a frozen candidate to production."""

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from verify_model_pipeline import expected_urls

from ml.artifacts import read_bytes, read_json, write_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--split", choices=("development", "test", "all"), default="development"
    )
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("Use 1–10 repetitions")
    os.environ["TRACKER_ML_APP_ROOT"] = str(args.app_root.resolve())
    from review_models import RefinementCandidate

    from app.tracker.cascade_model import load_cascade_model
    from app.tracker.parser import parse_page

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
    models = {
        "baseline": load_cascade_model(),
        "candidate": RefinementCandidate(read_json(args.candidate)),
    }
    report = dict(
        split=args.split,
        candidate_sha256=hashlib.sha256(read_bytes(args.candidate)).hexdigest(),
        repeats=args.repeats,
        pages={},
        skipped=[],
    )
    for source in sources.values():
        if source.get("status", "captured") != "captured":
            continue
        if args.split != "all" and (source.get("split") == "test") != (
            args.split == "test"
        ):
            continue
        file = ROOT / source["file"]
        if not file.is_file():
            possible = list((ROOT / "data").glob(f"*/{file.name}.html"))
            if len(possible) == 1:
                file = possible[0]
        if not file.is_file():
            report["skipped"].append(source["id"])
            continue
        html = file.read_text(encoding="utf8")
        expected = expected_urls(source, html)
        page = dict(
            expected=len(expected),
            capture_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
        )
        results = {}
        for name, model in models.items():
            elapsed = []
            with (
                patch.dict(os.environ, TRACKER_LINK_MODEL="cascade"),
                patch(
                    "app.tracker.context_model.active_context_model", return_value=model
                ),
            ):
                for _ in range(args.repeats):
                    start = time.perf_counter()
                    scan = parse_page(html, source["url"])[0]
                    elapsed.append(time.perf_counter() - start)
            actual = {entry.url for entry in scan.entries}
            results[name] = {entry.url: asdict(entry) for entry in scan.entries}
            page[name] = dict(
                correct=len(actual & expected),
                unwanted=len(actual - expected),
                missing=len(expected - actual),
                median_seconds=statistics.median(elapsed),
            )
        page["lost_expected"] = sorted(
            (results["baseline"].keys() - results["candidate"].keys()) & expected
        )
        page["gained_expected"] = sorted(
            (results["candidate"].keys() - results["baseline"].keys()) & expected
        )
        page["removed_unwanted"] = len(
            (results["baseline"].keys() - results["candidate"].keys()) - expected
        )
        page["added_unwanted"] = len(
            (results["candidate"].keys() - results["baseline"].keys()) - expected
        )
        page["changed_metadata"] = sum(
            {
                k: v
                for k, v in results["baseline"][u].items()
                if k not in {"method", "position"}
            }
            != {
                k: v
                for k, v in results["candidate"][u].items()
                if k not in {"method", "position"}
            }
            for u in results["baseline"].keys() & results["candidate"].keys()
        )
        report["pages"][source["id"]] = page
        print(source["id"], page["baseline"], "->", page["candidate"], flush=True)
        write_text(args.output, json.dumps(report, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
