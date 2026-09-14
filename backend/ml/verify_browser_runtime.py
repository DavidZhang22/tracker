"""Real networkless Chromium + broker test; all listing content is synthetic."""

import asyncio
import json
import time
from urllib.parse import parse_qs, urlsplit

from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.listing_recipes import cache_key
from app.tracker.urls import SafeFetcher, request_budget

SOURCE = "https://js-fixture.example/series"
HTML = """<!doctype html><html><head><title>Test series</title></head><body>
<main id="records"></main><button id="more">Load more chapters</button>
<img src="https://image-fixture.example/blocked.png">
<iframe src="http://169.254.169.254/blocked"></iframe>
<script src="/assets/list.js"></script></body></html>"""
JS = """let next='/api/posts?page=1';
async function load(){
const data=await fetch(next).then(r=>r.json());next=data.links.next;
document.querySelector('#records').innerHTML=data.posts.map(p=>`<article><a href="${p.url}">${p.title}</a><time datetime="${p.published_at}">${p.published_at}</time><span>English</span></article>`).join('');
if(!next)document.querySelector('#more').remove();
}
document.querySelector('#more').onclick=load;load();
fetch('/delete',{method:'POST'}).catch(()=>{});
fetch('http://169.254.169.254/latest/meta-data').catch(()=>{});
new WebSocket('wss://js-fixture.example/events').onerror=()=>{};
"""


class FixtureFetcher:
    def __init__(self):
        self.cache, self.calls, self.rows = FetchCache(), [], 6
        self.safe = SafeFetcher()

    async def get(self, url, **kwargs):
        self.calls.append(url)
        p = urlsplit(url)
        if p.hostname != "js-fixture.example":
            # Metadata addresses must fail before making any socket connection.
            await self.safe.get(url)
            raise AssertionError("Private request was not rejected")
        if request_budget.get():
            request_budget.get().take()
        if url == SOURCE:
            return url, HTML
        if p.path == "/assets/list.js":
            return url, JS
        assert p.path == "/api/posts", (
            "Content pages, mutations and images must not be fetched"
        )
        page = int(parse_qs(p.query)["page"][0])
        rows = [
            {
                "url": f"{SOURCE}/chapter/{i}",
                "title": f"Chapter {i}",
                "published_at": f"2026-09-{i:02}T00:00:00Z",
                "language": "English",
            }
            for i in range((page - 1) * 2 + 1, min(page * 2, self.rows) + 1)
        ]
        return url, json.dumps(
            {
                "posts": rows,
                "links": {
                    "next": f"/api/posts?page={page + 1}"
                    if page * 2 < self.rows
                    else None
                },
            }
        )


async def main():
    f = FixtureFetcher()
    scanner = Discoverer(f)
    start = time.monotonic()
    result = await scanner.scan(SOURCE, source_method="browser", deep=True)
    browser_seconds = time.monotonic() - start
    assert len(result.entries) == 6, result.to_dict()
    assert len({e.url for e in result.entries}) == 6
    assert all(e.published_at for e in result.entries)
    assert result.coverage == "complete" and "Learned listing API" in result.methods, (
        result.to_dict()
    )
    assert f.cache.get(cache_key(SOURCE))["recipe"]
    browser_calls = len(f.calls)
    f.calls.clear()
    f.rows = 8
    # Different method bypasses the complete-scan cache, exercising the saved recipe.
    start = time.monotonic()
    refreshed = await scanner.scan(SOURCE)
    api_seconds = time.monotonic() - start
    assert len(refreshed.entries) == 8 and refreshed.analysis_mode == "light"
    assert len(f.calls) == 4 and all("/api/posts?" in u for u in f.calls), f.calls
    assert "Browser JavaScript" not in refreshed.methods
    print(
        json.dumps(
            {
                "real_browser": "passed",
                "virtualized_rows": 6,
                "next_refresh_rows": 8,
                "browser_requests": browser_calls,
                "api_refresh_requests": len(f.calls),
                "browser_seconds": round(browser_seconds, 2),
                "api_seconds": round(api_seconds, 3),
                "article_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
