import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.mangadex import feed_url, title_id
from app.tracker.urls import DiscoveryError
from tests.test_discovery import FakeFetcher

SOURCE = "https://mangadex.org/title/d8a959f7-648e-4c8d-8f23-f1f3f8e129f3/one-punch-man?tab=chapters&order=asc"
ID = title_id(SOURCE)
FIXTURE = Path(__file__).parent / "fixtures/mangadex-chapters.json"


def payload(rows, offset=0, total=None):
    return json.dumps(
        {
            "result": "ok",
            "data": rows,
            "offset": offset,
            "total": len(rows) if total is None else total,
        }
    )


async def test_public_metadata_languages_dates_and_only_listing_requests():
    rows = json.loads(FIXTURE.read_text(encoding="utf8"))
    fetcher = FakeFetcher({feed_url(ID): payload(rows)})
    result = await Discoverer(fetcher).scan(SOURCE)
    assert len(result.entries) == len(rows) == 16
    assert result.coverage == "complete" and not result.warnings
    assert all(e.published_at and e.date_precision == "time" for e in result.entries)
    assert len(fetcher.calls) == result.pages_scanned == 1
    assert all(urlsplit(e.url).path.startswith("/chapter/") for e in result.entries)
    assert (
        len({e.url for e in result.entries}) == 16
    )  # Different translations of one chapter remain separate.


async def test_language_is_applied_at_api_and_verified_again_locally():
    rows = json.loads(FIXTURE.read_text(encoding="utf8"))
    row = next(r for r in rows if r["attributes"]["translatedLanguage"] == "it")
    wrong = next(r for r in rows if r["attributes"]["translatedLanguage"] != "it")
    endpoint = feed_url(ID, languages=["it"])
    fetcher = FakeFetcher({endpoint: payload([row, wrong])})
    result = await Discoverer(fetcher).scan(SOURCE, keywords="Italian")
    assert len(result.entries) == 1 and "Italian" in result.entries[0].context
    assert parse_qs(urlsplit(fetcher.calls[0]).query)["translatedLanguage[]"] == ["it"]
    assert result.coverage == "partial"


async def test_zero_available_english_does_not_substitute_other_languages():
    endpoint = feed_url(ID, languages=["en"])
    fetcher = FakeFetcher({endpoint: payload([])})
    result = await Discoverer(fetcher).scan(SOURCE, keywords="English")
    assert not result.entries and result.coverage == "complete"
    assert "no available chapters" in " ".join(result.warnings)
    assert fetcher.calls == [endpoint]


async def test_empty_language_results_are_cached_separately(tmp_path):
    english = feed_url(ID, languages=["en"])
    italian = feed_url(ID, languages=["it"])
    fetcher = FakeFetcher({english: payload([]), italian: payload([])})
    fetcher.cache = FetchCache(tmp_path / "cache.sqlite3")
    scanner = Discoverer(fetcher)
    await scanner.scan(SOURCE, keywords="English")
    second = await scanner.scan(SOURCE, keywords="English")
    assert second.cached and second.requests_made == 0
    await scanner.scan(SOURCE, keywords="Italian")
    assert fetcher.calls == [english, italian]


async def test_pagination_cap_and_duplicate_pages_stop_without_scraping_chapters():
    row = json.loads(FIXTURE.read_text(encoding="utf8"))[0]
    first = feed_url(ID)
    second = feed_url(ID, 1)
    fetcher = FakeFetcher(
        {
            first: payload([row], total=2000),
            second: payload([row], offset=1, total=2000),
        }
    )
    result = await Discoverer(fetcher, max_pages=2).scan(SOURCE)
    assert result.coverage == "partial" and len(result.entries) == 1
    assert fetcher.calls == [first, second]


async def test_wrong_manga_malicious_id_unavailable_and_invalid_number():
    row = json.loads(FIXTURE.read_text(encoding="utf8"))[0]
    bad = [
        dict(row, id="../../admin"),
        dict(row, relationships=[]),
        dict(row, attributes={**row["attributes"], "isUnavailable": True}),
    ]
    good = dict(row, attributes={**row["attributes"], "chapter": "NaN"})
    result = await Discoverer(FakeFetcher({feed_url(ID): payload([*bad, good])})).scan(
        SOURCE
    )
    assert len(result.entries) == 1 and result.entries[0].number is None
    assert result.coverage == "partial"
    assert title_id(SOURCE.replace("mangadex.org", "mangadex.org.evil.example")) is None
    with pytest.raises(DiscoveryError):
        await Discoverer(FakeFetcher({feed_url(ID): '{"error":"blocked"}'})).scan(
            SOURCE
        )
