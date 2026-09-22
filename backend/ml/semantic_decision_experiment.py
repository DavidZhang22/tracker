"""Offline, family-disjoint CPU experiments for contextual link decisions.

The encoder is frozen. Heads are trained only on captured training families.
This is a small decision-model experiment, not a reproduction of Jev's weights.
"""

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"

import numpy as np
import psutil
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from evaluate_breadth import aggregate, score_sets
from evaluate_breadth_upgrade import annotations, capture, digest_json
from evaluate_extraction_audit import parser_identity
from semantic_decision_data import extract_page
from summarize_breadth import HISTORICAL_MANIFESTS
from train_breadth_generalization import HISTORY, family

from app.tracker.cascade_model import CascadeModel, decision_score
from app.tracker.context_model import NUMERIC_FEATURES
from app.tracker.parser import parse_page
from app.tracker.semantic_model import Encoder

SEED = 20260922
EXPERIMENT = ROOT / "ml/experiments/semantic-decision"
CACHE = ROOT / "data/semantic-decision"
SPECS = {
    "numeric-neural": (None, "neural"),
    "lexical-linear": ("tfidf", "linear"),
    "minilm-l6-linear": ("minilm-l6", "linear"),
    "minilm-l6-neural": ("minilm-l6", "neural"),
    "minilm-l3-neural": ("minilm-l3", "neural"),
}
THRESHOLDS = (0.3, 0.5, 0.7, 0.85, 0.95)
POLICIES = ("replace", "uncertain")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf8"
    )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen(path, value):
    if path.exists() and json.loads(path.read_text(encoding="utf8")) != value:
        raise ValueError(
            f"Frozen input changed: {path}. Use a new experiment directory."
        )
    write(path, value)


def environment():
    return dict(
        platform=platform.platform(),
        python=platform.python_version(),
        cpu=platform.processor(),
        logical_cpus=psutil.cpu_count(),
        inference_threads=2,
        training_threads=1,
        cpu_affinity=psutil.Process().cpu_affinity(),
        memory_limit="No OS memory cap on local run; measured process RSS, not a service budget proof",
    )


def load_baseline(directory):
    path = directory / "baseline.json"
    if not path.exists():
        path.write_bytes((ROOT / "app/tracker/link-cascade-model.json").read_bytes())
    model = CascadeModel(json.loads(path.read_text(encoding="utf8")))
    model.artifact_sha256 = sha(path)
    return model


def sources_and_roles():
    prior = json.loads(
        (ROOT / "ml/experiments/breadth-generalization/protocol.json").read_text()
    )
    breadth_roles = {
        k: v
        for shard in prior["breadth_assignments"].values()
        for k, v in shard.items()
    }
    history_roles, history_ids = {}, set()
    rank = {"train": 0, "validation": 1, "test": 2}
    for name in HISTORY:
        for line in (
            (ROOT / "ml/datasets" / name).read_text(encoding="utf8").splitlines()
        ):
            row = json.loads(line)
            if row.get("origin") != "index":
                continue
            f = family(row)
            if rank[row["split"]] > rank.get(history_roles.get(f), -1):
                history_roles[f] = row["split"]
            history_ids.add(row["source_id"])
    sources, paths = {}, []
    for name in HISTORICAL_MANIFESTS:
        path = ROOT / "ml/datasets" / (name + ".json")
        paths.append(path)
        for s in json.loads(path.read_text(encoding="utf8")):
            if s["id"] not in history_ids or s.get("status", "captured") != "captured":
                continue
            f = family(dict(s, source=s["url"], source_id=s["id"]))
            if f in breadth_roles:
                continue
            sources[s["id"]] = dict(
                s, site_family=f, split=history_roles[f], cohort="historical"
            )
    for shard in ("cultural", "public", "technical"):
        path = ROOT / "ml/datasets" / f"breadth-{shard}-sources.json"
        paths.append(path)
        for s in json.loads(path.read_text(encoding="utf8")):
            if s.get("status") == "captured" and s.get("expected_urls"):
                sources[s["id"]] = dict(
                    s, split=breadth_roles[s["site_family"]], cohort="breadth"
                )
    return list(sources.values()), paths


