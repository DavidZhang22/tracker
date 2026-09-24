"""Fixed small-model/augmentation comparison; never promotes a runtime model."""

import argparse
import hashlib
import json
import math
import statistics
import time
import tracemalloc
import warnings
from collections import Counter, defaultdict
from pathlib import Path

from app.tracker import record_context
from app.tracker.record_context import FEATURES, load_record_model, predict
from ml import artifacts
from ml.context_expansion.data import document_hash, expanded, load_records
from ml.context_expansion.evaluate import candidate_rows, evaluate, family_weights

THRESHOLDS = (0.5, 0.65, 0.8, 0.9)
CANDIDATES = ("mlp32", "trees40")


def legacy_foundation():
    # Exact generated-page duplicates across historical splits must not train
    # the new candidates. Historical evaluations remain diagnostic only.
    from ml.train_record_context import pages

    groups = defaultdict(list)
    for source, split, soup in pages():
        groups[document_hash(str(soup))].append((source, split))
    leaked = {
        source
        for values in groups.values()
        if len({split for _, split in values}) > 1
        for source, split in values
        if split == "train"
    }
    path = Path(__file__).parents[1] / "datasets/record-context-dataset.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf8").splitlines()
        if line
    ]
    for row in rows:
        row["family"] = (
            row["source"].rsplit("-", 1)[0]
            if row["source"].startswith(("layout-", "paired-"))
            else row["source"].split(":")[0]
        )
        row["family"] = "legacy:" + row["family"]
    training = [r for r in rows if r["split"] == "train" and r["source"] not in leaked]
    return (
        training,
        [r for r in rows if r["split"] == "validation"],
        [r for r in rows if r["split"] == "test"],
        {
            "legacy_dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "cross_split_duplicate_document_groups": sum(
                len({split for _, split in values}) > 1 for values in groups.values()
            ),
            "excluded_training_pages": len(leaked),
            "retained_training_candidates": len(training),
        },
    )


def grouped_foundation():
    """Give each exact generated document one deterministic synthetic split."""
    from ml.train_record_context import pages

    groups = defaultdict(list)
    for source, _, soup in pages():
        groups[document_hash(str(soup))].append(source)
    assigned = {}
    by_topology = defaultdict(list)
    for digest, sources in groups.items():
        by_topology["paired" if sources[0].startswith("paired-") else "nested"].append(
            (digest, sorted(sources)[0])
        )
    topology_counts = {}
    for topology, values in by_topology.items():
        counts = Counter()
        for index, (digest, source) in enumerate(sorted(values)):
            slot = index % 20
            split = "train" if slot < 14 else "validation" if slot < 17 else "test"
            assigned[source] = split, digest
            counts[split] += 1
        topology_counts[topology] = dict(counts)
    path = Path(__file__).parents[1] / "datasets/record-context-dataset.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf8").splitlines()
        if line
    ]
    retained = []
    for row in rows:
        source = row["source"]
        if source.startswith(("layout-", "paired-")):
            if source not in assigned:
                continue
            row["split"], row["document_group"] = assigned[source]
        row["family"] = "legacy:" + (
            source.rsplit("-", 1)[0]
            if source.startswith(("layout-", "paired-"))
            else source.split(":")[0]
        )
        retained.append(row)
    subsets = [
        [r for r in retained if r["split"] == split]
        for split in ("train", "validation", "test")
    ]
    return *subsets, {
        "legacy_dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "original_authored_pages": sum(len(v) for v in groups.values()),
        "unique_authored_documents": len(groups),
        "removed_duplicate_pages": sum(len(v) - 1 for v in groups.values()),
        "synthetic_partition": "SHA256-sorted exact HTML groups; 14/3/3 slots per 20 within paired and nested strata; no shared exact documents; generator templates can remain similar across splits.",
        "topology_split_counts": topology_counts,
        "retained_candidate_rows": dict(
            zip(("train", "validation", "test"), map(len, subsets), strict=True)
        ),
    }


def collapse_rows(rows):
    weights = family_weights(rows)
    unique = {}
    for row, weight in zip(rows, weights, strict=True):
        key = (tuple(row["features"]), row["label"])
        unique[key] = unique.get(key, 0) + weight
    return (
        [features for features, _ in unique],
        [label for _, label in unique],
        list(unique.values()),
    )


