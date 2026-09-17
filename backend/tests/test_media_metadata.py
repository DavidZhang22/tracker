import json

import pytest
from bs4 import BeautifulSoup

from app.tracker.media_metadata import MAX_TAGS, annotate, classify, source_summary
from app.tracker.models import Entry, Scan
from app.tracker.parser import parse_page
from app.tracker.store import Store
from tests.test_store_api import add
from tests.test_store_api import client as media_client

client = media_client


def manga():
    return Scan(
        "https://w11.grandbluedreamingmanga.com/",
        "Grand Blue Dreaming Manga Online",
        "blog",
        entries=[
            Entry(
                "https://w11.grandbluedreamingmanga.com/manga/grand-blue-chapter-112",
                "Chapter 112",
            )
        ],
        source_summary="A comedy manga about college life and scuba diving. Available in English.",
        methods=["page"],
        pages_scanned=1,
    )


@pytest.mark.parametrize(
    "title,summary,entry,kind",
    [
        ("Grand Blue Dreaming Manga Online", "Comedy manga", "Chapter 112", "comic"),
        ("A Manhwa", "RSS feed for our series", "Chapter 8", "comic"),
        (
            "Manga industry blog",
            "News articles about manga",
            "Posts: annual industry report",
            "blog",
        ),
        ("The Last Novelist", "A fantasy web novel", "Chapter 17", "novel"),
        (
            "Ocean conversations",
            "A podcast about marine science",
            "Podcast 32",
            "podcast",
        ),
        (
            "Career opportunities",
            "Open positions and jobs",
            "Software engineer jobs",
            "jobs",
        ),
        ("Weekly coding contests", "Events calendar", "Contest 32", "events"),
        ("Project changelog", "Software release notes", "Release v2.5", "software"),
        ("Drawing course", "Learn with structured lessons", "Lesson 14", "course"),
        ("Journal publications", "Research papers", "Research paper 29", "research"),
        ("Skyward TV series", "Watch animated videos", "Episode 5", "video"),
        ("A band discography", "Our albums and songs", "Album 4", "music"),
    ],
)
def test_media_evidence_across_unseen_sites(title, summary, entry, kind):
    assert (
        classify(
            {
                "url": "https://unseen.example/list",
                "title": title,
                "source_summary": summary,
                "kind": "website",
                "entries": [{"title": entry}],
            }
        )
        == kind
    )


def test_metadata_does_not_change_scraping_strategy_or_fetch_links():
    html = '<title>Grand Blue Dreaming Manga Online</title><link rel="alternate" type="application/rss+xml" href="/feed"><main><p>A comedy about college life and scuba diving, published in English.</p>'
    html += (
        "".join(
            f'<a href="/manga/grand-blue-chapter-{i}">Chapter {i}</a>'
            for i in range(1, 12)
        )
        + "</main>"
    )
    scan, _, _ = parse_page(html, manga().url)
    before = scan.to_dict()
    tagged = annotate(before)
    assert scan.kind == "blog" and tagged["kind"] == "comic"
    assert tagged["entries"] == before["entries"] and len(scan.entries) == 11
    assert {"comedy", "college university", "diving scuba", "english"} <= set(
        tagged["search_tags"]
    )


def test_hidden_metadata_is_bounded_deduplicated_and_ignores_navigation():
    soup = BeautifulSoup(
        "<nav><p>"
        + "Lottery casino offers " * 10
        + "</p></nav><main><p>"
        + "Useful science discovery " * 5
        + "</p><p>"
        + '<a href="/x">Spam promotion </a>' * 10
        + "</p></main>",
        "html.parser",
    )
    summary = source_summary(soup)
    assert "casino" not in summary and "Spam" not in summary
    tags = annotate(manga().to_dict() | {"source_summary": summary + " word" * 10000})[
        "search_tags"
    ]
    assert len(tags) <= MAX_TAGS and len(tags) == len(set(tags))
    assert max(map(len, tags)) <= 48
    assert classify({"url": "https://manga.example/", "title": "Welcome"}) == "website"


def test_sidebar_synopsis_is_kept_only_when_it_describes_the_current_title():
    soup = BeautifulSoup(
        '<title>Grand Blue Dreaming Manga Online</title><main></main><aside><p>Grand Blue is a comedy manga about university life and scuba diving.</p><p>A different Fantasy novel: Forest Mage adventures in an enchanted kingdom.</p></aside><meta name="keywords" content="lottery casino spam">',
        "html.parser",
    )
    summary = source_summary(soup)
    assert "comedy" in summary and "university" in summary
    assert "Forest Mage" not in summary and "casino" not in summary


def test_manual_type_survives_refresh_reset_and_restart(client):
    client.fake.result = manga()
    item = add(client, kind_override="podcast")
    iid = item["id"]
    assert item["kind"] == "podcast" and item["detected_kind"] == "comic"
    client.app.state.store.merge(iid, manga().to_dict())
    reopened = Store(client.app.state.store.path)
    assert reopened.item(iid)["kind"] == "podcast"
    reset = client.patch(f"/api/items/{iid}", json={"kind_override": ""})
    assert reset.status_code == 200 and reset.json()["kind"] == "comic"
    assert reset.json()["auto_read"] == item["auto_read"]
    assert (
        client.patch(
            f"/api/items/{iid}", json={"kind_override": "sql;DROP"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/items", json={"scan_id": "unused", "kind_override": "invalid"}
        ).status_code
        == 422
    )


def test_old_libraries_backfill_locally_without_changing_progress(tmp_path):
    path = tmp_path / "old.sqlite3"
    store = Store(path)
    item = store.create(store.save_scan(manga().to_dict()), mark_read=True)
    store.update("items", item["id"], {"favorite": True, "ignored": True})
    with store.connection() as db:
        db.execute(
            "UPDATE items SET kind='blog',detected_kind='',media_version=0,search_tags='[]'"
        )
        db.execute("PRAGMA user_version=10")
    reopened = Store(path)
    migrated = reopened.item(item["id"])
    assert migrated["kind"] == "comic" and migrated["favorite"] and migrated["ignored"]
    assert migrated["read_count"] == 1 and migrated["total_count"] == 1
    assert migrated["search_tags"]
    assert Store(path).item(item["id"])["search_tags"] == migrated["search_tags"]


def test_invalid_direct_override_rolls_back_and_private_tags_are_not_writable(tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    item = store.create(store.save_scan(manga().to_dict()))
    with pytest.raises(ValueError):
        store.update("items", item["id"], {"kind_override": "invalid"})
    with pytest.raises(ValueError):
        store.update("items", item["id"], {"search_tags": json.dumps(["injected"])})
    assert store.item(item["id"])["kind_override"] == ""
