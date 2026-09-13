import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.analysis_pool import PageAnalyzer
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.parser import parse_page
from app.tracker.recipes import MAX_USES, analyze
from tests.test_discovery import FakeFetcher
from tests.test_store_api import FakeDiscoverer

SOURCE = "https://example.org/series/book"
FIXTURES = Path(__file__).parent / "fixtures"


def cards(count=30):
    return (
        "<h1>Book</h1><main>"
        + "".join(
            f'<article><h2><a href="/chapter/{i}">Chapter {i}</a></h2>'
            '<div><span lang="en">English</span>Official <time datetime="2026-09-10">September 10</time></div></article>'
            for i in range(count)
        )
        + "</main>"
    )


@pytest.fixture
def learned():
    html = cards()
    result, recipe, used = analyze(html, SOURCE)
    assert recipe and not used
    return html, result, recipe


@pytest.mark.parametrize(
    "fixture,source",
    [
        ("asura", "https://asurascans.com/comics/the-nebulas-civilization-53fc8424"),
        ("asura-astro", "https://asurascans.com/comics/dungeon-odyssey-53fc8424"),
        ("royalroad", "https://www.royalroad.com/fiction/21220/mother-of-learning"),
        ("hn", "https://news.ycombinator.com/"),
        ("xkcd", "https://xkcd.com/archive/"),
    ],
)
def test_real_fixtures_preserve_every_field_and_pagination(fixture, source):
    html = (FIXTURES / f"{fixture}.html").read_text(encoding="utf8")
    expected, recipe, _ = analyze(html, source)
    assert recipe
    actual, _, used = analyze(html, source, recipe=json.loads(json.dumps(recipe)))
    assert used and actual == expected


def test_new_links_dates_and_languages_are_extracted_without_model_calls(
    learned, monkeypatch
):
    import app.tracker.parser as parser
    from app.tracker.record_context import RecordContext

    html, _, recipe = learned
    changed = (
        cards(31)
        .replace('lang="en">English', 'lang="es">Spanish')
        .replace("2026-09-10", "2026-09-12")
    )
    expected = parse_page(changed, SOURCE)

    def unexpected(*args, **kwargs):
        pytest.fail("Stable recipe should not invoke a classifier")

    monkeypatch.setattr(parser, "classify_context", unexpected)
    monkeypatch.setattr(RecordContext, "_region", unexpected)
    actual, _, used = analyze(changed, SOURCE, recipe=recipe)
    assert used and actual == expected
    assert len(actual[0].entries) == 31
    assert all(
        e.language == "es" and e.published_at.startswith("2026-09-12")
        for e in actual[0].entries
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda h: h.replace("<article>", '<article class="new-layout">', 1),
        lambda h: h.replace("/chapter/0", "/episodes/zero"),
        lambda h: h.replace('datetime="2026-09-10"', "", 1),
        lambda h: h.replace("Chapter 0</a>", "Privacy policy</a>"),
        lambda h: h.replace(
            "<main>", '<main><article><a href="/new-format/a">A new entry</a></article>'
        ),
        lambda h: (
            h
            + '<script type="application/ld+json">{"@type":"Article","url":"/blog/new","headline":"New","datePublished":"2026-09-12"}</script>'
        ),
        lambda h: h.replace('href="/chapter/0"', 'href="/chapter/100"'),
        lambda h: h.replace('<time datetime="2026-09-10">September 10</time>', "", 1),
        lambda h: h.replace(
            'datetime="2026-09-10"', 'datetime="2026-09-10T10:00:00Z"', 1
        ),
        lambda h: h.replace('datetime="2026-09-10"', 'data-timestamp="1789034400"', 1),
    ],
)
def test_drift_and_missing_evidence_fall_back_to_exact_deep_output(learned, mutation):
    html, _, recipe = learned
    changed = mutation(html)
    actual, _, used = analyze(changed, SOURCE, recipe=recipe)
    assert not used and actual == parse_page(changed, SOURCE)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(created=0),
        lambda r: r.update(created="invalid"),
        lambda r: r.update(uses=MAX_USES),
        lambda r: r.update(policies={}),
        lambda r: r.update(version=-1),
    ],
)
def test_expired_exhausted_or_corrupt_recipes_rebuild_safely(learned, change):
    html, expected, recipe = learned
    change(recipe)
    actual, rebuilt, used = analyze(html, SOURCE, recipe=recipe)
    assert not used and actual == expected and rebuilt


