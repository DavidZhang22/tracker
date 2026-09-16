"""Sparse TF-IDF + compact MLP inference. Numeric JSON only, no ML dependencies."""

import json
import logging
import math
import os
import struct
from functools import lru_cache
from pathlib import Path

from .link_context import EXTRA_FEATURES, context_candidates, model_tokens
from .link_model import FEATURES, load_model

NUMERIC_FEATURES = FEATURES + EXTRA_FEATURES
MODEL_PATH = Path(__file__).with_name("link-context-model.json")


def nuisance_context(features):
    """Permit learned rejection of chrome/references without losing sparse indexes."""
    f = dict(zip(NUMERIC_FEATURES, features, strict=True))
    return bool(
        (f["root_target"] and not f["query_count"] and not f["semantic_title"])
        or (f["date_index"] and not f["sequence_label"])
        or f["semantic_navigation"]
        or f["rel_author"]
        or f["rel_tag"]
        or f["utility_path"]
        or f["utility_label"]
        or (
            f["metadata_class"]
            and not f["same_url_has_heading"]
            and not f["own_date_attribute"]
        )
        or (f["record_other_primary"] and f["record_heading_count"] <= 1 / 6)
        or (
            f["in_tr"]
            and f["record_links"] >= 0.2
            and not f["semantic_title"]
            and not f["contains_heading"]
            and not f["generic_label"]
        )
        or (f["paragraph_words"] >= 0.3 and f["inline_text_fraction"] < 0.5)
    )


class ContextModel:
    primary = True

    def __init__(self, data, allow_fallback=True):
        if data["version"] != 2 or data["features"] not in (
            list(NUMERIC_FEATURES),
            list(NUMERIC_FEATURES[:-8]),
        ):
            raise ValueError("Incompatible context model")
        self.numeric_count = len(data["features"])
        self.token_mode = data.get("token_mode", "words")
        if self.token_mode not in {"words", "subwords-v1"}:
            raise ValueError("Invalid token preprocessing")
        self.calibration = data.get("calibration", [1.0, 0.0])
        if (
            len(self.calibration) != 2
            or not all(math.isfinite(v) and abs(v) <= 100 for v in self.calibration)
            or self.calibration[0] <= 0
        ):
            raise ValueError("Invalid calibration")
        self.fallback = None
        if "fallback" in data:
            if not allow_fallback or data.get("gate_feature") != "job_table":
                raise ValueError("Invalid model routing")
            self.fallback = ContextModel(data["fallback"], allow_fallback=False)
        self.model_id = data["model_id"]
        if not isinstance(self.model_id, str) or not 1 <= len(self.model_id) <= 100:
            raise ValueError("Invalid model identity")
        self.upper = float(data["threshold"])
        self.lower = float(data.get("reject_threshold", 0.08))
        if not 0.05 <= self.upper <= 0.95:
            raise ValueError("Invalid model threshold")
        if not 0 <= self.lower < self.upper:
            raise ValueError("Invalid rejection threshold")
        vocabulary, idf = data["vocabulary"], data["idf"]
        if (
            len(vocabulary) != len(idf)
            or len(vocabulary) > 1024
            or len(set(vocabulary)) != len(vocabulary)
        ):
            raise ValueError("Invalid vocabulary size")
        if not all(isinstance(word, str) and len(word) < 90 for word in vocabulary):
            raise ValueError("Invalid vocabulary")
        if not all(math.isfinite(x) and 0 < x < 30 for x in idf):
            raise ValueError("Invalid IDF")
        self.vocabulary = {
            word: (self.numeric_count + i, float(idf[i]))
            for i, word in enumerate(vocabulary)
        }
        self.layers = data["layers"]
        self.trees = data.get("trees")
        width = self.numeric_count + len(vocabulary)
        if self.trees is not None:
            if not 1 <= len(self.trees) <= 240:
                raise ValueError("Invalid tree count")
            self.intercept = data["intercept"]
            if not math.isfinite(self.intercept) or abs(self.intercept) > 100:
                raise ValueError("Invalid intercept")
            for tree in self.trees:
                if not 1 <= len(tree) <= 63:
                    raise ValueError("Invalid tree size")
                for i, node in enumerate(tree):
                    if len(node) != 5:
                        raise ValueError("Invalid tree node")
                    feature, threshold, left, right, value = node
                    if not all(math.isfinite(v) for v in node) or abs(value) > 100:
                        raise ValueError("Invalid tree value")
                    if feature == -2 and left == right == -1:
                        continue
                    if (
                        not isinstance(feature, int)
                        or not 0 <= feature < width
                        or not all(
                            isinstance(j, int) and i < j < len(tree)
                            for j in (left, right)
                        )
                    ):
                        raise ValueError("Invalid tree edges")
            return
        if not 1 <= len(self.layers) <= 3:
            raise ValueError("Too many layers")
        for layer in self.layers:
            count = len(layer["bias"])
            if not 1 <= count <= 64 or len(layer["weights"]) != count:
                raise ValueError("Invalid layer width")
            if any(len(row) != width for row in layer["weights"]):
                raise ValueError("Invalid weight dimensions")
            if not all(
                math.isfinite(v) and abs(v) < 1000
                for row in layer["weights"] + [layer["bias"]]
                for v in row
            ):
                raise ValueError("Invalid numeric weights")
            width = count
        if width != 1:
            raise ValueError("Invalid model output")

    def score(self, numeric, words):
        if len(numeric) not in (self.numeric_count, len(NUMERIC_FEATURES)) or not all(
            math.isfinite(v) and 0 <= v <= 1 for v in numeric
        ):
            raise ValueError("Invalid context features")
        if self.fallback and not numeric[NUMERIC_FEATURES.index("job_table")]:
            return self.fallback.score(numeric, words)
        numeric = numeric[: self.numeric_count]
        sparse = [(i, value) for i, value in enumerate(numeric) if value]
        text = [
            self.vocabulary[word]
            for word in model_tokens(words, self.token_mode)
            if word in self.vocabulary
        ]
        norm = math.sqrt(sum(value * value for _, value in text)) or 1
        sparse += [(i, value / norm) for i, value in text]
        if self.trees is not None:
            # sklearn's decision trees compare float32 inputs to float64 thresholds.
            vector = {i: struct.unpack("f", struct.pack("f", v))[0] for i, v in sparse}
            total = self.intercept
            for tree in self.trees:
                index = 0
                while tree[index][0] != -2:
                    feature, threshold, left, right, _ = tree[index]
                    index = left if vector.get(feature, 0) <= threshold else right
                total += tree[index][4]
            return self.calibrate(1 / (1 + math.exp(-max(-60, min(60, total)))))
        values = [
            b + sum(weights[i] * value for i, value in sparse)
            for weights, b in zip(
                self.layers[0]["weights"], self.layers[0]["bias"], strict=True
            )
        ]
        for layer in self.layers[1:]:
            values = [max(v, 0) for v in values]
            values = [
                b + sum(w * v for w, v in zip(weights, values, strict=True))
                for weights, b in zip(layer["weights"], layer["bias"], strict=True)
            ]
        return self.calibrate(1 / (1 + math.exp(-max(-60, min(60, values[0])))))

    def calibrate(self, probability):
        if self.calibration == [1.0, 0.0]:
            return probability
        p = min(1 - 1e-12, max(1e-12, probability))
        value = self.calibration[0] * math.log(p / (1 - p)) + self.calibration[1]
        return 1 / (1 + math.exp(-max(-60, min(60, value))))

    def score_many(self, rows):
        from .native_model import predict

        if any(
            len(row["features"]) not in (self.numeric_count, len(NUMERIC_FEATURES))
            or not all(math.isfinite(v) and 0 <= v <= 1 for v in row["features"])
            for row in rows
        ):
            raise ValueError("Invalid context features")
        if self.fallback is None:
            return predict(self, rows)
        gate = NUMERIC_FEATURES.index("job_table")
        groups = [[], []]
        for i, row in enumerate(rows):
            groups[bool(row["features"][gate])].append((i, row))
        scores = [0.0] * len(rows)
        for model, group in zip((self.fallback, self), groups, strict=True):
            for (i, _), score in zip(
                group, predict(model, [row for _, row in group]), strict=True
            ):
                scores[i] = score
        return scores


