"""Validate hydrated record lists against visible links before extracting them."""

import re
from collections import Counter

from .dates import evidence
from .limits import MAX_LINKS
from .models import Entry
from .suggestions import safe_source
from .urls import DiscoveryError, canonical_url, content_key

URL_FIELDS = {"url", "href", "link", "permalink"}
DATE_FIELDS = {
    "datePublished",
    "published_at",
    "publishedAt",
    "published",
    "date",
    "publishDate",
}
SKIP_BRANCHES = {
    "media",
    "image",
    "imageMedia",
    "thumbnail",
    "avatar",
    "product",
    "category",
    "analytics",
}


def field(row, path):
    for key in path:
        if not isinstance(row, dict):
            return None
        row = row.get(key)
    return row


def paths(row, names, prefix=()):
    if not isinstance(row, dict) or len(prefix) > 3:
        return
    for key, value in list(row.items())[:80]:
        if key in names and isinstance(value, str):
            yield (*prefix, key)
        elif isinstance(value, dict) and key not in SKIP_BRANCHES:
            yield from paths(value, names, (*prefix, key))


def label(value):
    return re.sub(r"\W+", " ", value.casefold()).strip()


def infer_fields(rows, visible, source):
    if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_LINKS:
        return None
    votes = Counter()
    for row in rows[:40]:
        title = next(
            (
                row.get(k)
                for k in ("title", "headline", "name")
                if isinstance(row, dict) and isinstance(row.get(k), str)
            ),
            None,
        )
        if not title or len(label(title)) < 4:
            continue
        title_key = next(
            k for k in ("title", "headline", "name") if row.get(k) == title
        )
        for path in paths(row, URL_FIELDS):
            try:
                url = canonical_url(field(row, path), source)
            except (DiscoveryError, ValueError, TypeError):
                continue
            if any(label(title) in label(t) for t in visible.get(content_key(url), ())):
                votes[(path, title_key)] += 1
    if not votes or votes.most_common(1)[0][1] < 2:
        return None
    (url_path, title_key), _ = votes.most_common(1)[0]
    dates = Counter(
        path
        for row in rows[:20]
        for path in paths(row, DATE_FIELDS)
        if evidence(field(row, path), "embedded listing")
    )
    if not dates:
        return None
    return {
        "url_path": list(url_path),
        "title_field": title_key,
        "date_path": list(dates.most_common(1)[0][0]),
    }


def records(rows, fields, source):
    result = []
    for row in rows[:MAX_LINKS]:
        title = field(row, [fields["title_field"]])
        date = evidence(field(row, fields["date_path"]), "Embedded listing")
        if not isinstance(title, str) or not title.strip() or not date:
            return []  # A different record shape needs fresh evidence.
        try:
            url = canonical_url(field(row, fields["url_path"]), source)
        except (DiscoveryError, ValueError, TypeError):
            return []
        if not safe_source(url):
            continue
        context = []
        for key in ("language", "category", "tags", "type"):
            value = row.get(key)
            if isinstance(value, dict):
                value = value.get("title") or value.get("name")
            if isinstance(value, str):
                context.append(value[:300])
        result.append(
            Entry(
                url,
                title[:500],
                method="embedded data",
                context=" ".join(context),
                **date,
            )
        )
    return result


def extract(root, visible, source):
    stack, result, visited = [root], [], 0
    while stack and visited < 10_000:
        value = stack.pop()
        visited += 1
        if isinstance(value, dict):
            stack.extend(
                v for v in list(value.values())[:100] if isinstance(v, (dict, list))
            )
        elif isinstance(value, list):
            if fields := infer_fields(value, visible, source):
                result.extend(records(value, fields, source))
                if len(result) >= MAX_LINKS:
                    return result[:MAX_LINKS]
            else:
                stack.extend(
                    v for v in value[:MAX_LINKS] if isinstance(v, (dict, list))
                )
    return result
