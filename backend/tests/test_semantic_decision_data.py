import json

import pytest
from bs4 import BeautifulSoup

from app.tracker.context_model import NUMERIC_FEATURES
from app.tracker.link_context import context_candidates
from ml import semantic_decision_data as data

SOURCE = {
    "id": "capture",
    "url": "https://source.example/list",
    "site_family": "source.example",
    "split": "train",
}


def test_text_is_independent_of_annotation_and_features_match_current_builder():
    html = (
        "<title>Public reports</title><main><article><h2>"
        '<a href="/report/one">Coastal monitoring</a></h2>'
        "<p>A survey of coastal water.</p>"
        '<a href="/report/one">Read more</a></article>'
        '<nav><a href="/about">About</a></nav></main>'
    )
    source = {
        **SOURCE,
        "positive_selector": "article a",
        "expected_urls": ["https://source.example/report/one"],
        "ignored_urls": ["https://source.example/about"],
        "label": "secret gold label",
        "scope": "secret annotation",
    }
    changed = {
        **source,
        "positive_selector": "nav a",
        "expected_urls": ["https://source.example/about"],
        "ignored_urls": [],
        "label": "opposite secret label",
        "scope": "different annotation",
    }
    rows = data.extract_page(source, html)
    assert rows == data.extract_page(changed, html)
    soup = BeautifulSoup(html, "html.parser")
    try:
        original = list(context_candidates(soup, SOURCE["url"]))
        assert len(rows) == len(original)
        for row, candidate in zip(rows, original, strict=True):
            assert row["features"] == candidate["features"]
            assert row["tokens"] == candidate["tokens"]
            assert len(row["features"]) == len(NUMERIC_FEATURES)
            assert row["label"] == candidate["label"]
            assert row["source_id"] == "capture"
            assert "anchor" not in row and "element" not in row
            assert "expected_urls" not in row and "scope" not in row
        json.dumps(rows, allow_nan=False)
    finally:
        soup.decompose()


def test_authority_queries_and_nonvisible_content_do_not_enter_dense_text():
    html = (
        "<title>Research index</title><article>"
        "<h2>Field observations</h2>"
        '<a href="https://foreign-host.invalid/reports/ocean-study?token=QUERY_SECRET">'
        "Ocean study</a>"
        "<p>Visible coastal evidence.</p>"
        "<script>script_sentinel</script><style>style_sentinel</style>"
        "<template>template_sentinel</template><noscript>noscript_sentinel</noscript>"
        "<span hidden>hidden_sentinel</span>"
        '<span aria-hidden="true">aria_sentinel</span>'
        "<!-- comment_sentinel -->"
        "</article>"
    )
    row = data.extract_page(SOURCE, html)[0]
    assert "foreign-host.invalid" in row["url"]
    assert "reports ocean study" in row["link_text"]
    assert "Visible coastal evidence." in row["context_text"]
    for value in (
        "foreign-host",
        "QUERY_SECRET",
        "script_sentinel",
        "style_sentinel",
        "template_sentinel",
        "noscript_sentinel",
        "hidden_sentinel",
        "aria_sentinel",
        "comment_sentinel",
    ):
        assert value not in row["text"]
    assert (
        data.clean_text(
            "See https://foreign-host.invalid/reports/ocean?secret=QUERY_SECRET", 500
        )
        == "See reports ocean"
    )
    assert (
        data.path_text(
            "https://foreign-host.invalid/reports/ocean%3Ftoken=QUERY_SECRET"
        )
        == "reports ocean"
    )


def test_shared_record_is_rendered_once_and_all_input_lengths_are_bounded(monkeypatch):
    calls = []
    actual = data.record_text

    def counted(node):
        calls.append(id(node))
        return actual(node)

    monkeypatch.setattr(data, "record_text", counted)
    html = (
        "<title>"
        + "Page title " * 200
        + "</title><article><h2>"
        + "Section heading " * 200
        + "</h2>"
        + '<a href="/one">'
        + "Link label " * 200
        + "</a>"
        + "<p>"
        + "Record content " * 10000
        + "</p>"
        + '<a href="/two">Second link</a></article>'
    )
    rows = data.extract_page(SOURCE, html)
    assert len(rows) == 2 and len(calls) == 1
    assert rows[0]["record_id"] == rows[1]["record_id"]
    for row in rows:
        assert len(row["text"]) <= data.MAX_TEXT
        assert len(row["link_text"]) <= data.MAX_LINK_TEXT
        assert len(row["context_text"]) <= data.MAX_CONTEXT_TEXT
        assert row["text"].count("Record content") < 60


def test_page_body_is_not_repeated_for_each_link_and_accessible_labels_work():
    html = (
        "<title>Index</title><main><p>unrelated_page_body_sentinel</p>"
        '<a href="/one">One title</a><a href="/two" aria-label="Two title"></a>'
        '<a href="/three"><img alt="Three title"></a></main>'
    )
    rows = data.extract_page(SOURCE, html)
    assert len(rows) == 3
    assert all("unrelated_page_body_sentinel" not in row["text"] for row in rows)
    assert "Two title" in rows[1]["link_text"]
    assert "Three title" in rows[2]["link_text"]


def test_visible_walk_bounds_nodes_and_strings_and_rejects_crowded_container():
    soup = BeautifulSoup(
        "<div>" + "<span>x</span>" * 1000 + "<span>too_late_sentinel</span></div>",
        "html.parser",
    )
    try:
        text = data.visible_text(soup.div, 10000)
        assert "too_late_sentinel" not in text
        assert len(text.split()) <= data.MAX_STRINGS
    finally:
        soup.decompose()
    soup = BeautifulSoup(
        "<div>" + '<a href="/one">Link</a>' * 17 + "</div>", "html.parser"
    )
    try:
        assert data.record_text(soup.div) == ""
    finally:
        soup.decompose()


def test_neutral_filtering_and_url_probability_grouping_preserve_source_scope():
    rows = [
        {"source_id": "one", "url": "/same", "label": 1},
        {"source_id": "one", "url": "/same", "label": 1},
        {"source_id": "two", "url": "/same", "label": 0},
        {"source_id": "two", "url": "/neutral", "label": 0},
    ]
    assert data.without_neutral(rows, {"/neutral"}) == rows[:3]
    unique, probabilities = data.group_url_scores(rows[:3], [0.2, 0.8, 0.9])
    assert len(unique) == 2 and probabilities == [0.8, 0.9]
    assert unique[0]["label"] == 1 and unique[1]["label"] == 0
    with pytest.raises(ValueError, match="Conflicting labels"):
        data.group_url_scores([rows[0], {**rows[0], "label": 0}], [0.2, 0.8])
    with pytest.raises(ValueError):
        data.group_url_scores(rows, [0.1])


def test_empty_capture_and_missing_identity():
    assert data.extract_page(SOURCE, "<main>Nothing to list</main>") == []
    with pytest.raises(ValueError, match="source_id"):
        data.extract_page({"url": SOURCE["url"]}, "<a href='/one'>One</a>")