def assert_disjoint(pages):
    groups = defaultdict(set)
    for p in pages:
        groups[p["split"]].add(p["site_family"])
    keys = list(groups)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if groups[a] & groups[b]:
                raise ValueError(
                    f"Website leakage between {a} and {b}: {groups[a] & groups[b]}"
                )
    return {k: sorted(v) for k, v in groups.items()}


def prepare_page(source, cache, baseline):
    html, digest = capture(source)
    identity = digest_json(
        dict(
            source=source,
            capture=digest,
            extractor=sha(Path(__file__).with_name("semantic_decision_data.py")),
            runtime={p.name: sha(p) for p in (ROOT / "app/tracker").glob("*.py")},
            baseline=baseline.artifact_sha256,
        )
    )
    path = cache / (source["id"] + ".json")
    if path.exists():
        page = json.loads(path.read_text(encoding="utf8"))
        if page["fingerprint"] != identity:
            raise ValueError("Cached page changed: " + source["id"])
        return page
    started = time.perf_counter()
    rows = extract_page(source, html)
    expected, ignored = annotations(source, html)
    base = source.get("final_url", source["url"])
    for r in rows:
        r["identity"] = parser_identity(r["url"], base)
        r["label"] = int(r["identity"] in expected)
    scores = baseline.score_many(rows)
    for r, score in zip(rows, scores, strict=True):
        r["baseline"] = score
    rows = [r for r in rows if r["identity"] not in ignored]
    page = dict(
        source=source,
        id=source["id"],
        site_family=source["site_family"],
        split=source["split"],
        cohort=source.get("cohort", "fresh"),
        fingerprint=identity,
        expected=sorted(expected),
        ignored=sorted(ignored),
        rows=rows,
        extraction_seconds=time.perf_counter() - started,
    )
    write(path, page)
    print(
        "prepared",
        source["id"],
        len(rows),
        "candidates",
        len(expected),
        "expected",
        flush=True,
    )
    return page


def prepare(directory, cache):
    directory.mkdir(parents=True, exist_ok=True)
    sources, paths = sources_and_roles()
    baseline = load_baseline(directory)
    # Freeze candidates and selection rules before any fitting or new holdout scoring.
    protocol = dict(
        version=1,
        seed=SEED,
        purpose="CPU contextual decisions; not pretrained Jev reproduction",
        candidates=SPECS,
        thresholds=THRESHOLDS,
        policies=POLICIES,
        train_sampling="At most96 unique URLs per class per page; deterministic hash, one context representative per URL; equal family/class weight",
        selection="Validation only: maximize mean family F1, subject to overall precision/recall >= baseline minus .02 and each cohort macroF1 >= baseline minus .02. If none pass, evaluate best exploratory model but do not promote.",
        uncertainty_gate=[0.1, 0.9],
        encoder_tokens=192,
        heads=dict(neural=[64, 32], epochs=160, regularization=0.01, linear_C=1.0),
        inputs="HTML-derived bounded link and local record text +84 numeric features. No hostname/query values, scope annotation, or labels in text. No synthetic examples.",
        source_manifests={p.name: sha(p) for p in paths},
        sources={s["id"]: digest_json(s) for s in sources},
        baseline_sha256=sha(directory / "baseline.json"),
        experiment_code_sha256=sha(Path(__file__)),
        runtime_sha256={p.name: sha(p) for p in (ROOT / "app/tracker").glob("*.py")},
        extractor_sha256=sha(Path(__file__).with_name("semantic_decision_data.py")),
        semantic_assets_sha256=sha(ROOT / "app/tracker/semantic_assets.json"),
        limits="Single-listing snapshots; assistant-reviewed scopes, not independently adjudicated gold. Prior test sites are regression, only fresh external cohort is a new test.",
    )
    protocol = json.loads(json.dumps(protocol))
    frozen(directory / "protocol.json", protocol)
    pages = [prepare_page(s, cache, baseline) for s in sources]
    assignments = assert_disjoint(pages)
    write(
        directory / "data-summary.json",
        dict(
            assignments=assignments,
            pages=[
                dict(
                    id=p["id"],
                    site_family=p["site_family"],
                    split=p["split"],
                    cohort=p["cohort"],
                    candidates=len(p["rows"]),
                    expected=len(p["expected"]),
                    fingerprint=p["fingerprint"],
                )
                for p in pages
            ],
        ),
    )
    return pages


