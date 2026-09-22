"""Compare frozen link models through offline, source-scoped parser replays."""

import argparse
import errno
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from bs4 import BeautifulSoup
from evaluate_breadth import aggregate, checked_scope, score_sets
from evaluate_extraction_audit import memory_usage, parser_identity, scope
from summarize_breadth import HISTORICAL_MANIFESTS, historical_sources
from verify_model_pipeline import expected_urls

from app.tracker.cascade_model import CascadeModel
from app.tracker.context_model import ContextModel
from app.tracker.parser import parse_page
from ml.artifacts import exists, read_bytes, read_json, write_bytes


def load_model(path):
    data = read_json(path)
    return CascadeModel(data) if "light" in data else ContextModel(data)


def capture(source):
    path = ROOT / source["file"]
    if not path.is_file() and not source["file"].endswith(".html"):
        path = ROOT / "data/generalization" / (source["file"] + ".html")
    path = path.resolve()
    if not any(
        path.is_relative_to((ROOT / name).resolve())
        for name in ("data", "ml/raw", "tests/live")
    ):
        raise ValueError("Capture must be local public evaluation data")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if source.get("sha256") and source["sha256"] != digest:
        raise ValueError("Capture hash changed: " + source["id"])
    return raw.decode("utf8"), digest


def annotations(source, html):
    soup = BeautifulSoup(html, "html.parser")
    try:
        if source.get("scope_frozen_before_evaluation"):
            expected, ignored = checked_scope(soup, source)
        elif source["id"].startswith("audit-"):
            expected, ignored = scope(soup, source)
        else:
            expected = expected_urls(source, html)
            ignored = set(source.get("ignored_urls", []))
    finally:
        soup.decompose()
    base = source.get("final_url", source["url"])
    return (
        {parser_identity(url, base) for url in expected},
        {parser_identity(url, base) for url in ignored},
    )


def atomic_json(path, value, attempts=4):
    """Replace a checkpoint atomically, with bounded retries for transient locks."""
    encoded = (json.dumps(value, indent=2) + "\n").encode("utf8")
    transient = {errno.EACCES, errno.EBUSY, errno.EINTR, errno.EINVAL, errno.EPERM}
    for attempt in range(attempts):
        try:
            write_bytes(path, encoded)
            return
        except OSError as exc:
            if attempt + 1 == attempts or exc.errno not in transient:
                raise
            time.sleep(0.05 * 2**attempt)


def checkpoint(path, report, *, final=False):
    try:
        atomic_json(path, report)
    except OSError as exc:
        if final:
            raise
        print(
            "Checkpoint unavailable; continuing evaluation: " + str(exc),
            file=sys.stderr,
        )


def digest_json(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf8")
    ).hexdigest()


def run_inputs(args, sources, manifest_paths):
    inputs = dict(
        candidate_mode=args.candidate_mode,
        repeats=args.repeats,
        python=sys.version,
        model_sha256={
            name: hashlib.sha256(read_bytes(path)).hexdigest()
            for name, path in (
                ("baseline", args.baseline),
                ("candidate", args.candidate),
            )
        },
        manifests={
            str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in manifest_paths
        },
        sources={},
        runtime={
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "app/tracker").iterdir())
            if path.suffix in {".py", ".json", ".c"}
        },
        evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    for sid, source in sources.items():
        _, capture_digest = capture(source)
        inputs["sources"][sid] = dict(
            manifest_row_sha256=digest_json(source), capture_sha256=capture_digest
        )
    return inputs


