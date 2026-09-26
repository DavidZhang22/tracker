import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.api import refresh_item
from app.tracker.models import Entry, Scan
from app.tracker.urls import DiscoveryError
from tests.test_accounts import CONFIG, sign_up
from tests.test_accounts import client as account_client
from tests.test_store_api import FakeDiscoverer, add


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "library.db", FakeDiscoverer(), {"required": False})
    ) as session:
        yield session


def preview(client, entries=None, **options):
    return client.post(
        "/api/scans/manual",
        json={
            "entries": entries
            or [{"title": "Practice recursion", "context": "Try three examples"}],
            **options,
        },
    )


def manual_item(client):
    response = preview(client)
    assert response.status_code == 200, response.text
    saved = client.post(
        "/api/items",
        json={
            "scan_id": response.json()["scan_id"],
            "title": "Study plan",
            "read_indices": [0],
        },
    )
    assert saved.status_code == 201, saved.text
    return saved.json()


def test_manual_preview_create_and_add_preserves_metadata_and_progress(client):
    with patch("socket.create_connection", side_effect=AssertionError("No network")):
        item = manual_item(client)
        assert item["source_urls"] == [] and item["read_count"] == 1
        before = client.get(f"/api/items/{item['id']}").json()
        new = preview(
            client,
            [
                {
                    "title": "56. Merge Intervals",
                    "context": "Sort first",
                    "published_at": "2026-09-26",
                }
            ],
            item_id=item["id"],
        )
        assert new.status_code == 200, new.text
        row = new.json()["entries"][0]
        assert (
            row["number"] == 56
            and row["url"] == ""
            and row["published_at"].startswith("2026-09-26")
        )
        assert client.get(f"/api/items/{item['id']}").json()["total_count"] == 1
        result = client.post(
            f"/api/items/{item['id']}/entries", json={"scan_id": new.json()["scan_id"]}
        )
        assert result.status_code == 200, result.text
        saved = result.json()
        assert saved["total_count"] == 2 and saved["read_count"] == 1
        for field in (
            "url",
            "source_type",
            "source_name",
            "source_urls",
            "title",
            "last_checked_at",
            "methods",
        ):
            assert saved[field] == before[field]
        assert (
            client.post(
                f"/api/items/{item['id']}/entries",
                json={"scan_id": new.json()["scan_id"]},
            ).status_code
            == 422
        )
        assert client.post(f"/api/items/{item['id']}/refresh").status_code == 422


@pytest.mark.parametrize(
    "entry",
    [
        {"title": " "},
        {"title": "x", "url": "javascript:alert(1)"},
        {"title": "x", "url": "http://127.0.0.1/a"},
        {"title": "x", "url": "https://user:password@example.org"},
        {"title": "x", "number": -1},
        {"title": "x", "number": 1_000_001},
        {"title": "x", "published_at": "yesterday"},
        {"title": "x", "context": "a" * 8193},
    ],
)
def test_manual_validation(client, entry):
    assert preview(client, [entry]).status_code == 422
    assert client.app.state.discoverer.calls == []


def test_manual_batch_limit_and_url_dedup(client):
    assert preview(client, [{"title": str(i)} for i in range(101)]).status_code == 422
    result = preview(
        client,
        [
            {"title": "One", "url": "https://example.org/post?utm_source=x"},
            {"title": "Two", "url": "https://example.org/post/"},
        ],
    ).json()
    assert len(result["entries"]) == 1 and result["warnings"]


def test_manual_target_binding_and_upload_append_to_web_item(client):
    item = add(client)
    original = deepcopy(item)
    data = b"Problem,Approach\nCourse Schedule,DFS"
    response = client.post(
        "/api/scans/import",
        params={"filename": "notes.csv", "item_id": item["id"], "append": True},
        content=data,
    )
    assert response.status_code == 200, response.text
    scan_id = response.json()["scan_id"]
    assert client.post("/api/items", json={"scan_id": scan_id}).status_code == 422
    saved = client.post(f"/api/items/{item['id']}/entries", json={"scan_id": scan_id})
    assert saved.status_code == 200, saved.text
    assert saved.json()["total_count"] == 2 and saved.json()["source_type"] == "web"
    assert saved.json()["source_urls"] == original["source_urls"]
    assert client.app.state.discoverer.calls == [item["url"]]


