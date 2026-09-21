from bs4 import BeautifulSoup

from app.tracker import link_model
from app.tracker.link_model import (
    FEATURES,
    MAX_CANDIDATES,
    MAX_INSPECTED_ANCHORS,
    candidate_anchors,
    candidates,
)

SOURCE = "https://catalog.example/list"


def soup(markup):
    return BeautifulSoup(markup, "html.parser")


def test_ordinary_pages_keep_duplicate_features_and_raw_anchor_budget():
    document = soup(
        '<a href="javascript:alert(1)">Invalid</a>'
        '<a href="/one"><img alt=""/></a>'
        '<h2><a href="/one">A complete title</a></h2>'
        '<a href="/two">Second title</a><a href="/three">Third title</a>'
    )
    aliases = {}
    rows = list(candidates(document, SOURCE, limit=4, aliases=aliases))
    assert [url for _, url, _, _ in rows] == [
        "https://catalog.example/one",
        "https://catalog.example/one",
        "https://catalog.example/two",
    ]
    assert [row[3][FEATURES.index("position")] for row in rows] == [0, 0.5, 1]
    assert aliases == {id(row[0]): id(row[0]) for row in rows}
    assert (
        rows[0][3][FEATURES.index("shape_frequency")]
        == rows[2][3][FEATURES.index("shape_frequency")]
    )


def test_duplicate_flood_does_not_hide_later_unique_content():
    document = soup(
        '<a href="/same"><img alt=""/></a>' * (MAX_CANDIDATES + 1)
        + '<h2><a href="/same">Informative collection title</a></h2>'
        + '<h2><a href="/late">Late unique record</a></h2>'
    )
    aliases = {}
    rows = list(candidates(document, SOURCE, aliases=aliases))
    assert [url for _, url, _, _ in rows] == [
        "https://catalog.example/same",
        "https://catalog.example/late",
    ]
    assert rows[0][2] == "Informative collection title"
    assert len(aliases) == MAX_CANDIDATES + 3
    assert aliases[id(document.find("a"))] == id(rows[0][0])


def test_informative_heading_wins_over_image_and_menu_aliases():
    document = soup(
        '<a href="/same"><img alt=""/></a>' * (MAX_CANDIDATES + 1)
        + '<nav><h2><a href="/same">A long menu description</a></h2></nav>'
        + '<article><h2><a href="/same">The actual record</a></h2></article>'
        + '<p><a href="/same">An even longer inline reference description</a></p>'
    )
    chosen = candidate_anchors(document, SOURCE)
    assert len(chosen) == 1
    assert chosen[0][0].get_text() == "The actual record"


def test_malformed_anchors_stay_unclassified_without_spending_unique_slots():
    document = soup(
        '<a href="javascript:alert(1)">Invalid</a>' * (MAX_CANDIDATES + 1)
        + '<a href="/one">First record</a>'
        + '<a href="/one#details">Same record</a>'
        + '<a href="/two">Second record</a>'
    )
    aliases = {}
    rows = candidate_anchors(document, SOURCE, aliases=aliases)
    assert {url for _, url, _ in rows} == {
        "https://catalog.example/one",
        "https://catalog.example/two",
    }
    assert len(aliases) == 3
    assert id(document.find("a")) not in aliases


def test_unique_target_cap_is_deterministic_and_output_follows_dom_order():
    document = soup(
        "".join(
            f'<a href="/record/{i}">Record {i}</a>' for i in range(MAX_CANDIDATES + 3)
        )
        + '<h2><a href="/record/0">A better first-record heading</a></h2>'
    )
    first = candidate_anchors(document, SOURCE)
    second = candidate_anchors(document, SOURCE, limit=MAX_CANDIDATES * 2)
    assert [(id(a), url) for a, url, _ in first] == [
        (id(a), url) for a, url, _ in second
    ]
    assert len(first) == MAX_CANDIDATES
    assert {url for _, url, _ in first} == {
        f"https://catalog.example/record/{i}" for i in range(MAX_CANDIDATES)
    }
    assert first[-1][1] == "https://catalog.example/record/0"
    dom_order = {id(a): i for i, a in enumerate(document.find_all("a"))}
    assert [dom_order[id(a)] for a, _, _ in first] == sorted(
        dom_order[id(a)] for a, _, _ in first
    )


def test_crowded_page_stops_at_inspection_cap(monkeypatch):
    document = soup(
        '<a href="/repeated">Repeated title</a>' * MAX_INSPECTED_ANCHORS
        + '<a href="/outside-budget">Must remain unclassified</a>'
    )
    original = link_model.canonical_url
    calls = 0

    def counted(value, source):
        nonlocal calls
        calls += 1
        return original(value, source)

    monkeypatch.setattr(link_model, "canonical_url", counted)
    aliases = {}
    chosen = candidate_anchors(document, SOURCE, aliases=aliases)
    assert calls == MAX_INSPECTED_ANCHORS
    assert len(chosen) == 1
    assert len(aliases) == MAX_INSPECTED_ANCHORS
    assert id(document.find_all("a")[-1]) not in aliases


def test_zero_budget_does_not_inspect_urls(monkeypatch):
    def unexpected(*args):
        raise AssertionError("No URLs should be inspected with an empty budget")

    monkeypatch.setattr(link_model, "canonical_url", unexpected)
    assert candidate_anchors(soup('<a href="/one">One</a>'), SOURCE, limit=0) == []


def test_content_action_is_preferred_over_verbose_footer_alias():
    document = soup(
        '<article><h2>Actual entry title</h2><a href="/entry">Read more</a></article>'
        + '<a href="/filler">Filler</a>' * MAX_CANDIDATES
        + '<footer><a href="/entry">Entry related footer shortcut</a></footer>'
    )
    rows = candidate_anchors(document, SOURCE)
    entry = next(anchor for anchor, url, _ in rows if url.endswith("/entry"))
    assert entry is document.article.a
