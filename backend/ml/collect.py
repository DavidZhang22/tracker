"""Opt-in, bounded capture of public indexes. No article following or rendering."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tracker.cache import FetchCache
from app.tracker.urls import RequestBudget, SafeFetcher, request_budget


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="sources.json")
    args = parser.parse_args()
    sources = json.loads((ROOT / "ml" / args.manifest).read_text())
    raw = ROOT / "ml/raw"
    raw.mkdir(exist_ok=True)
    fetcher = SafeFetcher(
        FetchCache(raw / "capture-cache.sqlite3"), interval=3, ttl=86400
    )
    report = []
    for source in sources:
        path = ROOT / source["file"]
        if path.exists():
            print(source["id"], "reusing snapshot", flush=True)
            continue
        budget = RequestBudget(3)  # Includes redirects; no retries or child URLs.
        token = request_budget.set(budget)
        try:
            final, body = await fetcher.get(source["url"])
            path.write_text(body, encoding="utf-8")
            report.append(
                dict(
                    id=source["id"],
                    final=final,
                    requests=budget.requests,
                    bytes=len(body.encode()),
                )
            )
            print(source["id"], len(body), "characters", flush=True)
        except Exception as exc:
            report.append(
                dict(id=source["id"], error=str(exc), requests=budget.requests)
            )
            print(source["id"], str(exc), flush=True)
        finally:
            request_budget.reset(token)
    (raw / "capture-report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
