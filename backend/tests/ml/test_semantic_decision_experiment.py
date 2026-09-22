from types import SimpleNamespace

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

from ml import artifacts
from ml.semantic_decisions import experiment


def candidate(
    identity, label=1, *, family="example.org", title=0.0, nav=0.0, text=None
):
    features = [0.0] * len(experiment.NUMERIC_FEATURES)
    features[experiment.NUMERIC_FEATURES.index("semantic_title")] = title
    features[experiment.NUMERIC_FEATURES.index("semantic_navigation")] = nav
    return {
        "identity": identity,
        "url": identity,
        "label": label,
        "site_family": family,
        "source_id": family,
        "features": features,
        "tokens": ["t:example"],
        "link_text": text or "Example link",
        "text": text or "Example link and nearby record",
        "baseline": 0.3,
    }


def page(identifier, rows, expected, *, family=None, split="train", cohort="breadth"):
    return {
        "id": identifier,
        "site_family": family or identifier + ".org",
        "split": split,
        "cohort": cohort,
        "rows": rows,
        "expected": expected,
    }


def test_family_overlap_is_rejected_across_any_split():
    train = page("train", [], [], family="example.org", split="train")
    same_train = page("second", [], [], family="example.org", split="train")
    valid = page("valid", [], [], family="new.org", split="validation")
    result = experiment.assert_disjoint([train, same_train, valid])
    assert result == {"train": ["example.org"], "validation": ["new.org"]}
    with pytest.raises(ValueError, match="Website leakage"):
        experiment.assert_disjoint([train, {**valid, "site_family": "example.org"}])
    with pytest.raises(ValueError, match="Website leakage"):
        experiment.assert_disjoint(
            [train, {**valid, "site_family": "example.org", "split": "fresh"}]
        )


def test_url_representative_is_chosen_without_label_or_prediction():
    navigation = candidate("/same", 1, nav=1, text="Longer navigational text")
    heading = candidate("/same", 0, title=1, text="Title")
    rows = [navigation, heading]
    before = experiment.representatives(rows)
    rows = [
        {**navigation, "label": 0, "baseline": 0.99},
        {**heading, "label": 1, "baseline": 0.01},
    ]
    after = experiment.representatives(rows)
    assert len(before) == len(after) == 1
    assert before[0]["text"] == after[0]["text"] == "Title"


def test_training_cap_is_per_page_class_and_never_draws_validation_rows():
    many = [
        candidate(f"/class-{label}/record-{i}", label, family="one.org")
        for label in (0, 1)
        for i in range(130)
    ]
    duplicate = {**many[0], "text": "Another view", "link_text": "Another view"}
    pages = [
        page("one", [*many, duplicate], []),
        page("two", [candidate("/small", 1)], []),
        page("valid", [candidate("/secret-valid", 1)], [], split="validation"),
    ]
    chosen = experiment.training_rows(pages)
    assert len(chosen) == 193
    assert sum(r["label"] == 0 for r in chosen) == 96
    assert sum(r["label"] == 1 for r in chosen) == 97
    assert len({r["identity"] for r in chosen}) == 193
    assert "/secret-valid" not in {r["identity"] for r in chosen}
    reversed_pages = [{**p, "rows": list(reversed(p["rows"]))} for p in pages]
    assert {r["identity"] for r in experiment.training_rows(reversed_pages)} == {
        r["identity"] for r in chosen
    }


def test_sample_weights_equalize_families_and_available_classes():
    rows = (
        [candidate(f"/a/{i}", 1, family="a.org") for i in range(8)]
        + [candidate("/a/no", 0, family="a.org")]
        + [candidate(f"/b/{i}", 0, family="b.org") for i in range(2)]
    )
    weights = experiment.sample_weights(rows)
    assert weights.sum() == pytest.approx(len(rows))
    assert weights[:9].sum() == pytest.approx(weights[9:].sum())
    assert weights[:8].sum() == pytest.approx(weights[8])


def test_candidate_metrics_keep_empty_pages_missing_urls_and_page_scope():
    first = page(
        "one",
        [candidate("/one"), candidate("/one"), candidate("/unwanted", 0)],
        ["/one", "/never-candidate"],
        family="large.org",
    )
    empty = page("empty", [], ["/missing"], family="small.org")
    other = page("other", [candidate("/one")], ["/one"], family="large.org")
    result = experiment.quality(
        [first, empty, other], np.array([True, True, True, True])
    )
    assert result["correct"] == 2
    assert result["unwanted"] == 1
    assert result["missing"] == 2
    assert result["family_macro_f1"] == pytest.approx(((0.5 + 1) / 2 + 0) / 2)
    assert result["by_page"]["empty"]["recall"] == 0
    with pytest.raises(ValueError, match="Prediction count mismatch"):
        experiment.quality([empty], np.array([True]))
    with pytest.raises(ValueError):
        experiment.quality([first], np.array([True]))


