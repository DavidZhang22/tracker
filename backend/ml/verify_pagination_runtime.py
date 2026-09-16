"""Offline real-Chromium regression: load-more, virtualized Next, and navigation."""

import asyncio
import json
import time
from urllib.parse import parse_qs, urlsplit

from app.tracker import browser_client
from app.tracker.cache import FetchCache
from app.tracker.parser import parse_page

SOURCE = "https://pagination-fixture.example/news/"


def cards(page):
    return "".join(
        f'<article><a href="/news/story-{i}">Release announcement {i}</a><time datetime="2026-09-{i:02}T00:00:00Z"></time></article>'
        for i in range(page * 2 - 1, page * 2 + 1)
    )


class Fixture:
    def __init__(self, mode):
        self.mode, self.calls, self.cache = mode, [], FetchCache()

    async def get(self, url):
        self.calls.append(url)
        p = urlsplit(url)
        assert p.hostname == "pagination-fixture.example"
        if p.path == "/api/list":
            page = int(parse_qs(p.query)["page"][0])
            return url, json.dumps({"html": cards(page), "done": page >= 6})
        assert p.path == "/news/", "Article bodies must never be requested"
        page = int(parse_qs(p.query).get("page", ["1"])[0])
        if self.mode == "navigation":
            pager = (
                f'<nav><a href="?page={page + 1}" rel="next">Next</a></nav>'
                if page < 3
                else ""
            )
            return url, "<main>" + cards(page) + "</main>" + pager
        name = 'aria-label="Next page"' if self.mode == "next" else ""
        text = "→" if self.mode == "next" else "SHOW MORE"
        assignment = "=" if self.mode == "next" else "+="
        script = f"""let page=1; document.querySelector('#more').onclick=async()=>{{
          const data=await fetch('/api/list?page='+ (++page)).then(r=>r.json());
          document.querySelector('main').innerHTML {assignment} data.html;
          if(data.done)document.querySelector('#more').disabled=true;
        }};"""
        if self.mode == "unchanged":
            script = "document.querySelector('#more').onclick=()=>document.querySelector('#counter').textContent=Date.now();"
        return (
            url,
            f'<main>{cards(1)}</main><nav><button id="more" {name}>{text}</button></nav><p id="counter"></p><script>{script}</script>',
        )


async def main():
    results = []
    for mode in ("more", "next", "navigation", "unchanged"):
        print("Checking " + mode, flush=True)
        fixture = Fixture(mode)
        _, initial = await fixture.get(SOURCE)
        started = time.monotonic()
        result = await browser_client.render(fixture, SOURCE, initial)
        records = {}
        for html, location in zip(
            result["snapshots"], result["snapshot_urls"], strict=True
        ):
            scan, _, _ = parse_page(html, location)
            records.update((e.url, e) for e in scan.entries)
        expected = {"more": 12, "next": 12, "navigation": 6, "unchanged": 2}[mode]
        assert len(records) == expected, (mode, len(records), fixture.calls)
        assert all(e.published_at for e in records.values())
        if mode == "unchanged":
            assert result["steps"] == 2 and len(fixture.calls) == 1
        results.append(
            {
                "mode": mode,
                "entries": len(records),
                "snapshots": len(result["snapshots"]),
                "steps": result["steps"],
                "requests": len(fixture.calls),
                "seconds": round(time.monotonic() - started, 3),
            }
        )
    print(
        json.dumps(
            {"real_chromium": "passed", "article_requests": 0, "scenarios": results}
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
