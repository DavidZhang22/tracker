import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.api import refresh_events, refresh_item
from app.tracker.models import Entry, Scan


@pytest.fixture
def app(tmp_path, monkeypatch):
    clock = iter(range(1000, 100000, 8))
    monkeypatch.setattr("app.tracker.store.time", lambda: next(clock))
    return create_app(tmp_path / "library.sqlite3")


def add(app, name):
    url = f"https://{name}.example/series"
    scan = Scan(url, name, entries=[Entry(url + "/one", "One")])
    return app.state.store.create(app.state.store.save_scan(scan.to_dict()))


class ControlledScanner:
    def __init__(self):
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.active = 0
        self.calls = []

    async def scan(self, url, *args, **kwargs):
        self.active += 1
        self.calls.append(url)
        try:
            if "slow" in url:
                self.started.set()
                await self.release.wait()
            return Scan(
                url,
                "Updated " + url,
                entries=[Entry(url + "/one", "One"), Entry(url + "/two", "Two")],
            )
        finally:
            self.active -= 1


async def test_fast_item_is_emitted_while_other_item_is_still_scanning(
    app, monkeypatch
):
    slow, fast = add(app, "slow"), add(app, "fast")
    monkeypatch.setattr(
        "app.tracker.api.hash", lambda iid: 0 if iid == slow["id"] else 1, raising=False
    )
    scanner = app.state.discoverer = ControlledScanner()
    events = refresh_events([slow, fast], app, app.state.store)
    assert (await anext(events))["type"] == "start"
    update = await asyncio.wait_for(anext(events), 2)
    assert update["item"]["id"] == fast["id"]
    assert update["item"]["total_count"] == 2 and update["checked"] == 1
    assert scanner.started.is_set() and scanner.active == 1
    scanner.release.set()
    assert (await anext(events))["item"]["id"] == slow["id"]
    complete = await anext(events)
    assert complete["type"] == "complete" and complete["checked"] == 2
    await events.aclose()
    assert scanner.active == 0


async def test_cancelled_stream_releases_inflight_scanners(app):
    slow = add(app, "slow")
    scanner = app.state.discoverer = ControlledScanner()
    events = refresh_events([slow], app, app.state.store)
    await anext(events)
    await asyncio.wait_for(scanner.started.wait(), 2)
    await events.aclose()
    assert scanner.active == 0
    assert app.state.store.item(slow["id"])["total_count"] == 1


async def test_slow_scan_emits_heartbeat_without_finishing_early(app, monkeypatch):
    slow = add(app, "slow")
    scanner = app.state.discoverer = ControlledScanner()
    monkeypatch.setattr("app.tracker.api.REFRESH_HEARTBEAT_SECONDS", 0.01)
    events = refresh_events([slow], app, app.state.store)
    await anext(events)
    assert (await asyncio.wait_for(anext(events), 1))["type"] == "heartbeat"
    assert scanner.active == 1
    scanner.release.set()
    remaining = [event async for event in events]
    assert remaining[-1]["type"] == "complete"
    assert remaining[-1]["checked"] == 1


async def test_item_trashed_mid_scan_is_not_updated(app):
    item = add(app, "slow")
    scanner = app.state.discoverer = ControlledScanner()
    running = asyncio.create_task(refresh_item(item["id"], app, app.state.store))
    await asyncio.wait_for(scanner.started.wait(), 2)
    app.state.store.bulk_selected("items", [item["id"]], "delete")
    before = app.state.store.item(item["id"])
    scanner.release.set()
    assert (await running)["skipped"]
    assert app.state.store.item(item["id"]) == before


def test_stream_api_keeps_ignored_and_trash_out_and_releases_admission(app):
    active, ignored, trashed = [
        add(app, name) for name in ("fast", "ignored", "trashed")
    ]
    app.state.store.update("items", ignored["id"], {"ignored": True})
    app.state.store.bulk_selected("items", [trashed["id"]], "delete")
    scanner = app.state.discoverer = ControlledScanner()
    with TestClient(app) as client:
        response = client.post("/api/refresh?stream=true")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.text.splitlines()]
        assert [e["type"] for e in events] == ["start", "item", "complete"]
        assert events[1]["item"]["id"] == active["id"]
        assert events[-1]["checked"] == 1
        assert scanner.calls == [active["url"]]
        assert not app.state.scan_guard.active
        assert client.post(f"/api/items/{trashed['id']}/refresh").status_code == 422


def test_stream_quota_errors_are_http_errors_before_headers(app):
    add(app, "fast")
    with (
        TestClient(app) as client,
        app.state.scan_guard.operation(app.state.store.path),
    ):
        response = client.post("/api/refresh?stream=true")
        assert response.status_code == 429 and response.headers["retry-after"] == "10"


def test_database_failure_in_stream_is_safe_and_does_not_leave_busy_account(
    app, monkeypatch
):
    item = add(app, "fast")
    app.state.discoverer = ControlledScanner()

    def fail(*args):
        raise sqlite3.OperationalError("secret internal path")

    monkeypatch.setattr(app.state.store, "merge", fail)
    with TestClient(app) as client:
        response = client.post("/api/refresh?stream=true")
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "error" and "Storage" in events[-1]["detail"]
        assert "secret" not in response.text
        assert app.state.store.item(item["id"])["total_count"] == 1
        assert not app.state.scan_guard.active
