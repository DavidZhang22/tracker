"""Small, renamed reproductions of public listing layouts; no network requests."""

import html
import json

import pytest
from bs4 import BeautifulSoup

from app.tracker.dates import link_date
from app.tracker.discovery import Discoverer
from app.tracker.listing_structure import (
    joined_metadata,
    navigation_links,
    observed_identifiers,
)
from app.tracker.models import Entry
from app.tracker.parser import parse_page
from tests.test_discovery import FakeFetcher

SOURCE = "https://publisher.example/archive"


@pytest.mark.parametrize("wrapper", ["", '<div class="container sidebar-right"><main>'])
def test_publication_labels_beat_headline_and_summary_dates(wrapper):
    markup = (
        wrapper
        + """<ul class="list-news"><li>
    <h2><a href="/post/deadline">Applications close September 22, 2026</a></h2>
    <span class="meta">Posted by <strong>Editor</strong> on Sept. 16, 2026</span>
    <p>Apply by September 22, 2026.</p></li></ul>"""
    )
    scan, _, _ = parse_page(markup, SOURCE)
    assert len(scan.entries) == 1
    assert scan.entries[0].published_at.startswith("2026-09-16")


@pytest.mark.parametrize("month", ["Aug.", "Sept.", "September"])
def test_release_date_abbreviations(month):
    markup = f'<li><span><a href="/release/v4">Product 4.0</a></span><span class="release-date">{month} 5, 2026</span></li>'
    date = link_date(BeautifulSoup(markup, "html.parser").a)
    assert date["published_at"].startswith(
        "2026-08-05" if month == "Aug." else "2026-09-05"
    )


def test_definition_pairs_preserve_group_dates_and_exclude_format_links():
    markup = "<dl><h3>Wed, 16 Sep 2026 (first 2 of 80)</h3>"
    for n in range(2):
        markup += f'<dt><a href="/abs/26{n}">Record {n}</a><a href="/pdf/26{n}">pdf</a></dt><dd><div class="list-title">Title: Research result {n}</div><p>Discusses September 1, 2025</p></dd>'
    scan, _, _ = parse_page(markup + "</dl>", SOURCE)
    records = [e for e in scan.entries if e.method == "definition list"]
    assert len(records) == 2
    assert all(e.url.startswith("https://publisher.example/abs/") for e in records)
    assert all(
        e.title.startswith("Research result")
        and e.date_kind == "listed"
        and e.published_at.startswith("2026-09-16")
        for e in records
    )


@pytest.mark.parametrize("attribute", ["data-props", "data-page"])
def test_attribute_json_joins_metadata_to_existing_records(attribute):
    payload = {
        "items": [
            {
                "paper": {
                    "id": "42",
                    "title": "A useful research paper",
                    "publishedAt": "2026-09-10T12:00:00Z",
                },
                "publishedAt": "2026-09-16T12:00:00Z",
            }
        ]
    }
    markup = f"<main><article><a href=\"/papers/42\">View paper</a></article></main><div {attribute}='{html.escape(json.dumps(payload), quote=True)}'></div>"
    scan, _, _ = parse_page(markup, SOURCE)
    assert len(scan.entries) == 1
    assert scan.entries[0].title == "A useful research paper"
    assert scan.entries[0].published_at.startswith("2026-09-10")


def test_metadata_never_invents_routes_or_joins_ambiguous_ids():
    record = {"id": "42", "title": "Research paper", "publishedAt": "2026-09-10"}
    assert joined_metadata(record, {}) is None
    ids = observed_identifiers(
        [Entry(SOURCE + "/papers/42", "Paper"), Entry(SOURCE + "/authors/42", "Author")]
    )
    assert joined_metadata(record, ids) is None
    assert (
        joined_metadata(
            {"id": "42", "title": "Research paper", "createdAt": "2026-09-10"},
            {"42": {SOURCE + "/42"}},
        )
        is None
    )


def test_cards_keep_undated_siblings_without_copying_neighbor_dates_or_menus():
    markup = '<div id="global-navigation" hidden><a href="/post/unrelated">Interesting announcement</a></div><main>'
    for n in range(5):
        date = "<div>September 16, 2026</div>" if n < 4 else ""
        markup += f'<div class="content-item"><a href="/post/{n}"><img alt=""/></a><div><a class="item-heading" href="/post/{n}">Research announcement {n}</a>{date}</div></div>'
    scan, _, _ = parse_page(markup + "</main>", SOURCE)
    assert {e.url for e in scan.entries} == {
        f"https://publisher.example/post/{n}" for n in range(5)
    }
    assert next(e for e in scan.entries if e.url.endswith("/4")).published_at is None


def test_menu_class_does_not_hide_a_content_list_inside_main():
    markup = (
        '<section role="main"><ol class="menu">'
        + "".join(
            f'<li><a href="/release/{n}">Product release {n}</a><span>Sept. 16, 2026</span></li>'
            for n in range(3)
        )
        + "</ol></section>"
    )
    soup = BeautifulSoup(markup, "html.parser")
    assert not ({id(a) for a in soup.select("a")} & navigation_links(soup))


def test_numeric_range_pagination_chooses_nearest_forward_range():
    markup = '<div class="paging"><a href="?skip=0&show=50">1-50</a><a href="?skip=100&show=50">101-150</a><a href="?skip=50&show=50">51-100</a><a href="?show=2000">all</a></div>'
    assert parse_page(markup, SOURCE)[1] == [SOURCE + "?show=50&skip=50"]


def test_body_pagination_preserves_filters_over_incomplete_head_link():
    markup = '<link rel="next" href="?start_index=26"><nav><a href="?sort=latest&start_index=26">Next</a></nav>'
    assert parse_page(markup, SOURCE + "?sort=latest")[1] == [
        SOURCE + "?sort=latest&start_index=26"
    ]
    assert not parse_page(
        '<a rel="next" href="?page=2">Next</a>', SOURCE + "?language=en"
    )[1]


def test_pagination_cannot_switch_to_novel_reviews():
    source = "https://www.royalroad.com/fiction/42/story"
    assert not parse_page(
        '<a rel="next" href="/fiction/42/story/reviews?page=2">Next</a>', source
    )[1]


@pytest.mark.asyncio
async def test_repeated_listing_stops_even_if_next_url_keeps_changing():
    cards = "".join(
        f'<article><a href="/post/{n}">Announcement {n}</a></article>' for n in range(3)
    )
    fetcher = FakeFetcher(
        {
            SOURCE: cards + '<a rel="next" href="?page=2">Next</a>',
            SOURCE + "?page=2": cards + '<a rel="next" href="?page=3">Next</a>',
        }
    )
    scan = await Discoverer(fetcher).scan(SOURCE)
    assert len(fetcher.calls) == 2 and len(scan.entries) == 3
    assert any("no different records" in w for w in scan.warnings)
