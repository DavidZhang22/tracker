"""Compare media classifiers offline on source-disjoint public listing metadata.

This is a feasibility experiment, not training data from users' libraries.
Run: backend/.venv/Scripts/python.exe backend/ml/evaluate_media.py
"""

import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"

import numpy as np
from bs4 import BeautifulSoup
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupKFold
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tracker.media_metadata import annotate, classify, features, source_summary
from app.tracker.parser import kind_for

LABELS = {
    "comic": "comic",
    "novel": "novel",
    "books": "novel",
    "podcast": "podcast",
    "releases": "software",
    "software": "software",
    "contests": "events",
    "exhibitions": "events",
    "courses": "course",
    "tutorial": "course",
    "talks": "video",
    "proceedings": "research",
    "standards": "research",
    "images": "website",
    "aggregator": "website",
}


def capture_profiles():
    catalogs = [
        "sources",
        "v2_sources",
        "generalization-sources",
        "cascade-sources",
        "cascade-final-sources",
    ]
    datasets = [
        "dataset",
        "v2-dataset",
        "generalization",
        "cascade-dataset",
        "cascade-final-dataset",
    ]
    entries = defaultdict(dict)
    for name in datasets:
        for line in (
            (ROOT / "ml/datasets" / (name + ".jsonl"))
            .read_text(encoding="utf-8")
            .splitlines()
        ):
            row = json.loads(line)
            if row.get("label") == 1:
                entries[row["source_id"]][row["url"]] = {
                    "url": row["url"],
                    "title": row.get("text", ""),
                }
    profiles, seen, skipped = [], set(), []
    for catalog in catalogs:
        for source in json.loads(
            (ROOT / "ml/datasets" / (catalog + ".json")).read_text(encoding="utf-8")
        ):
            url = source["url"]
            if url in seen:
                continue
            file = ROOT / source.get("file", "missing")
            if not file.is_file():
                candidates = list((ROOT / "data").glob(f"*/{file.name}.html"))
                file = candidates[0] if len(candidates) == 1 else file
            if not file.is_file() or not entries[source["id"]]:
                skipped.append(source["id"])
                continue
            seen.add(url)
            raw = file.read_bytes()
            soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
            heading = (
                soup.select_one('meta[property="og:title"]')
                or soup.find("h1")
                or soup.title
            )
            title = (
                heading.get("content") or heading.get_text(" ", strip=True)
                if heading
                else url
            )
            kind = kind_for(url)
            if kind == "website" and (
                soup.find("article") or soup.find("link", type="application/rss+xml")
            ):
                kind = "blog"
            profiles.append(
                {
                    "source_id": source["id"],
                    "url": url,
                    "title": title[:300],
                    "source_summary": source_summary(soup),
                    "kind": kind,
                    "label": LABELS.get(source["family"], "blog"),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "entries": list(entries[source["id"]].values())[:32],
                }
            )
            soup.decompose()
    return profiles, skipped


def evaluate(profiles):
    x = np.asarray([features(row) for row in profiles], dtype=np.float64)
    y = np.asarray([row["label"] for row in profiles])
    groups = [
        ".".join(urlsplit(row["url"]).hostname.split(".")[-2:]) for row in profiles
    ]
    predictions = {
        "previous": [row["kind"] for row in profiles],
        "evidence_classifier": [classify(row) for row in profiles],
        "logistic": [""] * len(y),
        "neural_24": [""] * len(y),
    }
    training, inference = Counter(), Counter()
    for train, test in GroupKFold(n_splits=5).split(x, y, groups):
        for name, model in [
            (
                "logistic",
                LogisticRegression(C=1, class_weight="balanced", max_iter=1000),
            ),
            (
                "neural_24",
                MLPClassifier(
                    hidden_layer_sizes=(24,),
                    solver="lbfgs",
                    alpha=1,
                    max_iter=2000,
                    random_state=42,
                ),
            ),
        ]:
            start = time.perf_counter()
            model.fit(x[train], y[train])
            training[name] += time.perf_counter() - start
            start = time.perf_counter()
            result = model.predict(x[test])
            inference[name] += time.perf_counter() - start
            for i, guess in zip(test, result, strict=True):
                predictions[name][i] = str(guess)
    metrics = {
        name: {
            "accuracy": round(accuracy_score(y, result), 4),
            "macro_f1": round(f1_score(y, result, average="macro", zero_division=0), 4),
            "train_seconds": round(training[name], 4),
            "batch_inference_ms": round(inference[name] * 1000, 3),
            "errors": [
                {
                    "source": profiles[i]["source_id"],
                    "expected": str(y[i]),
                    "actual": guess,
                }
                for i, guess in enumerate(result)
                if guess != y[i]
            ],
        }
        for name, result in predictions.items()
    }
    timings = []
    for _ in range(10):
        start = time.perf_counter()
        for row in profiles:
            annotate(row)
        timings.append((time.perf_counter() - start) * 1000 / len(profiles))
    return {
        "profiles": len(profiles),
        "domains": len(set(groups)),
        "class_counts": dict(Counter(y)),
        "evaluation": "Five-fold domain-grouped cross-validation; one listing per observation, no network requests.",
        "metrics": metrics,
        "metadata_ms_per_item_median": round(float(np.median(timings)), 3),
        "metadata_ms_per_item_max_batch_mean": round(max(timings), 3),
        "decision": "Keep the bounded evidence classifier in production. Learned models remain an experiment: several media classes have fewer than five independent domains, and jobs/music are absent. Expand independently labeled source coverage before a learned rollout.",
        "limitations": "Exploratory comparison on existing development fixtures, not a blind generalization estimate. All models see the same 45 bounded evidence features; no domain names or source-family labels are input features.",
    }


if __name__ == "__main__":
    corpus = ROOT / "ml/datasets/media-profiles.json"
    if "--capture" in sys.argv:
        profiles, skipped = capture_profiles()
        corpus.write_text(
            json.dumps(profiles, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"captured": len(profiles), "skipped": skipped}))
    profiles = json.loads(corpus.read_text(encoding="utf-8"))
    with threadpool_limits(limits=1):
        report = evaluate(profiles)
    target = ROOT / "ml/reports/media-classification.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
