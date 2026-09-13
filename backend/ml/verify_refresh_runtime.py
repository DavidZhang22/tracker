"""Real HTTP refresh/cancellation verification using a disposable library only."""

import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app
from app.tracker.discovery import Discoverer
from app.tracker.models import Entry, Scan


class ReplayFetcher:
    async def get(self, url):
        await asyncio.sleep(0)
        return (
            url,
            '<article><a href="/chapter/1">Chapter 1</a></article><article><a href="/chapter/2">Chapter 2</a></article>',
        )


class BlockingParser(Discoverer):
    def __init__(self):
        super().__init__(ReplayFetcher())
        self.entered, self.release = threading.Event(), threading.Event()

    def _parse_page(self, text, url, selector, include_path):
        if "slow.example" in url:
            self.entered.set()
            assert self.release.wait(10), "Parser was never released"
        return super()._parse_page(text, url, selector, include_path)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        scanner = BlockingParser()
        app = create_app(Path(tmp) / "library.db", scanner, {"required": False})
        items = {}
        with patch("app.tracker.store.time", side_effect=range(1000, 100000, 8)):
            for name in ("slow", "fast"):
                url = f"https://{name}.example/series"
                scan = Scan(
                    url,
                    name,
                    entries=[Entry(f"https://{name}.example/chapter/1", "Chapter 1")],
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
                for _ in range(500):
                    if server.started:
                        break
                    time.sleep(0.01)
                assert server.started
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}", timeout=3
                ) as client:
                    with client.stream("POST", "/api/refresh?stream=true") as response:
                        assert response.status_code == 200
                        lines = response.iter_lines()
                        assert json.loads(next(lines))["type"] == "start"
                        assert scanner.entered.wait(3)
                        # The real parser worker is still blocked, but HTTP reads
                        # and the other item's committed update must get through.
                        assert client.get("/api/items").status_code == 200
                        event = json.loads(next(lines))
                        assert (
                            event["type"] == "item"
                            and event["item"]["id"] == items["fast"]["id"]
                        )
                        assert event["item"]["total_count"] == 2
                        assert not scanner.release.is_set()
                    # Closing the response must not release admission while the
                    # parser worker is still running in its protected thread.
                    assert client.post("/api/refresh?stream=true").status_code == 429
                    scanner.release.set()
                    for _ in range(300):
                        if not app.state.scan_guard.active:
                            break
                        time.sleep(0.01)
                    assert not app.state.scan_guard.active
                    assert app.state.store.item(items["slow"]["id"])["total_count"] == 1
                    assert client.post("/api/refresh").json()["checked"] == 2
                    assert app.state.store.item(items["slow"]["id"])["total_count"] == 2
                    assert client.get("/api/ready").status_code == 200
                    print(
                        json.dumps(
                            dict(
                                http_during_parse="passed",
                                immediate_item_update="passed",
                                disconnect_admission="passed",
                                cancelled_item_kept="passed",
                                next_refresh="passed",
                                source_requests=0,
                            )
                        )
                    )
            finally:
                scanner.release.set()
                server.should_exit = True
                thread.join(5)
                sock.close()


if __name__ == "__main__":
    main()
