"""Advanced arXiv searches preserve supported scope and report omitted filters."""

import itertools
import re
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest

from app.tracker.arxiv import scan_arxiv
from app.tracker.arxiv_urls import translate
from app.tracker.cache import FetchCache
from app.tracker.urls import DiscoveryError, SafeFetcher
from tests.test_arxiv import Fetcher, feed

ADVANCED = "https://arxiv.org/search/advanced?"


def advanced(*rows, **controls):
    params = {"advanced": "", "size": "200", "date-filter_by": "all_dates"}
    for index, row in enumerate(rows):
        operator, text, field = row
        params.update(
            {
                f"terms-{index}-operator": operator,
                f"terms-{index}-term": text,
                f"terms-{index}-field": field,
            }
        )
    params.update(controls)
    return ADVANCED + urlencode(params)


def emitted_truth(query, present):
    """Evaluate simple test terms independently of the production query AST."""
    tokens = iter(re.findall(r"\(|\)|[^\s()]+", query))
    current = next(tokens, None)

    def consume():
        nonlocal current
        previous, current = current, next(tokens, None)
        return previous

    def primary():
        if current == "(":
            consume()
            value = parse_or()
            assert consume() == ")"
            return value
        assert current and current not in {"AND", "ANDNOT", "OR", ")"}
        return consume() in present

    def parse_not():
        value = primary()
        while current == "ANDNOT":
            consume()
            other = primary()
            value = value and not other
        return value

    def parse_and():
        value = parse_not()
        while current == "AND":
            consume()
            other = parse_not()
            value = value and other
        return value

    def parse_or():
        value = parse_and()
        while current == "OR":
            consume()
            other = parse_and()
            value = value or other
        return value

    value = parse_or()
    assert current is None
    return value


def notes(query):
    return " ".join(query.notes).lower()


def test_users_options_pricing_search_preserves_only_selected_subjects():
    query = translate(
        advanced(
            ("AND", "Options Pricing", "title"),
            **{
                "classification-computer_science": "y",
                "classification-economics": "y",
                "classification-mathematics": "y",
                "classification-physics_archives": "all",
                "classification-include_cross_list": "include",
                "date-year": "",
                "date-from_date": "",
                "date-to_date": "",
                "date-date_type": "submitted_date",
                "abstracts": "show",
                "order": "",
            },
        )
    )
    search = query.params["search_query"]
    terms = {"ti:Options", "ti:Pricing"}
    subjects = {"cat:cs.*", "cat:econ.*", "cat:math.*"}
    assert all(term in search for term in terms | subjects)
    assert "physics" not in search and "astro-ph" not in search
    assert not emitted_truth(search, terms)
    assert not emitted_truth(search, {"ti:Options"} | subjects)
    assert all(emitted_truth(search, terms | {subject}) for subject in subjects)
    assert query.params["max_results"] == 200
    assert not query.notes


def test_advanced_boolean_rows_use_not_then_and_then_or_precedence():
    query = translate(
        advanced(
            ("OR", "alpha", "title"),
            ("OR", "beta", "title"),
            ("AND", "gamma", "title"),
            ("NOT", "delta", "title"),
        )
    )
    for a, b, c, d in itertools.product((False, True), repeat=4):
        present = {
            "ti:" + term
            for term, value in zip(
                ("alpha", "beta", "gamma", "delta"), (a, b, c, d), strict=True
            )
            if value
        }
        assert emitted_truth(query.params["search_query"], present) == (
            a or (b and c and not d)
        )


@pytest.mark.parametrize("operator", ["AND", "OR", "NOT"])
def test_first_row_operator_is_a_form_placeholder(operator):
    query = translate(advanced((operator, "alpha", "title")))
    assert query.params["search_query"] == "ti:alpha"
    assert not query.notes


def test_unsupported_field_and_extra_filter_are_removed_and_named():
    query = translate(
        advanced(
            ("AND", "alpha", "title"),
            ("AND", "10.1234/example", "doi"),
            **{"custom-availability": "open"},
        )
    )
    assert query.params["search_query"] == "ti:alpha"
    assert "doi" in notes(query)
    assert "custom availability" in notes(query)
    assert "10.1234" not in query.params["search_query"]


def test_omitting_positive_operand_never_promotes_excluded_term():
    query = translate(
        advanced(
            ("AND", "alpha", "title"),
            ("OR", "10.1234/example", "doi"),
            ("NOT", "beta", "title"),
        )
    )
    assert query.params["search_query"] == "ti:alpha"
    assert "doi" in notes(query)


@pytest.mark.parametrize(
    "rows",
    [
        (("AND", "10.1234/example", "doi"),),
        (("AND", "10.1234/example", "doi"), ("NOT", "beta", "title")),
    ],
)
def test_unsupported_only_search_cannot_become_match_all_or_positive_exclusion(rows):
    with pytest.raises(DiscoveryError):
        translate(advanced(*rows))


