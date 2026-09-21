"""Independent validation-only group routing parity and local inference timing."""

import argparse
import gc
import hashlib
import json
import platform
import statistics
import sys
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from train_breadth_generalization import explicit_chrome, freeze_protocol, prepare
from train_group_generalization import group_key, group_support

from app.tracker.cascade_model import CascadeModel
from app.tracker.context_model import ContextModel
from app.tracker.model_groups import group_key as runtime_group_key
from app.tracker.native_model import kernel


def timing(call, repeats=5):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        call()
        samples.append(time.perf_counter() - started)
    return dict(median_seconds=statistics.median(samples), samples_seconds=samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path(__file__).resolve().parent / "chosen-group-refinement.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "runtime-parity-benchmark.json",
    )
    args = parser.parse_args()
    first = ROOT / "ml/experiments/breadth-generalization"
    breadth, _ = freeze_protocol(first)
    groups, _ = prepare(breadth)
    rows = groups["breadth", "validation"] + [
        r for r in groups["historical", "validation"] if r["origin"] == "index"
    ]
    del groups, breadth
    gc.collect()
    artifact = args.candidate
    payload = json.loads(artifact.read_text(encoding="utf8"))
    policy = payload["refinement_policy"]
    baseline_payload = {
        k: v for k, v in payload.items() if k not in {"refinement", "refinement_policy"}
    }
    baseline = CascadeModel(baseline_payload)
    model = CascadeModel(payload)
    expert = ContextModel(payload["refinement"])
    base = np.array(baseline.score_many(rows))
    deep = np.array(expert.score_many(rows))
    chrome = explicit_chrome(rows)
    assert all(group_key(row) == runtime_group_key(row) for row in rows)
    warm, cold = group_support(
        rows,
        base,
        deep,
        chrome,
        seed_threshold=policy["seed_threshold"],
        cold_threshold=policy["cold_threshold"],
    )
    supported = warm | (
        policy["cold_group"] & cold & (deep >= policy["cold_threshold"])
    )
    expected = base.copy()
    rescue = (base < 0.5) & ~chrome & supported & (deep >= expert.upper)
    reject = (base >= 0.5) & chrome & (deep < expert.lower)
    expected[rescue] = 0.5 + (deep[rescue] - expert.upper) / (2 * (1 - expert.upper))
    expected[reject] = deep[reject] / (2 * expert.upper)
    actual = np.array(model.score_many(rows))
    max_error = float(np.max(abs(expected - actual)))
    mismatches = int(sum((expected >= 0.5) != (actual >= 0.5)))
    assert max_error < 1e-12 and mismatches == 0, (max_error, mismatches)
    sample = sorted(
        set(
            np.flatnonzero(rescue).tolist()[:64]
            + np.flatnonzero(reject).tolist()[:64]
            + list(range(64))
        )
    )
    native_error = max(
        abs(expert.score(rows[i]["features"], rows[i]["tokens"]) - deep[i])
        for i in sample
    )
    assert native_error < 1e-12, native_error
    pages = defaultdict(list)
    for row in rows:
        pages[row.get("source", row.get("source_id", ""))].append(row)
    per_page = [model.score_many(page) for page in pages.values()]
    offset_lookup = {
        id(row): float(value)
        for page, scores in zip(pages.values(), per_page, strict=True)
        for row, value in zip(page, scores, strict=True)
    }
    page_error = max(
        abs(offset_lookup[id(row)] - actual[i]) for i, row in enumerate(rows)
    )
    assert page_error < 1e-12, page_error
    baseline_timing = timing(lambda: baseline.score_many(rows))
    candidate_timing = timing(lambda: model.score_many(rows))
    largest = max(pages.values(), key=len)
    largest_baseline = timing(lambda: baseline.score_many(largest))
    largest_candidate = timing(lambda: model.score_many(largest))
    tracemalloc.start()
    model.score_many(largest)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report = dict(
        candidate_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        rows=len(rows),
        pages=len(pages),
        heldout_rows=0,
        native_kernel=bool(kernel()),
        platform=platform.platform(),
        max_score_error=max_error,
        classification_mismatches=mismatches,
        group_key_mismatches=0,
        partitioned_page_max_error=page_error,
        expert_native_python_max_error=native_error,
        expert_scalar_rows=len(sample),
        rescued_anchors=int(sum(rescue)),
        rejected_anchors=int(sum(reject)),
        baseline=baseline_timing,
        candidate=candidate_timing,
        largest_page=dict(
            rows=len(largest),
            baseline=largest_baseline,
            candidate=largest_candidate,
            traced_python_peak_bytes=peak,
        ),
        limitations="Local developer-machine inference timings, with resident feature rows and warmed kernels. Concurrent work may affect timing. Tracemalloc excludes some native allocations; imported training libraries make total process RSS unsuitable as production-memory evidence. This evaluates classifier inference only, not HTML parsing or network latency. Scalar group inference intentionally differs from whole-page inference.",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