class Sources:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.results = []

    async def scan(self, url, *args, **kwargs):
        self.calls.append((url, args, kwargs))
        if url == self.failure:
            raise DiscoveryError("HTTP 403")
        result = Scan(
            url,
            "Source",
            entries=[
                Entry("https://content.example/shared", "Shared"),
                Entry(url + "/entry", "Unique"),
            ],
            coverage="complete",
            requests_made=1,
            checked_at="2026-09-26T12:00:00+00:00",
        )
        self.results.append(deepcopy(result))
        return result


def test_multiple_sources_refresh_imported_items_and_deduplicate(client):
    item = manual_item(client)
    scanner = client.app.state.discoverer = Sources()
    urls = ["https://a.example/list", "https://b.example/list"]
    changed = client.patch(
        f"/api/items/{item['id']}",
        json={
            "source_urls": urls,
            "keywords": "English",
            "selector": "li",
            "include_path": "/entry",
        },
    )
    assert changed.status_code == 200 and changed.json()["source_urls"] == urls
    assert scanner.calls == []
    refreshed = client.post(f"/api/items/{item['id']}/refresh").json()
    assert (
        refreshed["ok"]
        and refreshed["new_count"] == 3
        and refreshed["requests_made"] == 2
    )
    rows = client.get(f"/api/items/{item['id']}/links").json()["links"]
    assert (
        len(rows) == 4
        and len([r for r in rows if r["url"] == "https://content.example/shared"]) == 1
    )
    assert client.get(f"/api/items/{item['id']}").json()["read_count"] == 1
    assert scanner.calls[0][1] == ("li", "/entry") and scanner.calls[1][1] == ("", "")
    assert all(call[2]["keywords"] == "English" for call in scanner.calls)
    assert client.post("/api/refresh").json()["checked"] == 1
    assert client.get(f"/api/items/{item['id']}").json()["total_count"] == 4
    client.patch(f"/api/items/{item['id']}", json={"source_urls": []})
    assert client.post("/api/refresh").json()["checked"] == 0


def test_failed_source_keeps_successes_and_existing_entries(client):
    item = manual_item(client)
    scanner = client.app.state.discoverer = Sources()
    scanner.failure = "https://b.example/list"
    client.patch(
        f"/api/items/{item['id']}",
        json={"source_urls": ["https://a.example/list", scanner.failure]},
    )
    result = client.post(f"/api/items/{item['id']}/refresh").json()
    assert (
        not result["ok"] and result["new_count"] == 2 and "b.example" in result["error"]
    )
    saved = client.get(f"/api/items/{item['id']}").json()
    assert saved["total_count"] == 3 and saved["read_count"] == 1
    assert saved["coverage"] == "partial" and "403" in saved["error"]


@pytest.mark.parametrize(
    "urls",
    [
        ["http://127.0.0.1/a"],
        ["https://user:secret@example.org"],
        ["https://example.org/a", "https://example.org/a/"],
        [f"https://example.org/{i}" for i in range(6)],
    ],
)
def test_source_validation_without_network(client, urls):
    item = manual_item(client)
    assert (
        client.patch(f"/api/items/{item['id']}", json={"source_urls": urls}).status_code
        == 422
    )
    assert client.app.state.discoverer.calls == []


def test_account_scope_origin_and_preview_ownership(tmp_path):
    app = create_app(tmp_path / "auth.db", Sources(), CONFIG)
    with account_client(app) as alice, account_client(app) as bob:
        assert preview(alice).status_code == 401
        sign_up(alice, "alice")
        sign_up(bob, "bob")
        item = manual_item(alice)
        assert preview(bob, item_id=item["id"]).status_code == 404
        result = preview(alice, item_id=item["id"]).json()
        assert (
            bob.post(
                f"/api/items/{item['id']}/entries", json={"scan_id": result["scan_id"]}
            ).status_code
            == 404
        )
        assert (
            alice.post(
                "/api/scans/manual",
                headers={"Origin": "https://evil.example"},
                json={"entries": [{"title": "x"}]},
            ).status_code
            == 403
        )


