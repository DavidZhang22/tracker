"""Offline fact-recovery and neighboring-record leakage measurements."""

import argparse
import json
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup

from app.tracker.parser import parse_page
from app.tracker.record_context import FEATURES, RecordContext, predict, snippets
from app.tracker.urls import canonical_url
from ml import artifacts
from ml.context_expansion.data import contains_fact, expanded, load_records, select_one


def facts(text, spec):
    required = spec["required"]
    found = [fact for fact in required if contains_fact(text, fact)]
    leaked = [fact for fact in spec.get("forbidden", []) if contains_fact(text, fact)]
    return {
        "found": found,
        "missing": [f for f in required if f not in found],
        "leaked": leaked,
    }


def _inside(node, roots):
    return node is None or any(
        node is root or any(parent is root for parent in node.parents) for root in roots
    )


def within_boundary(node, neighbor, roots):
    return _inside(node, roots) and _inside(neighbor, roots)


def annotated_anchors(soup, record):
    for spec in record["anchors"]:
        anchor = select_one(soup, spec["selector"])
        roots = [select_one(soup, spec["boundary_selector"])]
        if spec.get("neighbor_selector"):
            roots.append(select_one(soup, spec["neighbor_selector"]))
        yield anchor, spec, roots


def candidate_rows(record):
    soup = BeautifulSoup(record["html"], "html.parser")
    context = RecordContext(soup)
    for index, (anchor, spec, roots) in enumerate(annotated_anchors(soup, record)):
        for node, neighbor, features in context.regions(anchor):
            text = snippets(node) + (
                " " + snippets(neighbor) if neighbor is not None else ""
            )
            result = facts(text, spec)
            yield {
                "source": record["id"],
                "family": record["family"],
                "split": record["split"],
                "anchor": record["id"] + ":" + str(index),
                "original_anchor": record.get("synthetic", {}).get(
                    "parent_id", record["id"]
                )
                + ":"
                + str(index),
                "features": features,
                "label": int(
                    node.name not in {"html", "body", "main"}
                    and not result["missing"]
                    and not result["leaked"]
                    and within_boundary(node, neighbor, roots)
                ),
                "synthetic": bool(record.get("synthetic")),
            }


def family_weights(rows):
    # Equal family/original-anchor weight; variants and candidate counts cannot
    # multiply a source's influence on the fitted boundary model.
    families = defaultdict(set)
    variants = defaultdict(set)
    candidates = Counter()
    for row in rows:
        original = row.get("original_anchor", row.get("anchor", row["family"]))
        variant = row.get("anchor", original)
        families[row["family"]].add(original)
        variants[original].add(variant)
        candidates[variant] += 1
    result = []
    for row in rows:
        original = row.get("original_anchor", row.get("anchor", row["family"]))
        variant = row.get("anchor", original)
        result.append(
            1
            / len(families[row["family"]])
            / len(variants[original])
            / candidates[variant]
        )
    scale = len(rows) / max(sum(result), 1)
    return [value * scale for value in result]


def summarize(rows):
    count = len(rows)
    pipeline = [r["pipeline"] for r in rows if r.get("pipeline") is not None]
    pipeline_summary = (
        {
            "pipeline": {
                "present": sum(r["present"] for r in pipeline),
                "complete_fact_recovery": sum(
                    r["present"] and not r["missing"] for r in pipeline
                )
                / max(len(pipeline), 1),
                "neighbor_leakage": sum(bool(r["leaked"]) for r in pipeline)
                / max(len(pipeline), 1),
            }
        }
        if pipeline
        else {}
    )
    return {
        **pipeline_summary,
        "anchors": count,
        "required_fact_recall": sum(len(r["found"]) for r in rows)
        / max(sum(len(r["found"]) + len(r["missing"]) for r in rows), 1),
        "complete_fact_recovery": sum(not r["missing"] for r in rows) / max(count, 1),
        "neighbor_leakage": sum(bool(r["leaked"]) for r in rows) / max(count, 1),
        "boundary_accuracy": sum(r["within_boundary"] for r in rows) / max(count, 1),
        "correct_records": sum(
            not r["missing"] and not r["leaked"] and r["within_boundary"] for r in rows
        ),
        "candidate_coverage": sum(r["candidate_covered"] for r in rows) / max(count, 1),
    }


def evaluate(records, model=None, *, pipeline=True):
    rows, elapsed = [], []
    for record in records:
        started = time.perf_counter()
        soup = BeautifulSoup(record["html"], "html.parser")
        context = RecordContext(soup)
        if model is not None:
            if model["features"] != list(FEATURES):
                raise ValueError(
                    "Candidate model feature schema does not match runtime"
                )
            context.model = model
            context.score = lru_cache(maxsize=2048)(
                lambda values: predict(model, values)
            )
        pipeline_entries = (
            {
                entry.url: entry
                for entry in parse_page(record["html"], record["source_url"])[0].entries
            }
            if pipeline
            else {}
        )
        candidate_count = 0
        for index, (anchor, spec, roots) in enumerate(annotated_anchors(soup, record)):
            covered = False
            for node, neighbor, _ in context.regions(anchor):
                candidate_count += 1
                text = snippets(node) + (
                    " " + snippets(neighbor) if neighbor is not None else ""
                )
                result = facts(text, spec)
                covered |= (
                    node.name not in {"html", "body", "main"}
                    and not result["missing"]
                    and not result["leaked"]
                    and within_boundary(node, neighbor, roots)
                )
            text = context.text(anchor)
            node, neighbor = context.region(anchor)
            entry = pipeline_entries.get(
                canonical_url(spec["href"], record["source_url"])
            )
            pipeline_details = (
                {
                    "present": entry is not None,
                    **facts(
                        " ".join((entry.context, entry.summary)) if entry else "", spec
                    ),
                }
                if pipeline
                else None
            )
            rows.append(
                {
                    "source": record["id"],
                    "family": record["family"],
                    "split": record["split"],
                    "anchor": index,
                    "href": spec["href"],
                    **facts(text, spec),
                    "within_boundary": within_boundary(node, neighbor, roots),
                    "candidate_covered": bool(covered),
                    "pipeline": pipeline_details,
                }
            )
        elapsed.append(
            {
                "source": record["id"],
                "seconds": time.perf_counter() - started,
                "candidates": candidate_count,
            }
        )
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["family"]].append(row)
    return {
        "pipeline_note": "Full parser replay uses active application model; --model overrides record-region evaluation only.",
        "model_id": model["model_id"]
        if model
        else (context.model or {}).get("model_id")
        if records
        else None,
        "summary": summarize(rows),
        "by_family": {
            family: summarize(values) for family, values in sorted(grouped.items())
        },
        "by_split": {
            split: summarize([r for r in rows if r["split"] == split])
            for split in sorted({r["split"] for r in rows})
        },
        "timing": {
            "seconds": sum(r["seconds"] for r in elapsed),
            "pages": elapsed,
            "note": "Includes fact evaluation and coverage checks; not production latency.",
        },
        "records": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--augment", action="store_true")
    args = parser.parse_args()
    records = load_records(args.dataset)
    if args.augment:
        records = expanded(records)
    report = evaluate(records, artifacts.read_json(args.model) if args.model else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.write_text(args.output, json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
