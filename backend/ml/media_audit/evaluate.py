"""Offline fixed-model diagnostics and zero-network registry lookup profiling."""

import json
import statistics
import tempfile
import time
from pathlib import Path

from app.tracker.cascade_model import load_cascade_model
from app.tracker.context_model import load_context_model
from app.tracker.link_model import FEATURES, load_model
from app.tracker.source_registry import SourceRegistry
from ml.artifacts import read_json, read_text, write_text
from ml.evaluation import metrics

ROOT = Path(__file__).resolve().parents[2]


def main():
    report = read_json(ROOT / "ml/reports/media200.json.gz")
    rows = [
        json.loads(line)
        for line in read_text(ROOT / "ml/datasets/media200-links.jsonl.gz").splitlines()
    ]
    selected = [r for r in rows if r["split"] == "test"]
    legacy, context, cascade = load_model(), load_context_model(), load_cascade_model()
    models = {
        "legacy": (
            legacy,
            lambda values: [
                legacy.score(r["features"][: len(FEATURES)]) for r in values
            ],
        ),
        "context": (context, context.score_many),
        "deep": (cascade.deep, cascade.deep.score_many),
        "cascade": (cascade, cascade.score_many),
    }
    report["fixed_model_diagnostics"] = {}
    for name, (model, predict) in models.items():
        started = time.perf_counter()
        scores = predict(selected)
        elapsed = time.perf_counter() - started
        report["fixed_model_diagnostics"][name] = dict(
            model_id=model.model_id,
            seconds=round(elapsed, 5),
            threshold=model.upper,
            metrics=metrics(selected, [s >= model.upper for s in scores]),
        )
    report["evaluation_scope"] = (
        "Frozen models on partial weak-labelled test rows only. Rules have selection bias; these scores are diagnostic, not website support/accuracy claims. No training, threshold tuning, or production model promotion."
    )
    with tempfile.TemporaryDirectory(dir=ROOT / "data/media200") as directory:
        registry = SourceRegistry(Path(directory) / "sources.sqlite3")
        urls = [
            row["checked_url"]
            for row in read_json(ROOT / "app/tracker/source_status.json")
        ]
        times = []
        for _ in range(5):
            for url in urls:
                start = time.perf_counter()
                registry.lookup(url)
                times.append((time.perf_counter() - start) * 1000)
        report["registry_profile"] = dict(
            lookups=len(times),
            rows=len(urls),
            median_ms=round(statistics.median(times), 4),
            p95_ms=round(sorted(times)[int(len(times) * 0.95)], 4),
            max_ms=round(max(times), 4),
            network_requests=0,
            database_bytes=registry.path.stat().st_size,
        )
    write_text(
        ROOT / "ml/reports/media200.json.gz",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k in {"registry_profile", "evaluation_scope"}
            }
        )
    )
    print(
        json.dumps(
            {
                k: {
                    "seconds": v["seconds"],
                    "metrics": {
                        m: n for m, n in v["metrics"].items() if m != "by_source"
                    },
                }
                for k, v in report["fixed_model_diagnostics"].items()
            }
        )
    )


if __name__ == "__main__":
    main()
