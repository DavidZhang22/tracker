"""Behavior contracts for the optimized parser hot paths."""

import re
from datetime import UTC
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from app.tracker import urls
from app.tracker.dom import first_parent
from app.tracker.list_entries import LABEL, ROWS, candidates, label_node
from app.tracker.models import date_value
from app.tracker.page_context import PageContext
from app.tracker.parser import parse_page


@pytest.mark.parametrize(
    "raw",
    [
        "2026-09-19",
        "2026-09-19T04:15Z",
        "2026-09-19 04:15:03",
        "2026-09-19T04:15:03.123456-04:00",
        "2026-09-19T04:15:03+0530",
        "2026-09-19T04:15:03.123456789Z",
        "September 19, 2026",
        "19 Sep 2026 04:15 GMT",
        "2026/09/19",
        "09/19/2026",
    ],
)
def test_date_fast_path_matches_reference_parser(raw):
    parsed = date_parser.parse(raw, fuzzy=False)
    expected = (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    assert date_value(raw) == expected.isoformat()


@pytest.mark.parametrize(
    "raw", ["2026-13-45", "2026-09-19T25:00Z", "3 days ago", "47", None]
)
def test_invalid_or_relative_dates_do_not_gain_dates(raw):
    assert date_value(raw) is None


def test_iso_date_uses_native_parser():
    with patch(
        "app.tracker.models.parser.parse", side_effect=AssertionError("slow path")
    ):
        assert date_value("2026-09-19T04:15:03Z") == "2026-09-19T04:15:03+00:00"


@pytest.mark.parametrize("pattern", [r"pager", r"two three", r"^two$", r"absent"])
def test_fast_ancestor_walk_preserves_nearest_class_match(pattern):
    soup = BeautifulSoup(
        '<div class="pager"><section class="two three"><p class="other"><a>x</a></p></section></div>',
        "html.parser",
    )
    compiled = re.compile(pattern)
    assert first_parent(soup.a, class_pattern=compiled) is soup.a.find_parent(
        class_=compiled
    )
    assert first_parent(soup.a, {"section", "div"}) is soup.a.find_parent(
        ["section", "div"]
    )


@pytest.mark.parametrize(
    "markup",
    [
        '<p><i class="title">A</i><h3>B</h3></p>',
        '<div><span itemprop="name">A</span><span class="chapter-title extra">B</span></div>',
        "<article><p><span>Text</span></p><h5>C</h5></article>",
        '<li><span class="untitled">Nothing</span></li>',
        '<div><span itemprop="Name">Wrong</span><h2>Right</h2></div>',
    ],
)
def test_fast_label_selection_matches_css_in_document_order(markup):
    soup = BeautifulSoup(markup, "html.parser")
    assert label_node(soup) is soup.select_one(LABEL)


def test_list_candidates_match_css_selection_and_keep_boundaries():
    soup = BeautifulSoup(
        '<main><li>A</li><div role="listitem">B</div><div data-chapter-id="">C</div><article><h2>D</h2></article><table><tr><td>E</td></tr></table><div data-episode-id="3">F</div></main>',
        "html.parser",
    )
    assert [id(node) for node in candidates(soup, "")] == [
        id(node) for node in soup.select(ROWS)
    ]


def test_url_cache_is_scoped_bounded_and_preserves_validation():
    original = urls._canonical_url
    with patch.object(urls, "_canonical_url", wraps=original) as normalize:
        with urls.canonical_url_cache():
            assert (
                urls.canonical_url("/entry", "https://example.org/a")
                == "https://example.org/entry"
            )
            assert (
                urls.canonical_url("/entry", "https://example.org/a")
                == "https://example.org/entry"
            )
            assert normalize.call_count == 1
            assert (
                urls.canonical_url("/entry", "https://other.org/a")
                == "https://other.org/entry"
            )
            assert urls.canonical_url(
                "https://example.org/entry/", preserve_slash=True
            ).endswith("/")
            for _ in range(2):
                with pytest.raises(urls.DiscoveryError):
                    urls.canonical_url("https://user:password@example.org/")
            for index in range(5000):
                urls.canonical_url(f"https://example.org/{index}")
            cache, size = urls._url_cache.get()
            assert len(cache) <= 4096 and size <= 2_000_000
        assert urls._url_cache.get() is None
        assert (
            urls.canonical_url("/entry", "https://example.org/a")
            == "https://example.org/entry"
        )
        assert normalize.call_count >= 5005


def test_url_cache_is_cleared_after_parser_failure():
    with (
        pytest.raises(RuntimeError),
        patch("app.tracker.parser._parse_html", side_effect=RuntimeError("stop")),
    ):
        parse_page("<h1>A</h1>", "https://example.org/")
    assert urls._url_cache.get() is None


def test_shared_date_evidence_is_copied_between_consumers():
    soup = BeautifulSoup(
        '<article><h2>Chapter 1</h2><time datetime="2026-09-19">Today</time></article>',
        "html.parser",
    )
    context = PageContext()
    first = context.node_dates(soup.article)
    first["date_source"] = "modified by caller"
    assert context.node_dates(soup.article)["date_source"] == "time[datetime]"


@pytest.mark.parametrize(
    "keyword",
    [
        "Chinese",
        "Spanish",
        "Portuguese",
        "pt-br",
        "EN",
        "Japanese Romanized",
        "Chinese Traditional",
        "unknown",
        " Spanish Latin America ",
    ],
)
def test_precomputed_language_aliases_preserve_reference_order(keyword):
    from app.tracker.keywords import LANGUAGES, language_codes, normalize

    word = normalize(keyword)
    expected = [
        code
        for code, name in LANGUAGES.items()
        if word == normalize(code)
        or word == normalize(name)
        or word in {"spanish", "portuguese", "chinese"}
        and normalize(name).startswith(word + " ")
    ]
    assert language_codes(keyword) == expected
