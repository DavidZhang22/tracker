import time

import pytest
from bs4 import BeautifulSoup

from app.main import create_app
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.models import Entry, Scan
from app.tracker.parser import parse_page
from app.tracker.store import Store
from app.tracker.suggestions import (
    collect_cached,
    observed_sources,
    ranked_suggestions,
    save_observations,
)
from tests.test_accounts import CONFIG, sign_up
from tests.test_accounts import client as account_client

HTML = """<html><h1>Magic school</h1><a href='/series/magic-school/chapter-1'>Chapter 1</a>
<section class='related'><h2>You may also like</h2>
<article><a href='/series/time-mage'><img alt='The Time Mage'></a><p>Magic school and a mysterious spell.</p></article>
<article><a href='/series/forest-witch'>Forest Witch</a><p>A witch explores the forest.</p></article>
<a href='/series/time-mage/'>Duplicate title</a><a href='/series/magic-school'>Current series</a>
<a href='/series/time-mage/chapter-1'>Chapter 1</a><a href='/login'>Login</a>
<a href='https://127.0.0.1/series/private'>Private</a><a href='javascript:alert(1)'>Bad</a>
<a href='https://other.example/series/another'>Another Story</a>
<a href='https://facebook.com/'>Facebook</a></section>
<nav><a href='/series/menu'>Menu link</a></nav><footer><a href='/series/sponsor'>Sponsor</a></footer></html>"""
ROOT = "https://fiction.example/series/magic-school"


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Fixtures represent additions over time, not a way around the public cooldown.
    clock = iter(range(1000, 100000, 8))
    monkeypatch.setattr("app.tracker.store.time", lambda: next(clock))
    return Store(tmp_path / "library.sqlite3")


def add(store, url=ROOT, title="Magic school", suggestions=None):
    result = Scan(
        url,
        title,
        "novel",
        entries=[Entry(url + "/chapter-1", "Chapter 1")],
        suggestions=suggestions,
    )
    return store.create(store.save_scan(result.to_dict()))


def test_observed_collections_exclude_chapters_navigation_social_and_unsafe_links():
    result = observed_sources(BeautifulSoup(HTML, "html.parser"), ROOT, "novel")
    assert [c["title"] for c in result] == [
        "The Time Mage",
        "Forest Witch",
        "Another Story",
    ]
    assert "mysterious spell" in result[0]["summary"]
    scan, _, _ = parse_page(HTML, ROOT)
    assert scan.suggestions == observed_sources(
        BeautifulSoup(HTML, "html.parser"), ROOT
    )
    assert ROOT + "/chapter-1" in [entry.url for entry in scan.entries]


@pytest.mark.parametrize(
    "path,kind",
    [
        ("/fiction/123/a-story", "novel"),
        ("/comics/a-story", "comic"),
        ("/channel/UCRIgIJQWuBJ0Cv_VlU3USNA", "youtube"),
    ],
)
def test_related_sources_across_collection_formats(path, kind):
    host = "www.youtube.com" if kind == "youtube" else "related.example"
    html = f'<section aria-label="Related sources"><a href="https://{host}{path}">A useful source</a></section>'
    result = observed_sources(BeautifulSoup(html, "html.parser"), ROOT)
    assert len(result) == 1 and result[0]["kind"] == kind


def test_blogroll_requires_context_and_never_invents_an_archive():
    html = '<aside class="blogroll"><a href="https://science.example/">Science Notes</a><a href="https://news.example/post/123">One article</a></aside><a href="https://random.example/">Random footer</a>'
    result = observed_sources(
        BeautifulSoup(html, "html.parser"), "https://blog.example/"
    )
    assert [(c["url"], c["kind"]) for c in result] == [
        ("https://science.example/", "blog")
    ]


def test_cover_card_title_is_separate_from_counts_and_genres():
    html = '<div><a href="/series/time-mage"><img alt="Time Mage cover"><span>Chapters: 123</span><span>Views: 4.5K</span><p>Time Mage</p><span>Fantasy Adventure</span></a></div>'
    candidate = observed_sources(BeautifulSoup(html, "html.parser"), ROOT)[0]
    assert candidate["title"] == "Time Mage"
    assert candidate["summary"] == "Fantasy Adventure"


