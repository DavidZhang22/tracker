"""Replay full captured pages against frozen and current parsers without HTTP."""

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

os.environ["TRACKER_LINK_MODEL"] = "cascade"

from bs4 import BeautifulSoup

from app.tracker import parser as current_parser
from app.tracker import record_context as current_context
from app.tracker.context_model import active_context_model
from app.tracker.urls import canonical_url
from ml import artifacts
from ml.context_expansion.evaluate import contains_fact, facts

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_baseline(directory):
    def module(name, path):
        spec = importlib.util.spec_from_file_location("app.tracker." + name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load baseline module: {path}")
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = loaded
        spec.loader.exec_module(loaded)
        return loaded

    context = module(
        "_context_benchmark_baseline_record_context", directory / "record_context.py"
    )
    context.MODEL_PATH = directory / "record-context-model.json"
    context.load_record_model.cache_clear()
    parser = module("_context_benchmark_baseline_parser", directory / "parser.py")
    parser.RecordContext = context.RecordContext
    if context.load_record_model() is None:
        raise ValueError("Frozen record-context model did not load")
    return parser


def summarize(rows):
    eligible = [row for row in rows if not row["ambiguous_source_url"]]
    required = sum(len(row["found"]) + len(row["missing"]) for row in eligible)
    return {
        "annotated_targets": len(rows),
        "scored_targets": len(eligible),
        "excluded_shared_source_urls": len(rows) - len(eligible),
        "targets_present": sum(row["present"] for row in eligible),
        "required_facts": required,
        "found_facts": sum(len(row["found"]) for row in eligible),
        "required_fact_recall": sum(len(row["found"]) for row in eligible)
        / max(required, 1),
        "records_with_neighbor_leakage": sum(bool(row["leaked"]) for row in eligible),
        "correct_targets": sum(
            row["present"] and not row["missing"] and not row["leaked"]
            for row in eligible
        ),
    }


def score_entries(record, entries):
    by_url = defaultdict(list)
    for entry in entries:
        by_url[entry.url].append(entry)
    counts = Counter(
        canonical_url(spec["href"], record["source_url"]) for spec in record["anchors"]
    )
    rows = []
    for index, spec in enumerate(record["anchors"]):
        url = canonical_url(spec["href"], record["source_url"])
        matched = by_url.get(url, [])
        text = " ".join(
            " ".join((entry.context or "", entry.summary or "")) for entry in matched
        )
        rows.append(
            {
                "family": record["family"],
                "split": record["split"],
                "anchor": index,
                "title": spec["title"],
                "url": url,
                "present": bool(matched),
                "ambiguous_source_url": counts[url] > 1,
                "matching_entries": len(matched),
                **facts(text, spec),
            }
        )
    return rows


def dining_check(html, source, entries):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".hall-card")
    foods = {
        id(card): [node.get_text(" ", strip=True) for node in card.select(".food-name")]
        for card in cards
    }
    by_url = {entry.url: entry for entry in entries}
    card_urls = {
        id(card): canonical_url(card.select_one("a[href]")["href"], source)
        for card in cards
        if card.select_one("a[href]") is not None
    }
    rows = []
    for card in cards:
        anchor = card.select_one("a[href]")
        if anchor is None:
            continue
        url = canonical_url(anchor["href"], source)
        entry = by_url.get(url)
        text = " ".join((entry.context, entry.summary)) if entry else ""
        expected = foods[id(card)]
        own = [
            food
            for key, values in foods.items()
            if card_urls.get(key) == url
            for food in values
        ]
        foreign = sorted(
            {
                food
                for key, values in foods.items()
                if key != id(card) and card_urls.get(key) != url
                for food in values
                if not any(contains_fact(allowed, food) for allowed in own)
            }
        )
        rows.append(
            {
                "hall": anchor.get_text(" ", strip=True),
                "url": url,
                "present": entry is not None,
                "expected_food_count": len(expected),
                "found_foods": [food for food in expected if contains_fact(text, food)],
                "missing_foods": [
                    food for food in expected if not contains_fact(text, food)
                ],
                "other_hall_foods": [
                    food for food in foreign if contains_fact(text, food)
                ],
                "context_characters": len(text),
                "shared_url_halls": [
                    other.select_one("a[href]").get_text(" ", strip=True)
                    for other in cards
                    if card_urls.get(id(other)) == url
                ],
            }
        )
    jjs = next(row for row in rows if row["hall"] == "JJ's")
    return {
        "halls": rows,
        "jjs_all_21_foods": jjs["present"]
        and jjs["expected_food_count"] == 21
        and not jjs["missing_foods"],
        "any_cross_target_food_leakage": any(row["other_hall_foods"] for row in rows),
    }


