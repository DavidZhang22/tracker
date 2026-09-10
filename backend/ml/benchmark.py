"""Measure dependency-free inference and bounded feature extraction, without I/O."""

import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tracker.link_model import candidates, load_model


def main():
    tracemalloc.start()
    model = load_model()
    model_current, model_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    html = (
        "<main>"
        + "".join(
            f'<article><h2><a href="/blog/post-{i}">A story about software {i}</a></h2><time datetime="2025-01-01">Jan 1, 2025</time></article>'
            for i in range(1000)
        )
        + "</main>"
    )
    soup = BeautifulSoup(html, "html.parser")
    start = time.perf_counter()
    rows = list(candidates(soup, "https://benchmark.example/blog/"))
    extraction_ms = (time.perf_counter() - start) * 1000
    durations = []
    for _ in range(5):
        start = time.perf_counter()
        for _, _, _, features in rows:
            model.score(features)
        durations.append((time.perf_counter() - start) * 1000)
    result = dict(
        model_id=model.model_id,
        model_file_bytes=(ROOT / "app/tracker/link-model.json").stat().st_size,
        python_model_allocated_bytes=model_current,
        python_model_load_peak_bytes=model_peak,
        feature_extraction_ms_1000=round(extraction_ms, 2),
        inference_ms_1000_median=round(statistics.median(durations), 2),
        training_libraries_loaded=any(
            name in sys.modules for name in ("numpy", "sklearn", "torch", "tensorflow")
        ),
    )
    try:
        import resource

        result["process_peak_rss_kib"] = resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss
    except ImportError:
        pass
    assert not result["training_libraries_loaded"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
