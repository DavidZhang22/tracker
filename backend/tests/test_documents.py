import asyncio
import gc
import gzip
import hashlib
import pickle
import time
import weakref
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.tracker import documents
from app.tracker.analysis_pool import PageAnalyzer
from app.tracker.cache import FetchCache
from app.tracker.documents import Document
from app.tracker.parser import parse_page
from app.tracker.recipes import analyze
from app.tracker.urls import DiscoveryError, SafeFetcher, fetch_document, response_key


@pytest.mark.parametrize("compression", ["identity", "gzip", "deflate"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "windows-1252"])
def test_compressed_documents_preserve_text_digest_and_transport(compression, encoding):
    text = (
        '<article lang="en"><a href="/chapter/7">Chapter 7: café</a><time datetime="2026-09-10">Today</time></article>'
        * 1000
    )
    data = text.encode(encoding)
    wire = (
        gzip.compress(data)
        if compression == "gzip"
        else zlib.compress(data)
        if compression == "deflate"
        else data
    )
    doc = Document.from_wire(wire, compression, encoding)
    assert doc.text() == text
    assert doc.digest == hashlib.sha256(text.encode()).hexdigest()
    assert doc.expanded_size == len(data) and doc.text_size == len(text.encode())
    assert Document.from_cache(doc.to_cache()) == doc
    assert pickle.loads(pickle.dumps(doc)).text() == text
    if compression != "identity":
        assert doc.data == wire  # Keep the source's compression; do not recompress.
    assert len(doc.data) < len(data) / 10


@pytest.mark.parametrize("split", range(1, 32))
def test_title_and_script_detection_at_chunk_boundaries(monkeypatch, split):
    monkeypatch.setattr(documents, "CHUNK", split)
    for title in ["Just a moment", "Attention Required", "Access Denied"]:
        with pytest.raises(DiscoveryError, match="browser check"):
            Document.from_wire(
                gzip.compress(f"abc<TITLE>{' ' * 90}{title}</TITLE>".encode()), "gzip"
            )
    doc = Document.from_text("áé<title>Accessible</title><SCRIPT>keep me</SCRIPT>")
    assert doc.has_scripts and doc.text().endswith("</SCRIPT>")


@pytest.mark.parametrize(
    "compression,pack", [("gzip", gzip.compress), ("deflate", zlib.compress)]
)
def test_bombs_truncation_and_trailing_data_are_rejected(compression, pack):
    with pytest.raises(DiscoveryError, match="8 MB"):
        Document.from_wire(pack(b"x" * (documents.MAX_RESPONSE + 1)), compression)
    for wire in [
        pack(b"abc")[:-1],
        pack(b"abc") + b"x",
        pack(b"abc") + pack(b"abc"),
        b"not compressed",
    ]:
        with pytest.raises(DiscoveryError):
            Document.from_wire(wire, compression)
    assert Document.from_wire(pack(b""), compression).text() == ""


def test_invalid_encoding_and_cache_fail_safely():
    for encoding in ["not-a-codec", "base64_codec", "hex_codec"]:
        with pytest.raises(DiscoveryError):
            Document.from_wire(b"hello", encoding=encoding)
    doc = Document.from_text("hello")
    for change in [
        {"data": "*"},
        {"compression": "pickle"},
        {"expanded_size": -1},
        {"digest": None},
        {"has_scripts": "yes"},
    ]:
        with pytest.raises(DiscoveryError):
            Document.from_cache(doc.to_cache() | change)
    bad = Document.from_cache(
        doc.to_cache()
        | {
            "data": documents.base64.b64encode(
                gzip.compress(b"x" * 9_000_000)
            ).decode(),
            "compression": "gzip",
        }
    )
    with pytest.raises(DiscoveryError, match="8 MB"):
        bad.text()


def test_legacy_decoded_text_can_exceed_wire_size_without_being_truncated():
    text = "\ufffd" * 3_000_000
    doc = Document.from_text(text)
    assert Document.from_cache(doc.to_cache()).text() == text
    with pytest.raises(DiscoveryError):
        Document.from_wire(text.encode())


@pytest.mark.parametrize(
    "filename,source",
    [
        (
            "royalroad.html",
            "https://www.royalroad.com/fiction/21220/mother-of-learning",
        ),
        (
            "asura.html",
            "https://asurascans.com/comics/the-nebulas-civilization-53fc8424",
        ),
        ("hn.html", "https://news.ycombinator.com/"),
        ("xkcd.html", "https://xkcd.com/archive/"),
    ],
)
def test_compressed_and_plain_pages_and_recipes_have_identical_output(filename, source):
    html = (Path(__file__).parent / "fixtures" / filename).read_text(encoding="utf-8")
    doc = Document.from_text(html)
    expected = parse_page(html, source)
    assert parse_page(doc, source) == expected
    result, recipe, _ = analyze(doc, source)
    assert result == expected
    assert analyze(doc, source, recipe=recipe)[0] == expected


async def test_real_worker_decodes_only_after_admission():
    html = '<article><a href="/chapter/1">Chapter 1</a></article>' * 100
    doc = Document.from_text(html)
    analyzer = PageAnalyzer(1)
    try:
        results = await asyncio.gather(
            *(analyzer.analyze(doc, "https://example.org/book") for _ in range(3))
        )
        assert all(r == parse_page(html, "https://example.org/book") for r in results)
        assert all(j["input_bytes"] == len(doc.data) for j in analyzer.last_jobs)
        assert analyzer.last_jobs[-1]["queue_seconds"] > 0
    finally:
        await analyzer.aclose()


