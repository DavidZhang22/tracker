"""Conservative cross-site neural refinement and bounded candidate decisions."""

import copy

import pytest

from app.tracker.cascade_model import CascadeModel, structural_chrome
from app.tracker.context_model import NUMERIC_FEATURES


def constant_model(identity="tiny"):
    return dict(
        version=2,
        model_id=identity,
        features=list(NUMERIC_FEATURES),
        threshold=0.5,
        reject_threshold=0.08,
        vocabulary=[],
        idf=[],
        layers=[dict(weights=[[0.0] * len(NUMERIC_FEATURES)], bias=[0.0])],
    )


def refinement_payload():
    expert = constant_model("expert")
    expert.update(threshold=0.95, reject_threshold=0.05)
    return dict(
        version=1,
        model_id="test-refinement",
        light=constant_model("light"),
        deep=constant_model("deep"),
        gate=[0.2, 0.8],
        policy="rescue",
        refinement=expert,
        refinement_policy={"mode": "structural", "title_override": True},
    )


def feature_row(**values):
    features = [0.0] * len(NUMERIC_FEATURES)
    for key, value in values.items():
        features[NUMERIC_FEATURES.index(key)] = value
    return {"features": features, "tokens": []}


def test_refinement_only_corrects_the_intended_disagreement_groups():
    model = CascadeModel(refinement_payload())
    rows = [feature_row(position=i / 10) for i in range(6)]
    rows[1]["features"][NUMERIC_FEATURES.index("in_nav")] = 1.0
    rows[3]["features"][NUMERIC_FEATURES.index("in_nav")] = 1.0
    rows[4]["features"][NUMERIC_FEATURES.index("in_nav")] = 1.0
    rows[4]["features"][NUMERIC_FEATURES.index("semantic_title")] = 1.0
    seen = []

    def score(selected):
        indices = [
            round(r["features"][NUMERIC_FEATURES.index("position")] * 10)
            for r in selected
        ]
        seen.extend(indices)
        return [{0: 0.99, 3: 0.01, 4: 0.99, 5: 0.5}[i] for i in indices]

    model.refinement.score_many = score
    baseline = [0.1, 0.1, 0.9, 0.9, 0.1, 0.1]
    result = model.refine(rows, baseline)
    assert seen == [0, 3, 4, 5]
    assert result[0] >= 0.5 and result[4] >= 0.5
    assert result[3] < 0.5
    assert result[1] == 0.1 and result[2] == 0.9 and result[5] == 0.1
    assert baseline == [0.1, 0.1, 0.9, 0.9, 0.1, 0.1]


def test_paragraph_and_multilink_rows_are_not_automatically_chrome():
    assert not structural_chrome(
        feature_row(
            paragraph_words=1.0, inline_text_fraction=0.1, in_tr=1.0, record_links=1.0
        )["features"]
    )
    assert structural_chrome(feature_row(rel_author=1.0)["features"])
    assert not structural_chrome(
        feature_row(in_nav=1.0, semantic_title=1.0)["features"]
    )


@pytest.mark.parametrize(
    "policy",
    [
        None,
        {"mode": "unknown"},
        {"mode": "structural", "title_override": "yes"},
        {"mode": "structural", "title_override": True, "execute": "ignored"},
        {"mode": "disagreement", "ceiling": float("nan")},
        {"mode": "disagreement", "ceiling": 1.1},
    ],
)
def test_refinement_policy_is_strictly_validated(policy):
    payload = refinement_payload()
    payload["refinement_policy"] = policy
    with pytest.raises(ValueError):
        CascadeModel(payload)


def test_refinement_cannot_embed_another_routed_model():
    payload = refinement_payload()
    payload["refinement"]["fallback"] = copy.deepcopy(payload["light"])
    payload["refinement"]["gate_feature"] = "job_table"
    with pytest.raises(ValueError, match="routing"):
        CascadeModel(payload)


def test_unscored_duplicate_aliases_cannot_bypass_crowded_page_decisions(monkeypatch):
    from bs4 import BeautifulSoup

    from app.tracker.context_model import classify_context

    class Model:
        upper, lower = 0.5, 0.08

        def score_many(self, rows):
            return [float("/records/" in row["url"]) for row in rows]

    monkeypatch.setattr(
        "app.tracker.context_model.active_context_model", lambda: Model()
    )
    html = (
        "<nav>"
        + '<a href="/privacy">Privacy</a>' * 4001
        + '</nav><h2><a href="/records/42">Late report</a></h2>'
    )
    soup = BeautifulSoup(html, "html.parser")
    scores, _, rejected, _ = classify_context(soup, "https://reports.example/catalog")
    assert len(scores) == 2
    assert scores[id(soup.h2.a)] == 1
    assert all(id(anchor) in rejected for anchor in soup.nav.find_all("a"))


