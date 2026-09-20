"""Collect a fixed, held-out set of public listing pages; never content pages."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tracker.cache import FetchCache
from app.tracker.media_metadata import sample_entries
from app.tracker.parser import parse_page
from app.tracker.urls import RequestBudget, SafeFetcher, request_budget


async def collect(manifest, directory, fetch=False):
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "capture-report.json"
    previous = (
        {r["source_id"]: r for r in json.loads(report_path.read_text(encoding="utf8"))}
        if report_path.exists()
        else {}
    )
    fetcher = SafeFetcher(cache=FetchCache(directory / "cache.sqlite3"))
    gate = asyncio.Semaphore(3)

    async def one(source):
        row = dict(source)
        target = (
            ROOT / source["shared_capture"]
            if source.get("shared_capture")
            else directory / (source["source_id"] + ".html")
        )
        old = previous.get(source["source_id"], {})
        if old.get("status") == "unavailable":
            return old
        try:
            async with gate:
                if not target.exists() and fetch:
                    origin = urlsplit(source["url"])
                    robots_url = f"{origin.scheme}://{origin.netloc}/robots.txt"
                    try:
                        _, rules = await fetcher.get(robots_url)
                    except Exception as exc:
                        if not any(code in str(exc) for code in ("404", "410")):
                            raise
                    else:
                        policy = RobotFileParser()
                        policy.parse(rules.splitlines())
                        if not policy.can_fetch("MediaTracker", source["url"]):
                            raise ValueError(
                                "Listing excluded by robots.txt; no listing request sent."
                            )
                    final, html = await fetcher.get(source["url"])
                    target.write_text(html, encoding="utf8")
                    row["final_url"] = final
                raw = target.read_bytes()
                if b"AwsWafIntegration" in raw and b"challenge-container" in raw:
                    row.update(
                        status="excluded",
                        excluded_reason="AWS WAF challenge, not listing content; not retried or evaluated.",
                    )
                    return row
                row.update(
                    status="captured",
                    final_url=row.get("final_url", old.get("final_url", source["url"])),
                    file=str(target.relative_to(ROOT)).replace("\\", "/"),
                    bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
        except Exception as exc:
            row.update(status="unavailable", error=str(exc)[:250])
        print(json.dumps(row, ensure_ascii=True), flush=True)
        return row

    if len(manifest["sources"]) > 20:
        raise ValueError("A collection pass is limited to twenty declared sources.")
    budget = RequestBudget(limit=40, byte_limit=20_000_000)
    token = request_budget.set(budget)
    try:
        report = await asyncio.gather(*(one(source) for source in manifest["sources"]))
    finally:
        request_budget.reset(token)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    profiles = []
    for source in report:
        if source["status"] != "captured":
            continue
        raw = (ROOT / source["file"]).read_text(encoding="utf8")
        scan, *_ = parse_page(raw, source["final_url"])
        payload = scan.to_dict()
        profiles.append(
            {
                key: source[key]
                for key in ("source_id", "url", "label", "rationale", "sha256")
            }
            | {
                "split": "additional_source_holdout",
                "title": payload["title"],
                "source_summary": payload.get("source_summary", ""),
                "kind": payload["kind"],
                "entry_count": len(payload["entries"]),
                "entries": [
                    {key: entry.get(key, "") for key in ("title", "url")}
                    for entry in sample_entries(payload["entries"])
                ],
                "provenance": "New public listing; bounded deployed parser output; no detail requests; labels fixed before predictions.",
            }
        )
    return profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument(
        "--directory", type=Path, default=ROOT / "data/non-extraction-audit"
    )
    args = parser.parse_args()
    manifest = json.loads(
        (ROOT / "ml/datasets/non-extraction-audit-sources.json").read_text(
            encoding="utf8"
        )
    )
    profiles = asyncio.run(collect(manifest, args.directory, args.fetch))
    (ROOT / "ml/datasets/non-extraction-audit-profiles.json").write_text(
        json.dumps(profiles, indent=2, ensure_ascii=True) + "\n", encoding="utf8"
    )


if __name__ == "__main__":
    main()
