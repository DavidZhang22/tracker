"""Validation-only selection of page-group consistency for frozen link experts."""

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from train_breadth_generalization import (
    SPECS,
    explicit_chrome,
    freeze_protocol,
    frozen_baseline,
    prepare,
    summarize,
    write,
)

from app.tracker.context_model import NUMERIC_FEATURES, ContextModel
from ml.artifacts import exists, read_bytes, read_json, write_text

ROLE_NAMES = (
    "same_host",
    "semantic_title",
    "contains_heading",
    "record_primary_url",
    "record_other_primary",
    "in_tr",
    "inside_list_record",
    "image_label",
    "action_label",
)
ROLE_INDICES = tuple(NUMERIC_FEATURES.index(name) for name in ROLE_NAMES)
HEADING_INDEX = NUMERIC_FEATURES.index("heading_level")
HEX = re.compile(r"(?:[a-f0-9]{12,}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})", re.I)
DIGITS = re.compile(r"\d+")
EXTENSION = re.compile(r"\.[a-zA-Z][a-zA-Z0-9]{0,7}$")


def group_key(row):
    """Return a page-local identity, never a learned hostname feature."""
    url = row.get("url", "")
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        parts = [unquote(part).lower() for part in parsed.path.split("/") if part]
        normalized = [
            "{id}" if HEX.fullmatch(part) else DIGITS.sub("{n}", part) for part in parts
        ]
        if parts:
            suffix = EXTENSION.search(parts[-1])
            normalized[-1] = "*" + (suffix.group(0) if suffix else "")
        query = tuple(
            sorted(
                {
                    key[:80].lower()
                    for key, _ in parse_qsl(parsed.query, max_num_fields=40)
                }
            )[:16]
        )
    except (TypeError, ValueError):
        return None
    classes = tuple(
        sorted({word for word in row["tokens"] if word.startswith("c:")})[:48]
    )
    features = row["features"]
    roles = tuple(int(features[index] > 0) for index in ROLE_INDICES) + (
        round(features[HEADING_INDEX] * 6),
    )
    source = row.get("source", row.get("source_id", ""))
    return source, tuple(normalized), query, classes, roles


def group_support(
    rows, baseline, expert, chrome, *, seed_threshold, cold_threshold=0.995
):
    keys = [group_key(row) for row in rows]
    seeded = defaultdict(set)
    cold = defaultdict(set)
    for row, key, score, deep, excluded in zip(
        rows, keys, baseline, expert, chrome, strict=True
    ):
        if key is None or excluded:
            continue
        if score >= seed_threshold:
            seeded[key].add(row["url"])
        if deep >= cold_threshold:
            cold[key].add(row["url"])
    return np.array(
        [len(seeded[key]) >= 2 if key is not None else False for key in keys]
    ), np.array([len(cold[key]) >= 3 if key is not None else False for key in keys])


def predictions(baseline, expert, chrome, support, high, low):
    result = baseline >= 0.5
    result |= (baseline < 0.5) & ~chrome & support & (expert >= high)
    result &= ~((baseline >= 0.5) & chrome & (expert < low))
    return result


