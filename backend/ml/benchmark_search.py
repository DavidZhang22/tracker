"""Offline reproducible retrieval/model comparison; never fetches source pages."""

import argparse
import json
import os
import statistics
import sys
import time
from array import array
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tracker.media_metadata import annotate
from app.tracker.semantic_model import Encoder
from app.tracker.semantic_profile import describe, document
from app.tracker.semantic_search import corrected_query, rank


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        choices=["none", "minilm-l3", "minilm-l6", "bge-small"],
        required=True,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    base = Path(__file__).parent / "datasets"
    rows = [
        annotate(row) | {"id": row["source_id"]}
        for row in json.loads((base / "media-profiles.json").read_text(encoding="utf8"))
    ]
    queries = json.loads((base / "search-queries.json").read_text())["queries"]
    started = time.perf_counter()
    model = None if args.model == "none" else Encoder(args.model)
    load_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    for row in rows:
        row["description_auto"], row["description_method"] = describe(
            row["title"], row["source_summary"], model
        )
    if model:
        vectors = model.encode([document(row) for row in rows])
        for row, vector in zip(rows, vectors, strict=True):
            row["semantic_vector"] = array("f", vector).tobytes()
    index_ms = (time.perf_counter() - started) * 1000
    results, timings = [], []
    for query, expected, group in queries:
        for _repeat in range(3):
            started = time.perf_counter()
            corrected = corrected_query(query, rows, model)
            vector = model.encode([corrected], query=True)[0] if model else None
            scores = rank(
                corrected,
                rows,
                vector,
                floor=0.48 if args.model == "bge-small" else 0.30,
            )
            timings.append((time.perf_counter() - started) * 1000)
        ids = [entry["id"] for entry in scores]
        results.append(
            {
                "query": query,
                "expected": expected,
                "group": group,
                "top3": ids[:3],
                "rank": next(
                    (i + 1 for i, iid in enumerate(ids) if iid in expected), 0
                ),
            }
        )
    groups = {}
    for group in ["exact", "typo", "meaning", "negative"]:
        entries = [row for row in results if row["group"] == group]
        groups[group] = {
            "total": len(entries),
            "top1": sum(row["rank"] == 1 for row in entries),
            "recall3": sum(0 < row["rank"] <= 3 for row in entries),
            "empty": sum(not row["top3"] for row in entries),
        }
    memory = psutil.Process(os.getpid()).memory_info()
    report = {
        "model": args.model,
        "profiles": len(rows),
        "queries": len(queries),
        "load_ms": round(load_ms, 2),
        "index_ms": round(index_ms, 2),
        "query_p50_ms": round(statistics.median(timings), 2),
        "query_p95_ms": round(sorted(timings)[int(len(timings) * 0.95)], 2),
        "rss_mb": round(memory.rss / 2**20, 1),
        "groups": groups,
        "results": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps({key: val for key, val in report.items() if key != "results"}))


if __name__ == "__main__":
    main()
