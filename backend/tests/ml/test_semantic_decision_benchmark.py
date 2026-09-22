import hashlib
import json

import numpy as np
import pytest

from ml.semantic_decisions import benchmark as bench


def payload(encoder=None, dimensions=1, **extra):
    return dict(
        encoder=encoder,
        dimensions=dimensions,
        mean=[0.0] * dimensions,
        scale=[1.0] * dimensions,
        layers=[dict(weights=[[1.0]] + [[0.0]] * (dimensions - 1), bias=[0.0])],
        **extra,
    )


def row(text, number=0.0, url="https://example.test/item"):
    return dict(text=text, features=[number], identity=url)


def test_uncertain_policy_gates_before_encoding_and_preserves_boundaries():
    class Head:
        def __init__(self):
            self.seen = []

        def score(self, rows):
            self.seen.extend(rows)
            return [0.0, 1.0], {}

    rows = [row(str(i)) for i in range(6)]
    head = Head()
    chosen, details = bench.classify_rows(
        rows, [0.0, 0.1, 0.1001, 0.8999, 0.9, 1.0], head, 0.5, "uncertain"
    )
    assert chosen == [False, False, False, True, True, True]
    assert head.seen == rows[2:4]
    assert details["inferred_candidates"] == 2


def test_empty_uncertainty_gate_never_calls_encoder():
    class Encoder:
        def encode(self, texts):
            raise AssertionError("Empty gate must not call the encoder")

    head = bench.PortableHead(payload("minilm-l6", 385), Encoder())
    chosen, timing = bench.classify_rows(
        [row("a"), row("b")], [0.1, 0.9], head, 0.99, "uncertain"
    )
    assert chosen == [False, True]
    assert timing["unique_texts"] == timing["embedding_batches"] == 0
    assert timing["inferred_candidates"] == 0
    empty, _ = bench.classify_rows([], [], head, 0.5, "replace")
    assert empty == []


def test_text_deduplication_and_bounded_encoder_calls_keep_original_order():
    class Encoder:
        def __init__(self):
            self.calls = []

        def encode(self, texts):
            self.calls.append(texts)
            return [[float(text)] * 384 for text in texts]

    encoder = Encoder()
    model = payload("minilm-l3", 385)
    model["layers"][0]["weights"][0] = [0.0]
    model["layers"][0]["weights"][1] = [0.01]
    head = bench.PortableHead(model, encoder)
    rows = [row(str(i)) for i in range(259)] + [row("258"), row("0")]
    scores, timing = head.score(rows)
    assert [len(call) for call in encoder.calls] == [128, 128, 3]
    assert timing["unique_texts"] == 259
    assert timing["embedding_batches"] == 33
    assert scores[-2] == scores[258]
    assert scores[-1] == scores[0] == 0.5
    np.testing.assert_allclose(scores[:259], 1 / (1 + np.exp(-np.arange(259) * 0.01)))


def test_portable_tfidf_matches_training_vectorizer_on_unicode_and_empty_text():
    sklearn_text = pytest.importorskip("sklearn.feature_extraction.text")
    vectorizer = sklearn_text.TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
    texts = [
        "Coastal coastal OCEAN cafÃ© 42",
        "Water cafÃ© æ°´ç ”ç©¶",
        "X a !!!",
        "",
        "unknown_tokens",
    ]
    vectorizer.fit(texts[:3])
    head = bench.PortableHead(
        payload(
            "tfidf",
            1 + len(vectorizer.vocabulary_),
            vocabulary=vectorizer.vocabulary_,
            idf=vectorizer.idf_.tolist(),
        )
    )
    np.testing.assert_allclose(
        head.lexical(texts),
        vectorizer.transform(texts).toarray(),
        rtol=1e-14,
        atol=1e-14,
    )


