from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.tracker.semantic_model import (
    DIMENSIONS,
    MAX_BATCH,
    Encoder,
    EncoderUnavailable,
)


def stub_encoder(output):
    model = Encoder.__new__(Encoder)
    model.np = np
    model.query_prefix = ""
    model.pooling = "mean"
    model.failed = False
    model.lock = Lock()
    model.inputs = {"input_ids", "attention_mask", "token_type_ids"}
    model.tokenizer = Mock()
    model.tokenizer.encode_batch.side_effect = lambda texts: [
        SimpleNamespace(ids=[1, 0], attention_mask=[1, 0], type_ids=[0, 0])
        for _ in texts
    ]
    model.session = Mock()
    model.session.run.side_effect = lambda _, inputs: [output(len(inputs["input_ids"]))]
    return model


@pytest.mark.parametrize(
    "output",
    [
        lambda size: np.ones((size, 2, DIMENSIONS - 1)),
        lambda size: np.ones((size, DIMENSIONS)),
        lambda size: np.full((size, 2, DIMENSIONS), np.nan),
        lambda size: np.full((size, 2, DIMENSIONS), np.inf),
        lambda size: np.zeros((size, 2, DIMENSIONS)),
    ],
)
def test_invalid_inference_output_disables_encoder_for_text_fallback(output):
    model = stub_encoder(output)
    with pytest.raises(EncoderUnavailable):
        model.encode(["Example"])
    assert model.failed
    with pytest.raises(EncoderUnavailable):
        model.encode(["Another query"])
    assert model.session.run.call_count == 1


def test_masked_pooling_normalizes_vectors_and_respects_batch_limit():
    def output(size):
        values = np.zeros((size, 2, DIMENSIONS))
        values[:, 0, 0] = 3
        values[:, 0, 1] = 4
        values[:, 1, 2] = 100  # Padding must not contribute to the vector.
        return values

    model = stub_encoder(output)
    vectors = model.encode(["Example"] * (MAX_BATCH + 1))
    assert len(vectors) == MAX_BATCH + 1
    for vector in vectors:
        assert vector[:3] == pytest.approx([0.6, 0.8, 0])
        assert np.linalg.norm(vector) == pytest.approx(1)
    assert [
        call.args[1]["input_ids"].shape[0] for call in model.session.run.call_args_list
    ] == [MAX_BATCH, 1]
    assert model.encode([]) == []


def test_native_pooling_matches_reference_without_mutating_model_output():
    rng = np.random.default_rng(19)
    values = rng.normal(size=(8, 192, DIMENSIONS)).astype(np.float32)
    masks = rng.integers(0, 2, size=(8, 192), dtype=np.int64)
    expected = (values * masks[..., None]).sum(axis=1) / np.maximum(
        masks.sum(axis=1, keepdims=True), 1
    )
    expected /= np.linalg.norm(expected, axis=1, keepdims=True)
    original = values.copy()
    model = stub_encoder(lambda size: values[:size])
    model.tokenizer.encode_batch.side_effect = lambda texts: [
        SimpleNamespace(ids=[1] * 192, attention_mask=mask.tolist(), type_ids=[0] * 192)
        for mask in masks[: len(texts)]
    ]
    actual = model.encode(["Example"] * 8)
    np.testing.assert_array_equal(actual, expected.astype(np.float32))
    np.testing.assert_array_equal(values, original)
    model.pooling = "cls"
    actual = model.encode(["Example"] * 8)
    np.testing.assert_allclose(
        actual,
        original[:, 0] / np.linalg.norm(original[:, 0], axis=1, keepdims=True),
        atol=1e-7,
    )
    np.testing.assert_array_equal(values, original)
