"""Build auditable, page-rule-labelled public metadata; never open user databases."""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TRACKER_LINK_MODEL"] = "off"
from app.tracker.link_model import FEATURES, VERSION, candidates
from app.tracker.parser import candidate_url, parse_page


def build(split=None):
    records, report, hosts = [], [], {}
    sources = json.loads((ROOT / "ml/datasets/sources.json").read_text())
    sources = (
        [s for s in sources if s["split"] == split]
        if split
        else [s for s in sources if s["split"] != "holdout"]
    )
    for source in sources:
        host = urlsplit(source["url"]).hostname
        assert hosts.setdefault(host, source["split"]) == source["split"], (
            "Host leaked across splits"
        )
        path = ROOT / source["file"]
        html = path.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        selected = {
            id(a) for a in soup.select(source.get("positive_selector", "a[href]"))
        }
        positive_urls = set()
        for a in soup.select("a[href]"):
            url = candidate_url(a.get("href"), source["url"])
            if not url:
                continue
            p = urlsplit(url)
            if (
                id(a) in selected
                and re.search(
                    source.get("positive_text", "."), a.get_text(" ", strip=True)
                )
                and (source.get("external") or p.hostname == host)
                and re.search(source.get("positive", "."), p.path)
                and not re.search(source.get("exclude", "(?!)"), p.path)
            ):
                positive_urls.add(url)
        assert positive_urls, f"No labels found: review {source['id']}"
        baseline = {entry.url for entry in parse_page(html, source["url"])[0].entries}
        seen, rows = set(), []
        for _a, url, label, features in candidates(soup, source["url"]):
            fingerprint = hashlib.sha256(
                json.dumps([source["id"], url, features]).encode()
            ).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            rows.append(
                dict(
                    id=fingerprint[:20],
                    source_id=source["id"],
                    source=source["url"],
                    split=source["split"],
                    family=source["family"],
                    url=url,
                    text=label[:160],
                    label=int(url in positive_urls),
                    features=features,
                    baseline=int(url in baseline),
                )
            )
        # Cap training by class AND source. Large comic archives must not dominate.
        if source["split"] == "train":
            rows = [
                r
                for label in (0, 1)
                for r in sorted(
                    (r for r in rows if r["label"] == label), key=lambda r: r["id"]
                )[:128]
            ]
        records.extend(rows)
        report.append(
            dict(
                id=source["id"],
                url=source["url"],
                split=source["split"],
                family=source["family"],
                rows=len(rows),
                labels=dict(Counter(r["label"] for r in rows)),
                positive_urls=len(positive_urls),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                annotation=source["reason"],
            )
        )
    # Authored boundary cases supplement the real pages; never used for evaluation.
    # These teach archive-vs-entry and dateless cards without inventing public data.
    synthetic = []
    for i in range(0 if split else 36):
        source_url = f"https://boundary-{i}.example/blog/"
        cards = []
        positives = set()
        tags = ("div", "li", "article", "td")
        for j, title in enumerate(
            (
                "A small discovery",
                "Q&A",
                "Summer",
                "Side effects",
                "Version 3.2",
                "A new beginning",
            )
        ):
            url = (
                f"https://boundary-{i}.example/blog/2025/07/story-{j}"
                if i % 2
                else f"https://boundary-{i}.example/episodes/show/{j + 1}/story"
            )
            positives.add(url)
            anchor = f'<a href="{url}">{title}</a>'
            if i % 3 == 0:
                anchor = f"<h2>{anchor}</h2>"
            cards.append(f"<{tags[i % 4]}>{anchor}</{tags[i % 4]}>")
        negatives = "".join(
            f'<li><a href="/blog/{year}/{month:02d}/">{month:02d} / {year}</a></li>'
            for year in (2022, 2023, 2024)
            for month in (1, 5, 12)
        )
        html = (
            "<main>"
            + "".join(cards)
            + '</main><div class="archives">'
            + negatives
            + '</div><nav><a href="/blog/">Blog</a><a href="/topics/">Topics</a></nav>'
        )
        soup = BeautifulSoup(html, "html.parser")
        baseline = {e.url for e in parse_page(html, source_url)[0].entries}
        for _, url, label, features in candidates(soup, source_url):
            synthetic.append(
                dict(
                    id=hashlib.sha256((source_url + url).encode()).hexdigest()[:20],
                    source_id="authored-boundaries",
                    source=source_url,
                    split="train",
                    family="synthetic",
                    url=url,
                    text=label,
                    label=int(url in positives),
                    features=features,
                    baseline=int(url in baseline),
                )
            )
    records.extend(synthetic)
    report.append(
        dict(
            id="authored-boundaries",
            split="train",
            rows=len(synthetic),
            labels=dict(Counter(r["label"] for r in synthetic)),
            annotation="Authored training-only boundary cases; not captured web pages.",
        )
    )
    target = ROOT / ("ml/datasets/" + (split + "-" if split else "") + "dataset.jsonl")
    target.write_text(
        "".join(
            json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n"
            for row in records
        ),
        encoding="utf-8",
    )
    metadata = dict(
        feature_version=VERSION,
        features=FEATURES,
        built_at=datetime.now(timezone.utc).isoformat(),
        label_method="Source-specific rules reviewed against index markup; not independently double-annotated.",
        data_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        sources=report,
    )
    (
        ROOT / ("ml/datasets/" + (split + "-" if split else "") + "dataset-card.json")
    ).write_text(json.dumps(metadata, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                rows=len(records),
                sources=[
                    {k: s[k] for k in ("id", "split", "rows", "labels")} for s in report
                ],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["holdout"])
    build(parser.parse_args().split)
