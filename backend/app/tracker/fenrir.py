"""Fenrir Realm's public chapter index, also used by its JavaScript series page.

Only metadata is fetched. No authentication, purchases, or chapter bodies.
"""

import json
import re
from urllib.parse import urlsplit

from .dates import evidence
from .limits import MAX_LINKS
from .models import Entry, Scan
from .urls import DiscoveryError


def fenrir_endpoint(source):
    parsed = urlsplit(source)
    if parsed.hostname not in {"fenrirealm.com", "www.fenrirealm.com"}:
        return None
    match = re.fullmatch(r"/series/([a-zA-Z0-9][\w-]*)/?", parsed.path)
    if not match:
        return None
    return f"https://fenrirealm.com/api/new/v2/series/{match[1]}/chapters"


def numeric(value):
    return type(value) in (int, float) and 0 <= value <= 1_000_000_000


def chapter_suffix(row):
    """Mirror the public URL helper without evaluating any site JavaScript."""
    slug = row.get("slug")
    if slug is not None and not isinstance(slug, str):
        return None
    if slug and slug.strip():
        suffix = slug.strip().lstrip("/")
    else:
        number, part = row.get("number"), row.get("part")
        if not numeric(number) or (
            part is not None and (type(part) is not int or part < 0)
        ):
            return None
        suffix = f"{number:g}" + (f"-{part}" if part else "")
        group = row.get("group") or {}
        group_slug = group.get("slug")
        if group_slug:
            if not isinstance(group_slug, str):
                return None
            suffix = group_slug.strip("/") + "/" + suffix
    # Keep paths inside this series, including when an index contains hostile data.
    if len(suffix) > 300 or not re.fullmatch(r"[\w.-]+(?:/[\w.-]+)*", suffix):
        return None
    if any(segment in {".", ".."} for segment in suffix.split("/")):
        return None
    return suffix


def fenrir_scan(text, source):
    if not fenrir_endpoint(source):
        raise DiscoveryError("Use a Fenrir Realm series URL.")
    try:
        rows = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise DiscoveryError(
            "Fenrir Realm returned an unreadable chapter list. Retry later."
        ) from exc
    if not isinstance(rows, list):
        raise DiscoveryError(
            "Fenrir Realm's chapter-list format changed. Saved links were kept."
        )

    slug = urlsplit(source).path.strip("/").split("/")[-1]
    # The chapter-only endpoint omits the series title. Users can rename the item.
    words = slug.replace("-", " ").split()
    title = " ".join(
        word
        if i and word in {"a", "an", "and", "of", "the", "to", "with"}
        else word.capitalize()
        for i, word in enumerate(words)
    )
    result = Scan(
        "https://fenrirealm.com/series/" + slug,
        title,
        "novel",
        methods=["public API"],
        expected_count=len(rows),
        coverage="complete",
        order_hint="source",
    )
    accepted = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        group, locked = row.get("group") or {}, row.get("locked") or {}
        if not isinstance(group, dict) or not isinstance(locked, dict):
            continue
        suffix = chapter_suffix(row)
        if not suffix:
            continue
        number, part = row.get("number"), row.get("part")
        name = row.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            name = (
                (f"Chapter {number:g}" + (f".{part}" if part else ""))
                if numeric(number)
                else suffix
            )
        subtitle = row.get("title")
        if isinstance(subtitle, str) and subtitle.strip() and subtitle.strip() != name:
            name += ": " + subtitle.strip()
        label = group.get("abbreviation") or group.get("name")
        if isinstance(label, str) and label.strip():
            name = label.strip() + " · " + name
        date = (
            evidence(
                locked.get("unlocked_at"), "Fenrir Realm public unlock time", "listed"
            )
            or evidence(
                row.get("created_at"), "Fenrir Realm chapter created_at", "listed"
            )
            or evidence(
                row.get("updated_at"), "Fenrir Realm chapter updated_at", "updated"
            )
        )
        price = locked.get("price")
        entry = Entry(
            result.url + "/" + suffix,
            name[:1000],
            number=number if numeric(number) else None,
            method="public API",
            availability="paid"
            if numeric(price) and price > 0
            else "free"
            if numeric(price) and price == 0
            else "",
            **date,
        )
        accepted.append(
            (
                (
                    group.get("index") if numeric(group.get("index")) else 0,
                    row.get("index") if numeric(row.get("index")) else index,
                    index,
                ),
                entry,
            )
        )
    accepted.sort(key=lambda pair: pair[0])
    unique = {}
    for _, entry in accepted:
        unique.setdefault(entry.url, entry)
    result.entries = list(unique.values())[:MAX_LINKS]
    for index, entry in enumerate(result.entries):
        entry.position = index
    if len(result.entries) != len(rows):
        result.coverage = "partial"
        result.warnings.append(
            f"The chapter index lists {len(rows):,} records; kept {len(result.entries):,} distinct valid links (limit {MAX_LINKS:,})."
        )
    return result
