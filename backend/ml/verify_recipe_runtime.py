"""Real HTTP light/deep refresh checks in the production process pool.

Uses an isolated temporary library and offline listing responses only.
"""

import asyncio
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from benchmark_parallel import cards


class ReplayFetcher:
    def __init__(self):
        from app.tracker.cache import FetchCache

        self.cache = FetchCache()
        self.count = 1000
        self.calls = []

    async def get(self, url):
        self.calls.append(url)
        await asyncio.sleep(0)
        return url, cards(self.count)


def main():
    # Spawned workers import this module; keep application startup in main.
    from app.main import create_app

    os.environ["TRACKER_ANALYSIS_WORKERS"] = "2"
    source = "https://example.org/series/book"
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Path(tmp) / "library.db", auth_config={"required": False})
        fetcher = ReplayFetcher()
        app.state.discoverer.fetcher = fetcher
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
        processes = []
        try:
            for _ in range(1000):
                if server.started:
                    break
                time.sleep(0.01)
            assert server.started
            with httpx.Client(
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=60
            ) as client:
                response = client.post("/api/scans", json={"url": source})
                assert response.status_code == 200, response.text
                preview = response.json()
                assert preview["analysis_mode"] == "deep", preview.keys()
                response = client.post(
                    "/api/items", json={"scan_id": preview["scan_id"]}
                )
                assert response.status_code == 201, response.text
                iid = response.json()["id"]
                fetcher.count = 1001
                with fetcher.cache.lock:
                    for key, value in fetcher.cache.memory.items():
                        if key.startswith("scan:"):
                            value["checked"] = 0
                response = client.post(f"/api/items/{iid}/refresh")
                assert response.status_code == 200, response.text
                light = response.json()
                assert light["analysis_mode"] == "light" and light["new_count"] == 1, (
                    light
                )
                response = client.post(f"/api/items/{iid}/refresh?deep=true")
                assert response.status_code == 200, response.text
                deep = response.json()
                assert deep["analysis_mode"] == "deep" and not deep["cached"], deep
                assert client.get(f"/api/items/{iid}").json()["total_count"] == 1001
                assert deep["new_count"] == 0
                assert fetcher.calls == [source] * 3
                assert client.get("/api/ready").status_code == 200
                processes = list(app.state.analyzer._pool._processes.values())
                print(
                    json.dumps(
                        dict(
                            initial_deep="passed",
                            default_light="passed",
                            explicit_deep="passed",
                            complete_scan_entries=1001,
                            new_links=1,
                            listing_replays=3,
                            content_requests=0,
                            real_source_requests=0,
                            analysis_jobs=len(app.state.analyzer.last_jobs),
                            ready="passed",
                        )
                    )
                )
        finally:
            server.should_exit = True
            thread.join(30)
            sock.close()
        assert not thread.is_alive() and all(not p.is_alive() for p in processes)


if __name__ == "__main__":
    main()
