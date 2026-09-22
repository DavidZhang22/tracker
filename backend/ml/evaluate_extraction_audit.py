"""Offline, source-scoped audit of every deployed extraction model and full parser."""

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(os.environ.get("TRACKER_ML_APP_ROOT", ROOT))
sys.path[:0] = [str(APP_ROOT), str(ROOT), str(ROOT / "ml")]
from bs4 import BeautifulSoup
from evaluation import metrics

from app.tracker.cascade_model import load_cascade_model
from app.tracker.context_model import NUMERIC_FEATURES, load_context_model
from app.tracker.keywords import normalize
from app.tracker.link_context import context_candidates
from app.tracker.link_model import FEATURES, load_model
from app.tracker.native_model import kernel
from app.tracker.parser import parse_page
from app.tracker.record_context import RecordContext, load_record_model, predict
from app.tracker.urls import DiscoveryError, canonical_url, canonical_url_cache
from ml.artifacts import read_json, write_text

MANIFEST = ROOT / "ml/datasets/extraction-audit-sources.json"
DATASET = ROOT / "ml/datasets/extraction-audit-dataset.jsonl"
RECORDS = ROOT / "ml/datasets/extraction-audit-records.jsonl"


def target(anchor, source):
    try:
        return canonical_url(anchor.get("href"), source)
    except (DiscoveryError, ValueError, TypeError):
        return None


def scope(soup, source):
    import re

    selected = soup.select(source["positive_selector"])
    pattern = source.get("positive_path")
    expected = {
        url
        for a in selected
        if (url := target(a, source["url"]))
        and (not pattern or re.search(pattern, urlsplit(url).path))
    }
    ignored = (
        {
            url
            for a in soup.select(source["ignore_selector"])
            if (url := target(a, source["url"])) and url not in expected
        }
        if source.get("ignore_selector")
        else set()
    )
    return expected, ignored


