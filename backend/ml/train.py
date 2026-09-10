"""CPU-only reproducible training. Select on validation websites, test once later.

Run from backend: uv run --group ml python ml/train.py
No user data, raw HTML, network access, GPU, pickle or training service required.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tracker.link_model import FEATURES, VERSION, LinkModel


def metrics(rows, predictions):
    # Count each URL once, accepting it when any of its anchors is accepted.
    grouped = {}
    for row, prediction in zip(rows, predictions, strict=True):
        key = (row["source_id"], row["url"])
        old = grouped.setdefault(key, [row["label"], False])
        old[1] |= bool(prediction)

    def score(pairs):
        tp = sum(y and p for y, p in pairs)
        fp = sum(not y and p for y, p in pairs)
        fn = sum(y and not p for y, p in pairs)
        precision, recall = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
        return dict(
            tp=tp,
            fp=fp,
            fn=fn,
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1=round(2 * precision * recall / max(precision + recall, 1e-12), 4),
        )

    result = score(list(grouped.values()))
    result["by_source"] = {
        source: score([v for (s, _), v in grouped.items() if s == source])
        for source in sorted({r["source_id"] for r in rows})
    }
    result["macro_f1"] = round(
        float(np.mean([r["f1"] for r in result["by_source"].values()])), 4
    )
    return result


def export(estimator, thresholds, model_id):
    if isinstance(estimator, LogisticRegression):
        layers = [
            dict(weights=estimator.coef_.tolist(), bias=estimator.intercept_.tolist())
        ]
    else:
        layers = [
            dict(weights=w.T.tolist(), bias=b.tolist())
            for w, b in zip(estimator.coefs_, estimator.intercepts_, strict=True)
        ]
    return dict(
        version=VERSION,
        features=list(FEATURES),
        model_id=model_id,
        thresholds=thresholds,
        layers=layers,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write candidate artifacts here instead of replacing the bundled model.",
    )
    output_dir = parser.parse_args().output_dir
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    dataset = ROOT / "ml/dataset.jsonl"
    # Deliberately do not use test rows during model selection.
    rows = [
        r
        for line in dataset.read_text().splitlines()
        if (r := json.loads(line))["split"] != "test"
    ]
    train = [r for r in rows if r["split"] == "train"]
    valid = [r for r in rows if r["split"] == "validation"]
    x, y = (
        np.array([r["features"] for r in train]),
        np.array([r["label"] for r in train]),
    )
    xv = np.array([r["features"] for r in valid])
    counts = Counter(r["source_id"] for r in train)
    weights = np.array(
        [len(train) / len(counts) / counts[r["source_id"]] for r in train]
    )
    candidates = [
        ("logistic", LogisticRegression(C=1, max_iter=1000, random_state=81)),
        (
            "mlp-8",
            MLPClassifier(
                hidden_layer_sizes=(8,),
                solver="lbfgs",
                alpha=1,
                max_iter=600,
                random_state=81,
            ),
        ),
        (
            "mlp-16",
            MLPClassifier(
                hidden_layer_sizes=(16,),
                solver="lbfgs",
                alpha=1,
                max_iter=600,
                random_state=81,
            ),
        ),
    ]
    results, fitted = [], {}
    for name, model in candidates:
        with (
            warnings.catch_warnings(record=True) as caught,
            threadpool_limits(limits=1),
        ):
            warnings.simplefilter("always", ConvergenceWarning)
            model.fit(x, y, sample_weight=weights)
        fitted[name] = model
        probabilities = model.predict_proba(xv)[:, 1]
        options = []
        for threshold in (0.5, 0.7, 0.85, 0.9, 0.95, 0.98):
            measured = metrics(valid, probabilities >= threshold)
            options.append(dict(threshold=threshold, **measured))
        selected = max(
            options,
            key=lambda m: (m["precision"] >= 0.97, m["macro_f1"], m["precision"]),
        )
        results.append(
            dict(
                name=name,
                iterations=int(
                    model.n_iter_
                    if isinstance(model.n_iter_, int)
                    else model.n_iter_[0]
                ),
                converged=not any(
                    issubclass(w.category, ConvergenceWarning) for w in caught
                ),
                selected=selected,
                thresholds=options,
            )
        )
    # A neural network is justified only by validation; break ties toward smaller.
    best = max(
        [r for r in results if r["converged"]],
        key=lambda r: (
            r["selected"]["precision"] >= 0.97,
            r["selected"]["macro_f1"],
            -sum(
                weights.size
                for weights in (
                    fitted[r["name"]].coefs_
                    if hasattr(fitted[r["name"]], "coefs_")
                    else [fitted[r["name"]].coef_]
                )
            ),
        ),
    )
    if best["selected"]["precision"] < 0.97:
        raise RuntimeError(
            "No converged model met validation precision; keeping the existing artifact"
        )
    model = fitted[best["name"]]
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    payload = export(
        model,
        {"reject": 0.03, "accept": max(0.85, best["selected"]["threshold"])},
        best["name"] + "-" + digest[:12],
    )
    runtime = LinkModel(payload)
    error = max(
        abs(runtime.score(row.tolist()) - probability)
        for row, probability in zip(xv, model.predict_proba(xv)[:, 1], strict=True)
    )
    assert error < 1e-10, "Export changed model predictions"
    path = (
        output_dir / "link-model.json"
        if output_dir
        else ROOT / "app/tracker/link-model.json"
    )
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    baseline = metrics(valid, [r["baseline"] for r in valid])
    report = dict(
        seed=81,
        training_rows=len(train),
        validation_rows=len(valid),
        feature_count=len(FEATURES),
        data_sha256=digest,
        chosen=best["name"],
        runtime_thresholds=payload["thresholds"],
        baseline_validation=baseline,
        candidates=results,
        export_max_error=error,
        model_bytes=path.stat().st_size,
        parameters=sum(
            len(b) + sum(len(w) for w in ws)
            for ws, b in [
                (layer["weights"], layer["bias"]) for layer in payload["layers"]
            ]
        ),
        training_seconds=round(time.perf_counter() - started, 3),
        test_used_for_selection=False,
    )
    (
        output_dir / "training-report.json"
        if output_dir
        else ROOT / "ml/training-report.json"
    ).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "candidates"}, indent=2))
    for r in results:
        print(r["name"], r["selected"])


if __name__ == "__main__":
    main()
