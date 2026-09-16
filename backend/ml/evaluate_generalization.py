"""Compare a candidate with production, offline, using frozen index annotations."""

import argparse
import copy
import gc
import json
import re
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from train import metrics

from app.tracker.context_model import ContextModel
from app.tracker.parser import candidate_url, parse_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(
        (ROOT / "app/tracker/link-context-model.json").read_text(encoding="utf-8")
    )
    candidate = copy.deepcopy(baseline)
    candidate["fallback"] = json.loads(args.candidate.read_text(encoding="utf-8"))
    candidate["model_id"] = "generalization-candidate"
    # Preserve job-table behavior and compare at the existing acceptance threshold.
    datasets = [
        ROOT / "ml/datasets/v3-dataset.jsonl",
        ROOT / "ml/datasets/generalization.jsonl",
    ]
    rows = [
        json.loads(line)
        for path in datasets
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    sources = json.loads(
        (ROOT / "ml/datasets/generalization-sources.json").read_text(encoding="utf-8")
    )
    report = {}
    for name, payload in (("baseline", baseline), ("candidate", candidate)):
        model = ContextModel(payload)
        times = []
        for _ in range(3):
            started = time.perf_counter()
            predictions = [
                model.score(row["features"], row["tokens"]) >= model.upper
                for row in rows
            ]
            times.append(time.perf_counter() - started)
        partitions = {}
        for split in ("train", "validation", "test"):
            selected = [
                (r, p)
                for r, p in zip(rows, predictions, strict=True)
                if r["split"] == split
            ]
            partitions[split] = metrics(
                [r for r, _ in selected], [p for _, p in selected]
            )
        pages = {}
        with patch("app.tracker.context_model.load_context_model", return_value=model):
            for source in sources:
                html = (
                    ROOT / "data/generalization" / (source["file"] + ".html")
                ).read_text(encoding="utf-8")
                elapsed = []
                for _ in range(3):
                    gc.collect()
                    started = time.perf_counter()
                    scan = parse_page(html, source["url"])[0]
                    elapsed.append(time.perf_counter() - started)
                soup = BeautifulSoup(html, "html.parser")
                expected = {
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
                actual = {entry.url for entry in scan.entries}
                pages[source["id"]] = dict(
                    split=source["split"],
                    expected=len(expected),
                    found=len(actual),
                    correct=len(actual & expected),
                    unwanted=len(actual - expected),
                    missing=len(expected - actual),
                    median_seconds=round(statistics.median(elapsed), 4),
                    missing_sample=sorted(expected - actual)[:4],
                    unwanted_sample=sorted(actual - expected)[:4],
                )
        report[name] = dict(
            model_id=model.model_id,
            bytes=len(json.dumps(payload)),
            rows=len(rows),
            inference_median_seconds=round(statistics.median(times), 4),
            metrics=partitions,
            pages=pages,
        )
        print(name, json.dumps(pages), flush=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