def main():
    first = ROOT / "ml/experiments/breadth-generalization"
    second = ROOT / "ml/experiments/breadth-generalization-v2"
    output = ROOT / "ml/experiments/breadth-generalization-v3"
    output.mkdir(parents=True, exist_ok=True)
    protocol = dict(
        method="No new model fitting. Select page-group routing for frozen experts on original validation only.",
        excluded="Original9 and external8 now disclosed regression cohorts, neither used for V3 selection. No new external holdout evaluated here.",
        original_split_sha256=hashlib.sha256(
            read_bytes(first / "protocol.json")
        ).hexdigest(),
        experts=[name for name, _, _ in SPECS]
        + ["soft-numeric-trees", "soft-numeric-neural64x32", "soft-text-neural32"],
        key=dict(
            source="source URL or source_id; missing value means the caller supplies one page per batch",
            url="Lowercase percent-decoded path, UUID or >=12 hex segments become {id}, other numeric runs become {n}, final slug becomes * retaining an alphabetic file extension <=8 chars; sorted query names, no query values or hostname",
            classes="Sorted unique c: tokens, at most48",
            roles=list(ROLE_NAMES) + ["heading_level * 6"],
        ),
        support="At least2 distinct canonical URLs accepted by baseline in same group; duplicates never create support",
        seed_thresholds=[0.5, 0.65, 0.8],
        rescue_thresholds=[0.7, 0.8, 0.9, 0.95],
        rejection_thresholds=[0.01, 0.05, 0.1],
        cold_group=[False, True],
        cold_threshold=0.995,
        cold_min_distinct_urls=3,
        cold_semantics="If enabled, unsupported group needs>=3 distinct non-chrome links with expert>=.995; cold rescues individually require .995 too.",
        chrome="Explicit semantic_navigation/in_nav/in_footer/in_aside/rel_author/rel_tag/utility_path/utility_label, overridden by semantic_title",
        selection="Preserve historical public precision/recall/macroF1 within .03 of baseline, require breadth precision/recall not worse than baseline. Maximize breadth site macroF1, then precision, historical F1; ties prefer no cold group, higher seed confidence, smaller expert.",
        precision_target=0.8,
        target_note="Reported target only; full-parser quality gates decide promotion.",
        baseline_sha256=hashlib.sha256(
            read_bytes(first / "baseline-cascade.json")
        ).hexdigest(),
    )
    path = output / "protocol.json"
    if exists(path):
        if read_json(path) != protocol:
            raise ValueError(
                "V3 routing protocol already frozen with different content"
            )
    else:
        write(path, protocol)
    breadth, _ = freeze_protocol(first)
    groups, _ = prepare(breadth)
    datasets = {
        "breadth": groups["breadth", "validation"],
        "historical_public": [
            row
            for row in groups["historical", "validation"]
            if row["origin"] == "index"
        ],
    }
    baseline = frozen_baseline(first)
    base = {k: np.array(baseline.score_many(rows)) for k, rows in datasets.items()}
    chrome = {k: explicit_chrome(rows) for k, rows in datasets.items()}
    metrics = {k: summarize(rows, base[k] >= 0.5) for k, rows in datasets.items()}
    payloads = {name: read_json(first / (name + ".json")) for name, _, _ in SPECS}
    payloads.update(
        {
            name: read_json(second / (name + ".json"))
            for name in protocol["experts"]
            if name.startswith("soft-")
        }
    )
    report = dict(
        protocol_sha256=hashlib.sha256(read_bytes(path)).hexdigest(),
        baseline=metrics,
        candidates=[],
        heldout_rows=0,
    )
    for name, payload in payloads.items():
        expert = ContextModel(payload)
        scores = {k: np.array(expert.score_many(rows)) for k, rows in datasets.items()}
        for seed in protocol["seed_thresholds"]:
            support = {
                k: group_support(
                    rows, base[k], scores[k], chrome[k], seed_threshold=seed
                )
                for k, rows in datasets.items()
            }
            for cold in protocol["cold_group"]:
                for high in protocol["rescue_thresholds"]:
                    for low in protocol["rejection_thresholds"]:
                        quality = {}
                        routing = {}
                        for key, rows in datasets.items():
                            warm_support, cold_support = support[key]
                            final_support = warm_support | (
                                cold
                                & cold_support
                                & (scores[key] >= protocol["cold_threshold"])
                            )
                            actual = predictions(
                                base[key],
                                scores[key],
                                chrome[key],
                                final_support,
                                high,
                                low,
                            )
                            quality[key] = summarize(rows, actual)
                            routing[key] = dict(
                                supported_anchors=int(sum(final_support)),
                                rescued=int(sum((base[key] < 0.5) & actual)),
                                rejected=int(sum((base[key] >= 0.5) & ~actual)),
                            )
                        b, h = quality["breadth"], quality["historical_public"]
                        hb = metrics["historical_public"]
                        bb = metrics["breadth"]
                        eligible = (
                            all(
                                h[key] >= hb[key] - 0.03
                                for key in ("precision", "recall", "macro_f1")
                            )
                            and b["precision"] >= bb["precision"]
                            and b["recall"] >= bb["recall"]
                        )
                        report["candidates"].append(
                            dict(
                                name=name,
                                high=high,
                                low=low,
                                seed_threshold=seed,
                                cold_group=cold,
                                eligible=eligible,
                                quality=quality,
                                routing=routing,
                                model_bytes=len(
                                    json.dumps(payload, separators=(",", ":"))
                                ),
                            )
                        )
        choices = [c for c in report["candidates"] if c["name"] == name]
        best = max(choices, key=selection_key)
        print(
            name,
            json.dumps(
                {
                    k: (
                        {
                            n: {m: v for m, v in q.items() if m != "by_source"}
                            for n, q in value.items()
                        }
                        if k == "quality"
                        else value
                    )
                    for k, value in best.items()
                }
            ),
            flush=True,
        )
    best = max(report["candidates"], key=selection_key)
    payload = read_json(first / "baseline-cascade.json")
    payload["model_id"] = "breadth-group-refinement-v3"
    payload["refinement"] = json.loads(json.dumps(payloads[best["name"]]))
    payload["refinement"].update(threshold=best["high"], reject_threshold=best["low"])
    payload["refinement_policy"] = dict(
        mode="group",
        title_override=True,
        seed_threshold=best["seed_threshold"],
        min_seed_urls=2,
        cold_group=best["cold_group"],
        cold_threshold=0.995,
        min_cold_urls=3,
        key_version=1,
    )
    artifact = output / "chosen-group-refinement.json"
    write_text(
        artifact, json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf8"
    )
    report["selection"] = dict(
        best,
        artifact=artifact.name,
        sha256=hashlib.sha256(read_bytes(artifact)).hexdigest(),
        bytes=len(read_bytes(artifact)),
    )
    write(output / "selection-lock.json", report["selection"])
    write(output / "validation-report.json", report)
    print(
        "V3 CHOSEN",
        json.dumps(
            {
                k: v
                for k, v in report["selection"].items()
                if k not in ("quality", "routing")
            }
        ),
        flush=True,
    )