def representatives(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["identity"]].append(row)
    # Content-like representative independent of labels and model predictions.
    heading = NUMERIC_FEATURES.index("semantic_title")
    nav = NUMERIC_FEATURES.index("semantic_navigation")
    return [
        max(
            values,
            key=lambda r: (
                r["features"][heading] - r["features"][nav],
                len(r["link_text"]),
                r["text"],
            ),
        )
        for values in groups.values()
    ]


def training_rows(pages):
    rows = []
    for page in pages:
        if page["split"] != "train":
            continue
        unique = representatives(page["rows"])
        for label in (0, 1):
            selected = sorted(
                (r for r in unique if r["label"] == label),
                key=lambda r: hashlib.sha256(
                    (str(SEED) + r["identity"]).encode()
                ).hexdigest(),
            )[:96]
            rows.extend(selected)
    return rows


def sample_weights(rows):
    counts = Counter((r["site_family"], r["label"]) for r in rows)
    labels = defaultdict(set)
    for f, y in counts:
        labels[f].add(y)
    w = np.asarray(
        [
            1 / (len(labels[r["site_family"]]) * counts[r["site_family"], r["label"]])
            for r in rows
        ]
    )
    return w * len(w) / sum(w)


def matrix(rows):
    return np.asarray([r["features"] for r in rows], dtype=np.float32).reshape(
        -1, len(NUMERIC_FEATURES)
    )


def embeddings(rows, name, cache):
    if not rows:
        return np.empty((0, 384), dtype=np.float32)
    texts = list(dict.fromkeys(r["text"] for r in rows))
    digest = digest_json(
        dict(
            texts=texts,
            name=name,
            assets=sha(ROOT / "app/tracker/semantic_assets.json"),
            runtime=sha(ROOT / "app/tracker/semantic_model.py"),
        )
    )
    path = cache / (name + "-" + digest + ".npz")
    if path.exists():
        with np.load(path, allow_pickle=False) as stored:
            vectors = stored["vectors"]
    else:
        encoder = Encoder(name)
        vectors = np.empty((len(texts), 384), dtype=np.float32)
        started = time.perf_counter()
        for i in range(0, len(texts), 128):
            vectors[i : i + 128] = encoder.encode(texts[i : i + 128])
            if i % 1024 == 0:
                print(
                    name,
                    i,
                    "/",
                    len(texts),
                    round(time.perf_counter() - started, 1),
                    "seconds",
                    flush=True,
                )
        np.savez_compressed(path, vectors=vectors)
        del encoder
    by_text = {text: i for i, text in enumerate(texts)}
    return vectors[np.asarray([by_text[r["text"]] for r in rows])]


def export_head(model, scaler, encoder, vectorizer=None):
    layers = (
        [
            dict(weights=w.tolist(), bias=b.tolist())
            for w, b in zip(model.coefs_, model.intercepts_, strict=True)
        ]
        if hasattr(model, "coefs_")
        else [dict(weights=model.coef_.T.tolist(), bias=model.intercept_.tolist())]
    )
    payload = dict(
        version=1,
        encoder=encoder,
        dimensions=len(scaler.mean_),
        mean=scaler.mean_.tolist(),
        scale=scaler.scale_.tolist(),
        layers=layers,
    )
    if vectorizer is not None:
        payload["vocabulary"] = {k: int(v) for k, v in vectorizer.vocabulary_.items()}
        payload["idf"] = vectorizer.idf_.tolist()
    return payload


