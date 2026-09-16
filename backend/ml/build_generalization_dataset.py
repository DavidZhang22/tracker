"""Freeze new index annotations and format-neutral record examples; no fetching."""

import hashlib
import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from build_v2_dataset import annotated_index

SOURCES = [
    dict(
        id="nasa-index",
        file="nasa",
        url="https://www.nasa.gov/news/recently-published/",
        split="train",
        family="science-news",
        positive_selector="main .hds-content-item a[href]",
        external=True,
    ),
    dict(
        id="pmlr-volumes",
        file="pmlr",
        url="https://proceedings.mlr.press/",
        split="train",
        family="proceedings",
        positive=r"^/(?:v\d+|r\d+)/?$",
    ),
    dict(
        id="python-releases",
        file="python-releases",
        url="https://www.python.org/downloads/",
        split="validation",
        family="software",
        positive=r"^/downloads/release/[^/]+/?$",
    ),
    dict(
        id="esa-gallery",
        file="esa-images",
        url="https://www.esa.int/ESA_Multimedia/Images",
        split="validation",
        family="images",
        positive=r"^/ESA_Multimedia/Images/\d{4}/\d{2}/[^/]+/?$",
    ),
    dict(
        id="w3c-standards",
        file="w3c",
        url="https://www.w3.org/TR/",
        split="test",
        family="standards",
        positive=r"^/TR/[^/]+/?$",
    ),
]


def main():
    rows, manifest = [], []
    for source in SOURCES:
        source = source | {
            "reason": "Reviewed index scope; publisher detail links, excluding navigation and contributor pages."
        }
        path = ROOT / "data/generalization" / (source["file"] + ".html")
        html = path.read_text(encoding="utf-8")
        annotated, positives = annotated_index(
            BeautifulSoup(html, "html.parser"), source
        )
        rows.extend(annotated)
        manifest.append(
            source
            | {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "candidate_rows": len(annotated),
                "positive_urls": len(positives),
            }
        )
    for family in (
        "recipes",
        "courses",
        "exhibitions",
        "datasets",
        "lectures",
        "reports",
    ):
        for layout in range(8):
            source = dict(
                id=f"records-{family}-{layout}",
                url="https://records.example/collection",
                split="validation" if family in {"lectures", "reports"} else "train",
                family=family,
                positive=r"^/entry/",
                reason="Authored record boundaries; utility controls and contributor references are negative.",
            )
            records = []
            for i in range(12):
                link = f'<a href="/entry/{i}">{family.title()} {i}: Discovering patterns</a>'
                metadata = f'<time datetime="2026-09-{i + 1:02d}">2026-09-{i + 1:02d}</time> English <a rel="author" href="/people/{i}">Contributor {i}</a>'
                layouts = (
                    f'<article><h3>{link}</h3><div class="metadata">{metadata}</div></article>',
                    f"<li>{link}<span>{metadata}</span></li>",
                    f"<tr><td>{link}</td><td>{metadata}</td></tr>",
                    f'<div class="record"><div><strong>{link}</strong></div><div>{metadata}</div></div>',
                    f"<section><header><h2>{link}</h2></header><p>{metadata}</p></section>",
                    f'<div class="record">{link}<aside>{metadata}</aside></div>',
                    f"<details><summary>{family} {i}</summary>{link}<p>{metadata}</p></details>",
                    f"<p>{link} | {metadata}</p>",
                )
                records.append(layouts[layout])
            html = (
                '<nav><a href="/">Home</a><a href="/login">Sign in</a></nav><main>'
                + "".join(records)
                + '</main><footer><a href="/privacy">Privacy</a></footer>'
            )
            annotated, _ = annotated_index(BeautifulSoup(html, "html.parser"), source)
            for row in annotated:
                row["origin"] = "authored"
            rows.extend(annotated)
    output = ROOT / "ml/datasets/generalization.jsonl"
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (ROOT / "ml/datasets/generalization-sources.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    combined = ROOT / "data/generalization/training.jsonl"
    combined.write_bytes(
        (ROOT / "ml/datasets/v3-dataset.jsonl").read_bytes() + output.read_bytes()
    )
    print(
        json.dumps(
            {
                "new_rows": len(rows),
                "training_input": str(combined),
                "sources": manifest,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
