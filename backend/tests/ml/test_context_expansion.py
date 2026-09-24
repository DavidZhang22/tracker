import copy

import pytest
from bs4 import BeautifulSoup

from app.tracker.record_context import FEATURES
from ml.context_expansion.data import (
    TRANSFORMS,
    augment,
    document_hash,
    expanded,
    select_one,
    validate_records,
)
from ml.context_expansion.evaluate import (
    candidate_rows,
    contains_fact,
    evaluate,
    family_weights,
)


def record():
    return {
        "id": "menus",
        "family": "dining",
        "split": "development",
        "source_url": "https://dining.example/menus",
        "source_sha256": "reviewed-capture",
        "html": '<main><article id="one" class="card"><h2><a href="/menu/one">North Hall</a></h2><ul><li>Rice with beans</li><li>Vegetable soup</li></ul></article><article id="two" class="card"><h2><a href="/menu/two">South Hall</a></h2><p>Roasted carrots</p></article></main>',
        "anchors": [
            {
                "selector": "#one a",
                "href": "/menu/one",
                "title": "North Hall",
                "required": ["Rice with beans", "Vegetable soup"],
                "forbidden": ["Roasted carrots"],
                "boundary_selector": "#one",
            },
            {
                "selector": "#two a",
                "href": "/menu/two",
                "title": "South Hall",
                "required": ["Roasted carrots"],
                "forbidden": ["Rice with beans"],
                "boundary_selector": "#two",
            },
        ],
    }


@pytest.mark.parametrize("transform", TRANSFORMS)
def test_augmentation_preserves_reviewed_targets_and_facts(transform):
    original = record()
    frozen = copy.deepcopy(original)
    result = augment(original, transform)
    assert original == frozen
    assert result["family"] == original["family"]
    assert result["split"] == original["split"]
    assert result["synthetic"]["parent_id"] == original["id"]
    validate_records([result])
    soup = BeautifulSoup(result["html"], "html.parser")
    for spec in result["anchors"]:
        target = select_one(soup, spec["selector"])
        boundary = select_one(soup, spec["boundary_selector"])
        assert target.get("href") == spec["href"]
        assert all(
            contains_fact(boundary.get_text(" ", strip=True), fact)
            for fact in spec["required"]
        )
        assert not any(
            contains_fact(boundary.get_text(" ", strip=True), fact)
            for fact in spec["forbidden"]
        )


@pytest.mark.parametrize("split", ["validation", "heldout"])
def test_augmentation_cannot_touch_evaluation_families(split):
    source = record()
    source["split"] = split
    with pytest.raises(ValueError, match="Only development"):
        augment(source, "classless")
    assert expanded([source]) == [source]


def test_expansion_is_deterministic_and_deduplicates_noop_variants():
    source = record()
    source["html"] = source["html"].replace(' class="card"', "")
    first = expanded([source])
    assert first == expanded([source])
    assert len({document_hash(r["html"]) for r in first}) == len(first)
    assert not any(
        r.get("synthetic", {}).get("transform") == "classless" for r in first
    )


@pytest.mark.parametrize("identity", ["family", "host", "document"])
def test_split_guards_reject_source_and_document_leakage(identity):
    first, second = record(), record()
    second.update(id="evaluation", split="heldout")
    if identity != "family":
        second["family"] = "other"
    if identity == "document":
        second["source_url"] = "https://other.example/menus"
        second["html"] = second["html"].replace(
            '<article id="one" class="card">', '<article class="card" id="one">'
        )
    with pytest.raises(ValueError, match="Cross-split"):
        validate_records([first, second])


def test_fact_comparison_does_not_count_subword_matches():
    assert not contains_fact("Hamburger and beans", "ham")
    assert contains_fact("RICE  WITH\nBEANS", "rice with beans")
    assert contains_fact("Ｆｒｅｎｃｈ soup", "French")


def test_candidates_label_only_complete_scoped_records():
    rows = list(candidate_rows(record()))
    assert {r["anchor"] for r in rows if r["label"]} == {"menus:0", "menus:1"}
    assert any(not row["label"] for row in rows)
    assert all(row["split"] == "development" for row in rows)


def test_evaluation_distinguishes_candidate_coverage_from_model_failure():
    constant = {
        "features": list(FEATURES),
        "threshold": 0.5,
        "model_id": "constant-probe",
        "layers": [{"weights": [[0] * len(FEATURES)], "bias": [10]}],
    }
    result = evaluate([record()], constant, pipeline=False)
    assert result["summary"]["candidate_coverage"] == 1
    assert result["summary"]["complete_fact_recovery"] == 0
    assert result["summary"]["neighbor_leakage"] == 0
    assert result["summary"]["correct_records"] == 0


