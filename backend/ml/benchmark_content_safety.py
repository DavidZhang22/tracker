"""Offline excerpt-screening cost and benign replay; never fetch risky content."""

import argparse
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from app.tracker.content_safety import excerpt_risk, public_metadata

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--corpus-root", type=Path, default=root / "ml/datasets")
parser.add_argument("--output", type=Path)
args = parser.parse_args()
rows = []
for filename in (
    "media-profiles.json",
    "media-public-profiles.json",
    "media-final-profiles.json",
):
    rows.extend(json.loads((args.corpus_root / filename).read_text(encoding="utf-8")))
flags = [
    row["source_id"]
    for row in rows
    if excerpt_risk(row.get("title"), row.get("source_summary"), row.get("url"))
]
items = [
    dict(
        rows[index % len(rows)],
        description_auto="",
        description_method="source-excerpt",
        description_override=None,
    )
    for index in range(500)
]
entries = [
    dict(
        title="Senior Machine Learning Engineer",
        url=f"https://jobs.example.org/{index}",
        summary="Location: New York; Date: 2026-09-20",
        context="Job requirements: Python and data processing.",
    )
    for index in range(4999)
]
measurements = {}
for name, records in (("500_item_library", items), ("4999_job_summaries", entries)):
    times = []
    for _ in range(10):
        start = perf_counter()
        for row in records:
            public_metadata(row)
        times.append((perf_counter() - start) * 1000)
    measurements[name] = round(statistics.median(times), 3)
result = {
    "benign_profiles": len(rows),
    "flagged_benign_profiles": flags,
    "median_ms": measurements,
    "scope": "Local text screening only; no malware/legality/content-body guarantee",
}
text = json.dumps(result, indent=2) + "\n"
if args.output:
    args.output.write_text(text, encoding="utf-8")
print(text)
