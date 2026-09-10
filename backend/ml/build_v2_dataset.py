"""Context labels from reviewed indexes plus conservative CleanEval negatives.

CleanEval content masks are NOT link relevance labels. Only utility/chrome and
inline-citation examples meeting explicit rules enter the auxiliary negative set.
"""

import argparse
import hashlib
import html
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from context_boundaries import pages

from app.tracker.link_context import EXTRA_FEATURES, context_candidates
from app.tracker.link_model import FEATURES
from app.tracker.parser import candidate_url


def sample_record(row, source, label, origin="index", reason="source annotation"):
    fingerprint = hashlib.sha256(
        json.dumps([source["id"], row["url"], row["features"], row["tokens"]]).encode()
    ).hexdigest()
    return dict(
        id=fingerprint[:24],
        source_id=source["id"],
        source=source["url"],
        split=source["split"],
        family=source.get("family", "corpus"),
        origin=origin,
        url=row["url"],
        text=row["original_label"][:160],
        label=int(label),
        features=row["features"],
        tokens=row["tokens"],
        reason=reason,
    )


def annotated_index(soup, source):
    """The same source annotation for training and uncapped regression replay."""
    host = urlsplit(source["url"]).hostname
    selected = {id(a) for a in soup.select(source.get("positive_selector", "a[href]"))}
    positives = set()
    for a in soup.find_all("a", href=True):
        url = candidate_url(a["href"], source["url"])
        if not url:
            continue
        p = urlsplit(url)
        if (
            id(a) in selected
            and re.search(source.get("positive_text", "."), a.get_text(" ", strip=True))
            and (source.get("external") or p.hostname == host)
            and re.search(source.get("positive", "."), p.path)
            and not re.search(source.get("exclude", "(?!)"), p.path)
        ):
            positives.add(url)
    assert positives, source["id"] + " needs label review"
    rows = {}
    for row in context_candidates(soup, source["url"]):
        record = sample_record(
            row, source, row["url"] in positives, reason=source["reason"]
        )
        rows[record["id"]] = record
    return list(rows.values()), positives


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "ml/v2-dataset.jsonl")
    args = parser.parse_args()
    sources = json.loads((ROOT / "ml/sources.json").read_text())
    validation = {"hn", "django", "pythonbytes", "go", "cloudflare"}
    for source in sources:
        source["split"] = "validation" if source["id"] in validation else "train"
    sources += json.loads((ROOT / "ml/v2_sources.json").read_text())
    records, summary, hosts = [], [], {}
    for source in sources:
        path = ROOT / source["file"]
        if not path.exists():
            summary.append(
                dict(
                    id=source["id"],
                    skipped="Capture unavailable; no fabricated examples",
                )
            )
            continue
        host = urlsplit(source["url"]).hostname
        assert hosts.setdefault(host, source["split"]) == source["split"]
        soup = BeautifulSoup(path.read_text(encoding="utf8"), "html.parser")
        rows, positives = annotated_index(soup, source)
        if source["split"] == "train":
            rows = [
                r
                for label in (0, 1)
                for r in sorted(
                    (r for r in rows if r["label"] == label), key=lambda r: r["id"]
                )[:350]
            ]
        records += rows
        summary.append(
            dict(
                id=source["id"],
                split=source["split"],
                rows=len(rows),
                positive_urls=len(positives),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    # A bounded, diverse sample from one downloaded archive; no historical hosts contacted.
    corpus = ROOT / "ml/raw/cleaneval/web2text.zip"
    corpus_pages = 0
    with zipfile.ZipFile(corpus) as archive:
        originals = sorted(
            (
                f
                for f in archive.infolist()
                if "/cleaneval/orig/" in f.filename and not f.is_dir()
            ),
            key=lambda f: hashlib.sha256(f.filename.encode()).hexdigest(),
        )
        seen_hosts = set(hosts)
        for file in originals:
            if corpus_pages >= 160:
                break
            if file.file_size > 500_000:
                continue
            document = archive.read(file).decode("utf8", errors="replace")
            match = re.search(r'<text\s+id="([^"]+)"', document)
            if not match:
                continue
            url = html.unescape(match[1])
            host = urlsplit(url).hostname
            if not host or host in seen_hosts:
                continue
            # Resolve the provided clean-text partner by its original numeric id.
            alternatives = [
                n
                for n in archive.namelist()
                if "/cleaneval/clean/" in n and Path(n).stem == Path(file.filename).stem
            ]
            if not alternatives:
                continue
            clean = " ".join(
                BeautifulSoup(
                    archive.read(alternatives[0]).decode("utf8", errors="replace"),
                    "html.parser",
                ).stripped_strings
            ).casefold()
            soup = BeautifulSoup(document, "html.parser")
            source = dict(
                id="cleaneval-" + Path(file.filename).stem, url=url, split="train"
            )
            rows = []
            for row in context_candidates(soup, url, limit=1000):
                a = row["anchor"]
                label = row["original_label"].strip()
                path = urlsplit(row["url"]).path
                utility = bool(
                    re.fullmatch(
                        r"(?:home|log.?in|sign.?in|sign.?up|register|privacy(?: policy)?|terms(?: of use)?|contact(?: us)?|about(?: us)?|search|subscribe|sitemap|next|previous|print|email)",
                        label,
                        re.I,
                    )
                )
                paragraph = a.find_parent("p")
                inline = bool(
                    paragraph
                    and len(paragraph.get_text(" ", strip=True).split()) > 70
                    and len(label.split()) < 8
                    and label
                    and label.casefold() in clean
                )
                if not utility and not inline:
                    continue
                if utility and label.casefold() in clean:
                    continue
                record = sample_record(
                    row,
                    source,
                    0,
                    "cleaneval",
                    "utility absent from clean reference"
                    if utility
                    else "inline citation in long cleaned paragraph",
                )
                rows.append(record)
            rows = list({r["id"]: r for r in rows}.values())[:24]
            if rows:
                records += rows
                corpus_pages += 1
                seen_hosts.add(host)
    for url, soup, positives in pages():
        source = dict(id="authored-layouts", url=url, split="train", family="synthetic")
        for row in context_candidates(soup, url):
            records.append(
                sample_record(
                    row,
                    source,
                    row["url"] in positives,
                    "authored",
                    "Authored layout contract; training only",
                )
            )
    target = args.output
    target.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records),
        encoding="utf8",
    )
    card = dict(
        version=2,
        features=list(FEATURES) + list(EXTRA_FEATURES),
        rows=len(records),
        splits=dict(Counter(r["split"] for r in records)),
        origins=dict(Counter(r["origin"] for r in records)),
        corpus_pages=corpus_pages,
        corpus_provenance=json.loads(
            (ROOT / "ml/raw/cleaneval/provenance.json").read_text()
        ),
        sources=summary,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        label_quality="Reviewed source rules and conservative corpus weak labels; not independent gold annotation.",
        prior_test_policy="All v1 sites are development data. Only v2 test hosts are unused for fitting/selection.",
    )
    target.with_name(target.stem + "-card.json").write_text(
        json.dumps(card, indent=2) + "\n"
    )
    print(
        json.dumps(
            {k: v for k, v in card.items() if k not in ("features", "sources")},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