def sample(values, maximum=24):
    if len(values) <= maximum:
        return values
    return [values[i * (len(values) - 1) // (maximum - 1)] for i in range(maximum)]


def real_records(soup, source, expected):
    records = []
    for node in soup.select(source["record_selector"]):
        anchor = (
            node
            if source["anchor_selector"] == ":self"
            else node.select_one(source["anchor_selector"])
        )
        if anchor is None or target(anchor, source["url"]) not in expected:
            continue
        metadata = (
            node.select_one(source["metadata_selector"])
            if source.get("metadata_selector")
            else None
        )
        records.append((anchor, metadata))
    return sample(records)


def region_evidence(regions, own, expected, source, metadata):
    nodes = {
        id(node)
        for region in regions
        if region is not None
        for node in [region, *region.descendants]
    }
    links = {
        url
        for region in regions
        if region is not None
        for a in ([region] if region.name == "a" else [])
        + region.find_all("a", href=True)
        if (url := target(a, source)) in expected
    }
    return dict(
        metadata=metadata is None or id(metadata) in nodes,
        contamination=bool(links - {own}),
        other_targets=len(links - {own}),
    )


def record_audit(soup, source, expected):
    context = RecordContext(soup)
    rows, cases = [], []
    for index, (anchor, metadata) in enumerate(real_records(soup, source, expected)):
        own = target(anchor, source["url"])
        for node, neighbor, features in context.regions(anchor):
            evidence = region_evidence(
                (node, neighbor), own, expected, source["url"], metadata
            )
            rows.append(
                dict(
                    source_id=source["id"],
                    case=index,
                    source=source["url"],
                    label=int(
                        evidence["metadata"]
                        and not evidence["contamination"]
                        and node.name not in {"html", "body", "main"}
                    ),
                    features=features,
                )
            )
        chosen = context.region(anchor)
        evidence = region_evidence(chosen, own, expected, source["url"], metadata)
        text = context.text(anchor)
        marker = metadata.get_text(" ", strip=True) if metadata else ""
        cases.append(
            dict(
                source_id=source["id"],
                url=own,
                has_metadata=metadata is not None,
                metadata_in_region=evidence["metadata"],
                metadata_in_text=not marker or normalize(marker) in normalize(text),
                contaminated=evidence["contamination"],
                other_targets=evidence["other_targets"],
                fallback_to_anchor=chosen[0] is anchor and chosen[1] is None,
            )
        )
    return rows, cases


def build():
    rows, boundaries, record_cases, inventory = [], [], [], []
    for source in json.loads(MANIFEST.read_text(encoding="utf8")):
        if source["status"] != "captured":
            continue
        raw = (ROOT / source["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError("Capture changed: " + source["id"])
        soup = BeautifulSoup(raw.decode("utf8"), "html.parser")
        try:
            with canonical_url_cache():
                expected, ignored = scope(soup, source)
                candidates = list(context_candidates(soup, source["url"]))
                for row in candidates:
                    if row["url"] in ignored:
                        continue
                    rows.append(
                        dict(
                            source_id=source["id"],
                            source=source["url"],
                            split="test",
                            origin="captured-public-listing",
                            url=row["url"],
                            label=int(row["url"] in expected),
                            features=row["features"],
                            tokens=row["tokens"],
                        )
                    )
                region_rows, cases = record_audit(soup, source, expected)
                boundaries.extend(region_rows)
                record_cases.extend(cases)
                inventory.append(
                    dict(
                        id=source["id"],
                        expected=sorted(expected),
                        ignored_auxiliary=len(ignored),
                        candidates=len(candidates),
                        sampled_records=len(cases),
                    )
                )
        finally:
            soup.decompose()
    DATASET.write_text(
        "".join(
            json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf8",
    )
    RECORDS.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in boundaries),
        encoding="utf8",
    )
    output = ROOT / "ml/reports/extraction-audit-records.json"
    write_text(
        output,
        json.dumps(dict(cases=record_cases, inventory=inventory), indent=2) + "\n",
        encoding="utf8",
    )
    print(
        json.dumps(
            dict(
                rows=len(rows),
                positive=sum(r["label"] for r in rows),
                boundary_rows=len(boundaries),
                sources=len(inventory),
            )
        )
    )


def score_metrics(rows, scores, threshold):
    return metrics(rows, [p >= threshold for p in scores])


def evaluate(output, repeats=3):
    rows = [
        json.loads(line) for line in DATASET.read_text(encoding="utf8").splitlines()
    ]
    load_start = time.perf_counter()
    legacy, context, cascade = load_model(), load_context_model(), load_cascade_model()
    load_seconds = time.perf_counter() - load_start
    assert legacy and context and cascade
    models = {
        "legacy": (
            legacy,
            lambda rows: [
                legacy.score(row["features"][: len(FEATURES)]) for row in rows
            ],
        ),
        "context": (context, context.score_many),
        "cascade_deep_alone": (cascade.deep, cascade.deep.score_many),
        "cascade": (cascade, cascade.score_many),
    }
    result = dict(
        rows=len(rows),
        source_count=len({r["source_id"] for r in rows}),
        native_kernel_available=kernel() is not None,
        load_seconds=load_seconds,
        models={},
    )
    scores_by_name = {}
    for name, (model, run) in models.items():
        timing = []
        for _ in range(repeats):
            start = time.perf_counter()
            scores = run(rows)
            timing.append(time.perf_counter() - start)
        scores_by_name[name] = scores
        result["models"][name] = dict(
            model_id=model.model_id,
            threshold=model.upper,
            first_seconds=timing[0],
            median_seconds=statistics.median(timing),
            metrics=score_metrics(rows, scores, model.upper),
        )
        result["models"][name]["errors"] = [
            dict(
                source_id=row["source_id"],
                url=row["url"],
                label=row["label"],
                probability=p,
            )
            for row, p in zip(rows, scores, strict=True)
            if (p >= model.upper) != bool(row["label"])
        ][:30]
    light = cascade.light.score_many(rows)
    eligible = [
        i
        for i, p in enumerate(light)
        if cascade.gate[0] < p < cascade.gate[1] and p < cascade.light.upper
    ]
    rescued = [i for i in eligible if scores_by_name["cascade"][i] >= cascade.upper]
    result["cascade_routing"] = dict(
        eligible_rows=len(eligible),
        rescued_rows=len(rescued),
        rescued_positive=sum(rows[i]["label"] for i in rescued),
        rescued_negative=sum(not rows[i]["label"] for i in rescued),
        job_table_rows=sum(
            bool(r["features"][NUMERIC_FEATURES.index("job_table")]) for r in rows
        ),
    )
    authored_path = ROOT / "tests/fixtures/extraction-audit/authored-jobs.html"
    authored_soup = BeautifulSoup(
        authored_path.read_text(encoding="utf8"), "html.parser"
    )
    authored_rows = [
        row
        | dict(
            source_id="authored-job-columns",
            source="https://careers.example/",
            split="test",
            origin="authored-layout",
            label=int(urlsplit(row["url"]).hostname == "hiring.example"),
        )
        for row in context_candidates(authored_soup, "https://careers.example/")
    ]
    authored_soup.decompose()
    result["authored_job_layout"] = dict(
        rows=len(authored_rows),
        job_table_rows=sum(
            bool(r["features"][NUMERIC_FEATURES.index("job_table")])
            for r in authored_rows
        ),
        metrics={
            name: score_metrics(authored_rows, run(authored_rows), model.upper)
            for name, (model, run) in models.items()
        },
    )
    previous = os.environ.get("TRACKER_NATIVE_MODEL")
    try:
        os.environ["TRACKER_NATIVE_MODEL"] = "off"
        result["native_parity"] = {}
        for name in ("context", "cascade_deep_alone", "cascade"):
            reference = models[name][1](rows)
            actual = scores_by_name[name]
            threshold = models[name][0].upper
            result["native_parity"][name] = dict(
                max_abs_error=max(
                    abs(a - b) for a, b in zip(actual, reference, strict=True)
                ),
                decision_changes=sum(
                    (a >= threshold) != (b >= threshold)
                    for a, b in zip(actual, reference, strict=True)
                ),
            )
    finally:
        if previous is None:
            os.environ.pop("TRACKER_NATIVE_MODEL", None)
        else:
            os.environ["TRACKER_NATIVE_MODEL"] = previous
    boundary_rows = [
        json.loads(line) for line in RECORDS.read_text(encoding="utf8").splitlines()
    ]
    boundary_model = load_record_model()
    boundary_start = time.perf_counter()
    predictions = [predict(boundary_model, r["features"]) for r in boundary_rows]
    boundary_seconds = time.perf_counter() - boundary_start
    boundary_metrics = metrics(
        [
            r | dict(url=str(r["case"]) + ":" + str(i))
            for i, r in enumerate(boundary_rows)
        ],
        [p >= boundary_model["threshold"] for p in predictions],
    )
    cases = read_json(ROOT / "ml/reports/extraction-audit-records.json")["cases"]
    result["record_context"] = dict(
        model_id=boundary_model["model_id"],
        candidate_region_metrics=boundary_metrics,
        candidate_rows=len(boundary_rows),
        candidate_seconds=boundary_seconds,
        sampled_records=len(cases),
        correct_boundaries=sum(
            c["metadata_in_region"] and not c["contaminated"] for c in cases
        ),
        contaminated_records=sum(c["contaminated"] for c in cases),
        metadata_records=sum(c["has_metadata"] for c in cases),
        metadata_in_region=sum(
            c["has_metadata"] and c["metadata_in_region"] for c in cases
        ),
        metadata_in_text=sum(
            c["has_metadata"] and c["metadata_in_text"] for c in cases
        ),
        by_source={},
    )
    for source_id in sorted({c["source_id"] for c in cases}):
        selected = [c for c in cases if c["source_id"] == source_id]
        result["record_context"]["by_source"][source_id] = dict(
            records=len(selected),
            correct=sum(
                c["metadata_in_region"] and not c["contaminated"] for c in selected
            ),
            contaminated=sum(c["contaminated"] for c in selected),
            missing_metadata=sum(
                c["has_metadata"] and not c["metadata_in_text"] for c in selected
            ),
        )
    result["memory"] = memory_usage()
    write_text(output, json.dumps(result, indent=2) + "\n", encoding="utf8")
    print(
        json.dumps(
            {
                k: v["metrics"] | {"by_source": None}
                for k, v in result["models"].items()
            },
            indent=2,
        )
    )
    print(json.dumps(result["record_context"], indent=2))


def memory_usage():
    report = {}
    peak = Path("/sys/fs/cgroup/memory.peak")
    if peak.exists():
        report["container_peak_bytes"] = int(peak.read_text())
    try:
        import resource

        report["process_peak_bytes"] = (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        )
    except ImportError:
        pass
    try:
        import psutil

        memory = psutil.Process().memory_info()
        if getattr(memory, "peak_wset", None) is not None:
            report["process_peak_bytes"] = memory.peak_wset
        report["process_final_rss_bytes"] = memory.rss
    except ImportError:
        pass
    return report


def parser_identity(url, source):
    """Same-origin http links are promoted to https by the parser, not a new URL."""
    parsed, origin = urlsplit(url), urlsplit(source)
    if (
        parsed.hostname == origin.hostname
        and origin.scheme == "https"
        and parsed.scheme == "http"
    ):
        return parsed._replace(scheme="https").geturl()
    return url


def parser_replay(output, repeats=3):
    os.environ["TRACKER_LINK_MODEL"] = "cascade"
    report = dict(repeats=repeats, pages={})
    for source in json.loads(MANIFEST.read_text(encoding="utf8")):
        if source["status"] != "captured":
            continue
        html = (ROOT / source["file"]).read_text(encoding="utf8")
        soup = BeautifulSoup(html, "html.parser")
        expected, ignored = scope(soup, source)
        expected = {parser_identity(url, source["url"]) for url in expected}
        ignored = {parser_identity(url, source["url"]) for url in ignored}
        soup.decompose()
        elapsed = []
        for _ in range(repeats):
            start = time.perf_counter()
            scan, pages, feeds = parse_page(html, source["url"])
            elapsed.append(time.perf_counter() - start)
        actual = {parser_identity(e.url, source["url"]) for e in scan.entries}
        scored = actual - ignored
        report["pages"][source["id"]] = dict(
            expected=len(expected),
            correct=len(scored & expected),
            missing=len(expected - scored),
            unwanted=len(scored - expected),
            accepted_auxiliary=len(actual & ignored),
            entries=len(scan.entries),
            median_seconds=statistics.median(elapsed),
            dates=sum(bool(e.published_at) for e in scan.entries),
            warnings=scan.warnings,
            missing_examples=sorted(expected - scored)[:8],
            unwanted_examples=sorted(scored - expected)[:8],
            output_sha256=hashlib.sha256(
                json.dumps([asdict(scan), pages, feeds], sort_keys=True).encode()
            ).hexdigest(),
        )
        print(source["id"], report["pages"][source["id"]], flush=True)
    from app.tracker.limits import MAX_LINKS

    report["max_links"] = MAX_LINKS
    report["total_median_seconds"] = sum(
        p["median_seconds"] for p in report["pages"].values()
    )
    report["memory"] = memory_usage()
    write_text(output, json.dumps(report, indent=2) + "\n", encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["build", "evaluate", "parser"])
    parser.add_argument(
        "--output", type=Path, default=ROOT / "ml/reports/extraction-audit.json"
    )
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("Use 1-10 repeats")
    if args.mode == "build":
        build()
    elif args.mode == "evaluate":
        evaluate(args.output, args.repeats)
    else:
        parser_replay(args.output, args.repeats)


if __name__ == "__main__":
    main()
