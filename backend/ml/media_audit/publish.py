"""Publish reviewed audit facts and verified feed alternatives without more requests."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from defusedxml import ElementTree as ET

from app.tracker.parser import parse_feed
from app.tracker.source_registry import host_key, validated
from ml.artifacts import write_text
from ml.media_audit.inventory import family

ROOT = Path(__file__).resolve().parents[2]


def verified_feed(row):
    if row.get("status") != "captured":
        return None
    raw = (ROOT / row["file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ValueError("Alternative capture hash mismatch.")
    tree = ET.fromstring(raw)
    records = [
        node for node in tree.iter() if node.tag.rsplit("}", 1)[-1] in {"item", "entry"}
    ]
    result = parse_feed(raw.decode("utf8"), row.get("final_url", row["url"]))
    if not records or not result or len(result[0].entries) != len(records):
        raise ValueError(
            "A recommended feed must yield every captured record: " + row["url"]
        )
    entries = result[0].entries
    if not all(
        family(entry.url) == family("https://" + row["parent_host"])
        for entry in entries
    ):
        raise ValueError(
            "Feed links do not match the reviewed publisher: " + row["url"]
        )
    return dict(
        parent_host=row["parent_host"],
        url=row["url"],
        final_url=row.get("final_url", row["url"]),
        title=result[0].title,
        capture_sha256=row["sha256"],
        entries=[
            dict(url=e.url, title=e.title, published_at=e.published_at) for e in entries
        ],
    )


def main():
    audit = json.loads(
        (ROOT / "ml/datasets/media200-audit.json").read_text(encoding="utf8")
    )
    alternatives = json.loads(
        (ROOT / "ml/datasets/media200-alternatives.json").read_text(encoding="utf8")
    )
    verified, fixtures = {}, []
    for row in alternatives:
        if row.get("status") != "captured":
            row["verification"] = "unavailable; not recommended"
            continue
        fixture = verified_feed(row)
        if fixture:
            row["verification"] = (
                "RSS parsed; every record extracted; links match publisher"
            )
            row["entry_count"] = len(fixture["entries"])
            fixture["split"] = next(
                s["split"] for s in audit if host_key(s["url"]) == row["parent_host"]
            )
            fixtures.append(fixture)
            verified.setdefault(row["parent_host"], []).append(
                dict(
                    url=row["url"],
                    label=row["label"],
                    kind="feed",
                    relationship="official",
                    evidence_url=row["evidence_url"],
                    verified_at=row["captured_at"],
                )
            )
    seed = []
    for source in audit:
        seed.append(
            validated(
                dict(
                    checked_url=source["url"],
                    status=source["access_status"],
                    checked_at=source["captured_at"],
                    alternatives=verified.get(host_key(source["url"]), []),
                )
            )
        )
    write_text(
        ROOT / "app/tracker/source_status.json",
        json.dumps(seed, ensure_ascii=False, indent=2) + "\n",
    )
    write_text(
        ROOT / "ml/datasets/media200-alternatives.json",
        json.dumps(alternatives, indent=2) + "\n",
    )
    write_text(
        ROOT / "ml/datasets/media200-feeds.json.gz",
        json.dumps(fixtures, ensure_ascii=False, separators=(",", ":")) + "\n",
    )
    report = json.loads(
        (ROOT / "data/media200/corpus-report.json").read_text(encoding="utf8")
    )
    report.update(
        audit_statuses=dict(Counter(row["access_status"] for row in audit)),
        audit_requests=sum(row.get("network_requests", 0) for row in audit),
        audit_bytes=sum(row.get("bytes_received", 0) for row in audit),
        alternatives_tested=len(alternatives),
        verified_alternatives=len(fixtures),
        alternative_requests=sum(
            row.get("network_requests", 0) for row in alternatives
        ),
        feed_records=sum(len(f["entries"]) for f in fixtures),
    )
    write_text(
        ROOT / "ml/reports/media200.json.gz",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    print(json.dumps({k: v for k, v in report.items() if k != "pages"}))


if __name__ == "__main__":
    main()
