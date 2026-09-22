"""Offline complete-parser comparison. Test is reserved for a frozen candidate."""

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from build_v2_dataset import annotated_index
from train import metrics

from app.tracker.context_model import load_context_model
from app.tracker.parser import parse_page
from ml.artifacts import read_json, write_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "ml/datasets/v3-dataset.jsonl"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--split", choices=["train", "validation", "test"], default="validation"
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Refresh only the frozen v1 comparison in an existing report.",
    )
    args = parser.parse_args()
    rows = [
        r
        for line in args.dataset.read_text().splitlines()
        if (r := json.loads(line))["split"] == args.split and r["origin"] == "index"
    ]
    identifiers = {r["source_id"] for r in rows}
    sources = [
        s
        for s in json.loads((ROOT / "ml/datasets/sources.json").read_text())
        + json.loads((ROOT / "ml/datasets/v2_sources.json").read_text())
        if s["id"] in identifiers
    ]
    if args.split == "train":
        # Report full development pages, not the sampled training distribution.
        rows = []
        for source in sources:
            soup = BeautifulSoup(
                (ROOT / source["file"]).read_text(encoding="utf8"), "html.parser"
            )
            rows.extend(annotated_index(soup, source)[0])
    old_spec = importlib.util.spec_from_file_location(
        "app.tracker.v1_parser", ROOT / "ml/baselines/v1/parser.py"
    )
    old_parser = importlib.util.module_from_spec(old_spec)
    old_spec.loader.exec_module(old_parser)
    predictions = {}
    timings = {}
    unknown = {}
    for mode in ("legacy",) if args.baseline_only else ("legacy", "on", "primary"):
        os.environ["TRACKER_LINK_MODEL"] = mode
        found = {}
        for source in sources:
            start = time.perf_counter()
            scan = (old_parser.parse_page if mode == "legacy" else parse_page)(
                (ROOT / source["file"]).read_text(encoding="utf8"), source["url"]
            )[0]
            found[source["id"]] = {e.url for e in scan.entries}
            timings[mode + ":" + source["id"]] = round(time.perf_counter() - start, 3)
            unknown[mode + ":" + source["id"]] = len(
                found[source["id"]]
                - {r["url"] for r in rows if r["source_id"] == source["id"]}
            )
        predictions[mode] = metrics(
            rows, [r["url"] in found[r["source_id"]] for r in rows]
        )
    if args.baseline_only:
        path = ROOT / f"ml/reports/context-{args.split}-report.json"
        report = read_json(path)
        report["parser"]["legacy"] = predictions["legacy"]
        report["parse_seconds"].update(timings)
        report["unlabelled_predictions"].update(unknown)
        report["baseline"] = "Frozen v1 parser, with v1 classifier enabled"
        write_text(path, json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["parser"]["legacy"]))
        return
    model = load_context_model()
    report = dict(
        split=args.split,
        model_id=model.model_id,
        baseline="Frozen v1 parser, with v1 classifier enabled",
        parser=predictions,
        classifier=metrics(
            rows, [model.score(r["features"], r["tokens"]) >= model.upper for r in rows]
        ),
        parse_seconds=timings,
        unlabelled_predictions=unknown,
    )
    write_text(
        args.output or ROOT / f"ml/reports/context-{args.split}-report.json",
        json.dumps(report, indent=2) + "\n",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
