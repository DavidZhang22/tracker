"""Process boundary, admission, recovery, and complete-page output checks."""

import asyncio
import os
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest

from app.tracker.analysis_pool import PageAnalyzer
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.parser import parse_page
from app.tracker.urls import DiscoveryError


async def test_real_processes_preserve_page_output_and_recover_after_worker_crash():
    analyzer = PageAnalyzer(2)
    html = (Path(__file__).parent / "fixtures" / "royalroad.html").read_text()
    url = "https://www.royalroad.com/fiction/21220/mother-of-learning"
    try:
        await analyzer.start()
        expected = parse_page(html, url)
        results = await asyncio.gather(*(analyzer.analyze(html, url) for _ in range(4)))
        assert all(result == expected for result in results)
        assert all(job["pid"] != os.getpid() for job in analyzer.last_jobs)
        # Exercise an actual child death, without killing the API or any user data.
        broken = analyzer._pool.submit(os._exit, 1)
        with pytest.raises(BrokenProcessPool):
            await asyncio.wrap_future(broken)
        with pytest.raises(DiscoveryError, match="Saved links were kept"):
            await analyzer.analyze(html, url)
        assert await analyzer.analyze(html, url) == expected
        processes = list(analyzer._pool._processes.values())
    finally:
        await analyzer.aclose()
    assert all(not process.is_alive() for process in processes)
    with pytest.raises(DiscoveryError, match="stopping"):
        await analyzer.analyze(html, url)


async def test_cancelled_analysis_retains_admission_until_process_finishes():
    class Executor:
        def __init__(self):
            self.futures = []

        def submit(self, *args, **kwargs):
            future = Future()
            future.set_running_or_notify_cancel()
            self.futures.append(future)
            return future

        def shutdown(self, **kwargs):
            pass

    analyzer = PageAnalyzer(1)
    analyzer._pool = executor = Executor()
    first = asyncio.create_task(analyzer.analyze("", "https://example.org"))
    await asyncio.sleep(0)
    first.cancel()
    second = asyncio.create_task(analyzer.analyze("", "https://example.org"))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)
    assert len(executor.futures) == 1 and not first.done()
    executor.futures[0].set_result(("first", {}))
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.sleep(0)
    assert len(executor.futures) == 2
    executor.futures[1].set_result(("second", {}))
    assert await second == "second"
    await analyzer.aclose()


async def test_full_page_cache_bypasses_analysis_after_source_cooldown(monkeypatch):
    class Analyzer:
        calls = 0

        async def analyze(self, *args):
            self.calls += 1
            return parse_page(*args)

    class Fetcher:
        cache = FetchCache()
        calls = 0

        async def get(self, url):
            self.calls += 1
            return url, '<article><a href="/chapter/1">Chapter 1</a></article>'

    analyzer, fetcher = Analyzer(), Fetcher()
    scanner = Discoverer(fetcher, analyzer=analyzer)
    first = await scanner.scan("https://example.org/book")
    # Expire just the complete scan; unchanged page parsing remains reusable.
    for key in list(fetcher.cache.memory):
        if key.startswith("scan:"):
            fetcher.cache.memory[key]["checked"] = 0
    import time

    now = time.time()
    monkeypatch.setattr("time.time", lambda: now + 301)
    second = await scanner.scan("https://example.org/book")
    assert first.entries == second.entries
    assert fetcher.calls == 2 and analyzer.calls == 1


def test_application_owns_process_startup_and_shutdown(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("TRACKER_ANALYSIS_WORKERS", "2")
    app = create_app(tmp_path / "library.db", auth_config={"required": False})
    with TestClient(app) as client:
        assert client.get("/api/ready").status_code == 200
        assert app.state.discoverer.analyzer is app.state.analyzer
        processes = list(app.state.analyzer._pool._processes.values())
        assert len(processes) == 2 and all(p.is_alive() for p in processes)
    assert all(not p.is_alive() for p in processes)
