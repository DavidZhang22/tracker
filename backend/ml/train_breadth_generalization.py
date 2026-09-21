"""Reproducible site-disjoint breadth experiments with bounded runtime exports."""

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
from urllib.parse import urlsplit

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from dataset_provenance import freeze_protocol_document
from evaluation import metrics
from train_cascade import vectorize

from app.tracker.cascade_model import CascadeModel
from app.tracker.context_model import NUMERIC_FEATURES, ContextModel
from app.tracker.native_model import kernel

SEED = 20260922
ROLES = (
    "train",
    "train",
    "validation",
    "train",
    "test",
    "train",
    "train",
    "validation",
    "train",
    "test",
)
HISTORY = (
    "v3-dataset.jsonl",
    "generalization.jsonl",
    "cascade-dataset.jsonl",
    "review-dataset.jsonl",
    "cascade-final-dataset.jsonl",
    "extraction-audit-dataset.jsonl",
)
SHARDS = ("cultural", "public", "technical")
THRESHOLDS = (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80)
SPECS = (
    ("numeric-trees", "numeric", "trees"),
    ("text-trees", "words", "trees"),
    ("numeric-neural64x32", "numeric", "neural"),
    ("text-neural32", "words", "neural"),
)


def frozen_baseline(directory):
    path = directory / "baseline-cascade.json"
    if not path.exists():
        path.write_bytes((ROOT / "app/tracker/link-cascade-model.json").read_bytes())
    return CascadeModel(json.loads(path.read_text(encoding="utf8")))


def write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf8")


def family(row):
    if row.get("site_family"):
        return row["site_family"]
    if row.get("origin") == "authored":
        return "authored:" + row["source_id"]
    host = (urlsplit(row.get("source", "")).hostname or row["source_id"]).removeprefix(
        "www."
    )
    labels = host.split(".")
    width = (
        3
        if host.endswith(
            (".co.uk", ".org.uk", ".ac.uk", ".com.au", ".gov.au", ".co.nz")
        )
        else 2
    )
    return ".".join(labels[-width:])


