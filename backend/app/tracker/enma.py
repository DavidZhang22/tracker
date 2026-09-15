"""Read Enma's episode inventory without rendering its watch page or player."""

import json
import math
import re
from urllib.parse import parse_qs, urlsplit

from .dates import evidence
from .limits import MAX_LINKS
from .models import Entry, Scan
from .urls import DiscoveryError, canonical_url

API = "https://api.enma.lol/api/episodes/"
ORIGIN = "https://www.enma.lol"


def series_slug(source):
    p = urlsplit(canonical_url(source))
    match = re.fullmatch(r"/watch/([a-z0-9]+(?:-[a-z0-9]+)*-[1-9]\d{0,9})", p.path)
    query = parse_qs(p.query, keep_blank_values=True)
    if (
        p.hostname in {"enma.lol", "www.enma.lol"}
        and match
        and len(match[1]) <= 300
        and query.keys() <= {"ep"}
        and (
            not query
            or (len(query["ep"]) == 1 and re.fullmatch(r"[1-9]\d{0,9}", query["ep"][0]))
        )
    ):
        return match[1]
    return None


def episode(row, slug):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
        return None
    # The page uses each record's ?ep= identity in its click handler. Never
    # enumerate IDs, treat them as episode numbers, or accept another series.
    match = re.fullmatch(re.escape(slug) + r"\?ep=([1-9]\d{0,9})", row["id"])
    number = row.get("episode_no")
    if (
        not match
        or type(number) not in (int, float)
        or not 0 <= number <= 1_000_000
        or not math.isfinite(number)
    ):
        return None
    title = row.get("title")
    title = title.strip() if isinstance(title, str) else ""
    label = f"Episode {number:g}"
    if title and title.casefold() != label.casefold():
        label += ": " + title
    context = [v for v in [row.get("japanese_title")] if isinstance(v, str)]
    if row.get("filler") is True:
        context.append("Filler")
    return Entry(
        ORIGIN + "/watch/" + row["id"],
        label[:1000],
        number=number,
        source_id=f"enma:{slug}:{match[1]}",
        method="public API",
        context=" ".join(context)[:1000],
        **evidence(row.get("published_at"), "Enma episode published_at"),
    )


def parse_episodes(data, source):
    slug = series_slug(source)
    if not slug:
        raise DiscoveryError("Use an Enma /watch/ URL for its episode list.")
    result = data.get("results") if isinstance(data, dict) else None
    rows = result.get("episodes") if isinstance(result, dict) else None
    total = result.get("totalEpisodes") if isinstance(result, dict) else None
    if (
        not isinstance(rows, list)
        or type(total) is not int
        or not 0 <= total <= 1_000_000
        or data.get("success") is False
    ):
        raise DiscoveryError(
            "Enma returned an unreadable episode list. Saved links were kept."
        )
    records = {}
    invalid = False
    for row in rows[: MAX_LINKS + 1]:
        entry = episode(row, slug)
        if entry is None:
            invalid = True
        elif entry.url in records or len(records) < MAX_LINKS:
            records[entry.url] = entry
    if not records and (rows or total):
        raise DiscoveryError(
            "Enma did not return usable episode links. Saved links were kept."
        )
    scan = Scan(
        source,
        slug.rsplit("-", 1)[0].replace("-", " ").title(),
        "website",
        entries=sorted(records.values(), key=lambda e: (e.number, e.url)),
        methods=["Enma episodes API"],
        pages_scanned=1,
        expected_count=total,
        coverage="complete",
        analysis_mode="light",
    )
    if invalid or len(rows) > MAX_LINKS or len(records) != total:
        scan.coverage = "partial"
        scan.warnings.append(
            f"Enma reports {total:,} episodes; recovered {len(records):,} unique links. Saved links were kept."
        )
    if len(rows) > MAX_LINKS:
        scan.warnings.append(f"Scan reached the {MAX_LINKS:,}-link limit.")
    for i, entry in enumerate(scan.entries):
        entry.position = i
    return scan


async def scan_enma(fetcher, source):
    slug = series_slug(source)
    if not slug:
        raise DiscoveryError("Use an Enma /watch/ URL for its episode list.")
    try:
        _, text = await fetcher.get(API + slug)
    except DiscoveryError as exc:
        if "HTTP 403" in str(exc):
            raise DiscoveryError(
                "Enma blocked access to its episode list (HTTP 403). Try again later; saved links were kept."
            ) from exc
        raise DiscoveryError(f"Enma's episode list is unavailable. {exc}") from exc
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise DiscoveryError(
            "Enma returned an unreadable episode list. Saved links were kept."
        ) from exc
    return parse_episodes(data, source)
