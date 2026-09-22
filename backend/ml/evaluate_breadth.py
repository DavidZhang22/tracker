"""Freeze reviewed public-listing features and evaluate models offline by website."""

import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]

from bs4 import BeautifulSoup
from evaluate_extraction_audit import memory_usage, parser_identity
from evaluation import metrics

from app.tracker.cascade_model import load_cascade_model
from app.tracker.context_model import load_context_model
from app.tracker.errors import DiscoveryError
from app.tracker.link_context import context_candidates
from app.tracker.link_model import FEATURES, load_model
from app.tracker.native_model import kernel
from app.tracker.parser import parse_page
from app.tracker.urls import canonical_url, canonical_url_cache
from ml.artifacts import write_text


def target(href, base):
    try:
        return canonical_url(href, base)
    except (DiscoveryError, TypeError, ValueError):
        return None


def annotated_urls(soup, source, selector):
    base = source.get("final_url", source["url"])
    pattern = source.get("positive_path") if selector == "positive_selector" else None
    return {
        url
        for node in soup.select(source.get(selector, "a[href]"))
        if (url := target(node.get("href"), base))
        and (not pattern or re.search(pattern, urlsplit(url).path))
    }


def load_capture(source):
    path = (ROOT / source["file"]).resolve()
    if not path.is_relative_to((ROOT / "data").resolve()):
        raise ValueError("Capture is outside backend/data")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("Capture hash mismatch: " + source["id"])
    return raw.decode("utf8")


def checked_scope(soup, source):
    if not source.get("positive_selector") or not source.get("expected_urls"):
        raise ValueError("Source needs a reviewed, nonempty scope: " + source["id"])
    expected = annotated_urls(soup, source, "positive_selector")
    frozen = set(source["expected_urls"])
    if expected != frozen:
        raise ValueError(
            f"Frozen scope differs from selectors: {source['id']} ({len(expected)} vs {len(frozen)})"
        )
    ignored = (
        annotated_urls(soup, source, "ignore_selector")
        if source.get("ignore_selector")
        else set()
    )
    if "ignored_urls" in source and set(source["ignored_urls"]) != ignored:
        raise ValueError(
            "Frozen auxiliary scope differs from selectors: " + source["id"]
        )
    if expected & ignored:
        raise ValueError("Expected and auxiliary scopes overlap: " + source["id"])
    return expected, ignored


def score_sets(expected, actual):
    tp, fp, fn = len(expected & actual), len(actual - expected), len(expected - actual)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return dict(
        expected=len(expected),
        correct=tp,
        unwanted=fp,
        missing=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(2 * precision * recall / max(precision + recall, 1e-12), 4),
    )


def aggregate(pages):
    tp = sum(p["correct"] for p in pages)
    fp = sum(p["unwanted"] for p in pages)
    fn = sum(p["missing"] for p in pages)
    precision, recall = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return dict(
        pages=len(pages),
        expected=tp + fn,
        correct=tp,
        unwanted=fp,
        missing=fn,
        precision=round(precision, 4),
        recall=round(recall, 4),
        micro_f1=round(2 * precision * recall / max(precision + recall, 1e-12), 4),
        source_macro_f1=round(sum(p["f1"] for p in pages) / max(1, len(pages)), 4),
        complete_pages=sum(p["missing"] == 0 for p in pages),
        clean_complete_pages=sum(p["missing"] == p["unwanted"] == 0 for p in pages),
        zero_recall_pages=sum(p["correct"] == 0 for p in pages),
    )


