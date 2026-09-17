import pytest
from bs4 import BeautifulSoup

from app.tracker import native_model
from app.tracker.context_model import NUMERIC_FEATURES, ContextModel
from app.tracker.link_context import context_candidates
from app.tracker.parser import parse_page
from app.tracker.tables import table_context
from ml.evaluation import metrics


def test_negative_only_sources_do_not_dilute_collection_f1():
    rows = [dict(source_id="collection", url="/one", label=1)] + [
        dict(source_id=f"negative-{i}", url="/login", label=0) for i in range(100)
    ]
    result = metrics(rows, [True] + [False] * 100)
    assert result["macro_f1"] == 1
    assert result["negative_only_urls"] == 100
    assert result["negative_only_fp_rate"] == 0
    result = metrics(rows, [True, True] + [False] * 99)
    assert result["macro_f1"] == 1
    assert result["fp"] == 1
    assert result["negative_only_fp_rate"] == 0.01


def test_url_metrics_deduplicate_anchors_but_not_sources():
    rows = [dict(source_id="a", url="/one", label=1)] * 2
    rows += [dict(source_id="b", url="/one", label=1)]
    result = metrics(rows, [False, True, False])
    assert (result["tp"], result["fn"], result["recall"]) == (1, 1, 0.5)


@pytest.mark.parametrize("mode", ["on", "cascade"])
def test_named_table_title_column_reaches_existing_models(monkeypatch, mode):
    monkeypatch.setenv("TRACKER_LINK_MODEL", mode)
    titles = [
        "Smallest Multiple",
        "Sum Square Difference",
        "Highly Divisible Triangle",
        "Counting Sundays",
        "Factorial Digit Sum",
    ]
    html = "<main><h2>Archived Problems</h2><table><tr><th>ID</th><th>Title</th><th>Solved By</th></tr>"
    html += "".join(
        f'<tr><td>{i}</td><td><a href="problem={i}" title="Published on Friday, 30th November 2001, 06:00 pm">{title}</a><time datetime="2001-11-30"></time></td><td>1000</td></tr>'
        for i, title in enumerate(titles, 5)
    )
    html += "</table></main>"
    soup = BeautifulSoup(html, "html.parser")
    rows = list(context_candidates(soup, "https://problems.example/archives"))
    assert all(
        row["features"][NUMERIC_FEATURES.index("semantic_title")] == 1 for row in rows
    )
    scan = parse_page(html, "https://problems.example/archives")[0]
    assert {entry.title for entry in scan.entries} == set(titles)
    assert all(
        entry.published_at and entry.published_at.startswith("2001-11-30")
        for entry in scan.entries
    )


@pytest.mark.parametrize(
    "header,cell",
    [
        ("<td>Title</td>", '<a href="/a">A</a>'),
        ('<th colspan="2">Title</th>', '<a href="/a">A</a>'),
        ("<th>Author</th>", '<a href="/a">A</a>'),
        ("<th>Title</th>", '<a href="/a">A</a><a href="/b">B</a>'),
    ],
)
def test_ambiguous_table_evidence_does_not_override_features(header, cell):
    soup = BeautifulSoup(
        f"<table><tr>{header}</tr><tr><td>{cell}</td></tr></table>", "html.parser"
    )
    assert not any(value["primary"] for value in table_context(soup).values())


def test_job_application_and_identity_columns_keep_their_roles():
    soup = BeautifulSoup(
        '<table><tr><th>Company</th><th>Title</th><th>Apply</th></tr><tr><td><a href="/company">Company</a></td><td><a href="/job">Engineer</a></td><td><a href="/apply">Apply</a></td></tr></table>',
        "html.parser",
    )
    rows = list(table_context(soup).values())
    assert all(row["job"] and not row["primary"] for row in rows)
    assert sum(row["action"] for row in rows) == 1


def numeric_model():
    return ContextModel(
        dict(
            version=2,
            model_id="numeric-test",
            features=list(NUMERIC_FEATURES),
            threshold=0.5,
            vocabulary=[],
            idf=[],
            layers=[dict(weights=[[0.1] * len(NUMERIC_FEATURES)], bias=[0.2])],
        )
    )


def test_numeric_models_skip_unused_text_preprocessing(monkeypatch):
    def unexpected(*args):
        raise AssertionError("Numeric-only models must not tokenize")

    monkeypatch.setattr(native_model, "model_tokens", unexpected)
    monkeypatch.setattr("app.tracker.context_model.model_tokens", unexpected)
    model = numeric_model()
    rows = [dict(features=[0.1] * len(NUMERIC_FEATURES), tokens=["t:unused"])]
    expected = model.score(rows[0]["features"], rows[0]["tokens"])
    assert model.score_many(rows) == pytest.approx([expected], abs=1e-12)


def test_invalid_features_never_reach_native_boundary(monkeypatch):
    def unexpected(*args):
        raise AssertionError("Invalid features reached inference")

    monkeypatch.setattr(native_model, "predict_validated", unexpected)
    for features in ([float("nan")] * 84, [1.01] * 84, [0.0] * 85, [0.0] * 10):
        with pytest.raises(ValueError):
            numeric_model().score_many([dict(features=features, tokens=[])])
