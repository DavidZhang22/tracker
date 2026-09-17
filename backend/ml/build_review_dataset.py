"""Build frozen rows from independently inspected listing scopes, not model predictions."""

import hashlib
import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from build_v2_dataset import annotated_index

SCOPES = {
    "julia": {"positive": r"^/blog/\d{4}/\d{2}/[^/]+/?$"},
    "aclanthology": {
        "positive_selector": "main li a[href]",
        "positive": r"^/events/[^/]+/?$",
    },
    "gentoo": {"positive_selector": ".newsitem-headline a[href]"},
    "kde": {"positive_selector": "main h3 a[href]"},
    "publicdomainreview": {
        "positive_selector": "p.title a[href]",
        "positive": r"^/essay/[^/]+/?$",
    },
    "godot": {
        "positive_selector": "main .posts > a[href]",
        "positive": r"^/article/[^/]+/?$",
    },
    "signal": {"positive_selector": "h3 a[href]", "positive": r"^/blog/[^/]+/?$"},
    "projecteuler": {
        "positive_selector": "table a[href]",
        "positive": r"^/problem=\d+$",
    },
}


def main():
    directory = ROOT / "data/model-review"
    sources = json.loads((directory / "capture-report.json").read_text())
    rows, manifest = [], []
    for source in sources:
        name = source["id"].removeprefix("review-")
        if source["status"] != "captured":
            manifest.append(source)
            continue
        if name not in SCOPES:
            source.update(
                status="acquisition-only",
                reason=(
                    "NumPy has primarily unlinked announcement headings; CERN returns a script-populated listing. "
                    "Neither provides an unambiguous linked inventory in this capture. Excluded from link training."
                ),
            )
            manifest.append(source)
            continue
        source.update(
            SCOPES[name],
            reason="Primary listing titles manually scoped from saved DOM before inspecting model output; URL-level weak labels, not independent human gold.",
        )
        raw = (ROOT / source["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["sha256"]
        soup = BeautifulSoup(raw.decode("utf8"), "html.parser")
        annotated, positives = annotated_index(soup, source)
        soup.decompose()
        rows.extend(annotated)
        source.update(rows=len(annotated), expected_urls=sorted(positives))
        manifest.append(source)
        print(source["id"], source["split"], len(annotated), len(positives))
    (ROOT / "ml/datasets/review-sources.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf8"
    )
    (ROOT / "ml/datasets/review-dataset.jsonl").write_text(
        "".join(
            json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
            for r in rows
        ),
        encoding="utf8",
    )


if __name__ == "__main__":
    main()