def test_explicit_selectors_do_not_reuse_or_expand_a_recipe(learned):
    html, _, recipe = learned
    result, rebuilt, used = analyze(html, SOURCE, 'a[href$="/1"]', recipe=recipe)
    assert not used and rebuilt is None and len(result[0].entries) == 1


async def test_disk_recipe_survives_restart_and_deep_bypasses_all_analysis_caches(
    tmp_path,
):
    cache = FetchCache(tmp_path / "cache.db")
    fetcher = FakeFetcher({SOURCE: cards()})
    fetcher.cache = cache
    await Discoverer(fetcher).scan(SOURCE)
    # Revalidation has changed HTML; new scanner simulates an application restart.
    fetcher.pages[SOURCE] = cards(31)
    for key in list(cache_keys(cache)):
        if key.startswith("scan:"):
            value = cache.get(key)
            value["checked"] = 0
            cache.put(key, value)
    scanner = Discoverer(fetcher)
    quick = await scanner.scan(SOURCE, keywords="English")
    assert quick.analysis_mode == "light" and len(quick.entries) == 31
    full = await scanner.scan(SOURCE, keywords="English", deep=True)
    assert (
        full.analysis_mode == "deep"
        and not full.cached
        and full.entries == quick.entries
    )
    assert len(fetcher.calls) == 3  # No visits to individual chapters.


def cache_keys(cache):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(cache.path)) as db:
        return [r[0] for r in db.execute("SELECT key FROM cache")]


async def test_pagination_is_still_scanned_in_full_with_recipes():
    second = SOURCE + "?page=2"
    first_html = cards() + '<a rel="next" href="?page=2">Next</a>'
    fetcher = FakeFetcher(
        {SOURCE: first_html, second: cards().replace("/chapter/", "/chapter/10")}
    )
    fetcher.cache = FetchCache()
    scanner = Discoverer(fetcher)
    initial = await scanner.scan(SOURCE)
    fetcher.pages[SOURCE] = first_html.replace("Book</h1>", "Book revised</h1>")
    fetcher.pages[second] += "<!-- changed response -->"
    for key, value in list(fetcher.cache.memory.items()):
        if key.startswith("scan:"):
            value["checked"] = 0
    quick = await scanner.scan(SOURCE)
    full = await scanner.scan(SOURCE, deep=True)
    assert quick.entries == full.entries == initial.entries
    assert quick.pages_scanned == 2 and quick.analysis_mode == "light"
    assert fetcher.calls == [SOURCE, second] * 3


async def test_recipe_round_trip_through_real_process_workers(learned):
    html, expected, recipe = learned
    analyzer = PageAnalyzer(2)
    try:
        result, updated, used = await analyzer.analyze_learned(
            html, SOURCE, recipe=recipe
        )
        assert result == expected and used and updated["uses"] == 1
    finally:
        await analyzer.aclose()


def test_api_initial_scan_is_deep_and_both_refresh_modes_are_explicit(tmp_path):
    class Scanner(FakeDiscoverer):
        modes = []

        async def scan(self, *args, **kwargs):
            self.modes.append(kwargs.get("deep", False))
            return await super().scan(*args, **kwargs)

    scanner = Scanner()
    with TestClient(create_app(tmp_path / "db", scanner)) as client:
        preview = client.post("/api/scans", json={"url": SOURCE}).json()
        item = client.post("/api/items", json={"scan_id": preview["scan_id"]}).json()
        assert client.post(f"/api/items/{item['id']}/refresh").status_code == 200
        assert (
            client.post(f"/api/items/{item['id']}/refresh?deep=true").status_code == 200
        )
        stream = client.post("/api/refresh?stream=true&deep=true")
        assert (
            stream.status_code == 200
            and json.loads(stream.text.splitlines()[-1])["type"] == "complete"
        )
        assert scanner.modes == [True, False, True, True]
        assert client.post("/api/refresh?deep=invalid").status_code == 422
