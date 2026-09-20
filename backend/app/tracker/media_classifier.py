"""Small local media-format model. JSON weights only; no pickle or downloads."""

import json
import math
from functools import lru_cache
from pathlib import Path

from .media_features import (
    CHAPTER_FEATURE,
    CLASS_OFFSETS,
    EXTERNAL_LINK_FEATURE,
    FEATURE_COUNT,
    KINDS,
    vector,
)

MAX_MODEL_BYTES = 48_000


@lru_cache(maxsize=1)
def load():
    import numpy as np

    path = Path(__file__).with_name("media_classifier.json")
    if path.stat().st_size > MAX_MODEL_BYTES:
        raise ValueError("Media classifier exceeds its size limit.")
    model = json.loads(path.read_text(encoding="utf8"))
    classes = tuple(model["classes"])
    if model.get("version") != 1 or model.get("feature_count") != FEATURE_COUNT:
        raise ValueError("Unsupported media classifier.")
    if len(set(classes)) != len(classes) or set(classes) != set(KINDS) - {"youtube"}:
        raise ValueError("Unsupported media classes.")
    weights = np.asarray(model["weights"], dtype=np.float32)
    bias = np.asarray(model["bias"], dtype=np.float32)
    if weights.shape != (len(classes), FEATURE_COUNT) or bias.shape != (len(classes),):
        raise ValueError("Invalid media classifier shape.")
    if not np.isfinite(weights).all() or not np.isfinite(bias).all():
        raise ValueError("Invalid media classifier parameters.")
    minimum, margin = (
        float(model["minimum_probability"]),
        float(model["minimum_margin"]),
    )
    if not (
        math.isfinite(minimum)
        and math.isfinite(margin)
        and 0 < minimum < 1
        and 0 < margin < 1
    ):
        raise ValueError("Invalid media classifier thresholds.")
    weights.flags.writeable = bias.flags.writeable = False
    return classes, weights, bias, minimum, margin


def select(values, baseline, fallback, parameters):
    """Use learned evidence only when distinct enough to beat ambiguity."""
    import numpy as np

    if fallback in {"comic", "novel", "youtube"}:
        return baseline
    classes, weights, bias, minimum, margin = parameters
    scores = weights @ np.asarray(values, dtype=np.float32) + bias
    probabilities = np.exp(scores - scores.max())
    probabilities /= probabilities.sum()
    first = int(probabilities.argmax())
    maximum = float(probabilities[first])
    probabilities[first] = 0
    if maximum < minimum or maximum - float(probabilities.max()) < margin:
        return baseline
    winner = classes[first]
    # News about a medium is still news. A conflicting format needs inventory
    # evidence (content paths or chapter sequences), rather than topic words.
    if baseline == "blog" and winner not in {"blog", "website"}:
        offset = CLASS_OFFSETS[winner]
        explicit_blog = (
            values[CLASS_OFFSETS["blog"]] or values[CLASS_OFFSETS["blog"] + 4]
        )
        sequence = winner in {"comic", "novel"} and values[CHAPTER_FEATURE] >= 0.4
        if explicit_blog and not values[offset + 5] and not sequence:
            return baseline
    # A weakly described feed stays a feed. Downgrading to a generic website
    # requires observed aggregation across hosts, not merely absent keywords.
    if winner == "website" and values[EXTERNAL_LINK_FEATURE] < 0.7:
        return baseline
    return winner


def classify(scan, text, rows, baseline):
    fallback = scan.get("detected_kind") or scan.get("kind", "website")
    if fallback in {"comic", "novel", "youtube"}:
        return baseline
    try:
        parameters = load()
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return baseline
    return select(vector(scan, text, rows), baseline, fallback, parameters)
