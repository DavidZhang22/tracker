"""Runtime-only media model benchmark; no sklearn, model downloads, or network."""

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--app-root", type=Path, default=root)
    parser.add_argument("--corpus-root", type=Path, default=root / "ml/datasets")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 30:
        parser.error("--repeats must be between 1 and 30")
    sys.path.insert(0, str(args.app_root))
    import numpy as np

    from app.tracker.media_classifier import load, select
    from app.tracker.media_features import vector
    from app.tracker.media_metadata import (
        annotate,
        baseline_classify,
        classify,
        evidence_text,
        sample_entries,
    )

    groups = {}
    for name in (
        "media-profiles.json",
        "media-public-profiles.json",
        "media-final-profiles.json",
    ):
        groups[name] = [
            row
            for row in json.loads((args.corpus_root / name).read_text(encoding="utf8"))
            if row["source_id"] != "media-nin"
        ]
    rows = sum(groups.values(), [])
    rows_by_split = {
        "training_replay": groups["media-profiles.json"]
        + [
            row
            for row in groups["media-public-profiles.json"]
            if row["split"] == "train"
        ],
        "validation": [
            row
            for row in groups["media-public-profiles.json"]
            if row["split"] == "validation"
        ],
        "initial_source_holdout": [
            row
            for row in groups["media-public-profiles.json"]
            if row["split"] == "test"
        ],
        "final_fresh_source_holdout": groups["media-final-profiles.json"],
    }
    classes, weights, bias, minimum, margin = load()
    artifact = json.loads(
        (args.app_root / "app/tracker/media_classifier.json").read_text(encoding="utf8")
    )
    error = 0
    changed = 0
    for row in rows:
        values = vector(row, evidence_text(row), sample_entries(row["entries"]))
        reference = [
            sum(x * w for x, w in zip(values, output, strict=True)) + offset
            for output, offset in zip(
                artifact["weights"], artifact["bias"], strict=True
            )
        ]
        maximum = max(reference)
        reference = [math.exp(score - maximum) for score in reference]
        denominator = sum(reference)
        reference = [value / denominator for value in reference]
        scores = weights @ np.asarray(values, dtype=np.float32) + bias
        native = np.exp(scores - scores.max())
        native /= native.sum()
        error = max(
            error, max(abs(a - b) for a, b in zip(reference, native, strict=True))
        )
        slow = select(
            values,
            baseline_classify(row),
            row["kind"],
            (
                classes,
                np.asarray(artifact["weights"]),
                np.asarray(artifact["bias"]),
                minimum,
                margin,
            ),
        )
        changed += slow != classify(row)
    result = {
        "profiles": len(rows),
        "numeric_bytes": weights.nbytes + bias.nbytes,
        "float32_probability_max_error": float(error),
        "changed_decisions_from_float64": changed,
        "encoder_loaded": any(
            name.startswith(("onnxruntime", "tokenizers")) for name in sys.modules
        ),
        "timing_ms_per_item_median": {},
        "accuracy_counts": {},
    }
    for name, function in [
        ("baseline_classification", baseline_classify),
        ("learned_classification", classify),
        ("complete_metadata", annotate),
    ]:
        timings = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            for row in rows:
                function(row)
            timings.append((time.perf_counter() - start) * 1000 / len(rows))
        result["timing_ms_per_item_median"][name] = statistics.median(timings)
    for name, samples in rows_by_split.items():
        result["accuracy_counts"][name] = {
            "profiles": len(samples),
            "baseline_correct": sum(
                baseline_classify(row) == row["label"] for row in samples
            ),
            "deployed_correct": sum(classify(row) == row["label"] for row in samples),
        }
    try:
        import resource

        result["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
            1024 if sys.platform != "darwin" else 1024 * 1024
        )
    except ImportError:
        pass
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf8")
    print(rendered)
    if changed or result["encoder_loaded"] or error > 1e-5:
        raise SystemExit("Media inference parity or isolation failed.")


if __name__ == "__main__":
    main()