def test_uncertain_gate_preserves_baseline_outside_strict_bounds():
    base = np.array([0.0, 0.1, 0.10001, 0.5, 0.89999, 0.9, 1.0])
    scores = np.array([1.0, 1.0, 0.7, 0.69, 0.0, 0.0, 0.0])
    expected = [False, False, True, False, False, True, True]
    assert experiment.route(base, scores, 0.7, "uncertain").tolist() == expected
    assert experiment.route(base, scores, 0.7, "replace").tolist() == [
        True,
        True,
        True,
        False,
        False,
        False,
        False,
    ]


@pytest.mark.parametrize("kind", ["linear", "neural"])
def test_exported_head_matches_fitted_probabilities_with_scaling(kind):
    rng = np.random.default_rng(12)
    x = rng.normal(size=(48, 5))
    x[:, 4] = 1.0
    y = (x[:, 0] + x[:, 1] * 0.5 > 0).astype(int)
    scaler = StandardScaler().fit(x)
    model = (
        LogisticRegression(C=0.5, random_state=1)
        if kind == "linear"
        else MLPClassifier(
            hidden_layer_sizes=(4, 3),
            solver="lbfgs",
            alpha=1.0,
            random_state=1,
            max_iter=500,
        )
    )
    model.fit(scaler.transform(x), y)
    exported = experiment.export_head(model, scaler, None)
    probabilities = experiment.predict_head(exported, x)
    assert exported["scale"][4] == 1.0
    assert probabilities == pytest.approx(
        model.predict_proba(scaler.transform(x))[:, 1], abs=2e-7
    )


def test_prepare_scores_whole_page_before_removing_neutral_rows(tmp_path, monkeypatch):
    origin = "https://example.org/"
    rows = [
        candidate(origin + "positive"),
        candidate(origin + "neutral"),
        candidate(origin + "negative"),
    ]
    source = {
        "id": "example",
        "url": origin,
        "site_family": "example.org",
        "split": "train",
        "cohort": "breadth",
    }
    seen = []
    baseline = SimpleNamespace(
        model_id="frozen-baseline",
        artifact_sha256="baseline-hash",
        _artifact_sha256="baseline-hash",
    )

    def baseline_scores(values):
        seen.append([r["url"] for r in values])
        return [0.7, 0.8, 0.2]

    baseline.score_many = baseline_scores
    monkeypatch.setattr(experiment, "capture", lambda _: ("html", "capture-hash"))
    monkeypatch.setattr(experiment, "extract_page", lambda *_: [dict(r) for r in rows])
    monkeypatch.setattr(
        experiment,
        "annotations",
        lambda *_: ({origin + "positive"}, {origin + "neutral"}),
    )
    prepared = experiment.prepare_page(source, tmp_path, baseline)
    assert seen == [[r["url"] for r in rows]]
    assert [r["url"] for r in prepared["rows"]] == [
        origin + "positive",
        origin + "negative",
    ]
    assert [r["label"] for r in prepared["rows"]] == [1, 0]
    assert [r["baseline"] for r in prepared["rows"]] == [0.7, 0.2]
    assert experiment.prepare_page(source, tmp_path, baseline) == prepared
    assert len(seen) == 1
    with pytest.raises(ValueError, match="Cached page changed"):
        experiment.prepare_page({**source, "split": "test"}, tmp_path, baseline)


def test_frozen_document_rejects_silent_protocol_changes(tmp_path):
    path = tmp_path / "protocol.json"
    experiment.frozen(path, {"seed": 1, "scope": "fixed"})
    experiment.frozen(path, {"seed": 1, "scope": "fixed"})
    with pytest.raises(ValueError, match="Frozen input changed"):
        experiment.frozen(path, {"seed": 2, "scope": "fixed"})