def resumed_report(path, fingerprint):
    previous = read_json(path)
    if previous.get("run_fingerprint") != fingerprint:
        raise ValueError(
            "Resume inputs changed: models, captures, manifests, runtime, or evaluation options. "
            "Use a new output path or restart without --resume."
        )
    return previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--manifest", action="append", type=Path, default=[])
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--only", nargs="+")
    parser.add_argument(
        "--candidate-mode", choices=("cascade", "primary"), default="cascade"
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5:
        parser.error("Use 1-5 repetitions")
    sources = {}
    manifest_paths = list(args.manifest)
    if args.historical:
        historical_ids = {r["id"] for r in historical_sources()}
        for name in HISTORICAL_MANIFESTS:
            manifest_paths.append(ROOT / "ml/datasets" / (name + ".json"))
            for source in json.loads(
                (ROOT / "ml/datasets" / (name + ".json")).read_text(encoding="utf8")
            ):
                if source["id"] in historical_ids:
                    sources[source["id"]] = source
    for path in args.manifest:
        for source in json.loads(path.read_text(encoding="utf8")):
            if source.get("status", "captured") == "captured" and source.get(
                "expected_urls"
            ):
                sources[source["id"]] = source
    if args.only:
        missing = set(args.only) - sources.keys()
        if missing:
            parser.error("Unknown source IDs: " + ", ".join(sorted(missing)))
        sources = {sid: row for sid, row in sources.items() if sid in args.only}
    if not sources:
        parser.error("No reviewed captures selected")
    inputs = run_inputs(args, sources, manifest_paths)
    fingerprint = digest_json(inputs)
    models = {
        "baseline": load_model(args.baseline),
        "candidate": load_model(args.candidate),
    }
    report = dict(
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        model_ids={name: model.model_id for name, model in models.items()},
        inputs=inputs,
        run_fingerprint=fingerprint,
        complete=False,
        model_sha256={
            name: hashlib.sha256(read_bytes(path)).hexdigest()
            for name, path in (
                ("baseline", args.baseline),
                ("candidate", args.candidate),
            )
        },
        candidate_mode=args.candidate_mode,
        repeats=args.repeats,
        scope="Offline single-page replay; no source requests. Dataset scopes are weak reviewed annotations.",
        pages={},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.resume and exists(args.output):
        report = resumed_report(args.output, fingerprint)
    for source in sources.values():
        if source["id"] in report["pages"]:
            continue
        html, digest = capture(source)
        if digest != inputs["sources"][source["id"]]["capture_sha256"]:
            raise ValueError("Capture changed during replay: " + source["id"])
        expected, ignored = annotations(source, html)
        base = source.get("final_url", source["url"])
        page = dict(
            source=base,
            site_family=source.get("site_family"),
            capture_sha256=digest,
            original_split=source.get("split"),
            expected=len(expected),
        )
        page["expected_sha256"] = digest_json(sorted(expected))
        page["ignored_sha256"] = digest_json(sorted(ignored))
        page["annotation_protocol"] = (
            "frozen-selector"
            if source.get("scope_frozen_before_evaluation")
            else "extraction-audit"
            if source["id"].startswith("audit-")
            else "historical-frozen-urls-or-selector"
        )
        sets = {}
        for name, model in models.items():
            times = []
            mode = args.candidate_mode if name == "candidate" else "cascade"
            with (
                patch.dict(os.environ, TRACKER_LINK_MODEL=mode),
                patch(
                    "app.tracker.context_model.active_context_model", return_value=model
                ),
            ):
                for _ in range(args.repeats):
                    started = time.perf_counter()
                    scan, _, _ = parse_page(html, base)
                    times.append(time.perf_counter() - started)
            actual = {parser_identity(e.url, base) for e in scan.entries} - ignored
            sets[name] = actual
            page[name] = {
                **score_sets(expected, actual),
                "median_seconds": statistics.median(times),
                "dated_entries": sum(bool(e.published_at) for e in scan.entries),
                "missing_examples": sorted(expected - actual)[:5],
                "unwanted_examples": sorted(actual - expected)[:5],
            }
        page["lost_expected"] = sorted(
            (sets["baseline"] - sets["candidate"]) & expected
        )
        page["gained_expected"] = sorted(
            (sets["candidate"] - sets["baseline"]) & expected
        )
        report["pages"][source["id"]] = page
        checkpoint(args.output, report)
        print(
            source["id"],
            f"{page['baseline']['correct']}/{len(expected)} +{page['baseline']['unwanted']} -> {page['candidate']['correct']}/{len(expected)} +{page['candidate']['unwanted']}",
            flush=True,
        )
    report["summary"] = {
        name: aggregate([p[name] for p in report["pages"].values()]) for name in models
    }
    report["memory"] = memory_usage()
    report["complete"] = True
    checkpoint(args.output, report, final=True)
    print(json.dumps(report["summary"]), flush=True)


if __name__ == "__main__":
    main()