def predict_head(payload, x):
    a = (x - np.asarray(payload["mean"])) / np.asarray(payload["scale"])
    for i, layer in enumerate(payload["layers"]):
        a = a @ np.asarray(layer["weights"]) + np.asarray(layer["bias"])
        if i + 1 < len(payload["layers"]):
            a = np.maximum(a, 0)
    return 1 / (1 + np.exp(-np.clip(a[:, 0], -40, 40)))


def route(base, scores, threshold, policy):
    chosen = np.asarray(scores) >= threshold
    if policy == "uncertain":
        gate = (base > 0.1) & (base < 0.9)
        chosen = np.where(gate, chosen, base >= 0.5)
    return chosen


def quality(pages, predictions):
    offset, scored = 0, {}
    for page in pages:
        n = len(page["rows"])
        actual = {
            r["identity"]
            for r, p in zip(page["rows"], predictions[offset : offset + n], strict=True)
            if p
        }
        scored[page["id"]] = score_sets(set(page["expected"]), actual)
        offset += n
    if offset != len(predictions):
        raise ValueError("Prediction count mismatch")
    families = defaultdict(list)
    for p in pages:
        families[p["site_family"]].append(scored[p["id"]]["f1"])
    result = aggregate(list(scored.values()))
    result["family_macro_f1"] = (
        float(np.mean([np.mean(v) for v in families.values()])) if families else 0.0
    )
    result["by_page"] = scored
    return result


def validation_choice(pages, base, probability, threshold, policy):
    actual = route(base, probability, threshold, policy)
    result = quality(pages, actual)
    result["by_cohort"] = {}
    offset = 0
    indices = defaultdict(list)
    for p in pages:
        indices[p["cohort"]].extend(range(offset, offset + len(p["rows"])))
        offset += len(p["rows"])
    for c, inds in indices.items():
        result["by_cohort"][c] = quality(
            [p for p in pages if p["cohort"] == c], actual[inds]
        )
    return result


