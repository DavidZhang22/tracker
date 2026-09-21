import pytest
from bs4 import BeautifulSoup

from app.tracker.context_model import NUMERIC_FEATURES
from app.tracker.link_context import context_candidates
from app.tracker.tables import table_context

SOURCE = "https://catalog.example/courses"


def analyzed(markup):
    soup = BeautifulSoup(markup, "html.parser")
    return [
        (row, dict(zip(NUMERIC_FEATURES, row["features"], strict=True)))
        for row in context_candidates(soup, SOURCE)
    ]


def catalog_row(topic, code, title_href):
    return (
        '<tr><td class="entry-title"><a href="/topics/'
        + topic
        + '">'
        + topic
        + "</a></td>"
        '<td><a href="' + title_href + '#overview">' + code + "</a></td>"
        '<td class="entry-title"><a href="'
        + title_href
        + '">An informative course title</a></td></tr>'
    )


def test_verified_title_column_overrides_metadata_class_and_preserves_aliases():
    rows = analyzed(
        "<table><tr><th>Topic</th><th>Number</th><th>Course Title</th></tr>"
        + catalog_row("biology", "BIO101", "/courses/bio101")
        + catalog_row("chemistry", "CHEM201", "/courses/chem201")
        + "</table>"
    )
    assert len(rows) == 6
    for row, features in rows:
        primary = "/courses/" in row["url"]
        assert features["semantic_title"] == primary
        assert features["record_primary_url"] == primary
        assert features["record_other_primary"] == (not primary)
        assert len(row["features"]) == 84


def test_primary_target_is_scoped_to_its_own_row():
    rows = analyzed(
        "<table><tr><th>Related</th><th>Title</th></tr>"
        '<tr><td class="entry-title"><a href="/two">Related second entry</a></td><td><a href="/one">First entry</a></td></tr>'
        '<tr><td class="entry-title"><a href="/one">Related first entry</a></td><td><a href="/two">Second entry</a></td></tr></table>'
    )
    assert [f["record_primary_url"] for _, f in rows] == [0, 1, 0, 1]
    assert [f["record_other_primary"] for _, f in rows] == [1, 0, 1, 0]


@pytest.mark.parametrize(
    "headers,primary_cell,row_attributes",
    [
        ("<th>Name</th><th>Title</th>", '<a href="/one">One</a>', ""),
        ("<td>Topic</td><td>Title</td>", '<a href="/one">One</a>', ""),
        (
            "<th>Topic</th><th>Title</th>",
            '<a href="/one">One</a><a href="/two">Two</a>',
            "",
        ),
        ("<th>Topic</th><th>Title</th>", "Unlinked title", ""),
        ("<th>Topic</th><th>Title</th>", '<a href="#note">Note</a>', ""),
        (
            "<th>Topic</th><th>Title</th>",
            '<a href="javascript:alert(1)">Unsafe</a>',
            "",
        ),
        ("<th>Topic</th><th>Title</th>", '<a href="/one">One</a>', ' colspan="2"'),
        ("<th>Topic</th><th>Title</th>", '<a href="/one">One</a>', ' rowspan="2"'),
    ],
)
def test_ambiguous_or_unverifiable_rows_keep_existing_features(
    headers, primary_cell, row_attributes
):
    rows = analyzed(
        "<table><tr>"
        + headers
        + '</tr><tr><td class="entry-title"'
        + row_attributes
        + '><a href="/topic">A topic</a></td><td>'
        + primary_cell
        + "</td></tr></table>"
    )
    _, topic = next((r, f) for r, f in rows if r["url"].endswith("/topic"))
    assert topic["semantic_title"] == 1
    assert topic["record_primary_url"] == 0
    assert topic["record_other_primary"] == 0


def test_primary_cell_inspection_is_bounded_and_extra_targets_are_ambiguous():
    rows = analyzed(
        '<table><tr><th>Topic</th><th>Title</th></tr><tr><td class="entry-title"><a href="/topic">A topic</a></td><td>'
        + '<a href="/one">One</a>' * 20
        + '<a href="/two">A different target beyond the inspection allowance</a></td></tr></table>'
    )
    _, topic = next((r, f) for r, f in rows if r["url"].endswith("/topic"))
    assert topic["semantic_title"] == 1
    assert topic["record_other_primary"] == 0


def test_job_application_columns_keep_their_existing_routing():
    markup = (
        "<table><tr><th>Company</th><th>Job Title</th><th>Application</th></tr>"
        '<tr><td class="entry-title"><a href="/employer">An employer</a></td><td>Engineer</td><td><a href="https://jobs.example/apply/42">Apply</a></td></tr></table>'
    )
    rows = analyzed(markup)
    assert all(features["job_table"] == 1 for _, features in rows)
    assert rows[0][1]["semantic_title"] == 1
    assert all(features["record_other_primary"] == 0 for _, features in rows)
    soup = BeautifulSoup(markup, "html.parser")
    context = table_context(soup)
    action = soup.find("a", href="https://jobs.example/apply/42")
    assert context[id(action)]["action"] is True
    assert context[id(action)]["primary_href"] is None
    assert context[id(action)]["title"] == "An employer · Engineer"


def test_nested_table_targets_do_not_become_outer_primary_targets():
    rows = analyzed(
        '<table><tr><th>Related</th><th>Title</th></tr><tr><td class="entry-title"><a href="/topic">Topic</a></td><td>'
        '<table><tr><th>Title</th></tr><tr><td><a href="/nested">Inner record</a></td></tr></table>'
        "</td></tr></table>"
    )
    _, outer = next((r, f) for r, f in rows if r["url"].endswith("/topic"))
    _, inner = next((r, f) for r, f in rows if r["url"].endswith("/nested"))
    assert outer["semantic_title"] == 1
    assert outer["record_other_primary"] == 0
    assert inner["record_primary_url"] == 1
