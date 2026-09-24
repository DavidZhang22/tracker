"""Freeze partial, rule-labelled examples without using model predictions as labels."""

import argparse
import gzip
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.tracker.link_context import context_candidates
from app.tracker.urls import canonical_url_cache
from ml.artifacts import read_text
from ml.collect_breadth import write_manifest

ROOT = Path(__file__).resolve().parents[2]
CHROME = {"nav", "footer"}
UTILITY = {
    "privacy",
    "terms",
    "login",
    "sign in",
    "log in",
    "contact",
    "about",
    "subscribe",
    "advertise",
    "cookie",
    "accessibility",
}


def weak_label(candidate, base):
    node = candidate["anchor"]
    parents = list(node.parents)[:12]
    label = node.get_text(" ", strip=True)
    parsed = urlsplit(candidate["url"])
    if any(p.name in CHROME or p.get("role") == "navigation" for p in parents):
        if len(label) <= 70 and (
            len(parsed.path.strip("/").split("/")) <= 2
            or any(word in label.casefold() for word in UTILITY)
        ):
            return 0, "navigation"
        return None
    if any(word == label.casefold() for word in UTILITY):
        return 0, "utility"
    heading = node.find(["h1", "h2", "h3", "h4"]) or next(
        (p for p in parents[:2] if p.name in {"h1", "h2", "h3", "h4"}), None
    )
    if (
        heading is not None
        and 25 <= len(label) <= 300
        and parsed.path not in {"", "/"}
        and not parsed.fragment
    ):
        if (
            len(parsed.path.strip("/").split("/")) >= 2
            or len(parsed.path.strip("/")) >= 25
        ):
            return 1, "linked-headline"
    return None


