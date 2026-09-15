"""Bounded, reusable page-analysis processes; network and storage stay in the API."""

import asyncio
import multiprocessing
import os
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from .context_model import load_context_model
from .documents import Document
from .link_model import load_model
from .parser import parse_page
from .record_context import load_record_model
from .urls import DiscoveryError
from .workers import await_worker, run_blocking


def initialize_models():
    load_model()
    load_context_model()
    load_record_model()


def worker_ready():
    return os.getpid()


def analyze_page(
    text,
    url,
    selector="",
    include_path="",
    learn=False,
    recipe=None,
    *,
    operation="parse",
):
    """Only compressed documents/text and plain recipes cross the process boundary."""
    started = time.monotonic()
    cpu_started = time.thread_time()
    if operation == "readme":
        from .github import readme_part

        result = readme_part(text, url, bool(selector))
    elif operation == "markdown":
        from .github import render_markdown

        result = render_markdown(text)
    elif learn:
        from .recipes import analyze

        result = analyze(text, url, selector, include_path, recipe)
    else:
        result = parse_page(text, url, selector, include_path)
    return result, {
        "pid": os.getpid(),
        "started": started,
        "finished": time.monotonic(),
        "cpu_seconds": time.thread_time() - cpu_started,
        "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)(),
        "operation": operation,
    }


class PageAnalyzer:
    def __init__(self, workers=2):
        if not 0 <= workers <= 2:
            raise ValueError("TRACKER_ANALYSIS_WORKERS must be 0, 1 or 2")
        self.workers = workers
        self._slots = asyncio.Semaphore(workers or 2)
        self._lifecycle = asyncio.Lock()
        self._pool = None
        self._closed = False
        # Bounded diagnostics for profiling; never retain page text or URLs.
        self.last_jobs = deque(maxlen=16)

    async def _executor(self):
        async with self._lifecycle:
            if self._closed:
                raise DiscoveryError("Page analysis is stopping. Please retry shortly.")
            if self._pool is None:
                self._pool = ProcessPoolExecutor(
                    max_workers=self.workers,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=initialize_models,
                )
            return self._pool

    async def start(self):
        if self.workers:
            pool = await self._executor()
            futures = [
                asyncio.wrap_future(pool.submit(worker_ready))
                for _ in range(self.workers)
            ]
            await await_worker(asyncio.gather(*futures))

    async def analyze(self, text, url, selector="", include_path=""):
        return await self._run(text, url, selector, include_path)

    async def analyze_learned(
        self, text, url, selector="", include_path="", recipe=None
    ):
        return await self._run(text, url, selector, include_path, True, recipe)

    async def prepare_readme(self, text, prefix, find_blob=False):
        return await self._run(text, prefix, find_blob, operation="readme")

    async def prepare_markdown(self, text):
        return await self._run(text, "", operation="markdown")

    async def _run(self, *args, operation="parse"):
        # Admission happens before submit, keeping the executor's queue bounded.
        queued = time.monotonic()
        async with self._slots:
            admitted = time.monotonic()
            if self._closed:
                raise DiscoveryError("Page analysis is stopping. Please retry shortly.")
            if not self.workers:
                result, stats = await run_blocking(
                    analyze_page, *args, operation=operation
                )
            else:
                pool = await self._executor()
                try:
                    future = asyncio.wrap_future(
                        pool.submit(analyze_page, *args, operation=operation)
                    )
                    result, stats = await await_worker(future)
                except (BrokenProcessPool, OSError) as exc:
                    async with self._lifecycle:
                        if self._pool is pool:
                            await run_blocking(
                                pool.shutdown, wait=True, cancel_futures=True
                            )
                            self._pool = None
                    # Do not retry expensive work in a loop after a worker failure.
                    raise DiscoveryError(
                        "Page analysis was interrupted. Saved links were kept; refresh again to retry."
                    ) from exc
            body = args[0]
            stats.update(
                queue_seconds=admitted - queued,
                input_bytes=len(body.data)
                if isinstance(body, Document)
                else len(body.encode("utf-8")),
            )
            self.last_jobs.append(stats)
            return result

    async def aclose(self):
        async with self._lifecycle:
            self._closed = True
            if self._pool is not None:
                pool, self._pool = self._pool, None
                await run_blocking(pool.shutdown, wait=True, cancel_futures=True)