def frozen_models():
    legacy, context, cascade = load_model(), load_context_model(), load_cascade_model()
    if not all((legacy, context, cascade)):
        raise ValueError("Bundled model could not load")
    return {
        "legacy": (
            legacy,
            lambda rows: [legacy.score(r["features"][: len(FEATURES)]) for r in rows],
        ),
        "context": (context, context.score_many),
        "deep_alone": (cascade.deep, cascade.deep.score_many),
        "cascade": (cascade, cascade.score_many),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5:
        parser.error("Use 1–5 repetitions")
    os.environ["TRACKER_LINK_MODEL"] = "cascade"
    sources = [
        row
        for path in args.manifest
        for row in json.loads(path.read_text(encoding="utf8"))
    ]
    reviewed = [
        r
        for r in sources
        if r.get("status") == "captured"
        and r.get("expected_urls")
        and r.get("positive_selector")
        and (not args.only or r["id"] in args.only)
    ]
    families = [r["site_family"] for r in reviewed]
    if len(families) != len(set(families)):
        raise ValueError("Duplicate website families in new evaluation")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.dataset.parent.mkdir(parents=True, exist_ok=True)
    models = frozen_models()
    all_rows = []
    scores_by_model = {name: [] for name in models}
    report = dict(
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        model_mode="cascade",
        environment={
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        timing_scope="Offline local parser and model replay; no source network latency. Feature timing excludes the initial BeautifulSoup construction and includes scope validation.",
        native_kernel_available=kernel() is not None,
        network_requests=0,
        repeats=args.repeats,
        pages={},
        models={},
        labels="Assistant-reviewed source scopes, not independent human gold. No model training or threshold tuning.",
        scope="Single captured listings, not entire archives or a live discovery/API/browser benchmark.",
    )
    for source in reviewed:
        html = load_capture(source)
        base = source.get("final_url", source["url"])
        soup = BeautifulSoup(html, "html.parser")
        start = time.perf_counter()
        try:
            with canonical_url_cache():
                expected, ignored = checked_scope(soup, source)
                candidates = list(context_candidates(soup, base))
                rows = [
                    dict(
                        source_id=source["id"],
                        source=base,
                        site_family=source["site_family"],
                        split="test",
                        sector=source["sector"],
                        layout=source["layout"],
                        origin="captured-public-listing",
                        url=r["url"],
                        label=int(r["url"] in expected),
                        features=r["features"],
                        tokens=r["tokens"],
                    )
                    for r in candidates
                    if r["url"] not in ignored
                ]
        finally:
            soup.decompose()
        feature_seconds = time.perf_counter() - start
        candidate_urls = {r["url"] for r in rows}
        per_model = {}
        for name, (model, run) in models.items():
            times = []
            for _ in range(args.repeats):
                start = time.perf_counter()
                probabilities = run(rows)
                times.append(time.perf_counter() - start)
            scores_by_model[name].extend(probabilities)
            per_model[name] = dict(
                model_id=model.model_id,
                threshold=model.upper,
                median_seconds=statistics.median(times),
                metrics=metrics(rows, [p >= model.upper for p in probabilities]),
            )
        elapsed = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            scan, pages, feeds = parse_page(html, base)
            elapsed.append(time.perf_counter() - start)

        def normalize(urls, origin=base):
            return {parser_identity(u, origin) for u in urls}

        expected_parsed, ignored_parsed = normalize(expected), normalize(ignored)
        actual = normalize(e.url for e in scan.entries)
        scored = actual - ignored_parsed
        page = dict(
            source=base,
            site_family=source["site_family"],
            sector=source["sector"],
            layout=source["layout"],
            capture_sha256=source["sha256"],
            capture_bytes=source["bytes"],
            scope_sha256=hashlib.sha256(
                json.dumps(sorted(expected)).encode()
            ).hexdigest(),
            **score_sets(expected_parsed, scored),
            auxiliary_scope_sha256=hashlib.sha256(
                json.dumps(sorted(ignored)).encode()
            ).hexdigest(),
            auxiliary_urls=len(ignored),
            candidates=len(rows),
            candidate_unique_urls=len(candidate_urls),
            expected_in_candidates=len(expected & candidate_urls),
            feature_seconds=feature_seconds,
            median_seconds=statistics.median(elapsed),
            dated_entries=sum(bool(e.published_at) for e in scan.entries),
            accepted_auxiliary=len(actual & ignored_parsed),
            missing_examples=sorted(expected_parsed - scored)[:8],
            unwanted_examples=sorted(scored - expected_parsed)[:8],
            models=per_model,
            output_sha256=hashlib.sha256(
                json.dumps([asdict(scan), pages, feeds], sort_keys=True).encode()
            ).hexdigest(),
        )
        report["pages"][source["id"]] = page
        all_rows.extend(rows)
        write_text(args.output, json.dumps(report, indent=2) + "\n", encoding="utf8")
        print(
            source["id"],
            f"{page['correct']}/{page['expected']}",
            "unwanted",
            page["unwanted"],
            "seconds",
            round(page["median_seconds"], 3),
            flush=True,
        )
    if not reviewed:
        raise ValueError("No reviewed captures to evaluate")
    for name, (model, _) in models.items():
        report["models"][name] = dict(
            model_id=model.model_id,
            threshold=model.upper,
            metrics=metrics(
                all_rows, [p >= model.upper for p in scores_by_model[name]]
            ),
            sum_source_median_seconds=sum(
                p["models"][name]["median_seconds"] for p in report["pages"].values()
            ),
        )
    report["summary"] = aggregate(list(report["pages"].values()))
    report["by_sector"] = {
        sector: aggregate(
            [p for p in report["pages"].values() if p["sector"] == sector]
        )
        for sector in sorted({p["sector"] for p in report["pages"].values()})
    }
    report["feature_rows"] = len(all_rows)
    report["memory"] = memory_usage()
    args.dataset.write_text(
        "".join(
            json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n"
            for row in all_rows
        ),
        encoding="utf8",
        newline="\n",
    )
    report["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    write_text(args.output, json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report["summary"]), flush=True)


if __name__ == "__main__":
    main()