async def test_parallel_sources_cancel_cleanly_and_removed_sources_do_not_merge(
    tmp_path,
):
    app = create_app(tmp_path / "library.db", Sources(), {"required": False})
    store = app.state.store
    item = store.create(
        store.save_scan(
            Scan(
                "https://a.example/",
                "A",
                entries=[Entry("https://a.example/old", "Old")],
            ).to_dict()
        )
    )
    store.update(
        "items",
        item["id"],
        {"source_urls": ["https://a.example/", "https://b.example/"]},
    )
    started, release = asyncio.Event(), asyncio.Event()
    active = 0

    async def scan(url, *args, **kwargs):
        nonlocal active
        active += 1
        if active == 2:
            started.set()
        try:
            await release.wait()
            return Scan(url, "new", entries=[Entry(url + "new", "New")])
        finally:
            active -= 1

    app.state.discoverer.scan = scan
    task = asyncio.create_task(refresh_item(item["id"], app, store))
    await asyncio.wait_for(started.wait(), 2)
    assert active == 2
    store.update("items", item["id"], {"source_urls": []})
    release.set()
    assert (await task)["skipped"] and not active
    assert store.item(item["id"])["total_count"] == 1
    store.update(
        "items",
        item["id"],
        {"source_urls": ["https://a.example/", "https://b.example/"]},
    )
    release.clear()
    started.clear()
    task = asyncio.create_task(refresh_item(item["id"], app, store))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not active


@pytest.mark.parametrize("linked", [False, True])
def test_independent_sources_do_not_merge_equal_local_ids(client, linked):
    item = manual_item(client)
    urls = ["https://a.example/list", "https://b.example/list"]
    original = []

    async def scan(url, *args, **kwargs):
        entry = Entry(
            url + "/one" if linked else "",
            "Chapter 1",
            source_id="local-record-1",
            context=url,
        )
        result = Scan(url, "Source", entries=[entry])
        original.append(result)
        return result

    client.app.state.discoverer.scan = scan
    client.patch(f"/api/items/{item['id']}", json={"source_urls": urls})
    assert client.post(f"/api/items/{item['id']}/refresh").json()["new_count"] == 2
    assert client.post(f"/api/items/{item['id']}/refresh").json()["new_count"] == 0
    assert client.get(f"/api/items/{item['id']}").json()["total_count"] == 3
    assert all(result.entries[0].source_id == "local-record-1" for result in original)
    client.patch(f"/api/items/{item['id']}", json={"source_urls": list(reversed(urls))})
    assert client.post(f"/api/items/{item['id']}/refresh").json()["new_count"] == 0


def test_each_source_consumes_scan_quota(client):
    item = manual_item(client)
    client.app.state.discoverer = Sources()
    client.patch(
        f"/api/items/{item['id']}",
        json={"source_urls": ["https://a.example/list", "https://b.example/list"]},
    )
    guard = client.app.state.scan_guard
    with patch.object(guard.rates, "charge", wraps=guard.rates.charge) as charge:
        assert client.post(f"/api/items/{item['id']}/refresh").status_code == 200
        assert charge.call_args.args[1] == 2
        assert client.post("/api/refresh").status_code == 200
        assert charge.call_args.args[1] == 2


def test_original_source_identity_survives_url_normalization_and_shared_links(client):
    item = add(client)
    urls = [item["url"].rstrip("/"), "https://other.example/list"]
    source_id = "native-id-1"
    current = "https://content.example/first"

    async def scan(url, *args, **kwargs):
        return Scan(url, "A", entries=[Entry(current, "Original", source_id=source_id)])

    client.app.state.discoverer.scan = scan
    assert (
        client.patch(f"/api/items/{item['id']}", json={"source_urls": urls}).status_code
        == 200
    )
    assert client.post(f"/api/items/{item['id']}/refresh").json()["new_count"] == 1
    first = next(
        r
        for r in client.get(f"/api/items/{item['id']}/links").json()["links"]
        if r["url"] == current
    )
    assert first["source_id"] == source_id
    client.patch(f"/api/links/{first['id']}", json={"read": True})
    current = "https://content.example/renamed"
    assert client.post(f"/api/items/{item['id']}/refresh").json()["new_count"] == 0
    changed = next(
        r
        for r in client.get(f"/api/items/{item['id']}/links").json()["links"]
        if r["id"] == first["id"]
    )
    assert changed["url"] == current and changed["read"]


@pytest.mark.parametrize("date", ["1850-01-01", "2026-09-26T00:00:00Z", "2150-12-31"])
def test_manual_dates_keep_calendar_day_precision(client, date):
    result = preview(client, [{"title": "Dated entry", "published_at": date}])
    assert result.status_code == 200, result.text
    entry = result.json()["entries"][0]
    assert entry["published_at"].startswith(date[:10])
    assert entry["date_precision"] == "day"
