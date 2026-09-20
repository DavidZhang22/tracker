from bs4 import BeautifulSoup

from ml.evaluate_extraction_audit import parser_identity, region_evidence, scope
from ml.evaluation import metrics


def test_audit_keeps_primary_urls_when_they_also_appear_as_alternatives():
    soup = BeautifulSoup(
        '<main><a href="/entry/1">Primary</a></main><aside><a href="/entry/1">Again</a><a href="/transcript/1">Transcript</a></aside>',
        "html.parser",
    )
    expected, ignored = scope(
        soup,
        dict(
            url="https://example.com/",
            positive_selector="main a",
            ignore_selector="aside a",
        ),
    )
    assert expected == {"https://example.com/entry/1"}
    assert ignored == {"https://example.com/transcript/1"}


def test_audit_groups_duplicate_renderings_instead_of_inflating_recall():
    rows = [dict(source_id="s", url="https://example.com/1", label=1)] * 2
    measured = metrics(rows, [False, True])
    assert measured["tp"] == 1 and measured["fn"] == 0


def test_audit_same_host_http_promotion_does_not_make_false_positives():
    assert (
        parser_identity("http://example.com/chapter/1", "https://example.com/")
        == "https://example.com/chapter/1"
    )
    assert (
        parser_identity("http://other.com/chapter/1", "https://example.com/")
        == "http://other.com/chapter/1"
    )


def test_record_labels_require_own_metadata_and_reject_neighbor_contamination():
    soup = BeautifulSoup(
        '<ul><li><a href="/1">One</a><time>September 1</time></li><li><a href="/2">Two</a><time>September 2</time></li></ul>',
        "html.parser",
    )
    first, second = soup.select("li")
    expected = {"https://example.com/1", "https://example.com/2"}
    own = "https://example.com/1"
    assert region_evidence(
        (first,), own, expected, "https://example.com/", first.time
    ) == dict(metadata=True, contamination=False, other_targets=0)
    assert (
        region_evidence((first.a,), own, expected, "https://example.com/", first.time)[
            "metadata"
        ]
        is False
    )
    both = region_evidence(
        (first, second), own, expected, "https://example.com/", first.time
    )
    assert both["metadata"] is True and both["contamination"] is True