def fingerprint(row):
    return hashlib.sha256(
        json.dumps(
            [row.get("features"), row.get("tokens")],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).digest()


def deduplicate(rows, directory):
    historical_urls, historical_features = set(), set()
    for path in sorted(
        set(directory.glob("*.jsonl")) | set(directory.glob("*.jsonl.gz"))
    ):
        if path.name.startswith("media200-"):
            continue
        for line in read_text(path).splitlines():
            row = json.loads(line)
            if row.get("url"):
                historical_urls.add(row["url"])
            if row.get("features"):
                historical_features.add(fingerprint(row))
    result, removed = [], Counter()
    owners_url, owners_features = {}, {}
    priority = {"test": 0, "validation": 1, "train": 2}
    for row in sorted(
        rows,
        key=lambda value: (priority[value["split"]], value["source_id"], value["url"]),
    ):
        key = fingerprint(row)
        if row["url"] in historical_urls or key in historical_features:
            removed["historical_overlap"] += 1
            continue
        if row["url"] in owners_url or key in owners_features:
            removed["new_duplicate"] += 1
            continue
        owners_url[row["url"]] = row["split"]
        owners_features[key] = row["split"]
        result.append(row)
    return result, dict(removed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "data/media200/audit.json"
    )
    args = parser.parse_args()
    sources = json.loads(args.manifest.read_text(encoding="utf8"))
    rows, pages = [], []
    started = time.perf_counter()
    for source in sources:
        if source["status"] != "captured":
            continue
        raw = (ROOT / source["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError("Capture hash mismatch.")
        base = source.get("final_url", source["url"])
        soup = BeautifulSoup(raw.decode("utf8"), "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        text = soup.get_text(" ", strip=True)
        page = dict(
            source_id=source["id"],
            title=title[:200],
            anchor_count=len(soup.find_all("a", href=True)),
            existing=bool(source["existing"]),
            split=source["split"],
        )
        if (
            "verify that you're not a robot" in text.casefold()
            or "verify you are human" in text.casefold()
        ):
            source["access_status"] = "access_blocked"
            page["excluded"] = "HTTP-success challenge page, not a content listing."
        elif text.count("\ufffd") > max(20, len(text) * 0.01):
            page["excluded"] = (
                "Response decoding is lossy; do not train on corrupt text."
            )
        elif page["anchor_count"] < 5:
            source["access_status"] = (
                "javascript_required"
                if soup.find("script")
                and ("javascript" in text.casefold() or len(text) < 1500)
                else "no_listing"
            )
            page["excluded"] = (
                "No usable static listing; capture retained for acquisition evaluation."
            )
        elif source["existing"]:
            page["excluded"] = (
                "Source family already represented; preserve existing split."
            )
        else:
            with canonical_url_cache():
                candidates = list(context_candidates(soup, base))
            labels, samples, grouped = {}, [], {}
            for candidate in candidates:
                found = weak_label(candidate, base)
                if found is None:
                    continue
                label, rule = found
                key = candidate["url"]
                # A headline URL also used in a menu remains content, never contradictory labels.
                old = labels.get(key)
                if old is None or label > old[0]:
                    labels[key] = (label, rule)
                    grouped[key] = candidate
            selected = []
            for label, limit in ((1, 80), (0, 40)):
                keys = [key for key in labels if labels[key][0] == label]
                keys.sort(key=lambda value: hashlib.sha256(value.encode()).digest())
                selected.extend(keys[:limit])
            for key in selected:
                candidate = grouped[key]
                label, rule = labels[key]
                label_text = candidate["anchor"].get_text(" ", strip=True)[:300]
                row = dict(
                    source_id=source["id"],
                    source=base,
                    site_family=source["site_family"],
                    split=source["split"],
                    sector=source["category"],
                    origin="index",
                    acquisition="captured-public-listing",
                    label_quality="weak-partial-dom-scope-v1",
                    label_rule=rule,
                    capture_sha256=source["sha256"],
                    url=key,
                    label=label,
                    text=label_text,
                    features=candidate["features"],
                    tokens=candidate["tokens"],
                )
                rows.append(row)
                if sum(s["label"] == label for s in samples) < 3:
                    samples.append(dict(label=label, text=label_text, url=key))
            page.update(
                candidates=len(candidates),
                labelled=len(selected),
                positive=sum(labels[k][0] for k in selected),
                samples=samples,
                unlabelled_candidates=len(candidates) - len(selected),
            )
        pages.append(page)
        soup.decompose()
        print(
            source["id"],
            page.get("labelled", 0),
            page.get("positive", 0),
            page.get("excluded", ""),
            flush=True,
        )
    rows, removed = deduplicate(rows, ROOT / "ml/datasets")
    for page in pages:
        accepted = [r for r in rows if r["source_id"] == page["source_id"]]
        page["retained"] = len(accepted)
        page["retained_positive"] = sum(r["label"] for r in accepted)
    dataset = ROOT / "ml/datasets/media200-links.jsonl.gz"
    with (
        dataset.open("wb") as handle,
        gzip.GzipFile(
            filename="", mode="wb", fileobj=handle, mtime=0, compresslevel=9
        ) as compressed,
    ):
        for row in rows:
            compressed.write(
                (
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                ).encode()
            )
    write_manifest(ROOT / "ml/datasets/media200-audit.json", sources)
    report = dict(
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        rows=len(rows),
        deduplicated=removed,
        sources=len({r["source_id"] for r in rows}),
        splits=dict(Counter(r["split"] for r in rows)),
        labels=dict(Counter(r["label"] for r in rows)),
        compressed_bytes=dataset.stat().st_size,
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
        labelling="Partial deterministic DOM supervision, not model predictions or exhaustive gold. Unknown links abstain. Headline/navigation rules can be wrong and must be reviewed before promotion. Fixed source-level splits were assigned before acquisition.",
        pages=pages,
    )
    (ROOT / "data/media200/corpus-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf8",
        newline="\n",
    )
    print(json.dumps({k: v for k, v in report.items() if k != "pages"}), flush=True)


if __name__ == "__main__":
    main()