def peak_memory(parser, html, source):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    result = parser.parse_page(html, source)
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del result
    return {
        "peak_bytes": peak,
        "retained_bytes_at_return": current,
        "seconds_with_tracing": elapsed,
    }


def run(args):
    baseline = load_baseline(args.baseline)
    parsers = {"before": baseline, "after": current_parser}
    rows = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf8").splitlines()
        if line.strip()
    ]
    if args.only:
        rows = [row for row in rows if row["family"] in args.only]
        if not rows:
            raise ValueError("No dataset families match --only")
    sources = {
        row["site_family"]: row
        for row in json.loads(args.sources.read_text(encoding="utf8"))
    }
    model = active_context_model()
    if model is None or "cascade" not in type(model).__name__.lower():
        raise ValueError(f"Expected production cascade, got {type(model).__name__}")
    report = {
        "version": 1,
        "baseline_revision": "3eebc05a54ce4b8c98a7dc9fa62cb2e01a887c94",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reproduce": "cd backend && .venv/Scripts/python.exe -m ml.context_expansion.pipeline_benchmark --repeats 5",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "model_mode": os.environ["TRACKER_LINK_MODEL"],
            "concurrent_load_note": args.concurrent_load_note,
            "context_model_class": type(model).__name__,
        },
        "inputs": {
            "dataset_sha256": sha(args.dataset),
            "sources_sha256": sha(args.sources),
            "baseline_parser_sha256": sha(args.baseline / "parser.py"),
            "baseline_record_context_sha256": sha(args.baseline / "record_context.py"),
            "baseline_record_model_sha256": sha(
                args.baseline / "record-context-model.json"
            ),
            "current_parser_sha256": sha(current_parser.__file__),
            "current_record_context_sha256": sha(current_context.__file__),
            "current_record_model_sha256": sha(current_context.MODEL_PATH),
            "shared_page_context_sha256": sha(ROOT / "app/tracker/page_context.py"),
            "link_cascade_model_sha256": sha(
                ROOT / "app/tracker/link-cascade-model.json"
            ),
        },
        "protocol": {
            "network_requests": 0,
            "warmup_runs_per_parser_per_page": 1,
            "timed_runs_per_parser_per_page": args.repeats,
            "large_page_exception": {
                "above_bytes": args.large_page_bytes,
                "single_cold_parse_per_parser": True,
                "timeout_seconds": args.large_page_timeout,
                "excluded_from_warm_latency_sum": True,
            },
            "order": "Alternate before/after first on each repetition; collect cyclic garbage before each timed parse outside measurement.",
            "fact_text": "Entry.context plus Entry.summary; title alone does not count as detail recovery.",
            "shared_target_policy": "Multiple annotated DOM anchors with the same canonical href are reported but excluded from aggregate quality. SQLite rewrites placeholder hrefs in JavaScript; raw href identity cannot distinguish those records.",
            "memory": "Separate one-run LionDine tracemalloc peak after warmup. Python allocations only, excluding native allocations/RSS. Traced elapsed time is not used for latency comparisons.",
        },
        "limitations": [
            "Small targeted annotation set, not web-wide accuracy. Full-page quality is scored only at the annotated targets, not every extracted link.",
            "Local Windows CPU replay excludes fetching, decompression, networking, server concurrency, database writes and browser rendering.",
            "Baseline freezes parser.py, record_context.py and its model. Other imported dependency modules and cascade weights are shared with current code; this measures the changed parser/context code in the same environment.",
            "Timing includes pure parse/model work with model caches warm; cache hits and allocator reuse can affect very short pages.",
            "LionDine includes 21 JJ's foods and 34 Diana foods. Diana and Hewitt share one outbound URL. Hall food coverage is reported separately; leak checks exclude co-records sharing the same target, since a grouped entry may legitimately retain both named sections.",
            "A source family held out from context training may have appeared in older link-classification training. Results do not establish unseen-domain performance of the entire cascade.",
        ],
        "pages": [],
    }
    all_scores = {name: [] for name in parsers}
    lion = None
    for record in rows:
        capture = sources[record["family"]]
        path = ROOT / capture["file"]
        if capture.get("status") != "captured" or not path.is_file():
            raise ValueError(f"Missing full capture for {record['family']}")
        if sha(path) != record["source_sha256"]:
            raise ValueError(f"Capture fingerprint mismatch: {path}")
        html = path.read_text(encoding="utf8")
        source = record["source_url"]
        if len(html.encode("utf8")) > args.large_page_bytes:
            large = {
                "id": record["id"],
                "family": record["family"],
                "split": record["split"],
                "capture_bytes": path.stat().st_size,
                "capture_sha256": sha(path),
                "large_page_single_run": True,
            }
            for name in parsers:
                command = [
                    sys.executable,
                    "-m",
                    "ml.context_expansion.pipeline_benchmark",
                    "--worker",
                    name,
                    "--baseline",
                    str(args.baseline),
                    "--dataset",
                    str(args.dataset),
                    "--sources",
                    str(args.sources),
                    "--only",
                    record["family"],
                ]
                try:
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf8",
                        timeout=args.large_page_timeout,
                        check=True,
                    )
                    value = json.loads(completed.stdout)
                    large[name] = value
                    all_scores[name].extend(value["targets"])
                except subprocess.TimeoutExpired:
                    large[name] = {
                        "status": "timeout",
                        "timeout_seconds": args.large_page_timeout,
                        "timing_note": "No latency or quality result; exceeded one-parse resource budget.",
                    }
                except (subprocess.CalledProcessError, ValueError) as exc:
                    large[name] = {"status": "failed", "error": str(exc)[:300]}
            report["pages"].append(large)
            print(
                json.dumps(
                    {
                        "family": record["family"],
                        "large_page": {
                            name: large[name].get("status", "completed")
                            for name in parsers
                        },
                    }
                ),
                flush=True,
            )
            continue
        page = {
            "id": record["id"],
            "family": record["family"],
            "split": record["split"],
            "capture_bytes": path.stat().st_size,
            "capture_sha256": sha(path),
        }
        samples = {name: [] for name in parsers}
        for name, parser in parsers.items():
            scan = parser.parse_page(html, source)[0]
            score = score_entries(record, scan.entries)
            all_scores[name].extend(score)
            page[name] = {
                "entry_count": len(scan.entries),
                "quality": summarize(score),
                "targets": score,
            }
            if record["family"] == "liondine.com":
                page[name]["dining_check"] = dining_check(html, source, scan.entries)
                lion = (html, source)
        for iteration in range(args.repeats):
            names = ("before", "after") if iteration % 2 == 0 else ("after", "before")
            for name in names:
                gc.collect()
                started = time.perf_counter()
                result = parsers[name].parse_page(html, source)
                samples[name].append(time.perf_counter() - started)
                del result
        for name in parsers:
            page[name]["timing_seconds"] = {
                "median": statistics.median(samples[name]),
                "min": min(samples[name]),
                "max": max(samples[name]),
                "samples": samples[name],
            }
        page["median_latency_ratio_after_before"] = (
            page["after"]["timing_seconds"]["median"]
            / page["before"]["timing_seconds"]["median"]
        )
        report["pages"].append(page)
        print(
            json.dumps(
                {
                    "family": record["family"],
                    "before_ms": round(
                        page["before"]["timing_seconds"]["median"] * 1000, 2
                    ),
                    "after_ms": round(
                        page["after"]["timing_seconds"]["median"] * 1000, 2
                    ),
                }
            ),
            flush=True,
        )
    report["summary"] = {
        name: {
            **summarize(scores),
            "sum_page_median_seconds": sum(
                page[name]["timing_seconds"]["median"]
                for page in report["pages"]
                if not page.get("large_page_single_run")
            ),
            "by_split": {
                split: summarize([row for row in scores if row["split"] == split])
                for split in sorted({row["split"] for row in scores})
            },
        }
        for name, scores in all_scores.items()
    }
    if lion is not None:
        report["liondine_python_memory"] = {
            name: peak_memory(parser, *lion) for name, parser in parsers.items()
        }
    report["protocol"]["attempted_full_captures"] = len(report["pages"])
    report["protocol"]["completed_page_pairs"] = sum(
        all(page[name].get("status", "completed") == "completed" for name in parsers)
        for page in report["pages"]
    )
    report["protocol"]["source_files_unchanged_during_run"] = all(
        report["inputs"][key] == sha(path)
        for key, path in (
            ("current_parser_sha256", current_parser.__file__),
            ("current_record_context_sha256", current_context.__file__),
            ("shared_page_context_sha256", ROOT / "app/tracker/page_context.py"),
            ("current_record_model_sha256", current_context.MODEL_PATH),
            ("link_cascade_model_sha256", ROOT / "app/tracker/link-cascade-model.json"),
        )
    )
    report["protocol"]["record_model_unchanged_from_baseline"] = (
        report["inputs"]["baseline_record_model_sha256"]
        == report["inputs"]["current_record_model_sha256"]
    )
    artifacts.write_text(
        args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps({"output": str(args.output), "summary": report["summary"]}),
        flush=True,
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=ROOT / "data/context-expansion/baseline"
    )
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "ml/datasets/context-expansion-v1.jsonl"
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=ROOT / "ml/datasets/context-expansion-v1-sources.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "ml/reports/context-expansion-pipeline-v1.json.gz",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--large-page-bytes", type=int, default=500_000)
    parser.add_argument("--large-page-timeout", type=float, default=45)
    parser.add_argument("--worker", choices=("before", "after"), help=argparse.SUPPRESS)
    parser.add_argument(
        "--only", nargs="+", help="Optional source families for an isolated repeat"
    )
    parser.add_argument(
        "--concurrent-load-note",
        default="",
        help="Disclose other workloads during measurement",
    )
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("Use 1 to 20 measured repetitions")
    if args.worker:
        records = [
            json.loads(line)
            for line in args.dataset.read_text(encoding="utf8").splitlines()
            if line.strip()
        ]
        record = next(row for row in records if row["family"] == args.only[0])
        source = next(
            row
            for row in json.loads(args.sources.read_text(encoding="utf8"))
            if row["site_family"] == record["family"]
        )
        html = (ROOT / source["file"]).read_text(encoding="utf8")
        implementation = (
            load_baseline(args.baseline) if args.worker == "before" else current_parser
        )
        started = time.perf_counter()
        scan = implementation.parse_page(html, record["source_url"])[0]
        elapsed = time.perf_counter() - started
        scores = score_entries(record, scan.entries)
        print(
            json.dumps(
                {
                    "status": "completed",
                    "single_cold_parse_seconds": elapsed,
                    "entry_count": len(scan.entries),
                    "quality": summarize(scores),
                    "targets": scores,
                }
            )
        )
    else:
        run(args)


if __name__ == "__main__":
    main()
