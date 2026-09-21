"""Opt-in, one-listing-per-site corpus collection with robots and request budgets."""

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote_to_bytes, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tracker.cache import FetchCache
from app.tracker.errors import DiscoveryError
from app.tracker.http_identity import USER_AGENT
from app.tracker.urls import RequestBudget, SafeFetcher, request_budget


class ListingFetcher(SafeFetcher):
    def __init__(self, url, cache):
        super().__init__(cache)
        host = urlsplit(url).hostname
        base = host.removeprefix("www.")
        self.allowed_hosts = {host, base, "www." + base}
        self.robots = None

    def install_robots(self, text):
        self.robots = RobotsPolicy(text)

    async def _request(self, url, *args):
        if urlsplit(url).hostname not in self.allowed_hosts:
            raise DiscoveryError(
                "Cross-host redirect left the reviewed site; not followed."
            )
        if self.robots is not None and not self.robots.can_fetch(url):
            raise DiscoveryError(
                "Listing target is disallowed by robots.txt; not followed."
            )
        return await super()._request(url, *args)


def status_code(exc):
    while exc is not None:
        response = getattr(exc, "response", None)
        if response is not None:
            return response.status_code
        exc = exc.__cause__
    return None


def normalized_path(value):
    value = quote(value, safe="/%!$&'()*+,-.:;=?@_~")
    unreserved = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return re.sub(
        r"%([0-9a-fA-F]{2})",
        lambda match: (
            chr(int(match[1], 16))
            if chr(int(match[1], 16)) in unreserved
            else "%" + match[1].upper()
        ),
        value,
    )


class RobotsPolicy:
    """Match product groups and RFC 9309 paths without requesting any URLs."""

    def __init__(self, text):
        groups, agents, directives = [], [], []
        for line in text.lstrip("\ufeff").splitlines():
            name, separator, value = line.split("#", 1)[0].partition(":")
            if not separator:
                continue
            name, value = name.strip().lower(), value.strip()
            if name == "user-agent":
                if directives:
                    groups.append((agents, directives))
                    agents, directives = [], []
                agents.append(value.lower())
            elif agents and name in {
                "allow",
                "disallow",
                "crawl-delay",
                "request-rate",
            }:
                directives.append((name, value))
        if agents:
            groups.append((agents, directives))
        product = USER_AGENT.split("/", 1)[0].lower()
        matches = [
            (
                max(
                    (len(a) for a in agents if a and a != "*" and a in product),
                    default=0,
                ),
                rules,
            )
            for agents, rules in groups
        ]
        specificity = max((length for length, _ in matches), default=0)
        selected = (
            [rules for length, rules in matches if length == specificity]
            if specificity
            else [rules for agents, rules in groups if "*" in agents]
        )
        self.rules, self.interval = [], 2.0
        for rules in selected:
            for name, value in rules:
                if name in {"allow", "disallow"} and value:
                    pattern = normalized_path(value)
                    terminal = pattern.endswith("$")
                    if terminal:
                        pattern = pattern[:-1]
                    length = len(unquote_to_bytes(pattern.replace("*", "")))
                    self.rules.append(
                        (pattern.split("*"), terminal, length, name == "allow")
                    )
                elif name == "crawl-delay":
                    try:
                        seconds = float(value)
                    except ValueError:
                        continue
                    if math.isfinite(seconds) and seconds >= 0:
                        self.interval = max(self.interval, seconds)
                elif name == "request-rate":
                    match = re.fullmatch(r"(\d+)\s*/\s*(\d+(?:\.\d+)?)", value)
                    if match and int(match[1]):
                        self.interval = max(
                            self.interval, float(match[2]) / int(match[1])
                        )

    def can_fetch(self, url):
        parsed = urlsplit(url)
        path = normalized_path(
            (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        )
        matched = [
            (length, allow)
            for pieces, terminal, length, allow in self.rules
            if self.matches_path(pieces, terminal, path)
        ]
        return max(matched, default=(0, True))[1]

    @staticmethod
    def matches_path(pieces, terminal, path):
        if not path.startswith(pieces[0]):
            return False
        if len(pieces) == 1:
            return not terminal or path == pieces[0]
        offset = len(pieces[0])
        for piece in pieces[1:-1]:
            position = path.find(piece, offset)
            if position < 0:
                return False
            offset = position + len(piece)
        if terminal:
            return path.endswith(pieces[-1]) and len(path) - len(pieces[-1]) >= offset
        return path.find(pieces[-1], offset) >= 0


def robots_policy(text, url):
    policy = RobotsPolicy(text)
    return policy.can_fetch(url), policy.interval


def write_manifest(path, rows):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf8",
        newline="\n",
    )
    temporary.replace(path)


