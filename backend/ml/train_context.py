"""Train and compare pruned sparse text + record-context models on site splits."""

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ml"))
from train import metrics

from app.tracker.context_model import NUMERIC_FEATURES, ContextModel


def vectorize(rows, vocabulary, idf):
    mapping = {word: i for i, word in enumerate(vocabulary)}
    values = np.zeros(
        (len(rows), len(NUMERIC_FEATURES) + len(vocabulary)), dtype=np.float32
    )
    for i, row in enumerate(rows):
        values[i, : len(NUMERIC_FEATURES)] = row["features"]
        indices = [mapping[t] for t in set(row["tokens"]) if t in mapping]
        norm = math.sqrt(sum(idf[j] ** 2 for j in indices)) or 1
        for j in indices:
            values[i, len(NUMERIC_FEATURES) + j] = idf[j] / norm
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "ml/experiments/context"
    )
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "ml/datasets/v3-dataset.jsonl"
    )
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--fallback-model", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    path = args.dataset
    rows = [
        r
        for line in path.read_text(encoding="utf-8").splitlines()
        if (r := json.loads(line))["split"] != "test"
    ]
    train = [r for r in rows if r["split"] == "train"]
    valid = [r for r in rows if r["split"] == "validation"]
    df, sites = Counter(), defaultdict(set)
    for row in train:
        for word in set(row["tokens"]):
            df[word] += 1
            sites[word].add(row["source_id"])
    vocab = sorted(
        (
            word
            for word, n in df.items()
            if n >= 5 and len(sites[word]) >= 2 and n < len(train) * 0.9
        ),
        key=lambda w: (-df[w], w),
    )[:512]
    idf = [math.log((1 + len(train)) / (1 + df[word])) + 1 for word in vocab]
    x, xv = vectorize(train, vocab, idf), vectorize(valid, vocab, idf)
    y = np.array([r["label"] for r in train])
    index_sites = {r["source_id"] for r in train if r["origin"] == "index"}
    corpus_sites = {r["source_id"] for r in train if r["origin"] == "cleaneval"}
    authored_sites = {r["source_id"] for r in train if r["origin"] == "authored"}
    classes = Counter((r["source_id"], r["label"]) for r in train)

    def weight(r):
        share = (
            0.75 / len(index_sites)
            if r["origin"] == "index"
            else 0.10 / len(corpus_sites)
            if r["origin"] == "cleaneval"
            else 0.15 / len(authored_sites)
        )
        # Balance labels within each index so small headline sets still matter.
        nclasses = sum((r["source_id"], c) in classes for c in (0, 1))
        return len(train) * share / nclasses / classes[r["source_id"], r["label"]]

    weights = np.array([weight(r) for r in train], dtype=np.float32)
    models = [
        ("linear", LogisticRegression(C=2, max_iter=800, random_state=81)),
        (
            "mlp32",
            MLPClassifier(
                hidden_layer_sizes=(32,),
                alpha=2,
                solver="lbfgs",
                max_iter=400,
                random_state=81,
            ),
        ),
        (
            "mlp64x16",
            MLPClassifier(
                hidden_layer_sizes=(64, 16),
                alpha=2,
                solver="lbfgs",
                max_iter=400,
                random_state=81,
            ),
        ),
        (
            "trees-numeric",
            GradientBoostingClassifier(
                n_estimators=120, max_depth=4, min_samples_leaf=10, random_state=81
            ),
        ),
        (
            "trees-text",
            GradientBoostingClassifier(
                n_estimators=120, max_depth=4, min_samples_leaf=10, random_state=81
            ),
        ),
    ]
    results = []
    if args.expanded:
        models = [
            (
                "trees-numeric",
                GradientBoostingClassifier(
                    n_estimators=120, max_depth=4, min_samples_leaf=10, random_state=81
                ),
            ),
            (
                "trees180-numeric",
                GradientBoostingClassifier(
                    n_estimators=180, max_depth=5, min_samples_leaf=10, random_state=81
                ),
            ),
            (
                "trees240-numeric",
                GradientBoostingClassifier(
                    n_estimators=240, max_depth=5, min_samples_leaf=10, random_state=81
                ),
            ),
        ]
    trained = {}
    for name, model in models:
        step = time.perf_counter()
        xx, vv = (
            (x[:, : len(NUMERIC_FEATURES)], xv[:, : len(NUMERIC_FEATURES)])
            if name.endswith("-numeric")
            else (x, xv)
        )
        with threadpool_limits(limits=1):
            model.fit(xx, y, sample_weight=weights)
        trained[name] = model
        p = model.predict_proba(vv)[:, 1]
        options = [
            dict(threshold=threshold, **metrics(valid, p >= threshold))
            for threshold in (0.25, 0.35, 0.45, 0.5, 0.6, 0.7, 0.8)
        ]
        best = max(
            options, key=lambda r: (r["precision"] >= 0.95, r["macro_f1"], r["recall"])
        )
        result = dict(
            name=name,
            seconds=round(time.perf_counter() - step, 3),
            selected=best,
            thresholds=options,
        )
        results.append(result)
        print(name, json.dumps(best), flush=True)
    best = max(
        results,
        key=lambda r: (
            r["selected"]["precision"] >= 0.95,
            r["selected"]["macro_f1"],
            -next(i for i, m in enumerate(models) if m[0] == r["name"]),
        ),
    )
    model = trained[best["name"]]
    if isinstance(model, GradientBoostingClassifier):
        layers = []
    elif hasattr(model, "coefs_"):
        layers = [
            dict(
                weights=np.round(w.T.astype(float), 6).tolist(),
                bias=np.round(b.astype(float), 6).tolist(),
            )
            for w, b in zip(model.coefs_, model.intercepts_, strict=True)
        ]
    else:
        layers = [
            dict(
                weights=np.round(model.coef_, 6).tolist(),
                bias=np.round(model.intercept_, 6).tolist(),
            )
        ]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = dict(
        version=2,
        model_id="context-" + best["name"] + "-" + digest[:12],
        features=list(NUMERIC_FEATURES),
        threshold=best["selected"]["threshold"],
        vocabulary=vocab,
        idf=idf,
        layers=layers,
    )
    if isinstance(model, GradientBoostingClassifier):
        payload["trees"] = []
        payload["intercept"] = float(
            model._raw_predict_init(x[:1, : model.n_features_in_])[0, 0]
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
        if best["name"].endswith("-numeric"):
            payload["vocabulary"] = []
            payload["idf"] = []
    runtime = ContextModel(payload)
    actual = np.array([runtime.score(r["features"], r["tokens"]) for r in valid])
    error = float(
        np.max(abs(actual - model.predict_proba(xv[:, : model.n_features_in_])[:, 1]))
    )
    assert error < 0.0001, error
    if args.fallback_model:
        payload["fallback"] = json.loads(args.fallback_model.read_text(encoding="utf8"))
        payload["gate_feature"] = "job_table"
        payload["model_id"] = "context-tables-gated-" + digest[:12]
        runtime = ContextModel(payload)
        actual = np.array([runtime.score(r["features"], r["tokens"]) for r in valid])
    parameters = sum(
        len(layer["bias"]) + sum(map(len, layer["weights"])) for layer in layers
    )
    if "trees" in payload:
        parameters = 1 + sum(
            len(t) for t in payload["trees"]
        )  # Each node has one learned threshold or leaf score.
    report = dict(
        model_id=payload["model_id"],
        chosen=best["name"],
        seed=81,
        training_rows=len(train),
        validation_rows=len(valid),
        vocabulary_size=len(payload["vocabulary"]),
        candidate_vocabulary_size=len(vocab),
        numeric_features=len(NUMERIC_FEATURES),
        parameters=parameters,
        tree_count=len(payload.get("trees", [])),
        tree_nodes=sum(map(len, payload.get("trees", []))),
        export_error=error,
        validation=metrics(valid, actual >= payload["threshold"]),
        candidates=results,
        data_sha256=digest,
        seconds=round(time.perf_counter() - started, 3),
        origin_weights=dict(index=0.75, cleaneval=0.10, authored=0.15),
        promotion_ready=best["selected"]["precision"] >= 0.95
        and best["selected"]["recall"] >= 0.9,
    )
    model_path = args.output_dir / "link-context-model.json"
    model_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    report["model_bytes"] = model_path.stat().st_size
    (args.output_dir / "training-report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("candidates", "validation")},
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