def load_rows(path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf8").splitlines()]
    for row in rows:
        if len(row["features"]) != len(NUMERIC_FEATURES):
            raise ValueError("Unexpected feature schema in " + str(path))
        row["site_family"] = family(row)
    return rows


def freeze_protocol(output):
    paths = [ROOT / "ml/datasets" / name for name in HISTORY]
    paths += [ROOT / "ml/datasets" / f"breadth-{s}-dataset.jsonl" for s in SHARDS]
    protocol = dict(
        seed=SEED,
        bin_roles=ROLES,
        split_method="Per shard, sort complete site families by SHA256(seed:family), assign rank modulo 10 to interleaved 6 train / 2 validation / 2 test bins.",
        breadth_original_split="Immutable source datasets remain all test. This experiment overrides their split only in memory; these pages are development data for the new model.",
        label_limit="Frozen URL-level scope labels are weak labels, not independently adjudicated gold.",
        feature_schema=list(NUMERIC_FEATURES),
        candidates=SPECS,
        thresholds=THRESHOLDS,
        selection="Choose validation site-macro-F1 subject to precision >= max(0.80, deployed breadth validation precision minus 0.03), and historic validation macro-F1 no worse than deployed minus 0.04. No candidate meeting gates means no promotion.",
        vocabulary="Train only, <=384 terms, >=3 distinct training site families, TF-IDF normalized per row.",
        training="Equal site/class influence within breadth 55%, historical indexes 35%, authored 7%, CleanEval 3%. Exact duplicate examples removed; repeated URL anchors share total URL weight.",
        resource_limits=dict(
            trees=180, depth=4, neural_max_layers=[64, 32], vocabulary=384, threads=1
        ),
        dataset_sha256={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
        },
        breadth_assignments={},
    )
    breadth = []
    for shard in SHARDS:
        rows = load_rows(ROOT / "ml/datasets" / f"breadth-{shard}-dataset.jsonl")
        families = sorted(
            {r["site_family"] for r in rows},
            key=lambda s: hashlib.sha256(f"{SEED}:{s}".encode()).hexdigest(),
        )
        assignments = {f: ROLES[i % 10] for i, f in enumerate(families)}
        for row in rows:
            row["split"] = assignments[row["site_family"]]
            row["cohort"] = "breadth"
        breadth.extend(rows)
        protocol["breadth_assignments"][shard] = assignments
    protocol = freeze_protocol_document(output / "protocol.json", protocol, paths)
    return breadth, protocol


def prepare(breadth):
    history = [r for name in HISTORY for r in load_rows(ROOT / "ml/datasets" / name)]
    rank = {"train": 0, "validation": 1, "test": 2}
    roles = {}
    for r in history:
        f = r["site_family"]
        if rank[r["split"]] > rank.get(roles.get(f), -1):
            roles[f] = r["split"]
    breadth_roles = {r["site_family"]: r["split"] for r in breadth}
    overlaps = sorted(set(roles) & set(breadth_roles))
    # A frozen breadth holdout family may never contribute old training examples.
    history = [r for r in history if r["site_family"] not in breadth_roles]
    for row in history:
        row["split"] = roles[row["site_family"]]
        row["cohort"] = "historical"
    seen = set()
    labels_by_example = {}
    rows = []
    duplicates = 0
    for row in history + breadth:
        signature = (
            row["site_family"],
            row["url"],
            row["label"],
            tuple(row["features"]),
            tuple(sorted(set(row["tokens"]))),
        )
        identity = signature[:2] + signature[3:]
        previous = labels_by_example.setdefault(identity, row["label"])
        if previous != row["label"]:
            raise ValueError("Conflicting labels for one source/URL feature example")
        if signature in seen:
            duplicates += 1
            continue
        seen.add(signature)
        rows.append(row)
    groups = defaultdict(list)
    for row in rows:
        groups[row["cohort"], row["split"]].append(row)
    partitions = {
        split: {r["site_family"] for r in rows if r["split"] == split} for split in rank
    }
    assert not partitions["train"] & (partitions["validation"] | partitions["test"])
    assert not partitions["validation"] & partitions["test"]
    return groups, dict(
        removed_exact_duplicates=duplicates,
        excluded_historical_breadth_overlaps=overlaps,
        row_counts={"/".join(k): len(v) for k, v in groups.items()},
        family_counts={
            "/".join(k): len({r["site_family"] for r in v}) for k, v in groups.items()
        },
    )


def vocabulary(rows):
    sites = defaultdict(set)
    counts = Counter()
    for row in rows:
        for token in set(row["tokens"]):
            sites[token].add(row["site_family"])
            counts[token] += 1
    words = sorted(
        (
            w
            for w, n in counts.items()
            if n >= 5 and len(sites[w]) >= 3 and n < len(rows) * 0.9
        ),
        key=lambda w: (-len(sites[w]), -counts[w], w),
    )[:384]
    return words, [math.log((1 + len(rows)) / (1 + counts[w])) + 1 for w in words]


def weights(rows):
    def bucket(row):
        return "breadth" if row["cohort"] == "breadth" else row["origin"]

    shares = {"breadth": 0.55, "index": 0.35, "authored": 0.07, "cleaneval": 0.03}
    families = defaultdict(set)
    labels = defaultdict(set)
    urls = defaultdict(set)
    anchors = Counter()
    for row in rows:
        b = bucket(row)
        f = row["site_family"]
        y = row["label"]
        families[b].add(f)
        labels[b, f].add(y)
        urls[b, f, y].add(row["url"])
        anchors[b, f, y, row["url"]] += 1
    values = []
    for row in rows:
        b = bucket(row)
        f = row["site_family"]
        y = row["label"]
        values.append(
            shares[b]
            / len(families[b])
            / len(labels[b, f])
            / len(urls[b, f, y])
            / anchors[b, f, y, row["url"]]
        )
    values = np.array(values, dtype=np.float64)
    return values * len(values) / sum(values)


def summarize(rows, predictions):
    # Evaluation IDs are families, so multiple pages cannot dominate macro scores.
    views = [dict(r, source_id=r["site_family"]) for r in rows]
    return metrics(views, predictions)


def export(model, name, vocab, idf, threshold):
    payload = dict(
        version=2,
        model_id="breadth-generalization-" + name,
        features=list(NUMERIC_FEATURES),
        threshold=threshold,
        vocabulary=vocab,
        idf=idf,
        layers=[],
        token_mode="words",
    )
    if hasattr(model, "estimators_"):
        payload["intercept"] = float(
            model._raw_predict_init(np.zeros((1, model.n_features_in_)))[0, 0]
        )
        payload["trees"] = []
        for estimator in model.estimators_[:, 0]:
            t = estimator.tree_
            payload["trees"].append(
                [
                    [
                        int(t.feature[i]),
                        float(t.threshold[i]),
                        int(t.children_left[i]),
                        int(t.children_right[i]),
                        float(t.value[i, 0, 0] * model.learning_rate),
                    ]
                    for i in range(t.node_count)
                ]
            )
    else:
        payload["layers"] = [
            dict(weights=w.T.tolist(), bias=b.tolist())
            for w, b in zip(model.coefs_, model.intercepts_, strict=True)
        ]
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "ml/experiments/breadth-generalization"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    cascade_protocol = dict(
        light_candidates=[name for name, _, kind in SPECS if kind == "trees"],
        deep_candidates=[name for name, _, kind in SPECS if kind == "neural"],
        gates=["light threshold +/- 0.15, bounded to 0.01..0.99", "0.01..0.99"],
        policies=["rescue", "replace"],
        thresholds="Each component uses its separately validation-selected threshold.",
        selection="Same validation gates and metric as single models, ties prefer smaller artifact.",
        rationale="Frozen addendum before any candidate result or held-out test evaluation.",
    )
    write(args.output / "cascade-protocol.json", cascade_protocol)
    breadth, protocol = freeze_protocol(args.output)
    groups, counts = prepare(breadth)
    train = groups["breadth", "train"] + groups["historical", "train"]
    valid = groups["breadth", "validation"]
    historical_valid = groups["historical", "validation"]
    vocab, idf = vocabulary(train)
    sample_weight = weights(train)
    deployed = frozen_baseline(args.output)
    assert deployed is not None
    baseline = dict(
        breadth_validation=summarize(
            valid, np.array(deployed.score_many(valid)) >= deployed.upper
        ),
        historical_validation=summarize(
            historical_valid,
            np.array(deployed.score_many(historical_valid)) >= deployed.upper,
        ),
    )
    precision_floor = max(0.80, baseline["breadth_validation"]["precision"] - 0.03)
    historical_floor = baseline["historical_validation"]["macro_f1"] - 0.04
    report = dict(
        protocol_sha256=hashlib.sha256(
            (args.output / "protocol.json").read_bytes()
        ).hexdigest(),
        counts=counts,
        baseline=baseline,
        precision_floor=precision_floor,
        historical_macro_floor=historical_floor,
        vocabulary_size=len(vocab),
        candidates=[],
        native_kernel=bool(kernel()),
        notes="Test evaluated after locked validation selection. No training or threshold tuning uses breadth test or historical test pages.",
    )
    print("DATA", json.dumps(counts), flush=True)
    print(
        "BASELINE",
        json.dumps(
            {
                k: {a: b for a, b in v.items() if a != "by_source"}
                for k, v in baseline.items()
            }
        ),
        flush=True,
    )
    fitted = {}
    validation_scores = {}
    for name, mode, kind in SPECS:
        begin = time.perf_counter()
        words, word_idf = ([], []) if mode == "numeric" else (vocab, idf)
        x = vectorize(train, words, word_idf, "words")
        xv = vectorize(valid, words, word_idf, "words")
        xh = vectorize(historical_valid, words, word_idf, "words")
        y = np.array([r["label"] for r in train])
        model = (
            GradientBoostingClassifier(
                n_estimators=180,
                max_depth=4,
                min_samples_leaf=18,
                learning_rate=0.06,
                subsample=0.85,
                random_state=SEED,
            )
            if kind == "trees"
            else MLPClassifier(
                hidden_layer_sizes=(64, 32) if mode == "numeric" else (32,),
                solver="lbfgs",
                alpha=8 if mode == "numeric" else 12,
                max_iter=160 if mode == "numeric" else 120,
                max_fun=25000,
                random_state=SEED,
            )
        )
        with (
            warnings.catch_warnings(record=True) as caught,
            threadpool_limits(limits=1),
        ):
            warnings.simplefilter("always", ConvergenceWarning)
            model.fit(x, y, sample_weight=sample_weight)
            probabilities = model.predict_proba(xv)[:, 1]
            historical_p = model.predict_proba(xh)[:, 1]
        choices = []
        for threshold in THRESHOLDS:
            b = summarize(valid, probabilities >= threshold)
            h = summarize(historical_valid, historical_p >= threshold)
            choices.append(
                dict(
                    threshold=threshold,
                    breadth=b,
                    historical=h,
                    eligible=b["precision"] >= precision_floor
                    and h["macro_f1"] >= historical_floor,
                )
            )
        chosen = max(
            choices,
            key=lambda c: (
                c["eligible"],
                c["breadth"]["macro_f1"],
                c["historical"]["macro_f1"],
                c["breadth"]["precision"],
            ),
        )
        payload = export(model, name, words, word_idf, chosen["threshold"])
        runtime = ContextModel(payload)
        parity_rows = valid[:128]
        expected = model.predict_proba(xv[:128])[:, 1]
        scalar = np.array(
            [runtime.score(r["features"], r["tokens"]) for r in parity_rows]
        )
        batched = np.array(runtime.score_many(parity_rows))
        parity = max(
            float(np.max(abs(expected - scalar))),
            float(np.max(abs(expected - batched))),
        )
        assert parity < 1e-8, (name, parity)
        path = args.output / (name + ".json")
        path.write_text(
            json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf8"
        )
        assert path.stat().st_size < 2_000_000
        result = dict(
            name=name,
            seconds=round(time.perf_counter() - begin, 3),
            model_bytes=path.stat().st_size,
            export_max_error=parity,
            convergence_warnings=[str(w.message) for w in caught],
            selected=chosen,
            thresholds=choices,
        )
        report["candidates"].append(result)
        fitted[name] = (model, payload)
        validation_scores[name] = (probabilities, historical_p)
        write(args.output / "validation-report.json", report)
        print(
            name,
            json.dumps(
                dict(
                    seconds=result["seconds"],
                    eligible=chosen["eligible"],
                    threshold=chosen["threshold"],
                    breadth={
                        k: v for k, v in chosen["breadth"].items() if k != "by_source"
                    },
                    historical={
                        k: v
                        for k, v in chosen["historical"].items()
                        if k != "by_source"
                    },
                )
            ),
            flush=True,
        )
    singles = list(report["candidates"])
    for light in [c for c in singles if "trees" in c["name"]]:
        for deep in [c for c in singles if "neural" in c["name"]]:
            lp = fitted[light["name"]][1]
            dp = fitted[deep["name"]][1]
            for width, gate in (
                (
                    "narrow",
                    [
                        max(0.01, lp["threshold"] - 0.15),
                        min(0.99, lp["threshold"] + 0.15),
                    ],
                ),
                ("broad", [0.01, 0.99]),
            ):
                for policy in ("rescue", "replace"):
                    name = (
                        light["name"] + "-" + deep["name"] + "-" + width + "-" + policy
                    )
                    payload = dict(
                        version=1,
                        model_id="breadth-" + name,
                        light=lp,
                        deep=dp,
                        gate=gate,
                        policy=policy,
                    )
                    stats = []
                    routed = []
                    for rows, index in ((valid, 0), (historical_valid, 1)):
                        ls = validation_scores[light["name"]][index]
                        ds = validation_scores[deep["name"]][index]
                        eligible = (ls > gate[0]) & (ls < gate[1])
                        if policy == "rescue":
                            eligible &= ls < lp["threshold"]
                        prediction = ls >= lp["threshold"]
                        if policy == "replace":
                            prediction[eligible] = ds[eligible] >= dp["threshold"]
                        else:
                            prediction |= eligible & (ds >= dp["threshold"])
                        stats.append(summarize(rows, prediction))
                        routed.append(float(np.mean(eligible)))
                    chosen = dict(
                        threshold=0.5,
                        breadth=stats[0],
                        historical=stats[1],
                        eligible=stats[0]["precision"] >= precision_floor
                        and stats[1]["macro_f1"] >= historical_floor,
                    )
                    result = dict(
                        name=name,
                        kind="cascade",
                        selected=chosen,
                        validation_routing_fraction=routed,
                        model_bytes=len(
                            json.dumps(payload, separators=(",", ":")).encode()
                        ),
                    )
                    report["candidates"].append(result)
                    fitted[name] = (None, payload)
    best = max(
        report["candidates"],
        key=lambda c: (
            c["selected"]["eligible"],
            c["selected"]["breadth"]["macro_f1"],
            c["selected"]["historical"]["macro_f1"],
            -c["model_bytes"],
        ),
    )
    name = best["name"]
    model, payload = fitted[name]
    is_cascade = payload["version"] == 1
    selected_path = args.output / (
        "chosen-cascade.json" if is_cascade else "chosen-context.json"
    )
    selected_path.write_text(
        json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf8"
    )
    if is_cascade:
        (args.output / "chosen-context.json").write_text(
            json.dumps(payload["light"], separators=(",", ":")) + "\n", encoding="utf8"
        )
    selection = dict(
        candidate=name,
        threshold=0.5 if is_cascade else payload["threshold"],
        eligible=best["selected"]["eligible"],
        selection_metric="breadth validation site macro F1 with frozen gates",
        model_sha256=hashlib.sha256(selected_path.read_bytes()).hexdigest(),
        artifact=selected_path.name,
        validation=best["selected"],
    )
    write(args.output / "selection-lock.json", selection)
    write(args.output / "validation-report.json", report)
    # The locked candidate is the only trained candidate evaluated on held-out sites.
    report["selection"] = selection
    report["heldout"] = {}
    runtime = CascadeModel(payload) if is_cascade else ContextModel(payload)
    for cohort in ("breadth", "historical"):
        rows = groups[cohort, "test"]
        report["heldout"][cohort] = dict(
            candidate=summarize(
                rows, np.array(runtime.score_many(rows)) >= runtime.upper
            ),
            deployed=summarize(
                rows, np.array(deployed.score_many(rows)) >= deployed.upper
            ),
        )
    report["seconds"] = round(time.perf_counter() - started, 3)
    write(args.output / "training-report.json", report)
    print("SELECTED", json.dumps(selection), flush=True)
    print(
        "HELDOUT",
        json.dumps(
            {
                c: {
                    n: {k: v for k, v in m.items() if k != "by_source"}
                    for n, m in scores.items()
                }
                for c, scores in report["heldout"].items()
            }
        ),
        flush=True,
    )


def refine_weights(rows):
    def bucket(row):
        return "breadth" if row["cohort"] == "breadth" else row["origin"]

    shares = {"breadth": 0.45, "index": 0.45, "authored": 0.07, "cleaneval": 0.03}
    families = defaultdict(set)
    urls = defaultdict(set)
    anchors = Counter()
    for row in rows:
        b, f, y = bucket(row), row["site_family"], row["label"]
        families[b].add(f)
        urls[b, f, y].add(row["url"])
        anchors[b, f, y, row["url"]] += 1
    values = []
    for row in rows:
        b, f, y = bucket(row), row["site_family"], row["label"]
        present = [label for label in (0, 1) if urls[b, f, label]]
        empirical = len(urls[b, f, y]) / sum(
            len(urls[b, f, label]) for label in present
        )
        prior = 0.75 * empirical + 0.25 / len(present)
        values.append(
            shares[b]
            / len(families[b])
            * prior
            / len(urls[b, f, y])
            / anchors[b, f, y, row["url"]]
        )
    result = np.asarray(values)
    return result * len(rows) / result.sum()


def refined_prediction(baseline, expert, nuisance, high, low, rejection, ceiling):
    result = baseline >= 0.5
    result |= (baseline < 0.5) & (expert >= high)
    mask = nuisance if rejection == "nuisance" else nuisance | (baseline <= ceiling)
    result &= ~((baseline >= 0.5) & mask & (expert < low))
    return result


def refine_main():
    from app.tracker.context_model import nuisance_context

    parser = argparse.ArgumentParser(
        description="Conservative validation-only refinement after failed replacement experiment"
    )
    parser.add_argument("--refine", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "ml/experiments/breadth-generalization-v2"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    first = ROOT / "ml/experiments/breadth-generalization"
    breadth, old_protocol = freeze_protocol(first)
    groups, counts = prepare(breadth)
    train = groups["breadth", "train"] + groups["historical", "train"]
    valid = groups["breadth", "validation"]
    historical = [
        row for row in groups["historical", "validation"] if row["origin"] == "index"
    ]
    base_path = first / "baseline-cascade.json"
    protocol = dict(
        seed=SEED,
        original_protocol_sha256=hashlib.sha256(
            (first / "protocol.json").read_bytes()
        ).hexdigest(),
        partitions="Same family assignments as V1. Its nine test websites are now disclosed regression data, still excluded from all V2 fitting/selection. Fresh external holdout remains unexamined.",
        candidates=["v1:" + name for name, _, _ in SPECS]
        + ["soft-numeric-trees", "soft-numeric-neural64x32", "soft-text-neural32"],
        weight_method="Each site has equal influence within cohort. Class prior is 75% empirical unique-URL proportion and 25% uniform across present classes; repeated anchors share URL weight.",
        rescue_thresholds=[0.6, 0.7, 0.8, 0.9, 0.95],
        rejection_thresholds=[0.01, 0.03, 0.05, 0.1, 0.2],
        rejection_policies=[
            dict(mode="nuisance", ceiling=0),
            dict(mode="disagreement", ceiling=0.75),
            dict(mode="disagreement", ceiling=0.90),
        ],
        selection="Breadth precision >=.80, breadth macroF1 > baseline, historical public-only precision/recall/macroF1 at most .03 below baseline. Eligible choices ranked by breadth macroF1, historical public F1, smaller bytes.",
        baseline_sha256=hashlib.sha256(base_path.read_bytes()).hexdigest(),
        no_test_evaluation=True,
        max_new_trees=180,
        max_new_mlp=[64, 32],
        max_vocab=384,
    )
    protocol_path = args.output / "protocol.json"
    if protocol_path.exists() and json.loads(protocol_path.read_text()) != protocol:
        raise ValueError("Refinement protocol changed")
    write(protocol_path, protocol)
    baseline = frozen_baseline(first)
    assert baseline is not None
    base_payload = json.loads(base_path.read_text(encoding="utf8"))
    datasets = {"breadth": valid, "historical_public": historical}
    baseline_scores = {
        k: np.array(baseline.score_many(rows)) for k, rows in datasets.items()
    }
    nuisance = {
        k: np.array([nuisance_context(r["features"]) for r in rows])
        for k, rows in datasets.items()
    }
    baseline_metrics = {
        k: summarize(rows, baseline_scores[k] >= 0.5) for k, rows in datasets.items()
    }
    report = dict(
        protocol_sha256=hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        counts=counts,
        baseline=baseline_metrics,
        fits=[],
        candidates=[],
    )
    payloads = {}
    for name, _, _ in SPECS:
        payloads["v1:" + name] = json.loads(
            (first / (name + ".json")).read_text(encoding="utf8")
        )
    vocab, idf = vocabulary(train)
    sample_weight = refine_weights(train)
    for name, mode, kind in (
        ("soft-numeric-trees", "numeric", "trees"),
        ("soft-numeric-neural64x32", "numeric", "neural"),
        ("soft-text-neural32", "words", "neural"),
    ):
        begin = time.perf_counter()
        words, word_idf = ([], []) if mode == "numeric" else (vocab, idf)
        x = vectorize(train, words, word_idf, "words")
        xv = vectorize(valid, words, word_idf, "words")
        y = np.array([r["label"] for r in train])
        model = (
            GradientBoostingClassifier(
                n_estimators=180,
                max_depth=4,
                min_samples_leaf=18,
                learning_rate=0.06,
                subsample=0.85,
                random_state=SEED,
            )
            if kind == "trees"
            else MLPClassifier(
                hidden_layer_sizes=(64, 32) if mode == "numeric" else (32,),
                solver="lbfgs",
                alpha=8 if mode == "numeric" else 12,
                max_iter=240,
                max_fun=30000,
                random_state=SEED,
            )
        )
        with (
            warnings.catch_warnings(record=True) as caught,
            threadpool_limits(limits=1),
        ):
            warnings.simplefilter("always", ConvergenceWarning)
            model.fit(x, y, sample_weight=sample_weight)
        payload = export(model, name, words, word_idf, 0.5)
        runtime = ContextModel(payload)
        expected = model.predict_proba(xv[:128])[:, 1]
        parity = float(
            np.max(abs(np.array(runtime.score_many(valid[:128])) - expected))
        )
        assert parity < 1e-8
        path = args.output / (name + ".json")
        path.write_text(
            json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf8"
        )
        payloads[name] = payload
        report["fits"].append(
            dict(
                name=name,
                seconds=round(time.perf_counter() - begin, 3),
                export_max_error=parity,
                bytes=path.stat().st_size,
                warnings=[str(w.message) for w in caught],
            )
        )
        write(args.output / "validation-report.json", report)
        print("FIT", name, report["fits"][-1]["seconds"], flush=True)
    for name, payload in payloads.items():
        runtime = ContextModel(payload)
        expert = {k: np.array(runtime.score_many(rows)) for k, rows in datasets.items()}
        for high in protocol["rescue_thresholds"]:
            for low in protocol["rejection_thresholds"]:
                for policy in protocol["rejection_policies"]:
                    quality = {
                        k: summarize(
                            rows,
                            refined_prediction(
                                baseline_scores[k],
                                expert[k],
                                nuisance[k],
                                high,
                                low,
                                policy["mode"],
                                policy["ceiling"],
                            ),
                        )
                        for k, rows in datasets.items()
                    }
                    b, h = quality["breadth"], quality["historical_public"]
                    hb = baseline_metrics["historical_public"]
                    eligible = (
                        b["precision"] >= 0.8
                        and b["macro_f1"] > baseline_metrics["breadth"]["macro_f1"]
                        and all(
                            h[key] >= hb[key] - 0.03
                            for key in ("precision", "recall", "macro_f1")
                        )
                    )
                    report["candidates"].append(
                        dict(
                            name=name,
                            high=high,
                            low=low,
                            policy=policy,
                            eligible=eligible,
                            quality=quality,
                            model_bytes=len(json.dumps(payload, separators=(",", ":"))),
                        )
                    )
        options = [c for c in report["candidates"] if c["name"] == name]
        choice = max(
            options,
            key=lambda c: (
                c["eligible"],
                c["quality"]["breadth"]["macro_f1"],
                c["quality"]["historical_public"]["f1"],
            ),
        )
        print(
            "EXPERT",
            name,
            json.dumps(
                {
                    "eligible": choice["eligible"],
                    "high": choice["high"],
                    "low": choice["low"],
                    "policy": choice["policy"],
                    "metrics": {
                        k: {m: v for m, v in score.items() if m != "by_source"}
                        for k, score in choice["quality"].items()
                    },
                }
            ),
            flush=True,
        )
    best = max(
        report["candidates"],
        key=lambda c: (
            c["eligible"],
            c["quality"]["breadth"]["macro_f1"],
            c["quality"]["historical_public"]["f1"],
            -c["model_bytes"],
        ),
    )
    payload = json.loads(json.dumps(base_payload))
    payload["model_id"] = "breadth-generalization-refinement-v2"
    payload["refinement"] = json.loads(json.dumps(payloads[best["name"]]))
    payload["refinement"].update(threshold=best["high"], reject_threshold=best["low"])
    payload["refinement_policy"] = best["policy"]
    path = args.output / "chosen-refinement.json"
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf8")
    report["selection"] = dict(
        best,
        artifact=path.name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        bytes=path.stat().st_size,
    )
    write(args.output / "selection-lock.json", report["selection"])
    write(args.output / "validation-report.json", report)
    print(
        "V2 SELECTION",
        json.dumps({k: v for k, v in report["selection"].items() if k != "quality"}),
        flush=True,
    )


def explicit_chrome(rows):
    names = (
        "semantic_navigation",
        "in_nav",
        "in_footer",
        "in_aside",
        "rel_author",
        "rel_tag",
        "utility_path",
        "utility_label",
    )
    indices = [NUMERIC_FEATURES.index(name) for name in names]
    title = NUMERIC_FEATURES.index("semantic_title")
    return np.array(
        [
            any(row["features"][i] for i in indices) and not row["features"][title]
            for row in rows
        ]
    )


def structural_main():
    output = ROOT / "ml/experiments/breadth-generalization-v2"
    first = ROOT / "ml/experiments/breadth-generalization"
    protocol = json.loads(
        (output / "structural-routing-protocol.json").read_text(encoding="utf8")
    )
    breadth, _ = freeze_protocol(first)
    groups, _ = prepare(breadth)
    datasets = {
        "breadth": groups["breadth", "validation"],
        "historical_public": [
            r for r in groups["historical", "validation"] if r["origin"] == "index"
        ],
    }
    baseline = frozen_baseline(first)
    assert baseline is not None
    base_scores = {
        k: np.array(baseline.score_many(rows)) for k, rows in datasets.items()
    }
    chrome = {k: explicit_chrome(rows) for k, rows in datasets.items()}
    base_metrics = {
        k: summarize(rows, base_scores[k] >= 0.5) for k, rows in datasets.items()
    }
    candidates = {
        name: json.loads((first / (name + ".json")).read_text(encoding="utf8"))
        for name, _, _ in SPECS
    }
    for name in (
        "soft-numeric-trees",
        "soft-numeric-neural64x32",
        "soft-text-neural32",
    ):
        candidates[name] = json.loads(
            (output / (name + ".json")).read_text(encoding="utf8")
        )
    report = dict(
        protocol_sha256=hashlib.sha256(
            (output / "structural-routing-protocol.json").read_bytes()
        ).hexdigest(),
        baseline=base_metrics,
        candidates=[],
    )
    for name, payload in candidates.items():
        expert = ContextModel(payload)
        scores = {k: np.array(expert.score_many(rows)) for k, rows in datasets.items()}
        for high in protocol["high"]:
            for low in protocol["low"]:
                quality = {}
                for key, rows in datasets.items():
                    prediction = base_scores[key] >= 0.5
                    prediction |= ~chrome[key] & (scores[key] >= high)
                    prediction &= ~(chrome[key] & (scores[key] < low))
                    quality[key] = summarize(rows, prediction)
                h, b = quality["historical_public"], quality["breadth"]
                hb = base_metrics["historical_public"]
                bb = base_metrics["breadth"]
                eligible = (
                    all(
                        h[k] >= hb[k] - 0.03
                        for k in ("precision", "recall", "macro_f1")
                    )
                    and b["precision"] > bb["precision"]
                    and b["recall"] > bb["recall"]
                )
                report["candidates"].append(
                    dict(
                        name=name,
                        high=high,
                        low=low,
                        eligible=eligible,
                        quality=quality,
                        precision_target_met=b["precision"] >= 0.8,
                    )
                )
        options = [c for c in report["candidates"] if c["name"] == name]
        best = max(
            options,
            key=lambda c: (
                c["eligible"],
                c["quality"]["breadth"]["macro_f1"],
                c["quality"]["historical_public"]["f1"],
            ),
        )
        print(
            "STRUCTURAL",
            name,
            json.dumps(
                {
                    "eligible": best["eligible"],
                    "high": best["high"],
                    "low": best["low"],
                    "quality": {
                        k: {m: v for m, v in score.items() if m != "by_source"}
                        for k, score in best["quality"].items()
                    },
                }
            ),
            flush=True,
        )
    best = max(
        report["candidates"],
        key=lambda c: (
            c["eligible"],
            c["quality"]["breadth"]["macro_f1"],
            c["quality"]["historical_public"]["f1"],
        ),
    )
    payload = json.loads((first / "baseline-cascade.json").read_text(encoding="utf8"))
    payload["model_id"] = "breadth-structural-refinement-v2"
    payload["refinement"] = json.loads(json.dumps(candidates[best["name"]]))
    payload["refinement"].update(threshold=best["high"], reject_threshold=best["low"])
    payload["refinement_policy"] = {"mode": "structural", "title_override": True}
    path = output / "chosen-structural-refinement.json"
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf8")
    report["selection"] = dict(
        best,
        artifact=path.name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        bytes=path.stat().st_size,
    )
    write(output / "structural-selection-lock.json", report["selection"])
    write(output / "structural-validation-report.json", report)
    print(
        "STRUCTURAL SELECTION",
        json.dumps({k: v for k, v in report["selection"].items() if k != "quality"}),
        flush=True,
    )


if __name__ == "__main__":
    if "--structural-refine" in sys.argv:
        structural_main()
    elif "--refine" in sys.argv:
        refine_main()
    else:
        main()
