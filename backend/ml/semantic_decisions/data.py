"""Bounded, label-independent text for offline link-decision experiments.

Labels and neutral URL filtering belong to the caller, after extraction.
Features and lexical tokens are the candidate builder's unchanged inputs.
"""

import re
import unicodedata
from itertools import islice
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from app.tracker.dom import HEADINGS
from app.tracker.link_context import context_candidates
from app.tracker.link_model import MAX_CANDIDATES
from app.tracker.urls import canonical_url_cache

TEXT_VERSION = "semantic-decision-text-v1"
MAX_TEXT = 1600
MAX_LINK_TEXT = 520
MAX_CONTEXT_TEXT = 1040
MAX_RECORD_TEXT = 700
MAX_STRINGS = 48
MAX_NODES = 384
EXCLUDED = frozenset({"script", "style", "template", "noscript", "svg", "canvas"})
BROAD_CONTAINERS = frozenset(
    {"[document]", "html", "body", "main", "nav", "footer", "header"}
)
URL_LITERAL = re.compile(r"(?:https?://|//|www\.)[^\s<>]+", re.I)


def path_text(url, limit=240):
    """Only a URL's decoded path is input; never its authority/query."""
    try:
        path = unquote(urlsplit(str(url)[:4096]).path)
    except ValueError:
        return ""
    path = URL_LITERAL.sub(" ", path)
    path = re.split(r"[?#]", path, maxsplit=1)[0]
    return " ".join(re.sub(r"[/_\-]+", " ", path).split())[:limit]


def clean_text(value, limit):
    value = unicodedata.normalize("NFKC", str(value)[: max(limit * 4, 1024)])

    def path_only(match):
        literal = match.group()
        if literal.lower().startswith("www."):
            literal = "https://" + literal
        return path_text(literal)

    value = URL_LITERAL.sub(path_only, value)
    value = "".join(char if char.isprintable() else " " for char in value)
    return " ".join(value.split())[:limit]


def hidden(node):
    return (
        node.name in EXCLUDED
        or node.has_attr("hidden")
        or str(node.get("aria-hidden", "")).lower() == "true"
    )


def visible_text(node, limit):
    """Walk a fixed number of nodes/strings, skipping nonvisible subtrees."""
    if node is None or hidden(node):
        return ""
    parts, length, strings, visited = [], 0, 0, 0
    stack = [iter(node.children)]
    while stack and visited < MAX_NODES and strings < MAX_STRINGS and length < limit:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
            continue
        visited += 1
        if isinstance(child, Comment):
            continue
        if isinstance(child, Tag):
            if not hidden(child) and len(stack) < 16:
                stack.append(iter(child.children))
        elif isinstance(child, NavigableString):
            strings += 1
            text = clean_text(child, limit - length)
            if text:
                parts.append(text)
                length += len(text) + 1
    return " ".join(parts)[:limit]


def record_text(node):
    """Avoid repeating page-sized containers as per-link context."""
    if node is None or node.name in BROAD_CONTAINERS:
        return ""
    links = 0
    for child in islice(node.descendants, MAX_NODES):
        if isinstance(child, Tag) and child.name == "a" and child.has_attr("href"):
            links += 1
            if links > 16:
                return ""
    return visible_text(node, MAX_RECORD_TEXT)


def extract_page(source, html):
    """Return JSON-safe current candidates with bounded natural-language inputs.

    Source metadata supplies only id/source_id, url/source/final_url, site_family
    and split. Labels, expected/ignored URLs, and selectors are never read.
    Candidate label retains its display string until the caller assigns a binary
    label. record_id becomes a stable page-local ordinal.
    """
    base = source.get("final_url") or source.get("source") or source["url"]
    source_id = source.get("source_id") or source.get("id")
    if not source_id:
        raise ValueError("A source_id or id is required")
    soup = BeautifulSoup(html, "html.parser")
    try:
        with canonical_url_cache():
            candidates = list(context_candidates(soup, base, limit=MAX_CANDIDATES))
        if not candidates:
            return []
        page_title = visible_text(soup.title, 180)
        preceding, last_heading = {}, None
        for node in soup.descendants:
            if not isinstance(node, Tag):
                continue
            if node.name == "a":
                preceding[id(node)] = last_heading
            if node.name in HEADINGS:
                last_heading = node
        text_cache, heading_cache, record_ids, rows = {}, {}, {}, []
        for candidate in candidates:
            anchor = candidate["anchor"]
            parents = list(islice(anchor.parents, 10))
            record = next(
                (
                    node
                    for node in [anchor, *parents]
                    if id(node) == candidate["record_id"]
                ),
                None,
            )
            record_key = candidate["record_id"]
            if record_key not in text_cache:
                text_cache[record_key] = record_text(record)
                record_ids[record_key] = len(record_ids)
            heading = next((p for p in parents if p.name in HEADINGS), None)
            if heading is None:
                heading = preceding.get(id(anchor))
            heading_key = id(heading)
            if heading_key not in heading_cache:
                heading_cache[heading_key] = visible_text(heading, 180)
            label = visible_text(anchor, 270)
            if not label:
                label = clean_text(anchor.get("aria-label", ""), 270)
                if not label:
                    image = next(
                        (
                            n
                            for n in islice(anchor.descendants, 40)
                            if isinstance(n, Tag) and n.name == "img"
                        ),
                        None,
                    )
                    label = clean_text(image.get("alt", ""), 270) if image else ""
            link_text = "\n".join(
                value for value in (label, path_text(candidate["url"])) if value
            )[:MAX_LINK_TEXT]
            contexts = []
            for value in (
                text_cache[record_key],
                heading_cache[heading_key],
                page_title,
            ):
                if value and value != label and value not in contexts:
                    contexts.append(value)
            context_text = "\n".join(contexts)[:MAX_CONTEXT_TEXT]
            row = {key: value for key, value in candidate.items() if key != "anchor"}
            row.update(
                source_id=source_id,
                source=base,
                site_family=source.get("site_family", ""),
                split=source.get("split", ""),
                record_id=record_ids[record_key],
                link_text=link_text,
                context_text=context_text,
                text="\n".join(value for value in (link_text, context_text) if value)[
                    :MAX_TEXT
                ],
            )
            rows.append(row)
        return rows
    finally:
        soup.decompose()


def without_neutral(rows, ignored_urls):
    """Exclude auxiliary scope after extraction, never learn it as negative."""
    ignored = set(ignored_urls)
    return [row for row in rows if row["url"] not in ignored]


def group_url_scores(rows, probabilities):
    """Return representative rows and max probability per (source_id, URL).

    Keep source IDs so differently scoped pages on one domain are not conflated.
    """
    grouped = {}
    for row, probability in zip(rows, probabilities, strict=True):
        key = row["source_id"], row["url"]
        if key in grouped:
            old, maximum = grouped[key]
            if old["label"] != row["label"]:
                raise ValueError("Conflicting labels for one source URL")
            grouped[key] = old, max(maximum, float(probability))
        else:
            grouped[key] = row, float(probability)
    return (
        [value[0] for value in grouped.values()],
        [value[1] for value in grouped.values()],
    )
