"""Summarize historical and fresh listing evaluations without counting failures as coverage."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_MANIFESTS = (
    "sources",
    "v2_sources",
    "generalization-sources",
    "cascade-sources",
    "cascade-final-sources",
    "review-sources",
    "extraction-audit-sources",
)
HISTORICAL_REPORTS = ("model-review-after", "extraction-audit-parser")
# Explicitly reviewed domain families for the pre-existing source manifests.
HISTORICAL_HOST_FAMILIES = {
    "blog.python.org": "python.org",
    "blog.rust-lang.org": "rust-lang.org",
    "blog.cloudflare.com": "cloudflare.com",
    "news.ycombinator.com": "ycombinator.com",
    "proceedings.mlr.press": "mlr.press",
    "missing.csail.mit.edu": "mit.edu",
}


def historical_sources():
    sources = {}
    for name in HISTORICAL_MANIFESTS:
        for row in json.loads(
            (ROOT / "ml/datasets" / (name + ".json")).read_text(encoding="utf8")
        ):
            sources[row["id"]] = row
    result = []
    for name in HISTORICAL_REPORTS:
        report = json.loads(
            (ROOT / "ml/reports" / (name + ".json")).read_text(encoding="utf8")
        )
        for sid, page in report["pages"].items():
            row = sources[sid]
            host = urlsplit(row["url"]).hostname.removeprefix("www.")
            result.append(
                dict(
                    id=sid,
                    url=row["url"],
                    site_family=HISTORICAL_HOST_FAMILIES.get(host, host),
                    cohort="historical",
                    sector=row.get("family", row.get("layout", "unspecified")),
                    report=f"ml/reports/{name}.json",
                    capture_sha256=page.get("capture_sha256", row.get("sha256")),
                    expected=page["expected"],
                    correct=page["correct"],
                    unwanted=page["unwanted"],
                )
            )
    return result


def validate_scope(row, page):
    if row.get("status") != "captured" or not row.get("expected_urls"):
        raise ValueError("Source has no reviewed capture: " + row["id"])
    scope_hash = hashlib.sha256(
        json.dumps(sorted(set(row["expected_urls"]))).encode()
    ).hexdigest()
    if (
        row["sha256"] != page["capture_sha256"]
        or scope_hash != page["scope_sha256"]
        or hashlib.sha256(
            json.dumps(sorted(set(row.get("ignored_urls", [])))).encode()
        ).hexdigest()
        != page["auxiliary_scope_sha256"]
        or row["site_family"] != page["site_family"]
        or row.get("final_url", row["url"]) != page["source"]
    ):
        raise ValueError("Manifest does not match evaluated scope: " + row["id"])


def extraction_summary(pages):
    tp = sum(p["correct"] for p in pages)
    fp = sum(p["unwanted"] for p in pages)
    expected = sum(p["expected"] for p in pages)
    precision, recall = tp / max(1, tp + fp), tp / max(1, expected)
    return dict(
        pages=len(pages),
        expected=expected,
        correct=tp,
        unwanted=fp,
        missing=expected - tp,
        precision=round(precision, 4),
        recall=round(recall, 4),
        micro_f1=round(2 * precision * recall / max(1e-12, precision + recall), 4),
        source_macro_f1=round(sum(p["f1"] for p in pages) / max(1, len(pages)), 4),
        complete_pages=sum(p["correct"] == p["expected"] for p in pages),
        clean_complete_pages=sum(
            p["correct"] == p["expected"] and p["unwanted"] == 0 for p in pages
        ),
        zero_recall_pages=sum(p["correct"] == 0 for p in pages),
    )


def existing_source_overlap(families):
    overlaps, datasets = {}, []
    for path in sorted((ROOT / "ml/datasets").glob("*.jsonl")):
        if path.name.startswith("breadth-"):
            continue
        rows, rows_with_source_url = 0, 0
        with path.open(encoding="utf8") as handle:
            for line in handle:
                row = json.loads(line)
                rows += 1
                hosts = {
                    (urlsplit(value).hostname or "").lower()
                    for key in ("source", "source_url", "page_url")
                    if isinstance(value := row.get(key), str) and "://" in value
                }
                rows_with_source_url += bool(hosts)
                for family in families:
                    if any(
                        host == family or host.endswith("." + family) for host in hosts
                    ):
                        counts = overlaps.setdefault(family, {})
                        counts[path.name] = counts.get(path.name, 0) + 1
        datasets.append(
            {
                "file": path.name,
                "rows": rows,
                "rows_with_source_url": rows_with_source_url,
            }
        )
    return {
        "overlapping_families": overlaps,
        "datasets": datasets,
        "scope": "Only recorded source/page URLs, not target-link URLs. Synthetic or unrecorded origins cannot establish domain disjointness.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", type=Path, required=True)
    parser.add_argument("--report", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifests = [
        row
        for path in args.manifest
        for row in json.loads(path.read_text(encoding="utf8"))
    ]
    sources = {row["id"]: row for row in manifests}
    if len(sources) != len(manifests):
        raise ValueError("Duplicate source IDs")
    historical = historical_sources()
    old_families = {r["site_family"] for r in historical}
    fresh, seen = [], set()
    feature_rows = 0
    model_metrics = {}
    for path in args.report:
        report = json.loads(path.read_text(encoding="utf8"))
        feature_rows += report["feature_rows"]
        for sid, page in report["pages"].items():
            row = sources[sid]
            family = row["site_family"]
            if family in seen or family in old_families:
                raise ValueError("Duplicated website family: " + family)
            seen.add(family)
            validate_scope(row, page)
            fresh.append(
                dict(
                    id=sid,
                    url=row["url"],
                    final_url=row.get("final_url", row["url"]),
                    site_family=family,
                    sector=row["sector"],
                    layout=row["layout"],
                    cohort="fresh",
                    report=path.resolve().relative_to(ROOT).as_posix(),
                    capture_sha256=row["sha256"],
                    expected=page["expected"],
                    correct=page["correct"],
                    unwanted=page["unwanted"],
                    precision=page["precision"],
                    recall=page["recall"],
                    f1=page["f1"],
                    expected_in_candidates=page["expected_in_candidates"],
                    candidate_coverage=page["expected_in_candidates"]
                    / max(1, page["expected"]),
                    median_seconds=page["median_seconds"],
                )
            )
        for name, model in report["models"].items():
            record = model_metrics.setdefault(
                name,
                dict(model_id=model["model_id"], tp=0, fp=0, fn=0, source_scores=[]),
            )
            if record["model_id"] != model["model_id"]:
                raise ValueError("Model weights differ between shards")
            for key in ("tp", "fp", "fn"):
                record[key] += model["metrics"][key]
            record["source_scores"].extend(
                m["f1"]
                for m in model["metrics"]["by_source"].values()
                if m["tp"] + m["fn"]
            )
    for record in model_metrics.values():
        p = record["tp"] / max(1, record["tp"] + record["fp"])
        r = record["tp"] / max(1, record["tp"] + record["fn"])
        scores = record.pop("source_scores")
        record.update(
            precision=round(p, 4),
            recall=round(r, 4),
            micro_f1=round(2 * p * r / max(p + r, 1e-12), 4),
            source_macro_f1=round(sum(scores) / max(1, len(scores)), 4),
        )
    all_sources = historical + fresh
    report = dict(
        historical_pages=len(historical),
        historical_website_families=len(old_families),
        new_evaluated_pages=len(fresh),
        new_evaluated_website_families=len(seen),
        total_evaluated_pages=len(all_sources),
        total_website_families=len(old_families | seen),
        target_reached=len(old_families | seen) >= 100,
        new_feature_rows=feature_rows,
        new_candidate_sites=len(manifests),
        new_captured_pages=sum(
            bool(r.get("file") and r.get("sha256")) for r in manifests
        ),
        new_captured_but_not_evaluated=sum(
            bool(r.get("file") and r.get("sha256")) and r["site_family"] not in seen
            for r in manifests
        ),
        prior_dataset_source_overlap=existing_source_overlap(seen),
        new_sector_counts=dict(sorted(Counter(r["sector"] for r in fresh).items())),
        new_collection_statuses=dict(
            Counter(r.get("status", "pending") for r in manifests)
        ),
        network_requests=sum(r.get("network_requests", 0) for r in manifests),
        bytes_received=sum(r.get("bytes_received", 0) for r in manifests),
        fresh_extraction=extraction_summary(fresh),
        fresh_extraction_by_sector={
            sector: extraction_summary([p for p in fresh if p["sector"] == sector])
            for sector in sorted({p["sector"] for p in fresh})
        },
        expected_urls_in_candidates=sum(p["expected_in_candidates"] for p in fresh),
        models_on_fresh_feature_rows=model_metrics,
        model_metric_scope="Candidate-conditional classification only; missing candidates are counted in full parser extraction, not classifier recall.",
        sites=all_sources,
        not_evaluated=[
            {
                k: r[k]
                for k in (
                    "id",
                    "url",
                    "site_family",
                    "sector",
                    "status",
                    "error",
                    "acquisition_note",
                )
                if k in r
            }
            for r in manifests
            if r["id"] not in {s["id"] for s in fresh}
        ],
        limitations=[
            "Counts include historical development/regression pages, not 100 unseen test domains.",
            "One listing per new site, not complete archives or proof of support for every page on each site.",
            "Labels are source-scoped weak annotations. Out-of-scope links can still be useful in a different tracker. No model training or threshold tuning was performed.",
            "Auxiliary scopes for three technical pages were refined by an independent reviewer after initial evaluation; positive scopes were unchanged. The initial report is retained.",
            "Model metrics are conditional on generated candidates and must not be compared directly to full parser extraction recall.",
            "Timings are local Windows replays, some concurrent, not isolated production latency benchmarks. Process RSS includes all four models and accumulated feature rows.",
            "Refusals, robots exclusions, JavaScript shells and unreviewed captures do not count as evaluated websites.",
            "Historical parser results are retained with their original reports, not pooled into a current accuracy score.",
        ],
        input_sha256={
            p.resolve().relative_to(ROOT).as_posix(): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in args.manifest + args.report
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf8", newline="\n"
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "historical_website_families",
                    "new_evaluated_website_families",
                    "total_website_families",
                    "target_reached",
                    "new_feature_rows",
                    "network_requests",
                    "bytes_received",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
