"""Light/deep research cascade with explicit threshold and preprocessing contracts."""

import json
import math
from functools import lru_cache
from pathlib import Path

from .context_model import ContextModel

MODEL_PATH = Path(__file__).with_name("link-cascade-model.json")


def decision_score(score, threshold):
    return (
        score / (2 * threshold)
        if score < threshold
        else 0.5 + (score - threshold) / (2 * (1 - threshold))
    )


class CascadeModel:
    primary = True
    upper, lower = 0.5, 0.08

    def __init__(self, data):
        if data["version"] != 1:
            raise ValueError("Invalid cascade version")
        self.model_id = data["model_id"]
        if not isinstance(self.model_id, str) or not 1 <= len(self.model_id) <= 100:
            raise ValueError("Invalid cascade identity")
        self.light = ContextModel(data["light"])
        self.deep = ContextModel(data["deep"], allow_fallback=False)
        self.policy = data.get("policy", "replace")
        if self.policy not in {"replace", "rescue"}:
            raise ValueError("Invalid cascade policy")
        self.gate = data["gate"]
        if (
            len(self.gate) != 2
            or not all(math.isfinite(v) for v in self.gate)
            or not (0 <= self.gate[0] < self.light.upper < self.gate[1] <= 1)
        ):
            raise ValueError("Invalid cascade gate")

    def score_many(self, rows):
        light = self.light.score_many(rows)
        result = [decision_score(p, self.light.upper) for p in light]
        indices = [
            i
            for i, p in enumerate(light)
            if self.gate[0] < p < self.gate[1]
            and (self.policy == "replace" or p < self.light.upper)
        ]
        deep = self.deep.score_many([rows[i] for i in indices])
        for i, p in zip(indices, deep, strict=True):
            if self.policy == "replace" or p >= self.deep.upper:
                result[i] = decision_score(p, self.deep.upper)
        return result

    def score(self, numeric, words):
        return self.score_many([{"features": numeric, "tokens": words}])[0]


@lru_cache(maxsize=1)
def load_cascade_model():
    try:
        if MODEL_PATH.stat().st_size > 4_000_000:
            return None
        return CascadeModel(json.loads(MODEL_PATH.read_text(encoding="utf8")))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None