def group_model(*, cold=False):
    payload = refinement_payload()
    payload["refinement"].update(threshold=0.7, reject_threshold=0.1)
    payload["refinement_policy"] = dict(
        mode="group",
        title_override=True,
        seed_threshold=0.65,
        min_seed_urls=2,
        cold_group=cold,
        cold_threshold=0.995,
        min_cold_urls=3,
        key_version=1,
    )
    return CascadeModel(payload)


def group_rows(count, source="https://listing.example/index"):
    rows = [feature_row(same_host=1, semantic_title=1) for _ in range(count)]
    for i, row in enumerate(rows):
        row.update(
            url=f"https://listing.example/records/{i}",
            tokens=["c:record-title"],
            source=source,
        )
    return rows


def test_group_rescue_needs_distinct_urls_in_same_page_and_record_role():
    model = group_model()
    model.refinement.score_many = lambda rows: [0.99] * len(rows)
    rows = group_rows(3)
    assert model.refine(rows, [0.8, 0.8, 0.1])[2] >= 0.5
    rows[1]["url"] = rows[0]["url"]
    assert model.refine(rows, [0.8, 0.8, 0.1])[2] == 0.1
    rows = group_rows(3)
    rows[1]["source"] = "https://another.example/index"
    assert model.refine(rows, [0.8, 0.8, 0.1])[2] == 0.1
    rows = group_rows(3)
    rows[1]["tokens"] = ["c:category-title"]
    assert model.refine(rows, [0.8, 0.8, 0.1])[2] == 0.1
    rows = group_rows(3)
    rows[1]["features"][NUMERIC_FEATURES.index("record_other_primary")] = 1
    assert model.refine(rows, [0.8, 0.8, 0.1])[2] == 0.1


def test_cold_group_rescues_only_individually_high_confidence_links():
    model = group_model(cold=True)
    rows = group_rows(4)
    scores = dict(
        zip((row["url"] for row in rows), [0.999, 0.999, 0.999, 0.98], strict=True)
    )
    model.refinement.score_many = lambda selected: [
        scores[row["url"]] for row in selected
    ]
    result = model.refine(rows, [0.1] * 4)
    assert all(score >= 0.5 for score in result[:3])
    assert result[3] == 0.1
    assert model.refine(rows[:2], [0.1, 0.1]) == [0.1, 0.1]
    order = [3, 1, 0, 2]
    assert model.refine([rows[i] for i in order], [0.1] * 4) == [
        result[i] for i in order
    ]


def test_groups_preserve_baseline_without_context_and_reject_explicit_chrome():
    model = group_model(cold=True)
    model.refinement.score_many = lambda rows: [0.0] * len(rows)
    rows = group_rows(3)
    rows[0] = feature_row()
    rows[1] = feature_row(in_nav=1)
    assert model.refine(rows, [0.9, 0.9, 0.9]) == [0.9, 0.0, 0.9]
    model.refinement.score_many = lambda rows: [0.999] * len(rows)
    assert model.refine([feature_row()], [0.1]) == [0.1]


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_seed_urls", True),
        ("min_seed_urls", 1),
        ("min_cold_urls", 4000),
        ("key_version", 2),
        ("cold_threshold", float("nan")),
        ("seed_threshold", float("inf")),
        ("cold_group", 1),
    ],
)
def test_group_policy_rejects_unbounded_or_ambiguous_configuration(field, value):
    model = group_model()
    payload = refinement_payload()
    payload["refinement_policy"] = dict(model.refinement_policy, **{field: value})
    with pytest.raises(ValueError):
        CascadeModel(payload)


def test_group_key_bounds_urls_and_keeps_query_names_without_values():
    from app.tracker.model_groups import group_key

    left, right = group_rows(2)
    left["url"] = "https://listing.example/2025/123/title.pdf?chapter=1"
    right["url"] = "https://listing.example/2026/456/different.pdf?chapter=2"
    assert group_key(left) == group_key(right)
    right["url"] += "&category=3"
    assert group_key(left) != group_key(right)
    for value in (
        "javascript:alert(1)",
        "https://[broken",
        "https://example.com/" + "x" * 4096,
    ):
        right["url"] = value
        assert group_key(right) is None
