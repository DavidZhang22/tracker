"""Replay a selected split without networking; measure complete parser and model.

Train/validation can guide development. Run --split test only after freezing the
model and integration; preserve its report and do not tune against those sites.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml"))
from train import metrics

from app.tracker.link_model import load_model
from app.tracker.parser import parse_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        choices=["train", "validation", "test", "holdout"],
        default="validation",
    )
    args = parser.parse_args()
    rows = [
        r
        for line in (
            ROOT
            / (
                "ml/holdout-dataset.jsonl"
                if args.split == "holdout"
                else "ml/dataset.jsonl"
            )
        )
        .read_text()
        .splitlines()
        if (r := json.loads(line))["split"] == args.split and r["family"] != "synthetic"
    ]
    sources = [
        s
        for s in json.loads((ROOT / "ml/sources.json").read_text())
        if s["split"] == args.split
    ]
    sets, timings = {}, {}
    for source in sources:
        html = (ROOT / source["file"]).read_text(encoding="utf-8")
        for mode in ("off", "on"):
            os.environ["TRACKER_LINK_MODEL"] = mode
            start = time.perf_counter()
            scan, _, _ = parse_page(html, source["url"])
            sets[source["id"], mode] = {e.url for e in scan.entries}
            timings[source["id"] + "-" + mode] = round(time.perf_counter() - start, 4)
    model = load_model()
    report = dict(
        split=args.split,
        model_id=model.model_id,
        baseline=metrics(rows, [r["url"] in sets[r["source_id"], "off"] for r in rows]),
        assisted=metrics(rows, [r["url"] in sets[r["source_id"], "on"] for r in rows]),
        classifier_alone=metrics(
            rows, [model.score(r["features"]) >= model.upper for r in rows]
        ),
        parse_seconds=timings,
    )
    # Report out-of-universe URLs separately (e.g. embedded data not in anchors).
    report["unlabelled_predictions"] = {
        s["id"]: len(
            sets[s["id"], "on"] - {r["url"] for r in rows if r["source_id"] == s["id"]}
        )
        for s in sources
    }
    path = ROOT / ("ml/" + args.split + "-report.json")
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
