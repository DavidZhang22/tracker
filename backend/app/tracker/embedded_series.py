"""Recover a chapter index from embedded props and a verified visible URL shape."""

import math
import re
from urllib.parse import urlsplit

from .dates import evidence
from .flight import FlightData
from .limits import MAX_LINKS
from .models import Entry, Scan
from .urls import DiscoveryError, canonical_url


def embedded_series(soup, source, title):
    root = urlsplit(source)
    match = re.fullmatch(r"/series/([\w-]+)/?", root.path)
    if not match:
        return None
    base = canonical_url(source).split("?", 1)[0]
    prefix = base + "/chapter-"
    # Verify the route in real anchors before constructing any hidden chapter URL.
    visible = set()
    for anchor in soup.find_all("a", href=True):
        try:
            url = canonical_url(anchor["href"], source)
        except DiscoveryError:
            continue
        if url.startswith(prefix) and re.fullmatch(
            r"\d+(?:\.\d+)?", url[len(prefix) :]
        ):
            visible.add(url)
    if len(visible) < 2:
        return None
    data = FlightData(soup)
    collections = [
        obj
        for obj in data.objects()
        if obj.get("slug") == match[1]
        and isinstance(obj.get("chapters"), list)
        and obj.get("seriesId")
    ]
    if not collections:
        return None
    collection = max(collections, key=lambda obj: len(obj["chapters"]))
    rows = collection["chapters"]
    expected = len(rows)
    count = re.search(
        r"Showing\s+[\d,]+\s+of\s+([\d,]+)\s+chapters",
        soup.get_text(" ", strip=True),
        re.I,
    )
    if count:
        expected = max(expected, int(count[1].replace(",", "")))
    entries = {}
    for row in rows[: MAX_LINKS + 1]:
        row = data.resolve(row)
        if not isinstance(row, dict) or row.get("is_padding"):
            continue
        if row.get("seriesId", collection["seriesId"]) != collection["seriesId"]:
            continue
        number = row.get("chapter_num")
        if (
            type(number) not in (int, float)
            or not 0 <= number <= 1_000_000
            or not math.isfinite(number)
        ):
            continue
        label = row.get("title")
        if not isinstance(label, str) or not label.strip():
            continue
        url = prefix + f"{number:g}"
        date = evidence(
            row.get("created_on"), "Embedded chapter created_on", "listed"
        ) or evidence(row.get("updated_on"), "Embedded chapter updated_on", "updated")
        entries.setdefault(
            url,
            Entry(
                url,
                f"Chapter {number:g}: {label.strip()}"[:1000],
                number=number,
                method="embedded chapter index",
                availability="paid"
                if row.get("is_premium") is True
                else "free"
                if row.get("is_premium") is False
                else "",
                **date,
            ),
        )
    # A corrupted or changed payload must not replace the visible chapter list.
    if not visible.issubset(entries) and len(entries) <= len(visible):
        return None
    scan = Scan(
        source,
        title,
        "novel",
        methods=["page", "embedded chapter index"],
        expected_count=expected,
    )
    scan.entries = sorted(entries.values(), key=lambda e: e.number)[:MAX_LINKS]
    for index, entry in enumerate(scan.entries):
        entry.position = index
    scan.coverage = "complete" if len(scan.entries) == expected else "partial"
    if scan.coverage == "partial":
        scan.warnings.append(
            f"The source reports {expected:,} chapters; recovered {len(scan.entries):,} valid links (limit {MAX_LINKS:,}). Saved links were kept."
        )
    return scan
