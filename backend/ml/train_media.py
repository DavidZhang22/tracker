"""Compare compact media classifiers with public domains held out of training."""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupKFold
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tracker.media_classifier import load, select
from app.tracker.media_features import vector
from app.tracker.media_metadata import annotate, evidence_text, sample_entries
from app.tracker.media_metadata import baseline_classify as classify
from app.tracker.media_metadata import classify as deployed_classify
from ml.build_media_curriculum import build


def data():
    original = json.loads((ROOT / "ml/datasets/media-profiles.json").read_text())
    public = json.loads((ROOT / "ml/datasets/media-public-profiles.json").read_text())
    # Nin's /music redirects to an article; it is not the intended inventory.
    public = [row for row in public if row["source_id"] != "media-nin"]
    return (
        original + [row for row in public if row["split"] == "train"],
        [row for row in public if row["split"] == "validation"],
        [row for row in public if row["split"] == "test"],
    )


def matrix(rows):
    return np.asarray(
        [
            vector(row, evidence_text(row), sample_entries(row["entries"]))
            for row in rows
        ]
    )


def metrics(rows, guesses):
    y = [row["label"] for row in rows]
    return dict(
        accuracy=round(accuracy_score(y, guesses), 4),
        macro_f1=round(f1_score(y, guesses, average="macro", zero_division=0), 4),
        errors=[
            {"source": row["source_id"], "expected": row["label"], "actual": str(guess)}
            for row, guess in zip(rows, guesses, strict=True)
            if row["label"] != guess
        ],
    )


def model(name):
    if name.startswith("linear"):
        return LogisticRegression(
            C=float(name.split(":")[1]), max_iter=2000, class_weight="balanced"
        )
    width, alpha = map(int, name.split(":")[1:])
    return MLPClassifier(
        hidden_layer_sizes=(width,),
        solver="lbfgs",
        alpha=alpha,
        max_iter=1500,
        random_state=211,
        tol=1e-5,
    )


def fit(name, rows):
    authored = build()
    samples = rows + authored
    weights = [1.0] * len(rows) + [0.25] * len(authored)
    value = model(name)
    value.fit(
        matrix(samples),
        [row["label"] for row in samples],
        sample_weight=np.asarray(weights),
    )
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compare", action="store_true", help="Repeat all six development challengers."
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Write the selected model; never train on validation or holdout.",
    )
    args = parser.parse_args()
    development, validation, test = data()
    final = json.loads((ROOT / "ml/datasets/media-final-profiles.json").read_text())
    groups = [
        ".".join((urlsplit(row["url"]).hostname or "").split(".")[-2:])
        for row in development
    ]
    report = dict(
        development=len(development),
        validation=len(validation),
        test=len(test),
        authored=len(build()),
        classes=dict(Counter(row["label"] for row in development + validation + test)),
        baseline=dict(
            development=metrics(development, [classify(row) for row in development]),
            validation=metrics(validation, [classify(row) for row in validation]),
        ),
        candidates={},
    )
    names = [
        "linear:0.2",
        "linear:1",
        "linear:5",
        "neural:32:1",
        "neural:64:1",
        "neural:64:5",
    ]
    if not args.compare:
        names = ["linear:1"]
    selected_guesses = np.empty(len(development), dtype=object)
    for name in names:
        guesses = np.empty(len(development), dtype=object)
        for train_indices, held_indices in GroupKFold(5).split(
            development, groups=groups
        ):
            value = fit(name, [development[i] for i in train_indices])
            guesses[held_indices] = value.predict(
                matrix([development[i] for i in held_indices])
            )
            if name == "linear:1":
                for index in held_indices:
                    row = development[index]
                    selected_guesses[index] = select(
                        matrix([row])[0],
                        classify(row),
                        row["kind"],
                        (
                            tuple(value.classes_),
                            value.coef_,
                            value.intercept_,
                            0.35,
                            0.12,
                        ),
                    )
        value = fit(name, development)
        candidate = dict(
            grouped_cross_validation=metrics(development, guesses),
            validation=metrics(validation, value.predict(matrix(validation))),
        )
        report["candidates"][name] = candidate
        print(name, json.dumps(candidate), flush=True)
    selected = fit("linear:1", development)
    model_path = ROOT / "app/tracker/media_classifier.json"
    if args.export:
        artifact = dict(
            version=1,
            feature_count=selected.coef_.shape[1],
            classes=selected.classes_.tolist(),
            weights=selected.coef_.round(9).tolist(),
            bias=selected.intercept_.round(9).tolist(),
            minimum_probability=0.35,
            minimum_margin=0.12,
            training=dict(
                public_profiles=len(development),
                authored_profiles=len(build()),
                algorithm="L2 multinomial logistic regression",
                regularization_C=1,
                public_sources_only=True,
            ),
        )
        model_path.write_text(
            json.dumps(artifact, separators=(",", ":")) + "\n", encoding="utf8"
        )
        load.cache_clear()
    report["selected"] = {
        "domain_grouped_development": metrics(development, selected_guesses)
    }
    for name, rows in [
        ("validation", validation),
        ("initial_source_holdout", test),
        ("final_fresh_source_holdout", final),
    ]:
        report["selected"][name] = {
            "baseline": metrics(rows, [classify(row) for row in rows]),
            "deployed": metrics(rows, [deployed_classify(row) for row in rows]),
            "profiles": len(rows),
        }
    corpus = development + validation + test + final + build()
    expected = selected.predict_proba(matrix(corpus))
    classes, weights, bias, minimum, margin = load()
    scores = matrix(corpus).astype(np.float32) @ weights.T + bias
    actual = np.exp(scores - scores.max(axis=1, keepdims=True))
    actual /= actual.sum(axis=1, keepdims=True)
    assert classes == tuple(selected.classes_)
    report["runtime"] = {
        "numeric_parameters": int(weights.size + bias.size),
        "numeric_bytes": weights.nbytes + bias.nbytes,
        "artifact_bytes": model_path.stat().st_size,
        "float32_probability_max_error": float(np.max(np.abs(expected - actual))),
    }
    for name, function in [
        ("baseline_classification", classify),
        ("learned_classification", deployed_classify),
        ("complete_metadata", annotate),
    ]:
        timings = []
        for _ in range(7):
            start = time.perf_counter()
            for row in development + validation + test + final:
                function(row)
            timings.append(
                (time.perf_counter() - start)
                * 1000
                / (len(development) + len(validation) + len(test) + len(final))
            )
        report["runtime"][name + "_ms_per_item_median"] = float(np.median(timings))
    report["limitations"] = (
        "Development folds informed feature and threshold selection. Initial held-out sources were inspected for labels before feature work; no claim of blindness. Five final sources were collected after candidate freeze. Small English-heavy samples and partially synthetic training limit generalization; acquisition failures remain separate from classification. Neural candidates are evaluated only, not shipped."
    )
    target = ROOT / "ml/reports/media-strength.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"selected": report["selected"], "runtime": report["runtime"]}, indent=2
        )
    )


if __name__ == "__main__":
    with threadpool_limits(limits=1):
        main()