def export(model, name, threshold, dataset_hash):
    import numpy as np

    payload = {
        "version": 1,
        "features": list(FEATURES),
        "threshold": threshold,
        "model_id": f"context-expansion-{name}-{dataset_hash[:12]}",
    }
    if hasattr(model, "coefs_"):
        payload["layers"] = [
            {"weights": np.round(w.T, 7).tolist(), "bias": np.round(b, 7).tolist()}
            for w, b in zip(model.coefs_, model.intercepts_, strict=True)
        ]
    else:
        payload.update(
            intercept=float(
                model._raw_predict_init(np.zeros((1, len(FEATURES))))[0, 0]
            ),
            trees=[],
        )
        for estimator in model.estimators_[:, 0]:
            tree = estimator.tree_
            payload["trees"].append(
                [
                    [
                        int(tree.feature[i]),
                        float(tree.threshold[i]),
                        int(tree.children_left[i]),
                        int(tree.children_right[i]),
                        round(float(tree.value[i, 0, 0] * model.learning_rate), 8),
                    ]
                    for i in range(tree.node_count)
                ]
            )
    return payload


def rank(real, legacy):
    summary = real["summary"]
    return (
        -summary["neighbor_leakage"],
        summary["correct_records"],
        legacy["record_accuracy"],
        summary["required_fact_recall"],
    )


def inference_profile(model, features):
    sample = features[:512]
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        for row in sample:
            predict(model, row)
        timings.append(time.perf_counter() - start)
    encoded = json.dumps(model, separators=(",", ":")).encode()
    tracemalloc.start()
    decoded = json.loads(encoded)
    allocated, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert decoded["model_id"] == model["model_id"]
    return {
        "candidate_rows": len(sample),
        "median_seconds": statistics.median(timings),
        "model_json_bytes": len(encoded),
        "model_allocated_bytes": allocated,
        "model_load_peak_bytes": peak,
    }