async def test_readme_preparation_shares_the_bounded_process_pool():
    import json
    import os

    from app.tracker.github import github_readme

    source = "https://github.com/example/books"
    blob = source + "/blob/main/README.md"
    raw = source + "/raw/main/README.md"
    pages = {
        source: '<a href="/example/books/blob/main/README.md">README</a>',
        blob: '<script type="application/json">'
        + json.dumps({"rawBlobUrl": raw})
        + "</script>",
        raw: "# Chapters\n\n[Chapter 1](https://example.org/chapter/1)\n",
    }

    class Fetcher:
        calls = []

        async def get(self, url):
            self.calls.append(url)
            return url, Document.from_text(pages[url])

    fetcher, analyzer = Fetcher(), PageAnalyzer(2)
    try:
        doc, extra = await github_readme(
            fetcher, source, Document.from_text(pages[source]), analyzer
        )
        assert extra == 2 and isinstance(doc, Document)
        assert "https://example.org/chapter/1" in doc.text()
        assert fetcher.calls == [blob, raw]
        assert [j["operation"] for j in analyzer.last_jobs] == [
            "readme",
            "readme",
            "markdown",
        ]
        assert all(j["pid"] != os.getpid() for j in analyzer.last_jobs)
        assert await analyzer.analyze(doc, source) == parse_page(doc.text(), source)
    finally:
        await analyzer.aclose()


async def test_legacy_cache_and_compressed_304_keep_conditional_requests():
    cache = FetchCache()
    url, html = "https://example.org/", '<a href="/chapter/1">Chapter 1</a>'
    cache.put(
        url, {"body": html, "final": url, "etag": "v1", "expires": time.time() + 60}
    )
    fetcher = SafeFetcher(cache, interval=0)
    assert (await fetch_document(fetcher, url))[1].text() == html
    assert "document" in cache.get(response_key(url))
    assert "body" not in cache.get(response_key(url))
    cache.put(
        response_key(url),
        {
            "document": Document.from_text(html).to_cache(),
            "final": url,
            "etag": "v1",
            "expires": 0,
        },
    )
    calls = []

    def response(request):
        calls.append(request)
        assert request.headers["if-none-match"] == "v1"
        return httpx.Response(304)

    client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    with (
        patch(
            "app.tracker.urls.public_addresses",
            AsyncMock(return_value=["93.184.216.34"]),
        ),
        patch("app.tracker.urls.httpx.AsyncClient", return_value=client),
    ):
        assert await fetcher.get(url) == (url, html)
        assert (await fetcher.get_document(url))[1].text() == html
    assert len(calls) == 1


async def test_fetch_document_respects_injected_get():
    fetcher = SafeFetcher()
    fetcher.get = AsyncMock(return_value=("https://example.org/", "overridden"))
    assert await fetch_document(fetcher, "https://example.org/") == (
        "https://example.org/",
        "overridden",
    )


def test_dom_is_released_without_waiting_for_cyclic_gc(monkeypatch):
    from bs4 import BeautifulSoup

    refs = []

    def soup(*args, **kwargs):
        result = BeautifulSoup(*args, **kwargs)
        refs.append(weakref.ref(result))
        return result

    monkeypatch.setattr("app.tracker.parser.BeautifulSoup", soup)
    html = (
        "<main>"
        + "".join(
            f'<article><a href="/chapter/{i}">Chapter {i}</a></article>'
            for i in range(20)
        )
        + "</main>"
    )
    gc.disable()
    try:
        result, recipe, _ = analyze(
            Document.from_text(html), "https://example.org/book"
        )
        assert len(result[0].entries) == 20
        assert recipe is not None and len(refs) == 1
        assert all(ref() is None for ref in refs)
        replay, _, used = analyze(
            Document.from_text(html), "https://example.org/book", recipe=recipe
        )
        assert used and replay == result
        assert all(ref() is None for ref in refs)
    finally:
        gc.enable()


async def test_network_concurrency_is_bounded_and_same_host_stays_paced():
    active, peak, starts = 0, 0, {}
    ready, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        nonlocal active, peak
        host = request.headers["host"]
        starts.setdefault(host, []).append(time.monotonic())
        active += 1
        peak = max(peak, active)
        if active == 4:
            ready.set()
        try:
            await release.wait()
            return httpx.Response(200, text="<title>OK</title>")
        finally:
            active -= 1

    client_type = httpx.AsyncClient
    hosts = {f"site{i}.example": i for i in range(6)}
    fetcher = SafeFetcher(interval=0.2)
    with (
        patch(
            "app.tracker.urls.public_addresses",
            AsyncMock(return_value=["93.184.216.34"]),
        ),
        patch("app.tracker.urls.hash", lambda host: hosts[host], create=True),
        patch(
            "app.tracker.urls.httpx.AsyncClient",
            lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw),
        ),
    ):
        tasks = [
            asyncio.create_task(fetcher.get_document(f"https://{host}/"))
            for host in hosts
        ]
        tasks.append(
            asyncio.create_task(fetcher.get_document("https://site0.example/other"))
        )
        try:
            await asyncio.wait_for(ready.wait(), 2)
            await asyncio.sleep(0.02)
            assert peak == active == 4
        finally:
            release.set()
            await asyncio.gather(*tasks)
    assert peak == 4
    # Windows' monotonic clock can have 16 ms resolution; the full replay also
    # checks the production two-second interval on Linux.
    assert starts["site0.example"][1] - starts["site0.example"][0] >= 0.17