async def collect(row, directory, cache):
    url = row["url"]
    fetcher = ListingFetcher(url, cache)
    budget = RequestBudget(limit=6, byte_limit=10_000_000)
    token = request_budget.set(budget)
    started = time.perf_counter()
    try:
        parts = urlsplit(url)
        robots_url = parts.scheme + "://" + parts.netloc + "/robots.txt"
        row["robots_url"] = robots_url
        try:
            _, robots = await fetcher.get(robots_url)
            if len(robots) > 512_000 or "<html" in robots[:2000].lower():
                raise DiscoveryError(
                    "Robots response was oversized or HTML; listing not requested."
                )
            (directory / (row["id"] + "-robots.txt")).write_bytes(robots.encode("utf8"))
            row["robots_sha256"] = hashlib.sha256(robots.encode()).hexdigest()
            fetcher.install_robots(robots)
            allowed = fetcher.robots.can_fetch(url)
            interval = fetcher.robots.interval
            if not allowed:
                row.update(status="robots-disallowed")
                return
            if interval > 30:
                row.update(
                    status="robots-rate-limit",
                    error="Requested crawl interval exceeds this collection window.",
                )
                return
            fetcher.interval = interval
            row.update(robots="allowed", interval_seconds=interval)
        except Exception as exc:
            if status_code(exc) != 404:
                row.update(status="robots-unavailable", error=str(exc)[:300])
                return
            row["robots"] = "not-found"
        final, html = await fetcher.get(url)
        target = directory / (row["id"] + ".html")
        target.write_text(html, encoding="utf8")
        raw = target.read_bytes()
        row.update(
            status="captured",
            final_url=final,
            file=target.relative_to(ROOT).as_posix(),
            bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    except Exception as exc:
        row.update(status="unavailable", error=str(exc)[:300])
    finally:
        row.update(
            network_requests=budget.requests,
            cache_hits=budget.hits,
            bytes_received=budget.received,
            collection_seconds=round(time.perf_counter() - started, 3),
        )
        request_budget.reset(token)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to((ROOT / "data").resolve()):
        parser.error("Captures must stay under backend/data.")
    rows = json.loads(args.manifest.read_text(encoding="utf8"))
    if not isinstance(rows, list) or len(rows) > 60:
        parser.error("Use a list of at most 60 sources per shard.")
    ids, families = set(), set()
    for row in rows:
        if not re.fullmatch(r"breadth-[a-z0-9-]+", row["id"]) or row["id"] in ids:
            parser.error("Source IDs must be unique breadth-* identifiers.")
        ids.add(row["id"])
        host = urlsplit(row["url"]).hostname or ""
        family = row["site_family"].lower()
        if not (host == family or host.endswith("." + family)):
            parser.error("Source host does not belong to its annotated site family.")
        if family in families:
            parser.error("A shard must not count subdomains as separate sites.")
        families.add(family)
    directory.mkdir(parents=True, exist_ok=True)
    cache = FetchCache(directory / "cache.sqlite3")
    for row in rows:
        if args.only and row["id"] not in args.only:
            continue
        if row.get("status") not in (None, "pending"):
            print(row["id"], "retained", row["status"], flush=True)
            continue
        if not args.fetch:
            print(row["id"], "pending; use --fetch to opt in", flush=True)
            continue
        row.update(
            status="attempt-started", captured_at=datetime.now(timezone.utc).isoformat()
        )
        write_manifest(args.manifest, rows)
        await collect(row, directory, cache)
        write_manifest(args.manifest, rows)
        print(
            row["id"], row["status"], row.get("bytes", row.get("error", "")), flush=True
        )
    print(
        json.dumps(
            {
                "sources": len(rows),
                "captured": sum(r.get("status") == "captured" for r in rows),
                "network_requests": sum(r.get("network_requests", 0) for r in rows),
                "bytes_received": sum(r.get("bytes_received", 0) for r in rows),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
