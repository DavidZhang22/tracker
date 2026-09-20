import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from app.tracker.discovery import youtube_archive
from app.tracker.http_identity import USER_AGENT
from app.tracker.sitemaps import scan_sitemap
from app.tracker.urls import DiscoveryError, SafeFetcher


async def test_source_requests_and_redirects_identify_trackify(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "/listing"})
        return httpx.Response(200, text="<html><title>Listing</title></html>")

    monkeypatch.setattr(
        "app.tracker.urls.public_addresses", AsyncMock(return_value=["93.184.216.34"])
    )
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    final, _ = await SafeFetcher(interval=0).get("https://source.example/old")
    assert final == "https://source.example/listing"
    assert len(requests) == 2
    for request in requests:
        assert request.headers["User-Agent"] == USER_AGENT
        assert request.headers["User-Agent"].startswith("Trackify/")
        assert "https://mediatrackify.duckdns.org" in request.headers["User-Agent"]
        assert "contact: mediatrackify@gmail.com" in request.headers["User-Agent"]
        assert "Mozilla" not in request.headers["User-Agent"]
        assert request.headers["Host"] == "source.example"


def test_optional_youtube_archive_passes_the_same_identity(monkeypatch):
    monkeypatch.setenv("TRACKER_YOUTUBE_ARCHIVE", "1")
    run = Mock(
        return_value=SimpleNamespace(
            stdout=json.dumps({"title": "Channel", "entries": []}),
            stderr="",
            returncode=0,
        )
    )
    monkeypatch.setattr("app.tracker.discovery.subprocess.run", run)
    youtube_archive("https://www.youtube.com/@example")
    args = run.call_args.args[0]
    assert args[args.index("--user-agent") + 1] == USER_AGENT
    assert "--ignore-config" in args and "--skip-download" in args


@pytest.mark.parametrize("blocked_path", ["/sitemap.xml", "/private"])
async def test_sitemap_honors_trackify_specific_robots_rules(blocked_path):
    origin = "https://source.example"
    calls = []

    class Fetcher:
        async def get(self, url):
            calls.append(url)
            if url.endswith("/robots.txt"):
                return url, (
                    f"User-agent: Trackify\nDisallow: {blocked_path}\n\n"
                    "User-agent: *\nAllow: /\n"
                    f"Sitemap: {origin}/sitemap.xml\n"
                )
            assert url == origin + "/sitemap.xml"
            return url, (
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{origin}/public</loc></url>"
                f"<url><loc>{origin}/private</loc></url></urlset>"
            )

    if blocked_path == "/sitemap.xml":
        with pytest.raises(DiscoveryError, match="excluded by robots.txt"):
            await scan_sitemap(Fetcher(), origin + "/", 5)
        assert calls == [origin + "/robots.txt"]
    else:
        result = await scan_sitemap(Fetcher(), origin + "/", 5)
        assert calls == [origin + "/robots.txt", origin + "/sitemap.xml"]
        assert [entry.url for entry in result.entries] == [origin + "/public"]


async def test_browser_context_uses_same_honest_identity_without_disabling_sandbox(
    monkeypatch,
):
    class Configured(Exception):
        pass

    browser = SimpleNamespace(
        new_context=AsyncMock(side_effect=Configured), close=AsyncMock()
    )
    launch = AsyncMock(return_value=browser)

    @asynccontextmanager
    async def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    api_module = ModuleType("playwright.async_api")
    api_module.async_playwright = playwright
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", api_module)
    path = Path(__file__).resolve().parents[2] / "deploy/browser/worker.py"
    spec = importlib.util.spec_from_file_location("identity_test_browser_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    with pytest.raises(Configured):
        await worker.render(None, None, "https://source.example/")
    options = browser.new_context.call_args.kwargs
    assert options["user_agent"] == USER_AGENT
    assert options["service_workers"] == "block"
    assert options["accept_downloads"] is False
    assert launch.call_args.kwargs["chromium_sandbox"] is True
    browser.close.assert_awaited_once()
