"""Run in the production image with --network none --memory 1g --cpus 2.

Creates only temporary synthetic library data. Profiles real model inference
alongside both existing link parser workers; performs no website requests.
"""

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tracker.analysis_pool import PageAnalyzer
from app.tracker.models import Entry, Scan
from app.tracker.semantic_model import encoder
from app.tracker.semantic_search import SemanticSearch
from app.tracker.store import Store


async def main():
    profiles = json.loads(
        (Path(__file__).parent / "datasets/media-profiles.json").read_text()
    )
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "library.sqlite3")
        for index in range(200):
            profile = profiles[index % len(profiles)]
            payload = Scan(
                f"https://listing.example/{index}",
                profile["title"] or "Untitled",
                profile["kind"],
                [Entry(f"https://listing.example/{index}/1", "Entry 1")],
                source_summary=profile["source_summary"],
            ).to_dict()
            store.create(store.save_scan(payload))
            with store.connection() as db:
                db.execute("DELETE FROM addition_cooldown")
        model = encoder()
        assert model is not None, "Production model must be installed"
        service = SemanticSearch(lambda: model)
        analyzer = PageAnalyzer(2)
        await analyzer.start()
        try:
            started = time.perf_counter()
            await asyncio.to_thread(service.enrich, store)
            index_seconds = time.perf_counter() - started
            timings = []

            async def queries():
                for query in [
                    "pyhton bytes",
                    "young wizard studying magic",
                    "art shows and paintings",
                ] * 10:
                    started = time.perf_counter()
                    result = await asyncio.to_thread(service.search, store, query)
                    assert result["semantic"] and result["scores"]
                    timings.append((time.perf_counter() - started) * 1000)

            html = (
                "<title>Example Novel</title><main>"
                + "".join(
                    f'<div class="chapter"><a href="/chapter/{i}">Chapter {i}</a><time datetime="2026-09-01">September 1, 2026</time></div>'
                    for i in range(4000)
                )
                + "</main>"
            )
            await asyncio.gather(
                queries(),
                analyzer.analyze(html, "https://listing.example/novel"),
                analyzer.analyze(html, "https://listing.example/novel2"),
            )
            assert len(store.semantic_records()) == 200
            before = store.semantic_records()[0]["semantic_key"]
            started = time.perf_counter()
            service.enrich(store)
            cached_ms = (time.perf_counter() - started) * 1000
            assert store.semantic_records()[0]["semantic_key"] == before
            peak = Path("/sys/fs/cgroup/memory.peak")
            report = {
                "items": 200,
                "parsers": 2,
                "parallel_html_rows": 8000,
                "index_seconds": round(index_seconds, 3),
                "cached_index_check_ms": round(cached_ms, 2),
                "query_p50_ms_with_parsers": round(statistics.median(timings), 2),
                "query_p95_ms_with_parsers": round(sorted(timings)[28], 2),
                "container_peak_mb": round(int(peak.read_text()) / 2**20, 1)
                if peak.exists()
                else None,
                "network_requests": 0,
            }
            print(json.dumps(report))
        finally:
            await analyzer.aclose()


if __name__ == "__main__":
    os.environ["TRACKER_SEMANTIC_SEARCH"] = "1"
    asyncio.run(main())
