"""Compare optional native inference with the Python reference on frozen rows."""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tracker.context_model import load_context_model
from app.tracker.native_model import kernel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cascade", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if kernel() is None:
        parser.error("Build the native kernel before measuring it")
    rows = [
        json.loads(line)
        for name in ("v3-dataset.jsonl", "generalization.jsonl")
        for line in (ROOT / "ml/datasets" / name)
        .read_text(encoding="utf8")
        .splitlines()
        if line.strip()
    ]
    model = load_context_model()
    if args.cascade:
        from app.tracker.cascade_model import load_cascade_model

        model = load_cascade_model()
    if model is None:
        parser.error("Requested model is unavailable")
    results, timings = {}, {}
    for mode in ("off", "on"):
        os.environ["TRACKER_NATIVE_MODEL"] = mode
        model.score_many(rows[:128])
        elapsed = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            results[mode] = model.score_many(rows)
            elapsed.append(time.perf_counter() - start)
        timings[mode] = dict(runs=elapsed, median_seconds=statistics.median(elapsed))
    pairs = list(zip(results["off"], results["on"], strict=True))
    report = dict(
        rows=len(rows),
        model_id=model.model_id,
        timings=timings,
        maximum_error=max(abs(a - b) for a, b in pairs),
        decision_changes=sum(
            (a >= model.upper) != (b >= model.upper) for a, b in pairs
        ),
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, indent=2))
    if report["maximum_error"] > 1e-12 or report["decision_changes"]:
        raise SystemExit("Native inference parity failed")


if __name__ == "__main__":
    main()
