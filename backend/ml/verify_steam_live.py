"""Bounded live metadata check for the user-supplied Steam news page."""

import asyncio
import json
import time
from urllib.parse import urlsplit

from app.tracker.discovery import Discoverer
from app.tracker.urls import SafeFetcher


class AuditFetcher(SafeFetcher):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def get(self, url, **kwargs):
        assert urlsplit(url).hostname == "api.steampowered.com"
        assert urlsplit(url).path == "/ISteamNews/GetNewsForApp/v2/"
        self.calls.append(url)
        return await super().get(url, **kwargs)


async def main():
    fetcher = AuditFetcher()
    start = time.monotonic()
    result = await Discoverer(fetcher, max_pages=6).scan(
        "https://store.steampowered.com/news/app/1623730", deep=True
    )
    assert result.entries and result.coverage == "complete", result.warnings
    assert all(e.published_at and e.source_id for e in result.entries)
    assert len({e.url for e in result.entries}) == len(result.entries)
    assert result.entries == sorted(
        result.entries, key=lambda e: (e.published_at, e.url)
    )
    print(
        json.dumps(
            {
                "entries": len(result.entries),
                "dated": sum(bool(e.published_at) for e in result.entries),
                "requests": len(fetcher.calls),
                "article_requests": 0,
                "seconds": round(time.monotonic() - start, 2),
                "coverage": result.coverage,
                "warnings": result.warnings,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
