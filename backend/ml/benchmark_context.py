"""Measure v2 production inference without NumPy, networking, or database access."""

import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tracker.context_model import MODEL_PATH, load_context_model
from app.tracker.link_context import context_candidates


def main():
    tracemalloc.start()
    model = load_context_model()
    allocated, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert model is not None
    html = (
        "<main>"
        + "".join(
            f'<article><header><h2><a href="/blog/story-{i}">A story about software {i}</a></h2></header><time datetime="2026-09-01">September 1</time></article>'
            for i in range(1000)
        )
        + "</main>"
    )
    soup = BeautifulSoup(html, "html.parser")
    start = time.perf_counter()
    rows = list(context_candidates(soup, "https://benchmark.example/blog/"))
    extraction = (time.perf_counter() - start) * 1000
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        for row in rows:
            model.score(row["features"], row["tokens"])
        samples.append((time.perf_counter() - start) * 1000)
    report = dict(
        model_id=model.model_id,
        artifact_bytes=MODEL_PATH.stat().st_size,
        model_allocated_bytes=allocated,
        model_load_peak_bytes=peak,
        feature_ms_1000=round(extraction, 2),
        inference_ms_1000=round(statistics.median(samples), 2),
        training_libraries_loaded=any(
            m in sys.modules for m in ("numpy", "sklearn", "torch", "tensorflow")
        ),
    )
    try:
        import resource

        report["process_peak_rss_kib"] = resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss
    except ImportError:
        pass
    assert not report["training_libraries_loaded"]
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
