"""Reproducible large-collection benchmark; no network or account data."""

import json
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup

from app.tracker.record_context import MODEL_PATH, RecordContext, load_record_model


def main():
    tracemalloc.start()
    model = load_record_model()
    allocated, load_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert model is not None
    html = (
        "<main>"
        + "".join(
            f'<article><div><a href="/chapter/{i}">Chapter {i}</a></div><div><span lang="en">English</span>Official <time datetime="2026-09-10">Sep 10</time></div></article>'
            for i in range(4999)
        )
        + "</main>"
    )
    start = time.perf_counter()
    soup = BeautifulSoup(html, "html.parser")
    context = RecordContext(soup)
    matched = sum(
        "English" in context.text(a) and context.language(a) == "en"
        for a in soup.select("a")
    )
    seconds = time.perf_counter() - start
    report = dict(
        model_id=model["model_id"],
        model_bytes=MODEL_PATH.stat().st_size,
        model_allocated_bytes=allocated,
        model_load_peak_bytes=load_peak,
        links=4999,
        matched=matched,
        extraction_seconds=round(seconds, 3),
        training_libraries_loaded=any(
            name in sys.modules for name in ("numpy", "sklearn", "torch")
        ),
    )
    try:
        import resource

        report["peak_process_rss_mib"] = round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2
        )
    except ImportError:
        pass
    assert matched == 4999
    assert not report["training_libraries_loaded"]
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
