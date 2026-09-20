import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.cache import FetchCache
from app.tracker.guards import ApiGuard, RateLimits, ScanGuard
from app.tracker.limits import MAX_ITEMS, MAX_LINKS, MAX_PREVIEWS
from app.tracker.models import Entry, Scan
from app.tracker.store import Store
from app.tracker.urls import DiscoveryError, RequestBudget, SafeFetcher, request_budget
from tests.test_store_api import ROOT, FakeDiscoverer, add, scan


def create(store, entries, url=ROOT):
    data = Scan(url, "Collection", entries=entries).to_dict()
    return store.create(store.save_scan(data))


def test_real_link_cap_applies_to_preview_create_and_repeated_refresh(tmp_path):
    fake = FakeDiscoverer()
    fake.result = scan(
        [Entry(ROOT + f"posts/{n}", f"Chapter {n}", number=n) for n in range(5001)]
    )
    app = create_app(tmp_path / "cap.sqlite3", fake)
    with TestClient(app) as client:
        preview = client.post("/api/scans", json={"url": ROOT}).json()
        assert len(preview["entries"]) == MAX_LINKS == 4999
        item = client.post("/api/items", json={"scan_id": preview["scan_id"]}).json()
        assert item["total_count"] == MAX_LINKS
        store = app.state.store
        lid = item["latest_link"]["id"]
        store.update("links", lid, {"favorite": True, "read": True})
        store.bulk_selected("links", [lid], "delete", item["id"])
        fake.result = scan(
            [
                Entry(ROOT + "posts/4998", "Updated title", number=4998),
                Entry(ROOT + "brand-new", "Extra", number=9999),
            ]
        )
        for _ in range(2):
            result = client.post(f"/api/items/{item['id']}/refresh").json()
            assert result["ok"] and result["new_count"] == 0
        with store.connection() as db:
            assert db.execute("SELECT count(*) FROM links").fetchone()[0] == MAX_LINKS
            old = db.execute("SELECT * FROM links WHERE id=?", (lid,)).fetchone()
            assert old["read"] and old["favorite"] and old["deleted"]
            assert old["title"] == "Updated title"
        assert any("Storage limit" in w for w in store.item(item["id"])["warnings"])


@pytest.mark.parametrize("mode", ["number", "date", "source"])
def test_latest_uses_collection_order_and_skips_ignored_trash(tmp_path, mode):
    store = Store(tmp_path / "latest.sqlite3")
    rows = [
        Entry(ROOT + "posts/90000", "Older", position=0),
        Entry(ROOT + "posts/2", "Latest", position=1),
    ]
    if mode == "number":
        rows[0].number, rows[1].number = 5, 6
        rows.reverse()  # Deliberately newest-first input.
    elif mode == "date":
        rows[0].published_at, rows[1].published_at = "2026-01-01", "2026-01-02"
        rows.reverse()
    else:
        rows[
            0
        ].published_at = "2026-01-01"  # Partial dates must not hide undated latest.
    item = create(store, rows)
    latest = item["latest_link"]
    assert latest["title"] == "Latest"
    store.update("links", latest["id"], {"ignored": True})
    other = store.item(item["id"])["latest_link"]
    assert other["title"] == "Older"
    store.bulk_selected("links", [other["id"]], "delete", item["id"])
    assert store.item(item["id"])["latest_link"] is None