def test_empty_feature_inputs_never_load_encoder_or_vectorizer(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError("An empty input must not load a model")

    monkeypatch.setattr(experiment, "Encoder", unavailable)
    monkeypatch.setattr(experiment, "TfidfVectorizer", unavailable)
    assert experiment.matrix([]).shape == (0, len(experiment.NUMERIC_FEATURES))
    assert experiment.embeddings([], "minilm-l6", tmp_path).shape == (0, 384)
    for encoder, dimensions in ((None, 84), ("tfidf", 90), ("minilm-l6", 468)):
        payload = {"encoder": encoder, "dimensions": dimensions}
        assert experiment.features_for([], payload, tmp_path).shape == (0, dimensions)


def test_replay_preserves_probabilities_and_repeated_key_order():
    first = candidate("https://example.org/same", text="First nearby record")
    second = {**first, "text": "A different nearby record"}
    model = experiment.ReplayModel([first, second], [0.23, 0.81])
    assert model.score_many([first, second]) == [0.23, 0.81]
    # Each parser invocation replays from its first candidate.
    assert model.score_many([first, second]) == [0.23, 0.81]
    assert model.lower < 0.23 < model.upper
    with pytest.raises(ValueError, match="Parser candidate differs"):
        model.score_many([first, second, first])
    changed = {**first, "tokens": ["t:different"]}
    with pytest.raises(ValueError, match="Parser candidate differs"):
        model.score_many([changed])


def test_tfidf_feature_export_roundtrip_has_no_validation_vocabulary(tmp_path):
    rows = [
        candidate("/one", text="Coastal water monitoring"),
        candidate("/two", text="Coastal habitat survey"),
        candidate("/three", text="Air quality monitoring"),
        candidate("/four", text="unseenword evaluation text"),
    ]
    vectorizer = experiment.TfidfVectorizer(
        max_features=30, min_df=1, ngram_range=(1, 2), sublinear_tf=True
    )
    vectorizer.fit([r["text"] for r in rows[:3]])
    assert "unseenword" not in vectorizer.vocabulary_
    expected = np.hstack(
        (
            experiment.matrix(rows),
            vectorizer.transform([r["text"] for r in rows]).toarray(),
        )
    )
    payload = {
        "encoder": "tfidf",
        "vocabulary": vectorizer.vocabulary_,
        "idf": vectorizer.idf_.tolist(),
        "dimensions": expected.shape[1],
    }
    assert experiment.features_for(rows, payload, tmp_path) == pytest.approx(expected)


def test_parser_replay_pins_baseline_and_keeps_neutral_scores(monkeypatch):
    from app.tracker import context_model

    origin = "https://example.org/"
    positive = candidate(origin + "positive")
    neutral = candidate(origin + "neutral")
    prepared = {
        **page("example", [positive], [positive["url"]]),
        "source": {"id": "example", "url": origin},
        "ignored": [neutral["url"]],
    }
    baseline = SimpleNamespace(
        model_id="pinned-baseline",
        upper=0.5,
        score_many=lambda rows: [
            0.9 if r["url"] == neutral["url"] else 0.2 for r in rows
        ],
    )
    models_seen, scores_seen = [], []

    def fake_parse(html, base):
        assert (html, base) == ("saved snapshot", origin)
        model = context_model.active_context_model()
        models_seen.append(model)
        values = model.score_many([positive, neutral])
        scores_seen.append(values)
        entries = [
            SimpleNamespace(url=row["url"])
            for row, probability in zip([positive, neutral], values, strict=True)
            if probability >= 0.5
        ]
        return SimpleNamespace(entries=entries), [], []

    monkeypatch.setattr(experiment, "capture", lambda _: ("saved snapshot", "hash"))
    monkeypatch.setattr(experiment, "parse_page", fake_parse)
    deployed = experiment.pipeline([prepared], None, baseline)
    learned = experiment.pipeline([prepared], np.array([0.63]), baseline)
    assert models_seen[0] is baseline
    assert scores_seen == [[0.2, 0.9], [0.63, 0.9]]
    assert deployed["summary"]["missing"] == 1
    assert learned["summary"]["correct"] == 1
    assert learned["summary"]["unwanted"] == 0


def test_cache_fingerprint_includes_baseline_weights_not_only_name(
    tmp_path, monkeypatch
):
    source = {
        "id": "empty",
        "url": "https://example.org/",
        "site_family": "example.org",
        "split": "train",
    }
    monkeypatch.setattr(experiment, "capture", lambda _: ("snapshot", "hash"))
    monkeypatch.setattr(experiment, "extract_page", lambda *_: [])
    monkeypatch.setattr(experiment, "annotations", lambda *_: ({"/missed"}, set()))
    baseline = SimpleNamespace(
        model_id="unchanged-name",
        artifact_sha256="first-weights",
        score_many=lambda _: [],
    )
    first = experiment.prepare_page(source, tmp_path, baseline)
    assert first["rows"] == [] and first["expected"] == ["/missed"]
    baseline.artifact_sha256 = "different-weights"
    with pytest.raises(ValueError, match="Cached page changed"):
        experiment.prepare_page(source, tmp_path, baseline)


def test_frozen_compressed_protocol_preserves_bytes_and_rejects_changes(tmp_path):
    path = tmp_path / "protocol.json"
    original = b'{"version": 1}\r\n'
    path.write_bytes(original)
    stored = artifacts.compress(path)
    compressed = stored.read_bytes()
    experiment.frozen(path, {"version": 1})
    assert not path.exists()
    assert stored.read_bytes() == compressed
    assert artifacts.read_bytes(path) == original
    with pytest.raises(ValueError, match="Frozen input changed"):
        experiment.frozen(path, {"version": 2})
    assert stored.read_bytes() == compressed
