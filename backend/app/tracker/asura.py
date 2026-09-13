"""Recover exact Asura dates from the listing's Astro props, without JavaScript."""

import json
import math
import re
from urllib.parse import urlsplit

from .dates import evidence
from .limits import MAX_LINKS
from .models import date_rank
from .urls import DiscoveryError, canonical_url


def astro_props(text):
    """Decode only Astro's JSON object/array/scalar/Date types, with hard bounds."""
    if len(text) > 4_000_000:
        return {}
    remaining = 200_000

    def decode(value, depth=0):
        nonlocal remaining
        remaining -= 1
        if depth > 24 or remaining < 0:
            raise ValueError("Astro props exceed decoding limits")
        if not isinstance(value, list) or not 1 <= len(value) <= 2:
            return None
        tag, payload = value[0], value[1] if len(value) == 2 else None
        if type(tag) is not int:
            return None
        if tag == 0:
            if isinstance(payload, dict):
                return {k: decode(v, depth + 1) for k, v in payload.items()}
            if payload is None or type(payload) in (str, bool, int, float):
                return payload
        if tag == 1 and isinstance(payload, list):
            return [decode(v, depth + 1) for v in payload]
        if tag == 3 and isinstance(payload, str):
            return payload  # Dates are validated by evidence(), never evaluated.
        return None

    try:
        root = json.loads(text)
        return decode([0, root]) if isinstance(root, dict) else {}
    except (ValueError, RecursionError):
        return {}


def enrich_asura_dates(soup, source, entries):
    """Enrich already-selected chapter URLs; never expand selectors or scope."""
    root = urlsplit(source)
    if not (
        root.hostname == "asurascans.com"
        or (root.hostname or "").endswith(".asurascans.com")
    ) or not re.fullmatch(r"/comics/[\w-]+/?", root.path):
        return False
    base = canonical_url(source).split("?", 1)[0].rstrip("/")
    records, conflicts = {}, set()
    size = 0
    for island in soup.find_all("astro-island", limit=128):
        text = island.get("props", "")
        size += len(text)
        if size > 8_000_000:
            break
        props = astro_props(text)
        if not isinstance(props.get("chapters"), list):
            continue
        try:
            scoped = canonical_url(props.get("publicUrl"), source)
        except (DiscoveryError, TypeError, ValueError):
            continue
        slug = props.get("seriesSlug")
        if scoped.rstrip("/") != base or not isinstance(slug, str) or not slug:
            continue
        for row in props["chapters"][: MAX_LINKS + 1]:
            if not isinstance(row, dict) or row.get("series_slug") != slug:
                continue
            number = row.get("number")
            if (
                type(number) not in (int, float)
                or not 0 <= number <= 1_000_000
                or not math.isfinite(number)
            ):
                continue
            date = evidence(row.get("published_at"), "Astro chapter published_at")
            if not date:
                continue
            if number in records and records[number][0] != date:
                conflicts.add(number)
            records[number] = date, row.get("time_ago")
    changed = False
    for entry in entries:
        path = urlsplit(entry.url).path
        chapter = re.fullmatch(
            re.escape(root.path.rstrip("/")) + r"/chapter/(\d+(?:\.\d+)?)/?", path
        )
        if not chapter or urlsplit(entry.url).hostname != root.hostname:
            continue
        number = float(chapter[1])
        if number in conflicts or number not in records:
            continue
        date, age = records[number]
        if date_rank(date) >= date_rank(entry):
            for field, value in date.items():
                setattr(entry, field, value)
            changed = True
        if isinstance(age, str) and 0 < len(age.strip()) <= 100:
            age = age.strip()
            if entry.title.endswith(age):
                title = entry.title[: -len(age)].rstrip(" ·|-–")
                if title:
                    entry.title = title
    return changed
