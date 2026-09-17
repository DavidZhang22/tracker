import asyncio
from types import SimpleNamespace

import pytest

from app.tracker.api import refresh_item
from app.tracker.discovery import Discoverer
from app.tracker.models import Entry, Scan
from app.tracker.workers import KeyedLocks


def colliding_keys(prefix):
    buckets = {}
    for number in range(65):
        key = f"{prefix}{number}"
        bucket = hash(key) % 64
        if bucket in buckets:
            return buckets[bucket], key
        buckets[bucket] = key
    raise AssertionError("65 keys must collide across 64 stripes")


async def test_matching_keys_serialize_through_handoff_and_release():
    locks = KeyedLocks()
    release = asyncio.Event()
    entered = []

    async def operation(number):
        async with locks.hold("same"):
            entered.append(number)
            if number == 1:
                await release.wait()

    first = asyncio.create_task(operation(1))
    await asyncio.sleep(0)
    second = asyncio.create_task(operation(2))
    await asyncio.sleep(0)
    assert entered == [1]
    assert locks._entries["same"].users == 2
    release.set()
    third = asyncio.create_task(operation(3))
    await asyncio.wait_for(asyncio.gather(first, second, third), 1)
    assert entered == [1, 2, 3]
    assert not locks._entries


async def test_distinct_keys_remain_independent_even_with_identical_hashes():
    class CollidingKey(str):
        def __hash__(self):
            return 1

    locks = KeyedLocks()
    first, second = CollidingKey("first"), CollidingKey("second")
    assert first != second and hash(first) == hash(second)
    async with locks.hold(first):
        async with asyncio.timeout(1):
            async with locks.hold(second):
                assert len(locks._entries) == 2
        assert len(locks._entries) == 1
    assert not locks._entries


async def test_cancelled_waiter_does_not_remove_held_lock():
    locks = KeyedLocks()
    entered = []

    async def operation():
        async with locks.hold("same"):
            entered.append(True)

    async with locks.hold("same"):
        waiter = asyncio.create_task(operation())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert locks._entries["same"].users == 1
        replacement = asyncio.create_task(operation())
        await asyncio.sleep(0)
        assert not entered and locks._entries["same"].users == 2
    await asyncio.wait_for(replacement, 1)
    assert entered == [True] and not locks._entries


async def test_cancelled_holder_wakes_waiter_and_cleans_up():
    locks = KeyedLocks()
    entered = asyncio.Event()
    waiting = asyncio.Event()

    async def holder():
        async with locks.hold("same"):
            entered.set()
            await asyncio.Event().wait()

    async def waiter():
        async with locks.hold("same"):
            waiting.set()

    first = asyncio.create_task(holder())
    await entered.wait()
    second = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, 1)
    assert waiting.is_set() and not locks._entries


async def test_failed_operation_releases_key():
    locks = KeyedLocks()
    with pytest.raises(ValueError, match="worker failed"):
        async with locks.hold("same"):
            raise ValueError("worker failed")
    assert not locks._entries
    async with locks.hold("same"):
        assert locks._entries["same"].users == 1
    assert not locks._entries


async def test_key_retention_tracks_concurrent_operations_not_history():
    locks = KeyedLocks()
    for number in range(5000):
        async with locks.hold(number):
            assert len(locks._entries) == 1
    assert not locks._entries

    async def operation(number):
        async with locks.hold(number):
            await asyncio.sleep(0)

    await asyncio.gather(*(operation(number) for number in range(128)))
    assert not locks._entries


async def test_discoverer_does_not_serialize_sources_with_colliding_stripes(
    monkeypatch,
):
    first, second = colliding_keys("https://example.org/series/")
    discoverer = Discoverer(SimpleNamespace(cache=None))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def scan(url, *_):
        if url == first:
            entered.set()
            await release.wait()
        return Scan(url)

    monkeypatch.setattr(discoverer, "_cached_scan", scan)
    blocked = asyncio.create_task(discoverer.scan(first))
    try:
        await entered.wait()
        result = await asyncio.wait_for(discoverer.scan(second), 1)
        assert result.url == second and not blocked.done()
    finally:
        release.set()
        await blocked
    assert not discoverer.scan_locks._entries


@pytest.mark.parametrize("separate_libraries", [False, True])
async def test_refresh_does_not_serialize_unrelated_items(separate_libraries):
    first, second = colliding_keys("item-")
    if separate_libraries:
        second = first
    entered = asyncio.Event()
    release = asyncio.Event()

    async def scan(url, *_):
        if url.endswith("/first"):
            entered.set()
            await release.wait()
        return Scan(url, entries=[Entry(url + "/chapter/1", "Chapter 1")])

    app = SimpleNamespace(
        state=SimpleNamespace(
            refresh_locks=KeyedLocks(),
            scan_semaphore=asyncio.Semaphore(6),
            discoverer=SimpleNamespace(scan=scan),
            semantic=SimpleNamespace(enrich=lambda *_: None),
        )
    )

    def library(path, name):
        return SimpleNamespace(
            path=path,
            refresh_source=lambda _: {
                "url": "https://example.org/" + name,
                "selector": "",
                "include_path": "",
                "deleted": False,
            },
            check_active=lambda: None,
            merge=lambda *_: 0,
        )

    first_store = library("first.db", "first")
    second_store = library("second.db" if separate_libraries else "first.db", "second")
    blocked = asyncio.create_task(refresh_item(first, app, first_store))
    try:
        await entered.wait()
        result = await asyncio.wait_for(refresh_item(second, app, second_store), 2)
        assert result["ok"] and not blocked.done()
    finally:
        release.set()
        await blocked
    assert not app.state.refresh_locks._entries