def test_total_library_and_item_limits_cannot_be_bypassed_with_trash(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("app.tracker.store.MAX_LIBRARY_LINKS", 3)
    monkeypatch.setattr("app.tracker.store.MAX_ITEMS", 2)
    store = Store(tmp_path / "library.sqlite3")
    first = create(store, [Entry(ROOT + "1", "One"), Entry(ROOT + "2", "Two")])
    store.bulk_selected("items", [first["id"]], "delete")
    now = time.time() + 8
    monkeypatch.setattr("app.tracker.store.time", lambda: now)
    second = create(
        store, [Entry(ROOT + "3", "Three"), Entry(ROOT + "4", "Four")], ROOT + "other"
    )
    assert second["total_count"] == 1
    with pytest.raises(ValueError, match="2-item limit"):
        create(store, [], ROOT + "third")
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM links").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 2


def test_previews_are_bounded_and_oversized_payload_is_not_written(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "previews.sqlite3")
    old = store.save_scan(scan().to_dict())
    for _ in range(MAX_PREVIEWS):
        store.save_scan(scan().to_dict())
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM scans").fetchone()[0] == MAX_PREVIEWS
        assert db.execute("SELECT id FROM scans WHERE id=?", (old,)).fetchone() is None
    monkeypatch.setattr("app.tracker.store.MAX_PREVIEW_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        store.save_scan(scan().to_dict())


def test_rate_limits_recover_and_do_not_evict_buckets_to_bypass_limits():
    now = [0]
    rates = RateLimits(clock=lambda: now[0], max_keys=2)
    rules = [("global", 10, 60), ("account", 2, 60)]
    rates.charge(rules, 2)
    with pytest.raises(HTTPException) as blocked:
        rates.charge(rules)
    assert blocked.value.status_code == 429
    assert int(blocked.value.headers["Retry-After"]) == 30
    with pytest.raises(HTTPException):
        rates.charge([("new-key", 2, 60)])
    assert len(rates.buckets) == 2
    now[0] = 31
    rates.charge(rules)


def test_scan_admission_is_bounded_and_releases_slots_on_failure():
    guard = ScanGuard()
    with guard.operation("a"), guard.operation("b"), guard.operation("c"):
        for owner in ("a", "d"):
            with pytest.raises(HTTPException) as blocked:
                with guard.operation(owner):
                    pytest.fail("Excess scan admitted")
            assert blocked.value.status_code == 429
    with pytest.raises(RuntimeError), guard.operation("a"):
        raise RuntimeError("scan failed")
    assert not guard.active
    with guard.operation("a"):
        pass


def test_scan_quota_rejects_before_discovery_and_refresh_all_charges_each_item(
    tmp_path,
    monkeypatch,
):
    fake = FakeDiscoverer()
    app = create_app(tmp_path / "quota.sqlite3", fake)
    with TestClient(app) as client:
        add(client)
        fake.result = Scan(
            ROOT + "other", "Other", entries=[Entry(ROOT + "other/one", "One")]
        )
        now = time.time() + 8
        monkeypatch.setattr("app.tracker.store.time", lambda: now)
        add(client)
        guard = app.state.scan_guard
        guard.rates.charge(
            [(f"scan:{app.state.store.path}", MAX_ITEMS, 3600)], MAX_ITEMS - 3
        )
        calls = len(fake.calls)
        assert client.post("/api/refresh").status_code == 429
        assert len(fake.calls) == calls
        guard.rates.charge([(f"scan:{app.state.store.path}", MAX_ITEMS, 3600)])
        response = client.post("/api/scans", json={"url": ROOT})
        assert response.status_code == 429 and "retry-after" in response.headers
        assert len(fake.calls) == calls


async def test_concurrent_scan_is_rejected_instead_of_queued(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowDiscoverer:
        async def scan(self, *args, **kwargs):
            started.set()
            await release.wait()
            return scan()

    app = create_app(tmp_path / "concurrent.sqlite3", SlowDiscoverer())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        running = asyncio.create_task(client.post("/api/scans", json={"url": ROOT}))
        await asyncio.wait_for(started.wait(), 2)
        try:
            rejected = await asyncio.wait_for(
                client.post("/api/scans", json={"url": ROOT}), 2
            )
            assert rejected.status_code == 429
        finally:
            release.set()
            assert (await running).status_code == 200


@pytest.mark.parametrize(
    "headers,chunks,status",
    [
        ([(b"content-length", b"99")], [b"x"], 413),
        ([], [b"123456", b"78901"], 413),
        ([], [b"12", b"34"], 200),
    ],
)
async def test_body_guard_checks_declared_and_chunked_sizes(headers, chunks, status):
    seen, responses = [], []

    async def app(scope, receive, send):
        seen.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        body = chunks.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(chunks)}

    async def send(message):
        responses.append(message)

    guard = ApiGuard(app, max_body=10)
    await guard(
        {"type": "http", "path": "/api/scans", "headers": headers}, receive, send
    )
    assert responses[0]["status"] == status
    assert seen == ([b"1234"] if status == 200 else [])
    assert guard.active == 0


async def test_slow_request_body_times_out_and_releases_capacity():
    responses = []

    async def receive():
        await asyncio.Event().wait()

    async def app(*args):
        pytest.fail("Slow body reached app")

    async def send(message):
        responses.append(message)

    guard = ApiGuard(app, body_timeout=0.01)
    await guard({"type": "http", "path": "/api/scans"}, receive, send)
    assert responses[0]["status"] == 408 and guard.active == 0


async def test_total_response_budget_includes_cache_hits():
    cache = FetchCache()
    for suffix in ("a", "b"):
        url = ROOT + suffix
        cache.put(url, {"body": "123456", "final": url, "expires": time.time() + 60})
    token = request_budget.set(RequestBudget(byte_limit=10))
    try:
        fetcher = SafeFetcher(cache)
        assert (await fetcher.get(ROOT + "a"))[1] == "123456"
        with pytest.raises(DiscoveryError, match="response-size budget"):
            await fetcher.get(ROOT + "b")
    finally:
        request_budget.reset(token)


def test_existing_over_limit_library_is_preserved(tmp_path, monkeypatch):
    store = Store(tmp_path / "existing.sqlite3")
    item = create(store, [Entry(ROOT + str(n), f"Entry {n}") for n in range(4)])
    monkeypatch.setattr("app.tracker.store.MAX_LINKS", 3)
    assert store.merge(item["id"], scan([Entry(ROOT + "new", "New")]).to_dict()) == 0
    assert store.item(item["id"])["total_count"] == 4


def test_oversized_refresh_rolls_back_without_changing_progress(tmp_path, monkeypatch):
    store = Store(tmp_path / "large-refresh.sqlite3")
    item = create(store, [Entry(ROOT + "one", "Original")])
    monkeypatch.setattr("app.tracker.store.MAX_PREVIEW_BYTES", 10)
    with pytest.raises(DiscoveryError, match="too large"):
        store.merge(item["id"], scan([Entry(ROOT + "one", "Replacement")]).to_dict())
    assert store.item(item["id"])["latest_link"]["title"] == "Original"


def test_real_item_limit_includes_trash_and_allows_500th_item(tmp_path):
    store = Store(tmp_path / "item-cap.sqlite3")
    with store.connection() as db:
        db.executemany(
            "INSERT INTO items(id,url,title,kind,created_at,deleted,media_version) VALUES (?,?,?,?,?,?,999)",
            [
                (
                    f"old-{i}",
                    f"https://example.org/{i}",
                    f"Item {i}",
                    "website",
                    "2026-01-01",
                    i % 2,
                )
                for i in range(499)
            ],
        )
    last = create(store, [], ROOT + "last")
    assert MAX_ITEMS == 500 and last["id"]
    with pytest.raises(ValueError, match="500-item limit"):
        create(store, [], ROOT + "excess")
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 500
    assert len(store.semantic_records()) == 251


def test_full_library_refresh_fits_account_quota_but_remains_bounded():
    guard = ScanGuard()
    with guard.operation("one", cost=MAX_ITEMS):
        pass
    with pytest.raises(HTTPException):
        with guard.operation("one"):
            pytest.fail("Account allowance bypassed")
    with pytest.raises(HTTPException):
        with guard.operation("two", cost=MAX_ITEMS):
            pytest.fail("Global allowance bypassed")
