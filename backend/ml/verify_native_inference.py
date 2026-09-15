"""Compare native inference with the previous implementation on saved datasets."""

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from benchmark_parallel import reference_link, reference_record

from app.tracker.link_model import load_model
from app.tracker.record_context import load_record_model, predict


def check(name, rows, before, after, thresholds, repeats):
    timings = {"before": [], "after": []}
    scores = {}
    for _ in range(repeats):
        for label, function in (("before", before), ("after", after)):
            start = time.perf_counter()
            scores[label] = [function(row["features"]) for row in rows]
            timings[label].append(time.perf_counter() - start)
    differences = [
        abs(a - b) for a, b in zip(scores["before"], scores["after"], strict=True)
    ]
    changed = sum(
        any((a >= t) != (b >= t) for t in thresholds)
        for a, b in zip(scores["before"], scores["after"], strict=True)
    )
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        if "anchor" in row:
            groups[row["anchor"]].append(i)
    rank_changes = sum(
        max(indices, key=lambda i: scores["before"][i])
        != max(indices, key=lambda i: scores["after"][i])
        for indices in groups.values()
    )
    result = dict(
        name=name,
        rows=len(rows),
        thresholds=thresholds,
        threshold_changes=changed,
        region_groups=len(groups),
        region_selection_changes=rank_changes,
        max_absolute_probability_difference=max(differences),
        samples_s=timings,
        speedup=statistics.median(timings["before"])
        / statistics.median(timings["after"]),
    )
    assert max(differences) < 1e-12 and changed == 0 and rank_changes == 0, result
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(__file__).parent / "datasets")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    record, link = load_record_model(), load_model()

    def rows(name):
        return [
            json.loads(line)
            for line in (args.data / name).read_text(encoding="utf8").splitlines()
        ]

    results = [
        check(
            "record-context",
            rows("record-context-dataset.jsonl"),
            lambda f: reference_record(record, f),
            lambda f: predict(record, f),
            [record["threshold"]],
            args.repeats,
        ),
        check(
            "link",
            rows("dataset.jsonl") + rows("holdout-dataset.jsonl"),
            lambda f: reference_link(link, f),
            link.score,
            [link.lower, link.upper],
            args.repeats,
        ),
    ]
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
