"""Offline text/structure baselines and calibrated light/deep cascade selection."""

import argparse
import hashlib
import json
import math
import os
import sys
import time
import warnings
from collections import Counter, defaultdict
from pathlib import Path

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from train import metrics

from app.tracker.cascade_model import CascadeModel
from app.tracker.context_model import NUMERIC_FEATURES, ContextModel
from app.tracker.link_context import model_tokens
from ml.artifacts import read_bytes, read_text, write_text


def page_weights(rows, *, training=False):
    counts = Counter(r["source_id"] for r in rows)
    if not training:
        return np.array(
            [len(rows) / len(counts) / counts[r["source_id"]] for r in rows]
        )
    sites = defaultdict(set)
    for row in rows:
        sites[row["origin"]].add(row["source_id"])
    shares = {"index": 0.8, "authored": 0.15, "cleaneval": 0.05}
    return np.array(
        [
            len(rows)
            * shares[r["origin"]]
            / len(sites[r["origin"]])
            / counts[r["source_id"]]
            for r in rows
        ]
    )


def vocabulary(rows, mode):
    if mode == "numeric":
        return [], []
    count, sources = Counter(), defaultdict(set)
    for row in rows:
        for word in model_tokens(row["tokens"], mode):
            count[word] += 1
            sources[word].add(row["source_id"])
    eligible = [
        w
        for w, n in count.items()
        if n >= 5 and len(sources[w]) >= 3 and n < 0.9 * len(rows)
    ]
    # Keep words and subwords represented; fit solely on training sources.
    words = sorted(
        (w for w in eligible if not w.startswith(("gt:", "gu:"))),
        key=lambda w: (-count[w], w),
    )[:384]
    grams = sorted(
        (w for w in eligible if w.startswith(("gt:", "gu:"))),
        key=lambda w: (-count[w], w),
    )[:384]
    selected = words + grams
    return selected, [math.log((1 + len(rows)) / (1 + count[w])) + 1 for w in selected]


def vectorize(rows, vocab, idf, mode):
    index = {w: i for i, w in enumerate(vocab)}
    x = np.zeros((len(rows), len(NUMERIC_FEATURES) + len(vocab)), dtype=np.float64)
    for i, row in enumerate(rows):
        x[i, : len(NUMERIC_FEATURES)] = row["features"]
        selected = [index[w] for w in model_tokens(row["tokens"], mode) if w in index]
        norm = math.sqrt(sum(idf[j] ** 2 for j in selected)) or 1
        for j in selected:
            x[i, len(NUMERIC_FEATURES) + j] = idf[j] / norm
    return x


def export(estimator, name, vocab, idf, mode):
    if hasattr(estimator, "coefs_"):
        layers = [
            dict(weights=w.T.tolist(), bias=b.tolist())
            for w, b in zip(estimator.coefs_, estimator.intercepts_, strict=True)
        ]
    else:
        layers = [
            dict(weights=estimator.coef_.tolist(), bias=estimator.intercept_.tolist())
        ]
    return dict(
        version=2,
        model_id=name,
        features=list(NUMERIC_FEATURES),
        threshold=0.5,
        vocabulary=vocab,
        idf=idf,
        token_mode="words" if mode == "numeric" else mode,
        layers=layers,
    )


