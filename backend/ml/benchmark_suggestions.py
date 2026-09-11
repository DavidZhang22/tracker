"""Offline, bounded recommendation benchmark; never reads a user's library."""

import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.tracker.models import Entry, Scan
from app.tracker.store import Store
from app.tracker.suggestions import ranked_suggestions


def main():
    with (
        tempfile.TemporaryDirectory() as directory,
        patch("app.tracker.store.time", side_effect=range(1000, 100000, 8)),
    ):
        store = Store(Path(directory) / "benchmark.sqlite3")
        themes = [
            "magic school",
            "space exploration",
            "science nature",
            "software engineering",
            "music performance",
        ]
        for i in range(50):
            url = f"https://source{i}.example/series/original"
            theme = themes[i % len(themes)]
            candidates = [
                {
                    "url": f"https://source{i}.example/series/story-{j}",
                    "title": f"{theme} story {j}",
                    "summary": " ".join(themes) + " fiction, adventure and learning",
                    "kind": "novel",
                }
                for j in range(20)
            ]
            scan = Scan(
                url,
                theme,
                "novel",
                entries=[Entry(url + "/one", "One")],
                suggestions=candidates,
            )
            item = store.create(store.save_scan(scan.to_dict()))
            if i % 4 == 0:
                store.update("items", item["id"], {"favorite": True})
        started = time.perf_counter()
        result = ranked_suggestions(store)
        elapsed = time.perf_counter() - started
        tracemalloc.start()
        ranked_suggestions(store)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert result["total"] == 1000 and len(result["suggestions"]) == 40
        print(
            json.dumps(
                {
                    "candidates": result["total"],
                    "returned": len(result["suggestions"]),
                    "sources": 50,
                    "ranking_seconds": round(elapsed, 3),
                    "peak_rank_allocations_mib": round(peak / 1024 / 1024, 2),
                    "web_requests": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