def fit(directory, cache):
    pages = prepare(directory, cache)
    train = training_rows(pages)
    valid_pages = [p for p in pages if p["split"] == "validation"]
    valid = [r for p in valid_pages for r in p["rows"]]
    both = train + valid
    y = np.asarray([r["label"] for r in train])
    w = sample_weights(train)
    base = np.asarray([r["baseline"] for r in valid])
    baseline = validation_choice(valid_pages, base, base, 0.5, "replace")
    report = dict(
        environment=environment(),
        train_rows=len(train),
        validation_rows=len(valid),
        training_families=len({r["site_family"] for r in train}),
        validation_families=len({r["site_family"] for r in valid}),
        baseline=baseline,
        protocol_sha256=sha(directory / "protocol.json"),
        candidates=[],
        timings={},
    )
    encoded = {}
    for name, (encoder, kind) in SPECS.items():
        vectorizer = None
        started = time.perf_counter()
        x = matrix(both)
        if encoder == "tfidf":
            vectorizer = TfidfVectorizer(
                max_features=768, min_df=3, ngram_range=(1, 2), sublinear_tf=True
            )
            vectorizer.fit([r["text"] for r in train])
            x = np.hstack(
                (
                    x,
                    vectorizer.transform([r["text"] for r in both])
                    .toarray()
                    .astype(np.float32),
                )
            )
        elif encoder:
            if encoder not in encoded:
                encoded[encoder] = embeddings(both, encoder, cache)
            x = np.hstack((x, encoded[encoder]))
        scaler = StandardScaler().fit(x[: len(train)], sample_weight=w)
        z = scaler.transform(x)
        model = (
            MLPClassifier(
                hidden_layer_sizes=(64, 32),
                alpha=0.01,
                max_iter=160,
                early_stopping=False,
                random_state=SEED,
                batch_size=128,
                n_iter_no_change=20,
                learning_rate_init=0.001,
            )
            if kind == "neural"
            else LogisticRegression(C=1.0, max_iter=400, random_state=SEED)
        )
        with threadpool_limits(limits=1):
            model.fit(z[: len(train)], y, sample_weight=w)
            probability = model.predict_proba(z[len(train) :])[:, 1]
            payload = export_head(model, scaler, encoder, vectorizer)
            reference = predict_head(payload, x[len(train) :])
        if np.max(np.abs(probability - reference)) > 2e-5:
            raise ValueError("Numeric export parity failed: " + name)
        write(directory / (name + ".json"), payload)
        report["timings"][name] = dict(
            total_fit_and_features_seconds=time.perf_counter() - started,
            artifact_bytes=(directory / (name + ".json")).stat().st_size,
            export_max_absolute_error=float(np.max(np.abs(probability - reference))),
        )
        for threshold in THRESHOLDS:
            for policy in POLICIES:
                q = validation_choice(valid_pages, base, probability, threshold, policy)
                eligible = (
                    q["precision"] >= baseline["precision"] - 0.02
                    and q["recall"] >= baseline["recall"] - 0.02
                    and all(
                        q["by_cohort"][c]["family_macro_f1"]
                        >= baseline["by_cohort"][c]["family_macro_f1"] - 0.02
                        for c in q["by_cohort"]
                    )
                )
                report["candidates"].append(
                    dict(
                        name=name,
                        threshold=threshold,
                        policy=policy,
                        eligible=eligible,
                        quality=q,
                    )
                )
        print("fitted", name, report["timings"][name], flush=True)
        write(directory / "validation.json", report)
    eligible = [r for r in report["candidates"] if r["eligible"]]
    ranked = sorted(
        eligible or report["candidates"],
        key=lambda r: (
            -r["quality"]["family_macro_f1"],
            -r["quality"]["precision"],
            report["timings"][r["name"]]["artifact_bytes"],
        ),
    )
    selected = {k: ranked[0][k] for k in ("name", "threshold", "policy", "eligible")}
    # Freeze one contextual finalist too, even if a lexical/structural control wins.
    semantic = [r for r in report["candidates"] if r["name"].startswith("minilm")]
    semantic_ok = [r for r in semantic if r["eligible"]]
    semantic_best = sorted(
        semantic_ok or semantic,
        key=lambda r: (-r["quality"]["family_macro_f1"], -r["quality"]["precision"]),
    )[0]
    semantic_selected = {
        k: semantic_best[k] for k in ("name", "threshold", "policy", "eligible")
    }
    selection = dict(
        overall=selected,
        semantic=semantic_selected,
        validation_sha256=sha(directory / "validation.json"),
        protocol_sha256=sha(directory / "protocol.json"),
        finalists={
            r["name"]: sha(directory / (r["name"] + ".json"))
            for r in (selected, semantic_selected)
        },
    )
    frozen(directory / "selection.json", selection)
    print("selection", json.dumps(selection), flush=True)


def features_for(rows, payload, cache):
    if not rows:
        return np.empty((0, payload["dimensions"]), dtype=np.float32)
    x = matrix(rows)
    encoder = payload["encoder"]
    if encoder == "tfidf":
        v = TfidfVectorizer(
            vocabulary=payload["vocabulary"], ngram_range=(1, 2), sublinear_tf=True
        )
        v.idf_ = np.asarray(payload["idf"])
        return np.hstack((x, v.transform([r["text"] for r in rows]).toarray()))
    if encoder:
        return np.hstack((x, embeddings(rows, encoder, cache)))
    return x


