import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.tracker.discovery import Discoverer
from app.tracker.source_methods import detect_source_method

SOURCE = "https://store.steampowered.com/news/app/1623730"


def row(i, date=None):
    return {
        "gid": str(i),
        "appid": 1623730,
        "title": f"Announcement {i}",
        "url": f"https://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/{i}",
        "date": date or 1750000000 + i,
        "feedname": "steam_community_announcements",
    }


class SteamFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        assert url.startswith(
            "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?"
        )
        query = parse_qs(urlsplit(url).query)
        assert query["maxlength"] == ["1"]
        assert query["feeds"] == ["steam_community_announcements"]
        return url, json.dumps(
            {
                "appnews": {
                    "appid": 1623730,
                    "newsitems": self.pages[len(self.calls) - 1],
                }
            }
        )


@pytest.mark.asyncio
async def test_steam_detects_and_paginates_dated_announcements_without_article_requests():
    assert detect_source_method(SOURCE)["source_method"] == "steam"
    f = SteamFetcher(
        [[row(i) for i in range(117, 17, -1)], [row(i) for i in range(18, 0, -1)]]
    )
    result = await Discoverer(f).scan(SOURCE, source_method="steam")
    assert len(result.entries) == 117 and len(f.calls) == 2
    assert result.coverage == "complete"
    assert len({e.url for e in result.entries}) == 117
    assert all(
        e.date_kind == "published" and e.published_at and e.source_id
        for e in result.entries
    )
    assert result.entries == sorted(
        result.entries, key=lambda e: (e.published_at, e.url)
    )
    assert parse_qs(urlsplit(f.calls[1]).query)["enddate"] == [str(1750000019)]


@pytest.mark.asyncio
async def test_existing_automatic_steam_items_use_api_without_browser(monkeypatch):
    monkeypatch.setenv("TRACKER_BROWSER_SOCKET", "unavailable-worker")
    result = await Discoverer(SteamFetcher([[row(1)]])).scan(SOURCE)
    assert len(result.entries) == 1 and result.methods == ["Steam news API"]
    assert not result.warnings


@pytest.mark.asyncio
async def test_repeating_timestamp_boundary_stops_and_retains_partial_results():
    page = [row(i, 1750000000) for i in range(1, 101)]
    f = SteamFetcher([page, page])
    result = await Discoverer(f).scan(SOURCE, source_method="steam")
    assert len(f.calls) == 2 and len(result.entries) == 100
    assert result.coverage == "partial" and result.warnings


@pytest.mark.asyncio
async def test_page_limit_retains_discovered_news():
    f = SteamFetcher([[row(i) for i in range(1, 101)]])
    result = await Discoverer(f, max_pages=1).scan(SOURCE, source_method="steam")
    assert len(f.calls) == 1 and result.coverage == "partial"
    assert len(result.entries) == 100


@pytest.mark.parametrize(
    "url",
    [
        SOURCE + "/view/123",
        SOURCE + "?q=updates",
        "https://store.steampowered.com.evil.example/news/app/1623730",
        "https://store.steampowered.com/news/app/9999999999",
    ],
)
def test_steam_detection_does_not_widen_filtered_or_invalid_sources(url):
    assert detect_source_method(url)["source_method"] == "auto"
