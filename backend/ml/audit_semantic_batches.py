"""Audit cached versus page-gated embeddings without retraining or tuning."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
import benchmark_semantic_decisions as bench
import semantic_decision_experiment as experiment

from app.tracker.semantic_model import Encoder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        type=Path,
        default=ROOT / "ml/reports/semantic-decision-runtime-linux.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "ml/reports/semantic-decision-batch-parity.json",
    )
    args = parser.parse_args()
    directory = ROOT / "ml/experiments/semantic-decision"
    cache = ROOT / "data/semantic-decision"
    selection = json.loads((directory / "selection.json").read_text())["semantic"]
    name = selection["name"]
    manifest = json.loads(
        (ROOT / "ml/datasets/semantic-decision-holdout-sources.json").read_text()
    )
    pages = [
        json.loads((cache / (s["id"] + ".json")).read_text())
        for s in manifest
        if s.get("status") == "captured" and s.get("expected_urls")
    ]
    rows = [row for page in pages for row in page["rows"]]
    payload = json.loads((directory / (name + ".json")).read_text())
    probability = experiment.predict_head(
        payload, experiment.features_for(rows, payload, cache)
    )
    base = np.asarray([r["baseline"] for r in rows])
    offline = experiment.route(
        base, probability, selection["threshold"], selection["policy"]
    )
    runtime = json.loads(args.runtime.read_text())
    recorded = {p["page_id"]: p for p in runtime["cases"][name]["warmup"]}
    head = bench.PortableHead(payload, Encoder(payload["encoder"]))
    report = dict(
        selected_model_sha256=experiment.sha(directory / (name + ".json")),
        hypothesis="Compare cached global embedding batches with live page-local uncertainty batches; fixed weights and threshold, no tuning.",
        pages={},
    )
    offset = 0
    for page in pages:
        n = len(page["rows"])
        cached = offline[offset : offset + n]
        offset += n
        if page["id"] not in recorded:
            continue
        local, _ = bench.classify_rows(
            page["rows"],
            np.asarray([r["baseline"] for r in page["rows"]]),
            head,
            selection["threshold"],
            selection["policy"],
        )
        remote = np.asarray([v == "1" for v in recorded[page["id"]]["decisions_bits"]])
        changed = [
            dict(
                url=row["identity"],
                cached_probability=float(probability[offset - n + i]),
                offline=bool(a),
                runtime_linux=bool(b),
            )
            for i, (row, a, b) in enumerate(
                zip(page["rows"], cached, remote, strict=True)
            )
            if a != b
        ]
        report["pages"][page["id"]] = dict(
            rows=n,
            offline_vs_linux_changed_rows=int(np.sum(cached != remote)),
            offline_vs_local_gated_changed_rows=int(np.sum(cached != local)),
            local_gated_vs_linux_changed_rows=int(np.sum(local != remote)),
            offline_quality=experiment.quality([page], cached),
            runtime_linux_quality=experiment.quality([page], remote),
            changed=changed,
        )
        print(page["id"], len(changed), "cached/runtime differences", flush=True)
    experiment.write(args.output, report)


if __name__ == "__main__":
    main()