def selection_key(candidate):
    return (
        candidate["eligible"],
        candidate["quality"]["breadth"]["macro_f1"],
        candidate["quality"]["breadth"]["precision"],
        candidate["quality"]["historical_public"]["f1"],
        not candidate["cold_group"],
        candidate["seed_threshold"],
        -candidate["model_bytes"],
    )


def rejection_variants():
    previous = ROOT / "ml/experiments/breadth-generalization-v3"
    output = ROOT / "ml/experiments/breadth-generalization-v4"
    output.mkdir(parents=True, exist_ok=True)
    source = previous / "chosen-group-refinement.json"
    protocol = dict(
        source_candidate_sha256=hashlib.sha256(read_bytes(source)).hexdigest(),
        source_protocol_sha256=hashlib.sha256(
            read_bytes(previous / "protocol.json")
        ).hexdigest(),
        reason="The group candidate failed original historical full-parser validation. Explicit chrome was not reliable enough for learned rejection.",
        fixed="Expert weights, vocabulary, grouping, seed and cold support, and rescue threshold are unchanged.",
        rejection_thresholds=[0.0, 0.01, 0.05],
        comparisons="Full-parser original ten breadth validation websites and thirteen historical validation websites only; no held-out comparisons for selection.",
        selection="Highest breadth-validation micro-F1, subject to historical micro-F1 not dropping and per-cohort precision/recall no more than two percentage points below baseline. Ties prefer the lower rejection threshold.",
        final_gate="After validation selection is frozen, first external eight disclosed regression websites must pass the same no-micro-F1-drop and <=2pp precision/recall-loss gates before the new external eight websites are scored. Each cohort stays separate.",
        heldout_protection="The second external eight websites remain unscored and must not affect variant selection.",
    )
    path = output / "protocol.json"
    if exists(path):
        if read_json(path) != protocol:
            raise ValueError("V4 rejection protocol is already frozen")
    else:
        write(path, protocol)
    generated = []
    for threshold in protocol["rejection_thresholds"]:
        payload = read_json(source)
        payload["model_id"] = f"breadth-group-v4-reject-{threshold:.2f}"
        payload["refinement"]["reject_threshold"] = threshold
        artifact = output / f"reject-{threshold:.2f}.json"
        write_text(
            artifact, json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf8"
        )
        generated.append(
            dict(
                path=artifact.name,
                reject_threshold=threshold,
                sha256=hashlib.sha256(read_bytes(artifact)).hexdigest(),
                bytes=len(read_bytes(artifact)),
            )
        )
    write(output / "candidates.json", generated)
    print(json.dumps(generated, indent=2))


if __name__ == "__main__":
    if "--rejection-variants" in sys.argv:
        rejection_variants()
    else:
        main()
