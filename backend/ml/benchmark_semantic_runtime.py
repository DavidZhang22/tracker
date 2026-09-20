"""Offline embedding parity and allocation benchmark; no network or private data."""

import argparse
import importlib.util
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np

from app.tracker import semantic_model
from app.tracker.semantic_model import Encoder


def timed(fn, repeats=7):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        samples.append(time.perf_counter() - start)
    return result, statistics.median(samples)


def allocated(fn):
    tracemalloc.start()
    fn()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("baseline_encoder", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    baseline.__file__ = semantic_model.__file__
    before, before_load = timed(baseline.Encoder, 1)
    after, after_load = timed(Encoder, 1)
    rows = json.loads(
        (ROOT / "ml/datasets/media-profiles.json").read_text(encoding="utf-8")
    )
    texts = [
        " ".join(
            str(row.get(field, ""))
            for field in ("title", "source_summary", "source_url")
        )
        for row in rows
    ]
    queries = [
        row[0]
        for row in json.loads((ROOT / "ml/datasets/search-queries.json").read_text())[
            "queries"
        ]
    ]
    results = {}
    for name, inputs, query in [
        ("documents", texts, False),
        ("queries", queries, True),
    ]:
        old, bt = timed(
            lambda inputs=inputs, query=query: before.encode(inputs, query=query), 3
        )
        new, at = timed(
            lambda inputs=inputs, query=query: after.encode(inputs, query=query), 3
        )
        a, b = np.asarray(old), np.asarray(new)
        np.testing.assert_allclose(a, b, atol=1e-7, rtol=1e-6)
        results[name] = {
            "count": len(inputs),
            "before_s": bt,
            "after_s": at,
            "max_abs_error": float(np.max(np.abs(a - b))),
        }
    old_docs, new_docs = (
        np.asarray(before.encode(texts)),
        np.asarray(after.encode(texts)),
    )
    old_queries, new_queries = (
        np.asarray(before.encode(queries, query=True)),
        np.asarray(after.encode(queries, query=True)),
    )
    results["top3_order_equal"] = bool(
        np.array_equal(
            np.argsort(old_queries @ old_docs.T, axis=1)[:, -3:],
            np.argsort(new_queries @ new_docs.T, axis=1)[:, -3:],
        )
    )
    assert results["top3_order_equal"]
    rng = np.random.default_rng(19)
    output = rng.normal(size=(8, 192, 384)).astype(np.float32)
    mask = rng.integers(0, 2, size=(8, 192), dtype=np.int64)

    def old_pool():
        return (output * mask[..., None]).sum(axis=1) / np.maximum(
            mask.sum(axis=1, keepdims=True), 1
        )

    def new_pool():
        return np.einsum("bsd,bs->bd", output, mask, dtype=np.float64) / np.maximum(
            mask.sum(axis=1, keepdims=True), 1
        )

    a, bt = timed(old_pool, 100)
    b, at = timed(new_pool, 100)
    np.testing.assert_array_equal(a, b)
    results["pooling"] = {
        "before_ms": bt * 1000,
        "after_ms": at * 1000,
        "before_peak_bytes": allocated(old_pool),
        "after_peak_bytes": allocated(new_pool),
        "exact": True,
    }
    import hashlib

    model = ROOT / "models/minilm-l6/model.onnx"

    def stream_hash():
        with model.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def old_hash():
        return hashlib.sha256(model.read_bytes()).hexdigest()

    assert old_hash() == stream_hash()
    results["checksum"] = {
        "file_bytes": model.stat().st_size,
        "before_peak_bytes": allocated(old_hash),
        "after_peak_bytes": allocated(stream_hash),
    }
    results["load_s"] = {"before": before_load, "after": after_load}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