def run(dataset, directory, protocol_version="grouped-v2"):
    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.neural_network import MLPClassifier

    from ml.train_record_context import evaluate as legacy_evaluate

    started = time.perf_counter()
    records = load_records(dataset)
    development = [r for r in records if r["split"] == "development"]
    validation = [r for r in records if r["split"] == "validation"]
    heldout = [r for r in records if r["split"] == "heldout"]
    if not development or not validation or not heldout:
        raise ValueError(
            "Source-family development, validation, and heldout splits are required"
        )
    if artifacts.exists(directory / "protocol.json"):
        raise FileExistsError(
            "Choose a new experiment directory to preserve the frozen comparison"
        )
    directory.mkdir(parents=True, exist_ok=True)
    foundation, old_validation, old_test, audit = (
        grouped_foundation()
        if protocol_version == "grouped-v2"
        else legacy_foundation()
    )
    baseline = load_record_model()
    baseline_validation = legacy_evaluate(
        old_validation,
        np.array([predict(baseline, r["features"]) for r in old_validation]),
        baseline["threshold"],
    )
    minimum_accuracy = baseline_validation["record_accuracy"] - 0.01

    def selection_rank(value):
        guarded = (
            value["legacy"]["record_accuracy"] >= minimum_accuracy
            if protocol_version == "grouped-v2"
            else True
        )
        return (guarded, *rank(value["fresh"], value["legacy"]))

    dataset_hash = hashlib.sha256(Path(dataset).read_bytes()).hexdigest()
    protocol = {
        "version": protocol_version,
        "regression_guard": {
            "baseline_validation": baseline_validation,
            "minimum_validation_record_accuracy": minimum_accuracy,
        }
        if protocol_version == "grouped-v2"
        else None,
        "dataset_sha256": dataset_hash,
        "record_model_sha256": hashlib.sha256(
            record_context.MODEL_PATH.read_bytes()
        ).hexdigest(),
        "extractor_sha256": hashlib.sha256(
            Path(record_context.__file__).read_bytes()
        ).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "candidates": CANDIDATES,
        "thresholds": THRESHOLDS,
        "selection": "validation neighbor leakage, correct records, historical validation record accuracy, fact recall",
        "augmentation": "development only; family/original-anchor balanced; duplicate feature rows weighted and collapsed",
        "heldout": "Source-family-disjoint diagnostic set; baseline failures were inspected before this comparison. Not blind.",
        **audit,
    }
    artifacts.write_text(
        directory / "protocol.json", json.dumps(protocol, indent=2) + "\n"
    )
    outcomes, models = [], {}
    for condition, sources in (
        ("real", development),
        ("augmented", expanded(development)),
    ):
        rows = foundation + [
            row for record in sources for row in candidate_rows(record)
        ]
        features, labels, weights = collapse_rows(rows)
        x = np.array(features, dtype=np.float32)
        y = np.array(labels)
        for name in CANDIDATES:
            key = condition + "-" + name
            model = (
                MLPClassifier(
                    hidden_layer_sizes=(32,),
                    alpha=1,
                    max_iter=300,
                    solver="lbfgs",
                    random_state=81,
                )
                if name == "mlp32"
                else GradientBoostingClassifier(
                    n_estimators=40, max_depth=3, min_samples_leaf=8, random_state=81
                )
            )
            start = time.perf_counter()
            with warnings.catch_warnings(record=True) as fit_warnings:
                warnings.simplefilter("always")
                model.fit(x, y, sample_weight=np.array(weights))
            fit_seconds = time.perf_counter() - start
            candidates = []
            payload = export(model, key, 0.5, dataset_hash)
            check_features = x
            native = np.array([predict(payload, row) for row in check_features])
            error = float(
                np.max(abs(native - model.predict_proba(check_features)[:, 1]))
            )
            if not math.isfinite(error) or error >= 1e-5:
                raise ValueError(f"Exported probability mismatch: {key}: {error}")
            for threshold in THRESHOLDS:
                payload["threshold"] = threshold
                fresh = evaluate(validation, payload, pipeline=False)
                old = legacy_evaluate(
                    old_validation,
                    np.array([predict(payload, r["features"]) for r in old_validation]),
                    threshold,
                )
                candidates.append(
                    {"threshold": threshold, "fresh": fresh, "legacy": old}
                )
            choice = max(candidates, key=selection_rank)
            payload["threshold"] = choice["threshold"]
            models[key] = payload
            path = directory / (key + ".json")
            artifacts.write_text(
                path, json.dumps(payload, separators=(",", ":")) + "\n"
            )
            outcomes.append(
                {
                    "name": key,
                    "validation": choice,
                    "training_candidates": len(rows),
                    "unique_weighted_candidates": len(features),
                    "fit_seconds": fit_seconds,
                    "optimizer_iterations": int(model.n_iter_)
                    if hasattr(model, "n_iter_")
                    else None,
                    "fit_warnings": [str(w.message) for w in fit_warnings],
                    "export_probability_max_error": error,
                }
            )
            print(
                json.dumps(
                    {
                        "name": key,
                        "validation": choice["fresh"]["summary"],
                        "legacy": choice["legacy"],
                        "fit_seconds": fit_seconds,
                    }
                ),
                flush=True,
            )
    choice = max(
        outcomes,
        key=lambda value: selection_rank(value["validation"]),
    )
    chosen = models[choice["name"]]
    probe_features = [r["features"] for r in old_validation]
    report = {
        "protocol": protocol,
        "candidates": outcomes,
        "selected_by_validation": choice["name"],
        "passes_validation_regression_guard": choice["validation"]["legacy"][
            "record_accuracy"
        ]
        >= minimum_accuracy,
        "fresh_diagnostic": {
            "baseline": evaluate(heldout, baseline, pipeline=False),
            "candidate": evaluate(heldout, chosen, pipeline=False),
        },
        "legacy_test": {
            name: legacy_evaluate(
                old_test,
                np.array([predict(model, r["features"]) for r in old_test]),
                model["threshold"],
            )
            for name, model in (("baseline", baseline), ("candidate", chosen))
        },
        "inference_profile": {
            name: inference_profile(model, probe_features)
            for name, model in (("baseline", baseline), ("candidate", chosen))
        },
        "seconds": time.perf_counter() - started,
        "promotion": "No automatic promotion. Review leakage, regression, and full-page replay before changing runtime.",
    }
    if protocol_version == "grouped-v2":
        _, _, compatibility, _ = legacy_foundation()
        report["old_test_compatibility"] = {
            "note": "Original 417 anchors are not independent after synthetic groups were reassigned; compatibility diagnostic only.",
            **{
                name: legacy_evaluate(
                    compatibility,
                    np.array([predict(model, r["features"]) for r in compatibility]),
                    model["threshold"],
                )
                for name, model in (("baseline", baseline), ("candidate", chosen))
            },
        }
    artifacts.write_text(directory / "report.json", json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--protocol", choices=("legacy-v1", "grouped-v2"), default="grouped-v2"
    )
    args = parser.parse_args()
    report = run(args.dataset, args.directory, args.protocol)
    print(
        json.dumps(
            {
                "selected_by_validation": report["selected_by_validation"],
                "seconds": report["seconds"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
