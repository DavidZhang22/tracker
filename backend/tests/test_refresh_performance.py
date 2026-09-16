"""Behavioral checks for the optimized hot paths, without timing thresholds."""

import asyncio
import json
import sqlite3
import threading
import time
from contextlib import closing
from itertools import islice

import pytest
from anyio import CancelScope, create_task_group, sleep
from bs4 import BeautifulSoup, Tag

from app.tracker.cache import FetchCache
from app.tracker.context_model import classify_context
from app.tracker.discovery import Discoverer
from app.tracker.link_model import page_scores
from app.tracker.models import Entry, Scan
from app.tracker.record_context import RecordContext, predict
from app.tracker.store import Store
from app.tracker.urls import request_budget
from app.tracker.workers import run_blocking


def test_sibling_features_match_original_window_including_nodes_after_80():
    soup = BeautifulSoup(
        "<main>"
        + "".join(
            f'\n<div><a href="/{i}">{i}</a></div>\n<span>English</span><a href="/plain">Plain</a>'
            for i in range(100)
        )
        + "</main>",
        "html.parser",
    )
    context = RecordContext(soup)
    for node in soup.main.find_all(recursive=False):
        siblings = [
            s
            for s in islice(node.parent.children, 80)
            if isinstance(s, Tag) and s is not node
        ]
        expected = (
            sum(s.name == node.name and bool(s.find("a", href=True)) for s in siblings),
            sum(not s.find("a", href=True) and s.name != "a" for s in siblings),
        )
        assert context.siblings(node) == expected
    assert len(context.sibling_stats) == 1


def test_memoized_model_preserves_scores_and_is_bounded():
    soup = BeautifulSoup(
        '<article><a href="/1">Chapter 1</a><span>English</span></article>',
        "html.parser",
    )
    context = RecordContext(soup)
    for _, _, features in context.regions(soup.a):
        assert context.score(tuple(features)) == predict(context.model, features)
        context.score(tuple(features))
    assert context.score.cache_info().hits > 0
    assert context.score.cache_info().maxsize == 2048


def test_shared_features_produce_identical_fallback_predictions():
    soup = BeautifulSoup(
        '<nav><a href="/login">Login</a></nav><article><h2><a href="/post/1">A post</a></h2>'
        '<time datetime="2026-09-10">Sep 10</time><a href="/post/1">Read more</a></article>',
        "html.parser",
    )
    expected, model = page_scores(soup, "https://example.org/")
    scores = {}
    classify_context(soup, "https://example.org/", fallback_scores=scores)
    assert model is not None and scores == expected


@pytest.mark.parametrize("disk", [False, True])
async def test_unchanged_page_reuses_parse_but_changed_content_and_options_do_not(
    tmp_path, monkeypatch, disk
):
    import app.tracker.discovery as module
    import app.tracker.parser as parser_module

    cache = FetchCache(tmp_path / "cache.db" if disk else None)
    original_get = cache.get
    # Skip only the complete-scan cache to exercise ordinary source revalidation.
    monkeypatch.setattr(
        cache, "get", lambda key: None if key.startswith("scan:") else original_get(key)
    )

    class Fetcher:
        html = (
            '<article><a href="/chapter/1">Chapter 1</a><span>English</span></article>'
        )
        calls = 0

        async def get(self, url):
            self.calls += 1
            return url, self.html

    fetcher = Fetcher()
    fetcher.cache = cache
    scanner = Discoverer(fetcher)
    original_parse = parser_module.parse_page
    calls = []

    def parse(*args, **kwargs):
        calls.append(args)
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(parser_module, "parse_page", parse)
    source = "https://example.org/series/story"
    first = await scanner.scan(source)
    second = await scanner.scan(source, keywords="English")
    assert first.entries == second.entries and len(calls) == 1
    assert fetcher.calls == 2 and not second.cached  # The source was still checked.
    second.entries[0].title = "Changed in caller"
    second.warnings.append("Caller warning")
    second.methods.append("Caller method")
    now = time.time()
    monkeypatch.setattr("time.time", lambda: now + 301)
    again = await scanner.scan(source)
    assert again.entries == first.entries and again.warnings == first.warnings
    assert (
        "keyword context" not in again.methods and "Caller method" not in again.methods
    )
    fetcher.html = fetcher.html.replace("Chapter 1", "Chapter 1 revised")
    monkeypatch.setattr("time.time", lambda: now + 602)
    assert (await scanner.scan(source)).entries[0].title == "Chapter 1 revised"
    assert len(calls) == 2
    await scanner.scan(source, selector="article a")
    await scanner.scan(source, include_path="/chapter/")
    await scanner.scan(source + "/other")
    monkeypatch.setattr(module, "DISCOVERY_VERSION", "new-parser")
    await scanner.scan(source)
    assert len(calls) == 6