def test_portable_multilayer_head_matches_frozen_numeric_contract():
    model = dict(
        encoder=None,
        mean=[2.0, 4.0],
        scale=[2.0, 4.0],
        layers=[
            dict(weights=[[2.0, -1.0], [-2.0, 3.0]], bias=[0.0, -0.5]),
            dict(weights=[[2.0], [-1.0]], bias=[0.2]),
        ],
    )
    rows = [dict(features=[4.0, 8.0], text="a"), dict(features=[0.0, 4.0], text="b")]
    probability, _ = bench.PortableHead(model).score(rows)
    expected = 1 / (1 + np.exp(-np.asarray([-1.3, -0.3])))
    np.testing.assert_allclose(probability, expected)


def test_candidate_hash_preserves_duplicate_decisions_and_matches_offline_spec():
    rows = [row("one"), row("two"), row("three", url="https://example.test/other")]
    chosen = [True, False, False]
    pairs = [[r["identity"], bool(p)] for r, p in zip(rows, chosen, strict=True)]
    expected = hashlib.sha256(
        json.dumps(pairs, sort_keys=True, separators=(",", ":")).encode("utf8")
    ).hexdigest()
    hashes = bench.decision_hashes(rows, chosen)
    assert hashes["candidate_decisions_sha256"] == expected
    assert hashes["chosen_urls_sha256"] == bench.digest([rows[0]["identity"]])
    with pytest.raises(ValueError):
        bench.decision_hashes(rows, [True])


def test_align_keeps_baseline_associations_after_neutral_filtering():
    rows = [
        dict(url="http://example.test/one"),
        dict(url="https://example.test/neutral"),
        dict(url="https://example.test/two"),
    ]
    page = dict(
        source=dict(url="https://example.test/list"),
        ignored=["https://example.test/neutral"],
    )
    kept, scores = bench.aligned_rows(rows, page, [0.2, 0.4, 0.6])
    assert [r["identity"] for r in kept] == [
        "https://example.test/one",
        "https://example.test/two",
    ]
    assert scores == [0.2, 0.6]


def test_percentiles_are_interpolated_from_raw_timings():
    report = bench.summarize(
        [dict(total_seconds=value, page_id="a") for value in [1, 2, 3, 4, 5]]
    )
    assert report["total_seconds"] == dict(
        p50=3, p95=4.8, minimum=1, maximum=5, samples=5
    )


def test_volume_selection_uses_test_pages_without_reading_labels(tmp_path):
    for i, count in enumerate([100, 2, 30, 50, 7]):
        bench.write_json(
            tmp_path / f"{i}.json",
            dict(id=str(i), source={}, split="test", rows=[{}] * count),
        )
    bench.write_json(
        tmp_path / "train.json",
        dict(id="train", source={}, split="train", rows=[{}] * 999),
    )
    selected = bench.choose_pages(tmp_path, 3, None)
    assert [len(page["rows"]) for _, page in selected] == [2, 30, 100]


def test_offline_parity_requires_every_page_to_match_exactly():
    result = dict(
        case=dict(name="model"),
        decision_hashes={"a": dict(candidate_decisions_sha256="matching")},
    )
    reports = [dict(models=dict(model=dict(decision_hashes={"a": "matching"})))]
    assert bench.check_decision_parity(result, reports)["pages"] == {"a": True}
    with pytest.raises(ValueError, match="no decision hash"):
        bench.check_decision_parity(result, [])
    reports[0]["models"]["model"]["decision_hashes"]["a"] = "different"
    with pytest.raises(ValueError, match="Runtime decisions differ"):
        bench.check_decision_parity(result, reports)


def test_feature_parity_allows_only_tiny_cpu_roundoff():
    source = dict(
        url="https://example.test/a",
        tokens=["t:title"],
        identity="https://example.test/a",
        text="Title",
        features=[0.18310204811135164],
    )
    other = dict(source, features=[0.18310204811135158])
    assert 0 < bench.verify_rows([other], [source]) < 1e-12
    with pytest.raises(ValueError, match="numeric features"):
        bench.verify_rows([dict(source, features=[0.1832])], [source])
    with pytest.raises(ValueError, match="text differs"):
        bench.verify_rows([dict(source, text="Other title")], [source])
    with pytest.raises(ValueError, match="numeric features"):
        bench.verify_rows([dict(source, features=[float("nan")])], [source])
