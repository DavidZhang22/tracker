"""Real HTTP checks with the production process pool and a disposable library."""

import asyncio
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from benchmark_parallel import cards


class ReplayFetcher:
    async def get(self, url):
        await asyncio.sleep(0)
        return url, cards(1500 if "slow" in url else 20)


def main():
    # Keep application creation out of module scope: spawned children import this.
    from app.main import create_app
    from app.tracker.models import Entry, Scan

    os.environ["TRACKER_ANALYSIS_WORKERS"] = "2"
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Path(tmp) / "library.db", auth_config={"required": False})
        app.state.discoverer.fetcher = ReplayFetcher()
        items = {}
        with patch("app.tracker.store.time", side_effect=range(1000, 100000, 8)):
            for name in ("slow", "fast"):
                url = f"https://{name}.example/series"
                scan = Scan(
                    url,
                    name,
                    entries=[Entry(f"https://{name}.example/chapter/0", "Chapter 0")],
                )
                items[name] = app.state.store.create(
                    app.state.store.save_scan(scan.to_dict())
                )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        with (
            patch(
                "app.tracker.api.hash",
                lambda iid: 0 if iid == items["slow"]["id"] else 1,
                create=True,
            ),
            patch(
                "app.tracker.discovery.hash",
                lambda url: 0 if "slow" in url else 1,
                create=True,
            ),
        ):
            thread.start()
            try:
                for _ in range(1000):
                    if server.started:
                        break
                    time.sleep(0.01)
                assert server.started
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}", timeout=30
                ) as client:
                    with client.stream("POST", "/api/refresh?stream=true") as response:
                        assert response.status_code == 200
                        lines = response.iter_lines()
                        assert json.loads(next(lines))["type"] == "start"
                        event = json.loads(next(lines))
                        assert (
                            event["type"] == "item"
                            and event["item"]["id"] == items["fast"]["id"]
                        )
                        assert event["item"]["total_count"] == 20
                        assert (
                            app.state.store.item(items["slow"]["id"])["total_count"]
                            == 1
                        )
                        started = time.monotonic()
                        assert client.get("/api/items").status_code == 200
                        read_s = time.monotonic() - started
                        remaining = [json.loads(line) for line in lines]
                        assert remaining[-1]["type"] == "complete"
                        assert (
                            app.state.store.item(items["slow"]["id"])["total_count"]
                            == 1500
                        )
                    jobs = list(app.state.analyzer.last_jobs)
                    assert len({job["pid"] for job in jobs}) == 2
                    assert max(job["started"] for job in jobs) < min(
                        job["finished"] for job in jobs
                    )
                    assert client.get("/api/ready").status_code == 200
                    processes = list(app.state.analyzer._pool._processes.values())
                    print(
                        json.dumps(
                            dict(
                                http_during_parse="passed",
                                read_seconds=read_s,
                                immediate_item_update="passed",
                                concurrent_processes=2,
                                complete_scan_entries=1520,
                                source_requests=0,
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