class ReplayModel:
    """Replay candidate decisions through unchanged parser rules, without DOM-ID caching."""

    primary, upper, lower = True, 0.5, 0.08

    def __init__(self, rows, decisions):
        self.model_id = "offline-semantic-decisions"
        self.predictions = defaultdict(list)
        for row, value in zip(rows, decisions, strict=True):
            self.predictions[row_key(row)].append(float(value))
        self.unmatched = 0

    def score_many(self, rows):
        result, used = [], Counter()
        for row in rows:
            key = row_key(row)
            index = used[key]
            if index >= len(self.predictions.get(key, [])):
                self.unmatched += 1
                raise ValueError(
                    "Parser candidate differs from frozen semantic candidate: "
                    + row["url"]
                )
            result.append(self.predictions[key][index])
            used[key] += 1
        return result


def row_key(row):
    return (row["url"], tuple(row["features"]), tuple(row["tokens"]))


def pipeline(pages, decisions, baseline):
    offset, scored = 0, {}
    for page in pages:
        n = len(page["rows"])
        html, _ = capture(page["source"])
        base = page["source"].get("final_url", page["source"]["url"])
        model = (
            ReplayModel(page["rows"], decisions[offset : offset + n])
            if decisions is not None
            else None
        )
        # Neutral rows have no evaluated decisions. Defer those to the deployed model.
        if model is not None:
            original = model.score_many
            ignored = set(page["ignored"])

            def score_with_neutral(
                rows, fn=original, b=baseline, ignore=ignored, origin=base
            ):
                out = b.score_many(rows)
                chosen = [
                    (i, r)
                    for i, r in enumerate(rows)
                    if parser_identity(r["url"], origin) not in ignore
                ]
                for (i, _), s in zip(chosen, fn([r for _, r in chosen]), strict=True):
                    out[i] = s
                return out

            model.score_many = score_with_neutral
        started = time.perf_counter()
        with patch.dict(os.environ, TRACKER_LINK_MODEL="cascade"):
            if model is None:
                with patch(
                    "app.tracker.context_model.active_context_model",
                    return_value=baseline,
                ):
                    scan = parse_page(html, base)[0]
            else:
                with patch(
                    "app.tracker.context_model.active_context_model", return_value=model
                ):
                    scan = parse_page(html, base)[0]
        actual = {parser_identity(e.url, base) for e in scan.entries} - set(
            page["ignored"]
        )
        scored[page["id"]] = dict(
            **score_sets(set(page["expected"]), actual),
            parse_seconds=time.perf_counter() - started,
            missing_examples=sorted(set(page["expected"]) - actual)[:4],
            unwanted_examples=sorted(actual - set(page["expected"]))[:4],
        )
        offset += n
        print(
            "pipeline",
            model.model_id if model else "deployed",
            page["id"],
            scored[page["id"]]["correct"],
            flush=True,
        )
    return dict(
        summary=aggregate(list(scored.values())),
        pages=scored,
        timing_note="Replay only. Candidate embeddings were computed separately; these timings exclude semantic inference.",
    )


def decision_hashes(pages, predictions):
    result, offset = {}, 0
    for page in pages:
        n = len(page["rows"])
        result[page["id"]] = digest_json(
            [
                [r["identity"], bool(value)]
                for r, value in zip(
                    page["rows"], predictions[offset : offset + n], strict=True
                )
            ]
        )
        offset += n
    return result