@lru_cache(maxsize=1)
def load_context_model():
    try:
        if MODEL_PATH.stat().st_size > 2_000_000:
            raise ValueError("Model exceeds resource budget")
        return ContextModel(json.loads(MODEL_PATH.read_text(encoding="utf8")))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        logging.getLogger(__name__).warning(
            "Context model unavailable; falling back to existing extraction"
        )
        return None


def active_context_model():
    if os.environ.get("TRACKER_LINK_MODEL", "on").lower() == "cascade":
        from .cascade_model import load_cascade_model

        if candidate := load_cascade_model():
            return candidate
    return load_context_model()


def classify_context(soup, source, *, fallback_scores=None, tables=None):
    mode = os.environ.get("TRACKER_LINK_MODEL", "on").lower()
    if mode in ("0", "off", "false", "legacy"):
        return {}, {}, set(), None
    model = active_context_model()
    if model is None:
        return {}, {}, set(), None
    fallback = load_model() if fallback_scores is not None else None
    scores, labels, rejected = {}, {}, set()
    rows = list(context_candidates(soup, source, tables=tables))
    for row, score in zip(rows, model.score_many(rows), strict=True):
        key = id(row["anchor"])
        if fallback is not None:
            # The context model starts with the exact legacy feature vector.
            # Extract it once; both trained classifiers keep their own decision.
            fallback_scores[key] = fallback.score(row["features"][: len(FEATURES)])
        scores[key] = score
        labels[key] = row["label"]
        if scores[key] < model.upper and (
            mode == "primary" or nuisance_context(row["features"])
        ):
            rejected.add(key)
    return scores, labels, rejected, model
