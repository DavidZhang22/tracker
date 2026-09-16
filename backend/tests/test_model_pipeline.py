import copy
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.tracker import context_model, native_model
from app.tracker.cascade_model import CascadeModel, decision_score
from app.tracker.context_model import NUMERIC_FEATURES, ContextModel
from app.tracker.dates import link_date
from app.tracker.link_context import model_tokens
from app.tracker.page_context import PageContext


def linear(bias=0):
    return dict(
        version=2,
        model_id="test",
        features=list(NUMERIC_FEATURES),
        threshold=0.6,
        vocabulary=["t:chapter", "gt:^cha"],
        idf=[1.0, 2.0],
        token_mode="subwords-v1",
        layers=[dict(weights=[[0.1] * (len(NUMERIC_FEATURES) + 2)], bias=[bias])],
    )


def test_batch_matches_python_on_frozen_routed_models():
    root = Path(__file__).resolve().parents[1]
    rows = [
        json.loads(line)
        for line in (root / "ml/datasets/v3-dataset.jsonl")
        .read_text(encoding="utf8")
        .splitlines()
    ][::17]
    model = context_model.load_context_model()
    expected = [model.score(r["features"], r["tokens"]) for r in rows]
    actual = model.score_many(rows)
    assert actual == pytest.approx(expected, abs=1e-12)
    assert [p >= model.upper for p in actual] == [p >= model.upper for p in expected]


def test_dense_batch_calibration_subwords_and_python_fallback(monkeypatch):
    payload = linear()
    payload["calibration"] = [0.8, -0.3]
    payload["layers"] = [
        dict(weights=[[0.1] * 86] * 4, bias=[0.2] * 4),
        dict(weights=[[0.3] * 4] * 3, bias=[-0.1] * 3),
        dict(weights=[[0.2] * 3], bias=[0.1]),
    ]
    model = ContextModel(payload)
    rows = [
        dict(features=[i / 300] * 84, tokens=["t:chapter", "t:novel"])
        for i in range(300)
    ]
    expected = [model.score(r["features"], r["tokens"]) for r in rows]
    assert model.score_many(rows) == pytest.approx(expected, abs=1e-12)
    monkeypatch.setattr(native_model, "kernel", lambda: None)
    assert model.score_many(rows) == expected
    assert model.score_many([]) == []


@pytest.mark.parametrize(
    "features", [[0.1], [0.0] * 85, [float("nan")] * 84, [float("inf")] * 84, [-1] * 84]
)
def test_invalid_vectors_rejected_before_native_code(features):
    with pytest.raises(ValueError):
        ContextModel(linear()).score_many([dict(features=features, tokens=[])])


@pytest.mark.parametrize(
    "calibration", [[float("nan"), 0], [1, float("inf")], [0, 0], [1], [1, 200]]
)
def test_invalid_calibration_rejected(calibration):
    payload = linear()
    payload["calibration"] = calibration
    with pytest.raises(ValueError):
        ContextModel(payload)


def test_cascade_only_escalates_uncertain_rows(monkeypatch):
    model = CascadeModel(
        dict(
            version=1, model_id="test", light=linear(), deep=linear(2), gate=[0.1, 0.9]
        )
    )
    rows = [dict(features=[0.0] * 84, tokens=[]) for _ in range(3)]
    monkeypatch.setattr(model.light, "score_many", lambda batch: [0.01, 0.5, 0.99])
    called = []

    def deep(batch):
        called.extend(batch)
        return [0.7]

    monkeypatch.setattr(model.deep, "score_many", deep)
    scores = model.score_many(rows)
    assert called == [rows[1]]
    assert [p >= 0.5 for p in scores] == [False, True, True]
    assert decision_score(0.6, 0.6) == 0.5


