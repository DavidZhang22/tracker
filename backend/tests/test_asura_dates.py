import json
from pathlib import Path
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from app.tracker.asura import astro_props
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.parser import parse_page
from app.tracker.store import Store
from tests.test_discovery import FakeFetcher

SOURCE = "https://asurascans.com/comics/dungeon-odyssey-53fc8424"
HTML = (Path(__file__).parent / "fixtures/asura-astro.html").read_text(encoding="utf8")
EXPECTED = "2026-08-21T17:14:15.386260+00:00"


def changed_props(change):
    soup = BeautifulSoup(HTML, "html.parser")
    island = soup.find("astro-island")
    props = json.loads(island["props"])
    change(props)
    island["props"] = json.dumps(props)
    return str(soup)


def chapter(rows, number=166):
    return next(e for e in rows if e.number == number)


async def test_real_astro_dates_and_titles_need_only_the_listing():
    fetcher = FakeFetcher({SOURCE: HTML})
    scan = await Discoverer(fetcher).scan(SOURCE)
    assert fetcher.calls == [SOURCE]
    assert len(scan.entries) == 7 and all(e.published_at for e in scan.entries)
    assert [e.number for e in scan.entries] == [160, 164, 165, 166, 167, 168, 169]
    entry = chapter(scan.entries)
    assert entry.published_at == EXPECTED
    assert entry.date_precision == "time" and entry.date_kind == "published"
    assert entry.date_source == "Astro chapter published_at"
    assert entry.title == "Chapter 166"
    assert chapter(scan.entries, 168).title == "Chapter 168"  # "last week"
    assert "RESUMED" in chapter(scan.entries, 160).title


def test_selector_and_path_only_enrich_selected_links():
    for selector, path in [('a[href$="/166"]', ""), ("", "/chapter/166")]:
        scan, _, _ = parse_page(HTML, SOURCE, selector, path)
        assert len(scan.entries) == 1 and scan.entries[0].published_at == EXPECTED


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(publicUrl=[0, "/comics/another-series"]),
        lambda p: p.update(
            publicUrl=[0, "https://unrelated.example/comics/dungeon-odyssey-53fc8424"]
        ),
        lambda p: p.update(seriesSlug=[0, "another-series"]),
        lambda p: p.pop("publicUrl"),
        lambda p: p["chapters"][1][3][1].update(series_slug=[0, "another-series"]),
        lambda p: p["chapters"][1][3][1].update(number=[0, "166"]),
        lambda p: p["chapters"][1][3][1].update(number=[0, 10**400]),
        lambda p: p["chapters"][1][3][1].update(published_at=[0, "3 weeks ago"]),
        lambda p: p["chapters"][1][3][1].pop("published_at"),
    ],
)
def test_missing_or_wrong_series_metadata_never_borrows_another_date(change):
    scan, _, _ = parse_page(changed_props(change), SOURCE)
    assert chapter(scan.entries).published_at is None
    assert chapter(scan.entries).title == "Chapter 166 3 weeks ago"


def test_conflicting_duplicate_dates_are_ignored():
    def conflict(props):
        duplicate = json.loads(json.dumps(props["chapters"][1][3]))
        duplicate[1]["published_at"] = [0, "2026-08-22T00:00:00Z"]
        props["chapters"][1].append(duplicate)

    scan, _, _ = parse_page(changed_props(conflict), SOURCE)
    assert chapter(scan.entries).published_at is None


@pytest.mark.parametrize(
    "raw",
    [
        "{broken",
        "__import__('os').abort()",
        "[]",
        "x" * 4_000_001,
        '{"chapters":' + "[1,[" * 26 + "[0,1]" + "]]" * 26 + "}",
    ],
    ids=["broken", "executable", "not-object", "too-large", "too-deep"],
)
def test_invalid_or_excessive_astro_props_are_safe(raw):
    assert astro_props(raw) == {}


def test_astro_typed_values_include_dates_and_reject_unknown_tags():
    assert astro_props(
        '{"date":[3,"2026-08-21T17:14:15Z"],"missing":[0],"unknown":[99,"ignored"]}'
    ) == {
        "date": "2026-08-21T17:14:15Z",
        "missing": None,
        "unknown": None,
    }


async def test_refresh_backfills_dates_preserves_progress_and_sorts_by_date(tmp_path):
    soup = BeautifulSoup(HTML, "html.parser")
    del soup.find("astro-island")["props"]
    old, _, _ = parse_page(str(soup), SOURCE)
    store = Store(tmp_path / "library.db")
    item = store.create(store.save_scan(old.to_dict()))
    with store.connection() as db:
        db.execute("UPDATE links SET read=1,favorite=1,ignored=1 WHERE number=166")
        db.execute("UPDATE links SET deleted=1 WHERE number=167")
        before = [
            tuple(row)
            for row in db.execute(
                "SELECT id,read,favorite,ignored,deleted FROM links ORDER BY id"
            )
        ]
    fetcher = FakeFetcher({SOURCE: HTML})
    fetcher.cache = FetchCache(tmp_path / "cache.db")
    # A stale complete-scan record from the prior parser must not hide the fix.
    with patch(
        "app.tracker.discovery.DISCOVERY_VERSION", "context-keywords-suggestions-v2"
    ):
        stale_fetcher = FakeFetcher({SOURCE: str(soup)})
        stale_fetcher.cache = fetcher.cache
        await Discoverer(stale_fetcher).scan(SOURCE)
    scan = await Discoverer(fetcher).scan(SOURCE)
    assert fetcher.calls == [SOURCE]
    assert store.merge(item["id"], scan.to_dict()) == 0
    with store.connection() as db:
        assert before == [
            tuple(row)
            for row in db.execute(
                "SELECT id,read,favorite,ignored,deleted FROM links ORDER BY id"
            )
        ]
        assert db.execute(
            "SELECT title,published_at FROM links WHERE number=166"
        ).fetchone()[:] == ("Chapter 166", EXPECTED)
        ordered = [
            row[0]
            for row in db.execute("SELECT number FROM links ORDER BY published_at")
        ]
        assert ordered == [160, 164, 165, 166, 167, 168, 169]
