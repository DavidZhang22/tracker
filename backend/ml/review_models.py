"""Source-balanced model review. Fit/select on development data, export candidates only."""

import argparse
import hashlib
import json
import os
import sys
import time
import warnings
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    os.environ.get("TRACKER_ML_APP_ROOT", str(ROOT)),
    str(ROOT),
    str(ROOT / "ml"),
]
from app.tracker.cascade_model import CascadeModel, decision_score, load_cascade_model
from app.tracker.context_model import NUMERIC_FEATURES, ContextModel, nuisance_context
from ml.artifacts import read_json, write_text
from ml.evaluation import metrics


class RefinementCandidate(CascadeModel):
    """Offline challenger only; withheld when full-page regression gates fail."""

    def __init__(self, data):
        base = {
            k: v
            for k, v in data.items()
            if k not in {"refinement", "refinement_policy"}
        }
        super().__init__(base)
        self.review_refinement = ContextModel(data["refinement"], allow_fallback=False)

    def score_many(self, rows):
        result = super().score_many(rows)
        indices = [
            i
            for i, score in enumerate(result)
            if score < self.upper or nuisance_context(rows[i]["features"])
        ]
        scores = self.review_refinement.score_many([rows[i] for i in indices])
        for i, score in zip(indices, scores, strict=True):
            if (result[i] < self.upper and score >= self.review_refinement.upper) or (
                result[i] >= self.upper and score < self.review_refinement.lower
            ):
                result[i] = decision_score(score, self.review_refinement.upper)
        return result


def quality(rows, predictions):
    result = metrics(rows, predictions)
    # Negative-only auxiliary pages measure false alarms, not positive recall.
    collection = [m for m in result["by_source"].values() if m["tp"] + m["fn"]]
    result["collection_macro_f1"] = round(
        sum(m["f1"] for m in collection) / max(1, len(collection)), 4
    )
    negatives = [p for r, p in zip(rows, predictions, strict=True) if not r["label"]]
    result["negative_anchor_error_rate"] = round(
        sum(negatives) / max(1, len(negatives)), 4
    )
    public_sources = {r["source_id"] for r in rows if r["origin"] == "index"}
    public = [
        m
        for s, m in result["by_source"].items()
        if s in public_sources and m["tp"] + m["fn"]
    ]
    result["public_collection_macro_f1"] = round(
        sum(m["f1"] for m in public) / max(1, len(public)), 4
    )
    return result


def weights(rows):
    counts = Counter((r["origin"], r["source_id"], r["label"]) for r in rows)
    sources = {
        origin: {r["source_id"] for r in rows if r["origin"] == origin}
        for origin in {r["origin"] for r in rows}
    }
    shares = {"index": 0.85, "authored": 0.10, "cleaneval": 0.05}
    result = []
    for row in rows:
        origin, source, label = row["origin"], row["source_id"], row["label"]
        classes = sum((origin, source, c) in counts for c in (0, 1))
        result.append(
            shares[origin]
            / len(sources[origin])
            / classes
            / counts[origin, source, label]
        )
    result = np.asarray(result)
    return result * len(rows) / result.sum()


def export_tree(model, identity):
    payload = dict(
        version=2,
        model_id=identity,
        features=list(NUMERIC_FEATURES),
        vocabulary=[],
        idf=[],
        layers=[],
        threshold=0.5,
        intercept=float(
            model._raw_predict_init(np.zeros((1, len(NUMERIC_FEATURES))))[0, 0]
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
                    float(tree.value[i, 0, 0] * model.learning_rate),
                ]
                for i in range(tree.node_count)
            ]
        )
    return payload


