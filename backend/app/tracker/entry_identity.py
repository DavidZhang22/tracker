"""An entry can have a source identity without having a navigable URL."""

import hashlib
import unicodedata

from .urls import content_key


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def record_id(scope, value):
    return (
        "list:"
        + hashlib.sha256(
            (normalized(scope) + "\n" + normalized(value)).encode()
        ).hexdigest()
    )


def entry_key(entry):
    if not isinstance(entry, dict):
        entry = vars(entry) if hasattr(entry, "__dict__") else dict(entry)
    if entry.get("url"):
        return content_key(entry["url"])
    return entry.get("source_id") or record_id("title", entry["title"])