def test_optional_cascade_failure_uses_production_and_changes_cache_identity(
    tmp_path, monkeypatch
):
    from app.tracker import cascade_model
    from app.tracker.link_model import model_cache_tag

    monkeypatch.setattr(cascade_model, "MODEL_PATH", tmp_path / "candidate.json")
    cascade_model.load_cascade_model.cache_clear()
    monkeypatch.setenv("TRACKER_LINK_MODEL", "cascade")
    try:
        baseline = context_model.active_context_model()
        assert baseline is context_model.load_context_model()
        before = model_cache_tag()
        cascade_model.MODEL_PATH.write_text(
            json.dumps(
                dict(
                    version=1,
                    model_id="candidate-test",
                    light=linear(),
                    deep=linear(),
                    gate=[0.1, 0.9],
                )
            )
        )
        cascade_model.load_cascade_model.cache_clear()
        assert isinstance(context_model.active_context_model(), CascadeModel)
        assert model_cache_tag() != before
    finally:
        cascade_model.load_cascade_model.cache_clear()


def test_rescue_cascade_cannot_veto_light_acceptance(monkeypatch):
    model = CascadeModel(
        dict(
            version=1,
            model_id="rescue",
            policy="rescue",
            light=linear(),
            deep=linear(),
            gate=[0.1, 0.9],
        )
    )
    rows = [dict(features=[0.0] * 84, tokens=[]) for _ in range(3)]
    monkeypatch.setattr(model.light, "score_many", lambda batch: [0.7, 0.5, 0.4])
    seen = []

    def deep(batch):
        seen.extend(batch)
        return [0.01, 0.9]

    monkeypatch.setattr(model.deep, "score_many", deep)
    assert [score >= 0.5 for score in model.score_many(rows)] == [True, False, True]
    assert len(seen) == 2


def test_shared_dates_never_cross_records_or_leak_mutations():
    soup = BeautifulSoup(
        '<main><div class="card"><h2><a href="/a">A</a></h2><time datetime="2026-09-01"></time></div><div class="card"><h2><a href="/b">B</a></h2></div><div class="card"><h2><a href="/c">C</a></h2><time datetime="2026-09-03"></time></div></main>',
        "html.parser",
    )
    context = PageContext()
    anchors = soup.find_all("a")
    assert link_date(anchors[0], context)["published_at"].startswith("2026-09-01")
    assert link_date(anchors[1], context) == {}
    assert link_date(anchors[2], context)["published_at"].startswith("2026-09-03")
    record = soup.select_one(".card")
    first = context.node_dates(record)
    original = copy.deepcopy(first)
    first["date_kind"] = "wrong"
    assert context.node_dates(record) == original
    assert PageContext().dates == {}


def test_heading_cache_matches_css_boundaries():
    soup = BeautifulSoup(
        '<div class="entry-title"><span><a href="/one">One</a></span></div><section><h2><span><a href="/two">Two</a></span></h2><a href="/other">Other</a><h3><a href="/two">Again</a><a href="#skip">Jump</a></h3></section>',
        "html.parser",
    )
    context = PageContext()
    for node in soup.find_all(True):
        expected = {
            a["href"]
            for a in node.select(
                "h1 a[href],h2 a[href],h3 a[href],.entry-title a[href]"
            )
            if not a["href"].startswith("#")
        }
        assert context.heading_count(node) == min(len(expected), 2), str(node)


def test_subwords_are_bounded_and_do_not_add_query_values():
    values = model_tokens(["t:chapter", "u:num", "q:token"], "subwords-v1")
    assert "gt:^cha" in values
    assert "gu:^num" not in values


def test_chapter_numbers_that_look_like_years_are_not_archive_navigation():
    from app.tracker.parser import parse_page

    numbers = range(1899, 2101)
    html = (
        "<main>"
        + "".join(
            f'<article><h2><a href="/entry/{i}">Chapter {i}</a></h2><time datetime="2026-09-01"></time></article>'
            for i in numbers
        )
        + "</main>"
    )
    entries = parse_page(html, "https://stories.example/")[0].entries
    assert {entry.number for entry in entries} == set(numbers)
    features = dict.fromkeys(NUMERIC_FEATURES, 0.0)
    features["date_index"] = 1
    assert context_model.nuisance_context(list(features.values()))
    features["sequence_label"] = 1
    assert not context_model.nuisance_context(list(features.values()))
