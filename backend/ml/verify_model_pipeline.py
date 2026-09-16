"""Offline full-parser quality, timing, and two-worker container memory checks."""

import argparse
import gc
import hashlib
import json
import os
import re
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


def setup(path):
    sys.path.insert(0, path)


def parse_job(html, source):
    from app.tracker.parser import parse_page

    result = parse_page(html, source)
    payload = [asdict(result[0]), result[1], result[2]]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return len(result[0].entries), digest


def expected_urls(source, html):
    from bs4 import BeautifulSoup

    from app.tracker.parser import candidate_url

    if "expected_urls" in source:
        return set(source["expected_urls"])
    soup = BeautifulSoup(html, "html.parser")
    try:
        return {
            url
            for a in soup.select(source.get("positive_selector", "a[href]"))
            if (url := candidate_url(a.get("href"), source["url"]))
            and (
                source.get("external")
                or urlsplit(url).hostname == urlsplit(source["url"]).hostname
            )
            and (
                "positive_selector" in source
                or re.search(source["positive"], urlsplit(url).path)
            )
        }
    finally:
        soup.decompose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--data-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidates", action="store_true")
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--rescue", action="store_true")
    parser.add_argument("--only-final", action="store_true")
    parser.add_argument("--manifest", action="append", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    setup(str(args.app_root))
    from app.tracker.context_model import ContextModel, load_context_model
    from app.tracker.parser import parse_page

    manifests = [
        args.data_root / "ml/datasets/generalization-sources.json",
        args.data_root / "ml/datasets/cascade-final-sources.json",
    ]
    if args.only_final:
        manifests = manifests[-1:]
    if args.manifest:
        manifests = args.manifest
    sources = [
        s
        for path in manifests
        for s in json.loads(path.read_text(encoding="utf8"))
        if s.get("status", "captured") == "captured"
    ]
    models = {"production": load_context_model()}
    report = {
        "python": sys.version,
        "repeats": args.repeats,
        "network": "disabled externally for container runs",
        "models": {},
    }
    try:
        from app.tracker.native_model import kernel

        report["native_kernel"] = kernel() is not None
    except ImportError:
        report["native_kernel"] = False
    if args.candidates:
        from app.tracker.cascade_model import CascadeModel

        models["text-neural"] = ContextModel(
            json.loads(
                (args.data_root / "ml/experiments/cascade/neural64x32.json").read_text(
                    encoding="utf8"
                )
            )
        )
        models["tree-neural-cascade"] = CascadeModel(
            json.loads(
                (
                    args.data_root / "ml/experiments/structural-cascade/cascade.json"
                ).read_text(encoding="utf8")
            )
        )
    if args.rescue:
        from app.tracker.cascade_model import load_cascade_model

        models = {"rescue-cascade": load_cascade_model()}
        assert models["rescue-cascade"] is not None
    for name, model in models.items():
        pages = {}
        with (
            patch.dict(os.environ, {"TRACKER_LINK_MODEL": "on"}),
            patch("app.tracker.context_model.load_context_model", return_value=model),
        ):
            for source in sources:
                relative = (
                    source["file"]
                    if source["file"].endswith(".html")
                    else "data/generalization/" + source["file"] + ".html"
                )
                html = (args.data_root / relative).read_text(encoding="utf8")
                expected = expected_urls(source, html)
                elapsed = []
                for _ in range(args.repeats):
                    gc.collect()
                    start = time.perf_counter()
                    scan, pages_found, feeds = parse_page(html, source["url"])
                    elapsed.append(time.perf_counter() - start)
                actual = {entry.url for entry in scan.entries}
                output = [asdict(scan), pages_found, feeds]
                pages[source["id"]] = dict(
                    capture_sha256=hashlib.sha256(html.encode()).hexdigest(),
                    expected=len(expected),
                    found=len(actual),
                    correct=len(actual & expected),
                    unwanted=len(actual - expected),
                    missing=len(expected - actual),
                    dated=sum(bool(entry.published_at) for entry in scan.entries),
                    times=elapsed,
                    median_seconds=statistics.median(elapsed),
                    output_sha256=hashlib.sha256(
                        json.dumps(output, sort_keys=True).encode()
                    ).hexdigest(),
                )
                print(name, source["id"], json.dumps(pages[source["id"]]), flush=True)
        report["models"][name] = dict(model_id=model.model_id, pages=pages)
    if args.stress:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
        html = (
            "<main>"
            + "".join(
                f'<article><h2><a href="/entry/{i}">Chapter {i}</a></h2><time datetime="2026-09-01"></time><span lang="en">English</span></article>'
                for i in range(4999)
            )
            + "</main><script>"
            + "x" * 6_000_000
            + "</script>"
        )
        started = time.perf_counter()
        with ProcessPoolExecutor(
            max_workers=2,
            mp_context=get_context("spawn"),
            initializer=setup,
            initargs=(str(args.app_root),),
        ) as pool:
            results = list(
                pool.map(parse_job, [html] * 4, ["https://stress.example/"] * 4)
            )
        print("stress results", results, flush=True)
        report["stress"] = dict(
            workers=2,
            pages=4,
            links_per_page=4999,
            padding_bytes=6_000_000,
            seconds=time.perf_counter() - started,
            output_sha256=results[0][1],
            identical=all(result == results[0] for result in results),
            extracted_count=results[0][0],
        )
    peak = Path("/sys/fs/cgroup/memory.peak")
    report["container_peak_bytes"] = int(peak.read_text()) if peak.exists() else None
    report["native_disabled"] = os.environ.get("TRACKER_NATIVE_MODEL") == "off"
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