def test_no_details_records_remain_useful_negative_controls():
    source = record()
    source["anchors"] = [source["anchors"][0]]
    source["anchors"][0]["required"] = []
    source["anchors"][0]["boundary_selector"] = "#one a"
    validate_records([source])
    rows = list(candidate_rows(source))
    assert sum(r["label"] for r in rows) == 1


def test_family_weights_do_not_overweight_augmented_sources():
    rows = [{"family": "small"}] + [{"family": "large"}] * 9
    weights = family_weights(rows)
    assert weights[0] == pytest.approx(sum(weights[1:]))
    assert sum(weights) == pytest.approx(len(rows))


def test_variant_counts_do_not_multiply_original_anchor_weight():
    rows = [
        {"family": "same", "original_anchor": "one", "anchor": "one:base"},
        {"family": "same", "original_anchor": "one", "anchor": "one:wrapped"},
        {"family": "same", "original_anchor": "one", "anchor": "one:wrapped"},
        {"family": "same", "original_anchor": "two", "anchor": "two:base"},
    ]
    weights = family_weights(rows)
    assert sum(weights[:3]) == pytest.approx(weights[3])
    assert weights[0] == pytest.approx(sum(weights[1:3]))


def test_duplicate_features_preserve_total_sample_weight():
    from ml.context_expansion.train import collapse_rows

    rows = [
        {"family": "one", "anchor": "one", "features": [1, 0], "label": 1},
        {"family": "one", "anchor": "one", "features": [1, 0], "label": 1},
        {"family": "two", "anchor": "two", "features": [1, 0], "label": 0},
    ]
    features, labels, weights = collapse_rows(rows)
    assert len(features) == 2
    assert set(labels) == {0, 1}
    assert sum(weights) == pytest.approx(len(rows))
    assert weights[0] == pytest.approx(weights[1])


def test_historical_training_removes_exact_cross_split_documents():
    pytest.importorskip("sklearn")
    from ml.context_expansion.train import legacy_foundation

    training, validation, heldout, audit = legacy_foundation()
    assert training and validation and heldout
    assert audit["cross_split_duplicate_document_groups"] > 0
    assert audit["excluded_training_pages"] > 0
    assert all(not row["source"].startswith("layout-4-") for row in training)
    assert all(not row["source"].startswith("layout-5-") for row in training)


def test_grouped_foundation_has_disjoint_documents_and_sibling_coverage():
    pytest.importorskip("sklearn")
    from ml.context_expansion.train import grouped_foundation

    training, validation, heldout, audit = grouped_foundation()
    seen = set()
    for rows in (training, validation, heldout):
        groups = {row["document_group"] for row in rows if "document_group" in row}
        assert groups and not seen & groups
        seen.update(groups)
        assert any(row["source"].startswith("paired-") for row in rows)
    assert audit["removed_duplicate_pages"] > 0
    assert {r["split"] for r in training if r["source"].startswith("jobs-table")} == {
        "train"
    }
    assert {r["split"] for r in validation if r["source"].startswith("hn.html")} == {
        "validation"
    }
    assert {
        r["split"] for r in heldout if r["source"].startswith("github-releases.html")
    } == {"test"}


@pytest.mark.parametrize("error", ["outside", "missing", "forbidden"])
def test_reviewed_boundaries_reject_inconsistent_labels(error):
    source = record()
    spec = source["anchors"][0]
    if error == "outside":
        spec["boundary_selector"] = "#two"
        match = "outside annotated boundary"
    elif error == "missing":
        spec["required"].append("Fact that is not in the capture")
        match = "Required fact absent"
    else:
        spec["forbidden"].append("Rice with beans")
        match = "Forbidden fact present"
    with pytest.raises(ValueError, match=match):
        validate_records([source])


def test_reviewed_neighbor_can_supply_facts_without_broadening_boundary():
    source = record()
    source["html"] = (
        '<main><h2 id="title"><a href="/menu/one">North Hall</a></h2><p id="details">Rice with beans</p><p>Neighbor food</p></main>'
    )
    source["anchors"] = [
        {
            "selector": "#title a",
            "href": "/menu/one",
            "required": ["Rice with beans"],
            "forbidden": ["Neighbor food"],
            "boundary_selector": "#title",
            "neighbor_selector": "#details",
        }
    ]
    validate_records([source])