def test_cache_migrates_old_payloads_and_prunes_using_metadata(tmp_path, monkeypatch):
    import app.tracker.cache as module

    path = tmp_path / "cache.db"
    now = time.time()
    old = {"body": "été", "checked": now}
    payload = json.dumps(old)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE cache(key TEXT PRIMARY KEY,payload TEXT NOT NULL,checked REAL NOT NULL)"
        )
        db.execute("INSERT INTO cache VALUES (?,?,?)", ("old", payload, now))
    cache = FetchCache(path)
    assert cache.get("old") == old
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT size FROM cache").fetchone()[0] == len(
            payload.encode()
        )
        plan = str(
            db.execute(
                "EXPLAIN QUERY PLAN SELECT key,size FROM cache ORDER BY checked DESC,key"
            ).fetchall()
        )
        assert "COVERING INDEX cache_eviction" in plan
    monkeypatch.setattr(module, "MAX_BYTES", 180)
    cache.put("new", {"body": "x" * 100, "checked": now + 1})
    assert cache.get("new") and cache.get("old") is None
    cache.put("expired", {"body": "x", "checked": now - 8 * 86400})
    assert cache.get("expired") is None
    # Connections must be closed immediately, including read-only lookups.
    path.rename(tmp_path / "closed.db")


def test_memory_cache_replacement_and_eviction_keep_correct_byte_total(monkeypatch):
    import app.tracker.cache as module

    monkeypatch.setattr(module, "MAX_BYTES", 200)
    cache = FetchCache()
    cache.put("one", {"body": "x" * 80})
    cache.put("one", {"body": "small"})
    assert cache.total_bytes == len(json.dumps(cache.get("one")).encode())
    for i in range(5):
        cache.put(str(i), {"body": "y" * 80})
    assert cache.total_bytes <= 200 and len(cache.memory) == 1
    assert cache.total_bytes == sum(cache.sizes.values())


async def test_blocking_worker_allows_other_tasks_and_retains_lock_on_cancellation():
    entered, release = threading.Event(), threading.Event()
    lock = asyncio.Lock()
    seen = []
    token = request_budget.set("test context")

    def blocking():
        seen.append(request_budget.get())
        entered.set()
        assert release.wait(5)

    async def operation():
        async with lock:
            await run_blocking(blocking)

    task = asyncio.create_task(operation())
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        assert seen == ["test context"]
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated disconnect/shutdown cancellation cannot abandon it.
        await asyncio.sleep(0.01)
        assert lock.locked() and not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        request_budget.reset(token)
    assert not lock.locked()


async def test_starlette_cancellation_waits_without_blocking_the_event_loop():
    entered, release = threading.Event(), threading.Event()
    done = asyncio.Event()

    def blocking():
        entered.set()
        assert release.wait(5)

    async def operation(scope):
        with scope:
            try:
                await run_blocking(blocking)
            finally:
                done.set()

    async with create_task_group() as group:
        scope = CancelScope()
        group.start_soon(operation, scope)
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await sleep(0.001)
            scope.cancel()
            await sleep(0.02)
            assert not done.is_set()
        finally:
            release.set()
        await asyncio.wait_for(done.wait(), 2)


def test_unchanged_merge_writes_no_link_rows_and_keeps_user_state(tmp_path):
    store = Store(tmp_path / "library.db")
    scan = Scan(
        "https://example.org/book",
        "Book",
        entries=[
            Entry(
                f"https://example.org/chapter/{i}", f"Chapter {i}", number=i, position=i
            )
            for i in range(10)
        ],
    ).to_dict()
    item = store.create(store.save_scan(scan))
    with store.connection() as db:
        db.execute(
            "UPDATE links SET read=1,favorite=1,ignored=1,deleted=1 WHERE number=3"
        )
        db.executescript("""
            CREATE TABLE writes(n INTEGER NOT NULL);
            INSERT INTO writes VALUES(0);
            CREATE TRIGGER count_writes AFTER UPDATE ON links BEGIN UPDATE writes SET n=n+1; END;
        """)
        before = db.execute("SELECT * FROM links ORDER BY position").fetchall()
    assert store.merge(item["id"], scan) == 0
    with store.connection() as db:
        assert db.execute("SELECT n FROM writes").fetchone()[0] == 0
        assert before == db.execute("SELECT * FROM links ORDER BY position").fetchall()
    scan["entries"][4]["title"] = "Revised title"
    store.merge(item["id"], scan)
    with store.connection() as db:
        assert db.execute("SELECT n FROM writes").fetchone()[0] == 1