def evaluate(directory, cache, manifest, output):
    selection = json.loads((directory / "selection.json").read_text())
    if selection["protocol_sha256"] != sha(directory / "protocol.json") or selection[
        "validation_sha256"
    ] != sha(directory / "validation.json"):
        raise ValueError("Selection provenance changed")
    baseline = load_baseline(directory)
    protocol = json.loads((directory / "protocol.json").read_text())
    for expected, actual in (
        (protocol["baseline_sha256"], sha(directory / "baseline.json")),
        (
            protocol["extractor_sha256"],
            sha(Path(__file__).with_name("semantic_decision_data.py")),
        ),
        (
            protocol["semantic_assets_sha256"],
            sha(ROOT / "app/tracker/semantic_assets.json"),
        ),
    ):
        if expected != actual:
            raise ValueError("Evaluation runtime differs from frozen protocol")
    if protocol["runtime_sha256"] != {
        p.name: sha(p) for p in (ROOT / "app/tracker").glob("*.py")
    }:
        raise ValueError("Parser runtime differs from frozen protocol")
    training_summary = json.loads((directory / "data-summary.json").read_text())
    if manifest:
        sources = [
            dict(s, split="fresh", cohort="fresh")
            for s in json.loads(manifest.read_text())
            if s.get("status") == "captured" and s.get("expected_urls")
        ]
        known = {f for fs in training_summary["assignments"].values() for f in fs}
        prior = set()
        for path in (ROOT / "ml/datasets").glob("*.jsonl"):
            if path.name.startswith("semantic-decision-holdout"):
                continue
            for line in path.read_text(encoding="utf8").splitlines():
                r = json.loads(line)
                if isinstance(r, dict) and (r.get("source_id") or r.get("site_family")):
                    prior.add(family(r))
        overlaps = {s["site_family"] for s in sources} & (known | prior)
        if overlaps:
            raise ValueError("Fresh holdout overlaps prior data: " + str(overlaps))
        frozen(
            directory / "holdout-lock.json",
            dict(
                manifest_sha256=sha(manifest),
                selection_sha256=sha(directory / "selection.json"),
            ),
        )
        pages = [prepare_page(s, cache, baseline) for s in sources]
    else:
        pages = [
            json.loads((cache / (p["id"] + ".json")).read_text())
            for p in training_summary["pages"]
            if p["split"] == "test"
        ]
    if not pages:
        raise ValueError("No evaluation pages")
    rows = [r for p in pages for r in p["rows"]]
    base = np.asarray([r["baseline"] for r in rows])
    report = dict(
        environment=environment(),
        selection=selection,
        scope="Fresh unseen websites"
        if manifest
        else "Previously disclosed regression websites",
        rows=len(rows),
        pages=len(pages),
        families=len({p["site_family"] for p in pages}),
        candidate_ceiling=quality(pages, np.ones(len(rows), dtype=bool)),
        models={},
    )
    report["models"]["deployed"] = dict(
        candidates=quality(pages, base >= 0.5), pipeline=pipeline(pages, None, baseline)
    )
    for finalist in {
        v["name"]: v for v in (selection["overall"], selection["semantic"])
    }.values():
        name = finalist["name"]
        if sha(directory / (name + ".json")) != selection["finalists"][name]:
            raise ValueError("Finalist weights changed")
        payload = json.loads((directory / (name + ".json")).read_text())
        probability = predict_head(payload, features_for(rows, payload, cache))
        actual = route(base, probability, finalist["threshold"], finalist["policy"])
        routed_scores = np.asarray(
            [decision_score(float(p), finalist["threshold"]) for p in probability]
        )
        if finalist["policy"] == "uncertain":
            routed_scores = np.where((base > 0.1) & (base < 0.9), routed_scores, base)
        report["models"][name] = dict(
            selection=finalist,
            candidates=quality(pages, actual),
            pipeline=pipeline(pages, routed_scores, baseline),
            decision_hashes=decision_hashes(pages, actual),
        )
        write(output, report)
    report["complete"] = True
    write(output, report)
    print(
        json.dumps({k: v["pipeline"]["summary"] for k, v in report["models"].items()}),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "fit", "evaluate"))
    parser.add_argument("--directory", type=Path, default=EXPERIMENT)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "ml/reports/semantic-decision-regression.json",
    )
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    with threadpool_limits(limits=1):
        if args.action == "prepare":
            prepare(args.directory, args.cache)
        elif args.action == "fit":
            fit(args.directory, args.cache)
        else:
            evaluate(args.directory, args.cache, args.manifest, args.output)


if __name__ == "__main__":
    main()