def test_supported_subject_survives_omitted_search_field():
    query = translate(
        advanced(
            ("AND", "10.1234/example", "doi"),
            **{"classification-computer_science": "y"},
        )
    )
    assert query.params["search_query"] == "cat:cs.*"
    assert "doi" in notes(query)


def test_excluding_cross_lists_is_omitted_with_visible_notice():
    query = translate(
        advanced(
            ("AND", "alpha", "title"),
            **{
                "classification-computer_science": "y",
                "classification-include_cross_list": "exclude",
            },
        )
    )
    assert "cat:cs.*" in query.params["search_query"]
    assert "cross" in notes(query)
    assert "primary-category-only" in notes(query)


def test_inactive_date_controls_are_not_applied_or_reported_as_omissions():
    query = translate(
        advanced(
            ("AND", "alpha", "title"),
            **{
                "date-filter_by": "all_dates",
                "date-year": "2022",
                "date-from_date": "2021-01-01",
                "date-to_date": "2023-01-01",
                "date-date_type": "announced_date_first",
            },
        )
    )
    assert query.params["search_query"] == "ti:alpha"
    assert not query.notes


@pytest.mark.parametrize(
    "controls, expected",
    [
        (
            {"date-filter_by": "specific_year", "date-year": "2022"},
            "submittedDate:[202201010500 TO 202301010500]",
        ),
        (
            {
                "date-filter_by": "date_range",
                "date-from_date": "2022-02-01",
                "date-to_date": "2022-03-03",
            },
            "submittedDate:[202202010500 TO 202203030500]",
        ),
    ],
)
def test_original_submission_date_filters_are_exact(controls, expected):
    query = translate(
        advanced(
            ("AND", "alpha", "title"),
            **controls,
            **{"date-date_type": "submitted_date_first"},
        )
    )
    assert expected in query.params["search_query"]
    assert not query.notes


@pytest.mark.parametrize(
    "date_type", ["submitted_date", "announced_date_first", "announced_date_last"]
)
def test_unavailable_update_or_announcement_date_filters_are_omitted(date_type):
    query = translate(
        advanced(
            ("AND", "alpha", "title"),
            **{
                "date-filter_by": "specific_year",
                "date-year": "2022",
                "date-date_type": date_type,
            },
        )
    )
    assert query.params["search_query"] == "ti:alpha"
    assert "date" in notes(query)
    assert query.notes


def test_latest_submission_sort_maps_to_last_update():
    query = translate(advanced(("AND", "alpha", "title"), order="-submitted_date"))
    assert query.params["sortBy"] == "lastUpdatedDate"
    assert query.params["sortOrder"] == "descending"


def test_unknown_sort_is_omitted_and_named():
    query = translate(advanced(("AND", "alpha", "title"), order="-citation_count"))
    assert "sortBy" not in query.params and "sortOrder" not in query.params
    assert "sort order" in notes(query) and "relevance" in notes(query)


def test_basic_search_can_omit_unknown_filters_without_losing_terms():
    query = translate(
        "https://arxiv.org/search/?query=alpha&searchtype=title&date=2022"
    )
    assert query.params["search_query"] == "ti:alpha"
    assert "date" in notes(query)


@pytest.mark.parametrize(
    "url",
    [
        advanced(("AND", '"unterminated', "title")),
        advanced(("AND", "alpha OR", "title")),
        advanced(("AND", "(alpha", "title")),
        advanced(("AND", "alpha", "title")) + "&terms-0-term=beta",
        advanced(
            ("AND", "alpha", "title"),
            **{
                "date-filter_by": "date_range",
                "date-from_date": "2022-02-30",
                "date-to_date": "2022-03-01",
                "date-date_type": "submitted_date_first",
            },
        ),
        advanced(
            ("AND", "alpha", "title"),
            **{
                "date-filter_by": "date_range",
                "date-from_date": "2023-01-01",
                "date-to_date": "2022-01-01",
                "date-date_type": "submitted_date_first",
            },
        ),
    ],
)
def test_malformed_values_still_fail_instead_of_being_silently_removed(url):
    with pytest.raises(DiscoveryError):
        translate(url)


async def test_preview_scan_includes_filter_omission_notes():
    source = advanced(
        ("AND", "alpha", "title"),
        ("AND", "10.1234/example", "doi"),
        **{"classification-include_cross_list": "exclude"},
    )
    query = translate(source)
    fetcher = Fetcher(lambda params: feed())
    result = await scan_arxiv(fetcher, source, 2)
    assert query.notes and set(query.notes) <= set(result.warnings)
    assert len(result.entries) == 1
    assert len(fetcher.calls) == 1
    assert result.url == source