def main():
    from train_cascade import export as export_neural

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra", type=Path)
    parser.add_argument(
        "--refinement",
        choices=("trees80x3", "trees120x4", "trees180x4", "neural64x32"),
        default="trees80x3",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "ml/experiments/review")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    paths = [
        ROOT / "ml/datasets" / name
        for name in (
            "v3-dataset.jsonl",
            "generalization.jsonl",
            "cascade-dataset.jsonl",
        )
    ]
    if args.extra:
        paths.append(args.extra)
    rows = [
        json.loads(line)
        for p in paths
        for line in p.read_text(encoding="utf8").splitlines()
    ]
    # Do not even score the test partition during architecture/threshold selection.
    train = [r for r in rows if r["split"] == "train"]
    valid = [r for r in rows if r["split"] == "validation"]

    # Identical examples cannot gain weight merely by being repeated in a file.
    def deduplicate(group):
        unique = {}
        for row in group:
            key = json.dumps(
                [row["source"], row["url"], row["features"], row["tokens"]],
                sort_keys=True,
            )
            if key in unique and unique[key]["label"] != row["label"]:
                raise ValueError("Conflicting labels for one feature row")
            unique[key] = row
        return list(unique.values())

    train, valid = deduplicate(train), deduplicate(valid)
    public_train = {
        urlsplit(r["source"]).hostname.removeprefix("www.")
        for r in train
        if r["origin"] == "index"
    }
    public_valid = {
        urlsplit(r["source"]).hostname.removeprefix("www.")
        for r in valid
        if r["origin"] == "index"
    }
    if public_train & public_valid:
        raise ValueError("A public source occurs in both training and validation")
    x = np.asarray([r["features"] for r in train], dtype=np.float32)
    xv = np.asarray([r["features"] for r in valid], dtype=np.float32)
    y = np.asarray([r["label"] for r in train])
    report = dict(
        training_rows=len(train),
        validation_rows=len(valid),
        hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        candidates={},
    )
    baseline = load_cascade_model()
    report["baseline"] = quality(
        valid, [p >= baseline.upper for p in baseline.score_many(valid)]
    )
    specs = [
        (
            "trees80x3",
            GradientBoostingClassifier(
                n_estimators=80, max_depth=3, min_samples_leaf=12, random_state=81
            ),
        ),
        (
            "trees120x4",
            GradientBoostingClassifier(
                n_estimators=120, max_depth=4, min_samples_leaf=12, random_state=81
            ),
        ),
        (
            "trees180x4",
            GradientBoostingClassifier(
                n_estimators=180, max_depth=4, min_samples_leaf=12, random_state=81
            ),
        ),
        (
            "neural64x32",
            MLPClassifier(
                hidden_layer_sizes=(64, 32),
                solver="lbfgs",
                alpha=3,
                max_iter=450,
                max_fun=40000,
                random_state=81,
            ),
        ),
    ]
    digest = hashlib.sha256(
        json.dumps(report["hashes"], sort_keys=True).encode()
    ).hexdigest()[:12]
    for name, estimator in specs:
        start = time.perf_counter()
        with (
            threadpool_limits(limits=1),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always", ConvergenceWarning)
            estimator.fit(x, y, sample_weight=weights(train))
        payload = (
            export_tree(estimator, "review-" + name + "-" + digest)
            if name.startswith("trees")
            else export_neural(
                estimator, "review-" + name + "-" + digest, [], [], "numeric"
            )
        )
        runtime = ContextModel(payload)
        probabilities = runtime.score_many(valid)
        parity = float(
            np.max(abs(np.asarray(probabilities) - estimator.predict_proba(xv)[:, 1]))
        )
        assert parity < 1e-6, parity
        options = [
            dict(threshold=t, **quality(valid, [p >= t for p in probabilities]))
            for t in (0.15, 0.25, 0.35, 0.5, 0.65, 0.8, 0.9)
        ]
        selected = max(
            options,
            key=lambda m: (
                m["precision"] >= 0.98,
                m["collection_macro_f1"],
                m["recall"],
            ),
        )
        payload["threshold"] = selected["threshold"]
        write_text(
            args.output / (name + ".json"),
            json.dumps(payload, separators=(",", ":")) + "\n",
        )
        report["candidates"][name] = dict(
            seconds=time.perf_counter() - start,
            parity=parity,
            converged=not any(
                issubclass(w.category, ConvergenceWarning) for w in caught
            ),
            selected=selected,
            thresholds=[
                {k: v for k, v in option.items() if k != "by_source"}
                for option in options
            ],
        )
        print(name, {k: v for k, v in selected.items() if k != "by_source"}, flush=True)
    payload = json.loads((ROOT / "app/tracker/link-cascade-model.json").read_text())
    payload["refinement"] = read_json(args.output / (args.refinement + ".json"))
    payload["refinement"].update(threshold=0.9, reject_threshold=0.1)
    payload["model_id"] = (
        "cascade-refined-v2-"
        + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    )
    candidate = RefinementCandidate(payload)
    report["refinement_validation"] = quality(
        valid, [p >= candidate.upper for p in candidate.score_many(valid)]
    )
    report["deployment"] = (
        "Research artifact only. Requires complete-page holdout and runtime gates; never auto-promoted."
    )
    write_text(
        args.output / "cascade.json", json.dumps(payload, separators=(",", ":")) + "\n"
    )
    write_text(args.output / "report.json", json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
