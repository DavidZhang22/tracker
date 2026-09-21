"""Bounded page-local groups for conservative model refinement."""

import re
from urllib.parse import parse_qsl, unquote, urlsplit

from .context_model import NUMERIC_FEATURES

ROLE_NAMES = (
    "same_host",
    "semantic_title",
    "contains_heading",
    "record_primary_url",
    "record_other_primary",
    "in_tr",
    "inside_list_record",
    "image_label",
    "action_label",
)
ROLE_INDICES = tuple(NUMERIC_FEATURES.index(name) for name in ROLE_NAMES)
HEADING_INDEX = NUMERIC_FEATURES.index("heading_level")
HEX = re.compile(r"(?:[a-f0-9]{12,}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})", re.I)
DIGITS = re.compile(r"\d+")
EXTENSION = re.compile(r"\.[a-zA-Z][a-zA-Z0-9]{0,7}$")


def group_key(row):
    """Return a page-local identity, never a learned hostname feature."""
    url = row.get("url", "")
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        parts = [unquote(part).lower() for part in parsed.path.split("/") if part]
        normalized = [
            "{id}" if HEX.fullmatch(part) else DIGITS.sub("{n}", part) for part in parts
        ]
        if parts:
            suffix = EXTENSION.search(parts[-1])
            normalized[-1] = "*" + (suffix.group(0) if suffix else "")
        query = tuple(
            sorted(
                {
                    key[:80].lower()
                    for key, _ in parse_qsl(parsed.query, max_num_fields=40)
                }
            )[:16]
        )
    except (TypeError, ValueError):
        return None
    classes = tuple(
        sorted({word for word in row["tokens"] if word.startswith("c:")})[:48]
    )
    features = row["features"]
    roles = tuple(int(features[index] > 0) for index in ROLE_INDICES) + (
        round(features[HEADING_INDEX] * 6),
    )
    source = row.get("source", row.get("source_id", ""))
    return source, tuple(normalized), query, classes, roles