def test_favorites_feedback_and_trash_exclusions_persist(store):
    candidates = observed_sources(BeautifulSoup(HTML, "html.parser"), ROOT, "novel")
    original = add(store, suggestions=candidates)
    ranked = ranked_suggestions(store)["suggestions"]
    assert len(ranked) == 3
    hidden = ranked[0]
    with store.connection() as db:
        db.execute("UPDATE suggestions SET dismissed=1 WHERE id=?", (hidden["id"],))
        save_observations(db, original["id"], candidates)
    assert hidden["id"] not in [
        s["id"] for s in ranked_suggestions(Store(store.path))["suggestions"]
    ]
    tracked = add(store, candidates[1]["url"], "Known witch")
    store.bulk_selected("items", [tracked["id"]], "delete")
    assert candidates[1]["url"] not in [
        s["url"] for s in ranked_suggestions(store)["suggestions"]
    ]
    store.update("items", original["id"], {"favorite": True})
    assert all(
        s["reason"] == "From a favorite source"
        for s in ranked_suggestions(store)["suggestions"]
    )
    store.update("items", original["id"], {"ignored": True})
    assert ranked_suggestions(store)["suggestions"] == []


def test_favorite_signal_changes_rank_without_external_training(store):
    first = add(
        store,
        "https://one.example/",
        "Magic",
        [{"url": "https://one.example/series/alpha", "title": "Magic Alpha"}],
    )
    second = add(
        store,
        "https://two.example/",
        "Magic",
        [{"url": "https://two.example/series/beta", "title": "Magic Beta"}],
    )
    store.update("items", first["id"], {"favorite": True})
    assert ranked_suggestions(store)["suggestions"][0]["source_id"] == first["id"]
    store.update("items", first["id"], {"favorite": False})
    store.update("items", second["id"], {"favorite": True})
    assert ranked_suggestions(store)["suggestions"][0]["source_id"] == second["id"]


def test_cache_backfill_is_local_incremental_and_keeps_dismissals(store):
    item = add(store)
    cache = FetchCache()
    cache.put(ROOT, {"body": HTML, "final": ROOT})
    assert collect_cached(store, cache)["pages_used"] == 1
    assert len(ranked_suggestions(store)["suggestions"]) == 3
    assert collect_cached(store, cache)["pages_used"] == 0
    store.bulk_selected("items", [item["id"]], "delete")
    cache.put(ROOT, {"body": HTML, "final": ROOT, "checked": time.time() + 1})
    assert collect_cached(store, cache)["pages_used"] == 0
    assert ranked_suggestions(store)["suggestions"] == []


def test_storage_and_per_source_candidate_caps(store, monkeypatch):
    monkeypatch.setattr("app.tracker.suggestions.MAX_CANDIDATES", 3)
    candidates = [
        {"url": f"https://fiction.example/series/story-{i}", "title": f"Story {i}"}
        for i in range(40)
    ]
    add(store, suggestions=candidates)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM suggestions").fetchone()[0] == 3
    html = "".join(f'<a href="{c["url"]}">{c["title"]}</a>' for c in candidates)
    assert len(observed_sources(BeautifulSoup(html, "html.parser"), ROOT)) == 20


def test_suggestion_api_never_fetches_and_is_account_private(tmp_path):
    class CacheOnlyFetcher:
        cache = FetchCache()

        async def get(self, url):
            pytest.fail("Suggestion pages must not fetch the web")

    app = create_app(
        tmp_path / "library.sqlite3", Discoverer(CacheOnlyFetcher()), CONFIG
    )
    with account_client(app) as alice, account_client(app) as bob:
        sign_up(alice, "alice")
        sign_up(bob, "bob")
        # Resolve the authenticated account just as the API does, without exposing sessions.
        user = app.state.accounts.user(next(iter(alice.cookies.values())))
        personal = app.state.accounts.store(user)
        add(
            personal,
            suggestions=observed_sources(BeautifulSoup(HTML, "html.parser"), ROOT),
        )
        candidate = alice.get("/api/suggestions").json()["suggestions"][0]
        assert bob.get("/api/suggestions").json()["suggestions"] == []
        assert (
            bob.patch(
                f"/api/suggestions/{candidate['id']}", json={"dismissed": True}
            ).status_code
            == 404
        )
        assert (
            alice.patch(
                f"/api/suggestions/{candidate['id']}", json={"dismissed": True}
            ).status_code
            == 200
        )
        assert alice.post("/api/suggestions/rebuild").status_code == 200