def selection(rows, scores):
    mask = np.array([r["origin"] == "index" for r in rows])
    scores = scores[mask]
    rows = [r for r in rows if r["origin"] == "index"]
    choices = [
        dict(threshold=t, **metrics(rows, scores >= t))
        for t in (0.2, 0.35, 0.5, 0.65, 0.8, 0.9)
    ]
    return max(
        choices, key=lambda m: (m["precision"] >= 0.97, m["macro_f1"], m["recall"])
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "ml/experiments/cascade")
    parser.add_argument("--extra", type=Path)
    parser.add_argument("--structural", action="store_true")
    parser.add_argument("--policy", choices=("replace", "rescue"), default="replace")
    parser.add_argument(
        "--allow-weak-labels",
        action="store_true",
        help="Explicitly include reviewed weak-label experiment data.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    paths = [
        ROOT / "ml/datasets/v3-dataset.jsonl",
        ROOT / "ml/datasets/generalization.jsonl",
    ]
    if args.extra:
        paths.append(args.extra)
    rows = [json.loads(line) for path in paths for line in read_text(path).splitlines()]
    if not args.allow_weak_labels and any(
        r.get("label_quality", "").startswith("weak") for r in rows
    ):
        parser.error(
            "This corpus includes weak labels. Review them, then use --allow-weak-labels for an offline experiment."
        )
    # Source identity partitions calibration and threshold selection; never individual links.
    train = [r for r in rows if r["split"] == "train"]
    validation = [r for r in rows if r["split"] == "validation"]
    groups = sorted(
        {r["source_id"] for r in validation if r["origin"] == "index"},
        key=lambda s: hashlib.sha256(s.encode()).hexdigest(),
    )
    calibration_groups = set(groups[::3])
    calibration = [r for r in validation if r["source_id"] in calibration_groups]
    valid = [r for r in validation if r["source_id"] not in calibration_groups]
    assert calibration and valid and set(r["label"] for r in calibration) == {0, 1}
    report = {
        "training_rows": len(train),
        "calibration_groups": sorted(calibration_groups),
        "selection_groups": sorted({r["source_id"] for r in valid}),
        "dataset_hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
        },
        "candidates": {},
        "notes": "Existing test pages are regression holdouts; new extra test domains were excluded from fitting, calibration, and selection. Labels are scope annotations, not independent human gold.",
    }
    weights = page_weights(train, training=True)
    fitted = {}
    specs = [
        ("linear", "words", None),
        ("neural32", "words", (32,)),
        ("neural64x32", "subwords-v1", (64, 32)),
    ]
    teacher_payload = json.loads(
        (ROOT / "app/tracker/link-context-model.json").read_text(encoding="utf8")
    )
    teacher = ContextModel(teacher_payload)
    if args.structural:
        specs = [
            ("numeric32", "numeric", (32,)),
            ("numeric64x32", "numeric", (64, 32)),
            ("distilled64x32", "numeric", (64, 32)),
        ]
    for name, mode, hidden in specs:
        started = time.perf_counter()
        vocab, idf = vocabulary(train, mode)
        x = vectorize(train, vocab, idf, mode)
        y = np.array([r["label"] for r in train])
        estimator = (
            LogisticRegression(C=2, max_iter=800, random_state=81)
            if hidden is None
            else MLPClassifier(
                hidden_layer_sizes=hidden,
                solver="lbfgs",
                alpha=3,
                max_iter=350,
                max_fun=30000,
                random_state=81,
            )
        )
        with (
            warnings.catch_warnings(record=True) as caught,
            threadpool_limits(limits=1),
        ):
            warnings.simplefilter("always", ConvergenceWarning)
            if name.startswith("distilled"):
                target = 0.7 * y + 0.3 * np.array(teacher.score_many(train))
                estimator.fit(
                    np.concatenate((x, x)),
                    np.concatenate((np.ones(len(y)), np.zeros(len(y)))),
                    sample_weight=np.concatenate(
                        (weights * target, weights * (1 - target))
                    ),
                )
            else:
                estimator.fit(x, y, sample_weight=weights)
        payload = export(estimator, "cascade-research-" + name, vocab, idf, mode)
        raw = ContextModel(payload)
        xc = vectorize(calibration, vocab, idf, mode)
        probabilities = estimator.predict_proba(xc)[:, 1]
        logits = np.log(
            np.clip(probabilities, 1e-12, 1 - 1e-12)
            / (1 - np.clip(probabilities, 1e-12, 1 - 1e-12))
        )
        calibrator = LogisticRegression(C=1, max_iter=300).fit(
            logits.reshape(-1, 1),
            [r["label"] for r in calibration],
            sample_weight=page_weights(calibration),
        )
        payload["calibration"] = [
            float(calibrator.coef_[0, 0]),
            float(calibrator.intercept_[0]),
        ]
        model = ContextModel(payload)
        xv = vectorize(valid, vocab, idf, mode)
        expected = np.array(
            [model.calibrate(p) for p in estimator.predict_proba(xv)[:, 1]]
        )
        actual = np.array(model.score_many(valid))
        parity = float(max(abs(actual - expected)))
        assert parity < 1e-9, parity
        chosen = selection(valid, actual)
        payload["threshold"] = chosen["threshold"]
        payload["model_id"] += (
            "-"
            + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[
                :12
            ]
        )
        path = args.output / (name + ".json")
        write_text(
            path, json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf8"
        )
        fitted[name] = payload
        report["candidates"][name] = dict(
            selection=chosen,
            seconds=time.perf_counter() - started,
            converged=not any(
                issubclass(w.category, ConvergenceWarning) for w in caught
            ),
            parameters=sum(
                len(layer["bias"]) + sum(map(len, layer["weights"]))
                for layer in payload["layers"]
            ),
            bytes=len(read_bytes(path)),
            export_max_error=parity,
            calibration=payload["calibration"],
        )
        print(
            name,
            json.dumps(
                {
                    k: v
                    for k, v in report["candidates"][name].items()
                    if k != "selection"
                }
            ),
            {k: v for k, v in chosen.items() if k != "by_source"},
            flush=True,
        )
        del raw, x, xc, xv, estimator
    deep_name = max(
        (n for n in fitted if n != "linear"),
        key=lambda n: (
            report["candidates"][n]["converged"],
            report["candidates"][n]["selection"]["precision"] >= 0.97,
            report["candidates"][n]["selection"]["macro_f1"],
        ),
    )
    options = []
    light_payload = teacher_payload if args.structural else fitted["linear"]
    light = ContextModel(light_payload)
    light_scores = np.array(light.score_many(valid))
    gates = ((0.02, 0.98), (0.05, 0.95), (0.1, 0.9))
    if args.structural:
        gates += ((0.1, 0.5), (0.15, 0.4), (0.2, 0.3))
    mask = np.array([r["origin"] == "index" for r in valid])
    real_valid = [r for r in valid if r["origin"] == "index"]
    light_metrics = metrics(real_valid, light_scores[mask] >= light.upper)
    for low, high in gates:
        if not low < light.upper < high:
            continue
        payload = dict(
            version=1,
            model_id="cascade-text-structure-v1",
            light=light_payload,
            deep=fitted[deep_name],
            gate=[low, high],
            policy=args.policy,
        )
        measured = metrics(
            real_valid, np.array(CascadeModel(payload).score_many(valid))[mask] >= 0.5
        )
        uncertain = (light_scores > low) & (light_scores < high)
        if args.policy == "rescue":
            uncertain &= light_scores < light.upper
        escalation = float(np.mean(uncertain))
        options.append((payload, measured, escalation))
    payload, measured, escalation = max(
        options,
        key=lambda option: (
            option[1]["precision"] >= light_metrics["precision"]
            and option[1]["recall"] >= light_metrics["recall"] - 0.005,
            option[1]["precision"] >= 0.97,
            option[1]["macro_f1"],
            -option[2],
        ),
    )
    payload["model_id"] += (
        "-"
        + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    )
    write_text(
        args.output / "cascade.json",
        json.dumps(payload, separators=(",", ":")) + "\n",
        encoding="utf8",
    )
    report["cascade"] = dict(
        deep=deep_name,
        gate=payload["gate"],
        selection=measured,
        selection_escalation=escalation,
        selection_baseline=light_metrics,
    )
    # Test only after freezing every candidate's threshold and the selected cascade gate.
    test = [r for r in rows if r["split"] == "test"]
    models = {n: ContextModel(p) for n, p in fitted.items()}
    models["cascade"] = CascadeModel(payload)
    models["production"] = ContextModel(
        json.loads(
            (ROOT / "app/tracker/link-context-model.json").read_text(encoding="utf8")
        )
    )
    report["test"] = {}
    for name, model in models.items():
        start = time.perf_counter()
        scores = model.score_many(test)
        report["test"][name] = dict(
            seconds=time.perf_counter() - start,
            **metrics(test, np.array(scores) >= model.upper),
        )
        print(
            "test",
            name,
            {k: v for k, v in report["test"][name].items() if k != "by_source"},
            flush=True,
        )
    write_text(
        args.output / "report.json",
        json.dumps(report, indent=2) + "\n",
        encoding="utf8",
    )


if __name__ == "__main__":
    main()