async def test_api_body_cache_reuse_keeps_per_source_omission_notices(monkeypatch):
    requests = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(200, text=feed())

    monkeypatch.setattr(
        "app.tracker.urls.public_addresses", AsyncMock(return_value=["93.184.216.34"])
    )
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    fetcher = SafeFetcher(FetchCache(), interval=0)
    plain = advanced(("AND", "alpha", "title"))
    unsupported = advanced(("AND", "alpha", "title"), **{"unavailable-filter": "x"})
    plain_result = await scan_arxiv(fetcher, plain, 2)
    omitted_result = await scan_arxiv(fetcher, unsupported, 2)
    plain_again = await scan_arxiv(fetcher, plain, 2)
    assert len(requests) == 1
    assert not plain_result.warnings and not plain_again.warnings
    assert "unavailable filter" in " ".join(omitted_result.warnings).lower()
    assert plain_result.entries == omitted_result.entries


async def test_original_date_range_filters_exclusive_upper_boundary_locally():
    source = advanced(
        ("AND", "alpha", "title"),
        **{
            "date-filter_by": "date_range",
            "date-from_date": "2022-02-01",
            "date-to_date": "2022-03-03",
            "date-date_type": "submitted_date_first",
        },
    )
    identifiers = ("2202.00001v1", "2203.00002v1", "2203.00003v1")
    body = feed(identifiers)
    for published in (
        "2022-02-01T05:00:00Z",
        "2022-03-03T04:59:59Z",
        "2022-03-03T05:00:00Z",
    ):
        body = body.replace(
            "<published>2022-03-29T12:00:00Z</published>",
            f"<published>{published}</published>",
            1,
        )
    fetcher = Fetcher(lambda params: body)
    result = await scan_arxiv(fetcher, source, 2)
    assert [entry.source_id for entry in result.entries] == [
        "arxiv:2202.00001",
        "arxiv:2203.00002",
    ]
    assert result.coverage == "complete"
    assert not any("invalid" in warning for warning in result.warnings)
    assert len(fetcher.calls) == 1


@pytest.mark.parametrize("identity", ["2203.15556", "hep-th/9901001"])
@pytest.mark.parametrize("version", ["", "v2"])
def test_paper_id_search_keeps_identity_and_reports_removed_version(identity, version):
    query = translate(advanced(("AND", identity + version, "paper_id")))
    assert query.params["search_query"] == "id:" + identity
    if version:
        assert "paper version restriction" in notes(query)
        assert "latest version" in notes(query)
    else:
        assert not query.notes


def test_paper_id_validation_does_not_allow_embedded_query_operators():
    query = translate(
        advanced(
            ("AND", "alpha", "title"),
            ("AND", "2203.15556 OR all:*", "paper_id"),
        )
    )
    assert query.params["search_query"] == "ti:alpha"
    assert "paper id" in notes(query)
    assert "one complete paper id" in notes(query)


async def test_original_date_filter_never_substitutes_update_date_for_missing_publication():
    source = advanced(
        ("AND", "alpha", "title"),
        **{
            "date-filter_by": "specific_year",
            "date-year": "2022",
            "date-date_type": "submitted_date_first",
        },
    )
    body = feed().replace("<published>2022-03-29T12:00:00Z</published>", "")
    body = body.replace(
        "<updated>2023-01-01T12:00:00Z</updated>",
        "<updated>2022-03-29T12:00:00Z</updated>",
    )
    fetcher = Fetcher(lambda params: body)
    result = await scan_arxiv(fetcher, source, 2)
    assert not result.entries
    assert result.coverage == "partial"
    assert result.expected_count is None
    assert any("original submission date" in warning for warning in result.warnings)
    assert len(fetcher.calls) == 1


@pytest.mark.parametrize(
    "sort, retained, notice",
    [
        (
            {"sortBy": "citations", "sortOrder": "ascending"},
            {},
            "sort order",
        ),
        (
            {"sortBy": "lastUpdatedDate", "sortOrder": "newest"},
            {"sortBy": "lastUpdatedDate"},
            "sort direction",
        ),
    ],
)
def test_direct_api_omits_unsupported_parameters_and_sort_without_rewriting_query(
    sort, retained, notice
):
    search = '(ti:"Options Pricing" OR abs:volatility) ANDNOT au:Smith'
    params = {
        "search_query": search,
        "start": "10",
        "max_results": "50",
        "license": "open",
        **sort,
    }
    query = translate("https://export.arxiv.org/api/query?" + urlencode(params))
    assert query.params == {
        "search_query": search,
        "start": 10,
        "max_results": 50,
        **retained,
    }
    assert "license" in notes(query)
    assert notice in notes(query)
