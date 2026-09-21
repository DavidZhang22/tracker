"""Light/deep research cascade with explicit threshold and preprocessing contracts."""

import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from .context_model import NUMERIC_FEATURES, ContextModel, nuisance_context
from .model_groups import group_key

MODEL_PATH = Path(__file__).with_name("link-cascade-model.json")
CHROME_FEATURES = tuple(
    NUMERIC_FEATURES.index(name)
    for name in (
        "semantic_navigation",
        "in_nav",
        "in_footer",
        "in_aside",
        "rel_author",
        "rel_tag",
        "utility_path",
        "utility_label",
    )
)
TITLE_FEATURE = NUMERIC_FEATURES.index("semantic_title")


def structural_chrome(features, title_override=True):
    return any(features[i] for i in CHROME_FEATURES) and not (
        title_override and features[TITLE_FEATURE]
    )


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
        self.refinement = None
        self.refinement_policy = None
        if "refinement" in data:
            self.refinement = ContextModel(data["refinement"], allow_fallback=False)
            policy = data.get("refinement_policy")
            if not isinstance(policy, dict):
                raise ValueError("Invalid refinement policy")
            mode = policy.get("mode")
            if mode == "structural":
                if set(policy) != {"mode", "title_override"} or not isinstance(
                    policy["title_override"], bool
                ):
                    raise ValueError("Invalid structural refinement policy")
            elif mode == "group":
                required = {
                    "mode",
                    "title_override",
                    "seed_threshold",
                    "min_seed_urls",
                    "cold_group",
                    "cold_threshold",
                    "min_cold_urls",
                    "key_version",
                }
                if set(policy) != required or any(
                    type(policy[key]) is not bool
                    for key in ("title_override", "cold_group")
                ):
                    raise ValueError("Invalid group refinement policy")
                if any(
                    type(policy[key]) is not int or policy[key] != value
                    for key, value in (
                        ("min_seed_urls", 2),
                        ("min_cold_urls", 3),
                        ("key_version", 1),
                    )
                ):
                    raise ValueError("Invalid group support bounds")
                if any(
                    type(policy[key]) not in (int, float)
                    or not math.isfinite(policy[key])
                    or not low <= policy[key] <= high
                    for key, low, high in (
                        ("seed_threshold", 0.5, 0.95),
                        ("cold_threshold", 0.95, 1.0),
                    )
                ):
                    raise ValueError("Invalid group confidence bounds")
            elif mode in {"nuisance", "disagreement"}:
                if (
                    set(policy) != {"mode", "ceiling"}
                    or not isinstance(policy["ceiling"], (int, float))
                    or not math.isfinite(policy["ceiling"])
                    or not 0 <= policy["ceiling"] <= 1
                ):
                    raise ValueError("Invalid confidence refinement policy")
            else:
                raise ValueError("Invalid refinement mode")
            self.refinement_policy = dict(policy)

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
        if self.refinement is not None:
            result = self.refine(rows, result)
        return result

    def refine(self, rows, baseline):
        policy = self.refinement_policy
        if policy["mode"] == "group":
            return self.refine_groups(rows, baseline)
        if policy["mode"] == "structural":
            chrome = [
                structural_chrome(row["features"], policy["title_override"])
                for row in rows
            ]
            indices = [
                i
                for i, score in enumerate(baseline)
                if (score < self.upper and not chrome[i])
                or (score >= self.upper and chrome[i])
            ]
        else:
            indices = [
                i
                for i, score in enumerate(baseline)
                if score < self.upper
                or nuisance_context(rows[i]["features"])
                or (policy["mode"] == "disagreement" and score <= policy["ceiling"])
            ]
        scores = self.refinement.score_many([rows[i] for i in indices])
        result = list(baseline)
        for i, score in zip(indices, scores, strict=True):
            if (baseline[i] < self.upper and score >= self.refinement.upper) or (
                baseline[i] >= self.upper and score < self.refinement.lower
            ):
                result[i] = decision_score(score, self.refinement.upper)
        return result

    def refine_groups(self, rows, baseline):
        """Use one page per batch; explicit source fields partition offline datasets."""
        policy = self.refinement_policy
        chrome = [
            structural_chrome(row["features"], policy["title_override"]) for row in rows
        ]
        indices = [
            i
            for i, score in enumerate(baseline)
            if not chrome[i] or score >= self.upper
        ]
        expert = self.refinement.score_many([rows[i] for i in indices])
        keys = [
            None if excluded else group_key(row)
            for row, excluded in zip(rows, chrome, strict=True)
        ]
        seeded, cold = defaultdict(set), defaultdict(set)
        for i, score in zip(indices, expert, strict=True):
            key = keys[i]
            if key is None:
                continue
            url = rows[i]["url"]
            if (
                baseline[i] >= policy["seed_threshold"]
                and len(seeded[key]) < policy["min_seed_urls"]
            ):
                seeded[key].add(url)
            if (
                policy["cold_group"]
                and score >= policy["cold_threshold"]
                and len(cold[key]) < policy["min_cold_urls"]
            ):
                cold[key].add(url)
        result = list(baseline)
        for i, score in zip(indices, expert, strict=True):
            if baseline[i] >= self.upper:
                if chrome[i] and score < self.refinement.lower:
                    result[i] = decision_score(score, self.refinement.upper)
                continue
            key = keys[i]
            supported = key is not None and (
                len(seeded[key]) >= policy["min_seed_urls"]
                or (
                    policy["cold_group"]
                    and score >= policy["cold_threshold"]
                    and len(cold[key]) >= policy["min_cold_urls"]
                )
            )
            if supported and score >= self.refinement.upper:
                result[i] = decision_score(score, self.refinement.upper)
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
