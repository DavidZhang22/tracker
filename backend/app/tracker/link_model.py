"""Small, local link classifier. Standard-library inference; no requests or training.

Features describe relationships and markup, never memorize a hostname or title.
The versioned JSON contains numeric weights only, never executable pickle data.
"""

import json
import logging
import math
import os
import re
from collections import Counter
from functools import lru_cache
from itertools import chain, islice
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .dom import HEADINGS, first_tag, tags
from .tables import anchor_label
from .urls import DiscoveryError, canonical_url

VERSION = 1
MAX_CANDIDATES = 4000
MAX_INSPECTED_ANCHORS = 20000
MODEL_PATH = Path(__file__).with_name("link-model.json")
CONTENT = re.compile(
    r"\b(?:chapter|episode|posts?|articles?|blog|news|essays?|releases?|contest|watch|stories|story)\b",
    re.I,
)
UTILITY = re.compile(
    r"\b(?:login|logout|sign.?in|sign.?up|register|privacy|terms|contact|about|search|tags?|category|author|users?|members|profile|settings|subscribe|donate|cart|sponsor|standings|submission|commit|compare|tree|issues|pull|discussion|comments?|archives?|pagination)\b",
    re.I,
)
GENERIC = re.compile(
    r"^(?:read(?: more)?|view(?: details)?|enter\s*[»→]?|next|previous|older|newer|home|more(?: articles)?[. ]*)$",
    re.I,
)
DATE = re.compile(
    r"\b(?:19|20)\d{2}[-/]|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b|\b\d+ (?:days?|hours?|years?) ago\b",
    re.I,
)
ASSET = re.compile(
    r"\.(?:png|jpe?g|gif|svg|css|js|ico|zip|gz|xz|exe|json|xml|atom|woff2?)(?:$|\?)",
    re.I,
)
TAGS = (
    "h1",
    "h2",
    "h3",
    "h4",
    "article",
    "li",
    "tr",
    "main",
    "nav",
    "header",
    "footer",
    "aside",
    "details",
    "p",
    "code",
)
FEATURES = (
    "same_host",
    "same_path",
    "child_path",
    "path_depth",
    "common_depth",
    "url_length",
    "query_count",
    "numeric_path",
    "dated_path",
    "content_path",
    "utility_path",
    "asset_path",
    "label_length",
    "label_words",
    "numeric_label",
    "generic_label",
    "date_label",
    "sequence_label",
    "content_label",
    "utility_label",
    "contains_heading",
    "semantic_title",
    "semantic_navigation",
    "semantic_body",
    "has_time",
    "record_links",
    "label_density",
    "shape_frequency",
    "position",
    "date_index",
    "month_label",
    "metadata_class",
) + tuple("in_" + name for name in TAGS)


def bounded(value, limit):
    return min(max(value / limit, 0.0), 1.0)


def _representative_quality(anchor):
    label = anchor_label(anchor)
    parents = list(islice(anchor.parents, 5))
    informative = bool(
        len(label) >= 3
        and not GENERIC.fullmatch(label)
        and not re.fullmatch(r"\d+(?:\.\d+)*", label)
    )
    heading = bool(first_tag(anchor, HEADINGS)) or any(
        parent.name in HEADINGS for parent in parents[:3]
    )
    title = any(
        re.search(r"title|headline", " ".join(node.get("class", [])), re.I)
        for node in [anchor] + parents[:3]
    )
    chrome = any(parent.name in {"nav", "footer"} for parent in parents)
    return not chrome, informative, heading or title, min(len(label), 150)


def candidate_anchors(soup, source, limit=MAX_CANDIDATES, *, aliases=None):
    """Select bounded anchors; crowded pages spend the budget on distinct URLs.

    ``aliases`` optionally maps inspected anchor IDs to their selected representative
    IDs. Missing IDs are unclassified, not accepted. Small pages retain their original
    duplicate anchors and raw-anchor limit for feature compatibility.
    """
    if limit < 0:
        raise ValueError("Candidate limit cannot be negative")
    limit = min(limit, MAX_CANDIDATES)
    if not limit:
        return []
    iterator = tags(soup, {"a"}, attribute="href", limit=MAX_INSPECTED_ANCHORS)
    initial = list(islice(iterator, MAX_CANDIDATES + 1))
    crowded = len(initial) > MAX_CANDIDATES
    selected, alias_ids, anchors = {}, {}, []
    inspected = chain(initial, iterator) if crowded else initial[:limit]
    for position, anchor in enumerate(inspected):
        try:
            url = canonical_url(anchor["href"], source)
        except (DiscoveryError, ValueError, UnicodeError):
            continue
        if not crowded:
            anchors.append((anchor, url, urlsplit(url)))
            if aliases is not None:
                aliases[id(anchor)] = id(anchor)
            continue
        previous = selected.get(url)
        if previous is None and len(selected) >= limit:
            continue
        quality = _representative_quality(anchor)
        if previous is None or quality > previous[0]:
            selected[url] = quality, position, anchor
        if aliases is not None:
            alias_ids.setdefault(url, []).append(id(anchor))
    if crowded:
        chosen = sorted(selected.items(), key=lambda item: item[1][1])
        anchors = [(row[2], url, urlsplit(url)) for url, row in chosen]
        if aliases is not None:
            for url, (_, _, anchor) in selected.items():
                aliases.update((key, id(anchor)) for key in alias_ids[url])
    return anchors


def candidates(soup, source, limit=MAX_CANDIDATES, *, aliases=None):
    """Bounded feature extraction shared verbatim by dataset builder and runtime."""
    root = urlsplit(source)
    root_parts = root.path.strip("/").split("/") if root.path.strip("/") else []
    anchors = candidate_anchors(soup, source, limit, aliases=aliases)
    shapes = Counter(
        (p.hostname, re.sub(r"/[^/]+/?$", "/*", p.path)) for _, _, p in anchors
    )
    record_cache = {}
    for position, (a, url, path) in enumerate(anchors):
        label = anchor_label(a)
        parents = list(islice(a.parents, 10))
        names = {p.name for p in parents}
        # Stop at the nearest record. Never aggregate an entire article archive.
        record = next(
            (p for p in parents[:5] if p.name in ("article", "li", "tr", "p")),
            a.parent or a,
        )
        if id(record) not in record_cache:
            text = " ".join(islice(record.stripped_strings, 60))[:1200]
            record_cache[id(record)] = (
                sum(1 for _ in tags(record, {"a"}, attribute="href", limit=32)),
                len(text),
                bool(first_tag(record, {"time", "relative-time"})),
            )
        link_count, record_length, has_time = record_cache[id(record)]
        ancestry = " ".join(
            str(p.get("id", "")) + " " + " ".join(p.get("class", []))
            for p in [a] + parents[:4]
        )[:1500]
        parts = path.path.strip("/").split("/") if path.path.strip("/") else []
        common = 0
        for left, right in zip(parts, root_parts, strict=False):
            if left != right:
                break
            common += 1
        values = [
            path.hostname == root.hostname,
            path.path.rstrip("/") == root.path.rstrip("/"),
            bool(root_parts)
            and parts[: len(root_parts)] == root_parts
            and len(parts) > len(root_parts),
            bounded(len(parts), 8),
            bounded(common, 6),
            bounded(len(path.path), 160),
            bounded(len(parse_qsl(path.query)), 5),
            bool(re.search(r"/\d+(?:/|$)", path.path)),
            bool(re.search(r"/(?:19|20)\d{2}/", path.path)),
            bool(CONTENT.search(path.path)),
            bool(UTILITY.search(path.path)),
            bool(ASSET.search(path.path)),
            bounded(len(label), 150),
            bounded(len(label.split()), 20),
            bool(re.fullmatch(r"\d+(?:\.\d+)*", label)),
            bool(GENERIC.fullmatch(label)),
            bool(DATE.search(label)),
            bool(
                re.search(r"\b(?:ch(?:apter)?|ep(?:isode)?|part)\.?\s*\d+", label, re.I)
            ),
            bool(CONTENT.search(label)),
            bool(UTILITY.search(label)),
            bool(first_tag(a, HEADINGS)),
            bool(re.search(r"title|headline", ancestry, re.I)),
            bool(
                re.search(
                    r"nav|menu|footer|sidebar|pagination|breadcrumb|subtext|subline|social",
                    ancestry,
                    re.I,
                )
            ),
            bool(
                re.search(
                    r"entry-content|post-content|article-body|markdown-body|description|summary",
                    ancestry,
                    re.I,
                )
            ),
            has_time,
            bounded(link_count, 20),
            bounded(len(label) / max(record_length, 1), 1),
            bounded(
                math.log1p(
                    shapes[path.hostname, re.sub(r"/[^/]+/?$", "/*", path.path)]
                ),
                6,
            ),
            bounded(position, max(len(anchors) - 1, 1)),
            bool(
                re.search(r"/(?:19|20)\d{2}(?:/(?:\d{1,2}|[a-z]{3,9}))?/?$", path.path)
            ),
            bool(
                re.fullmatch(
                    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+\d{4})?",
                    label,
                    re.I,
                )
            ),
            bool(
                re.search(
                    r"sitebit|domain|byline|author|tag|subtext|subline|meta|age|comment",
                    ancestry,
                    re.I,
                )
            ),
        ] + [tag in names for tag in TAGS]
        yield a, url, label, [float(value) for value in values]


class LinkModel:
    def __init__(self, data):
        if data["version"] != VERSION or data["features"] != list(FEATURES):
            raise ValueError("Incompatible link model features")
        self.lower, self.upper = (
            data["thresholds"]["reject"],
            data["thresholds"]["accept"],
        )
        if not 0 <= self.lower < self.upper <= 1:
            raise ValueError("Invalid model thresholds")
        self.layers = data["layers"]
        previous = len(FEATURES)
        if not 1 <= len(self.layers) <= 2:
            raise ValueError("Invalid model depth")
        for layer in self.layers:
            width = len(layer["bias"])
            if not 1 <= width <= 32 or len(layer["weights"]) != width:
                raise ValueError("Invalid model width")
            for weights, bias in zip(layer["weights"], layer["bias"], strict=True):
                if len(weights) != previous or not all(
                    math.isfinite(x) and abs(x) < 1e4 for x in [bias] + weights
                ):
                    raise ValueError("Invalid model weights")
            previous = width
        if previous != 1:
            raise ValueError("Model must have one output")
        self.model_id = data["model_id"]

    def score(self, features):
        if len(features) != len(FEATURES) or not all(
            math.isfinite(x) and 0 <= x <= 1 for x in features
        ):
            raise ValueError("Invalid link feature vector")
        values = features
        for index, layer in enumerate(self.layers):
            values = [
                bias + math.sumprod(weights, values)
                for weights, bias in zip(layer["weights"], layer["bias"], strict=True)
            ]
            if index != len(self.layers) - 1:
                values = [max(0.0, x) for x in values]
        return 1 / (1 + math.exp(-max(-60, min(60, values[0]))))


@lru_cache(maxsize=1)
def load_model():
    try:
        if MODEL_PATH.stat().st_size > 200_000:
            raise ValueError("Link model exceeds size limit")
        return LinkModel(json.loads(MODEL_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        logging.getLogger(__name__).warning(
            "Link model unavailable; using rule-based discovery"
        )
        return None


def page_scores(soup, source):
    if os.environ.get("TRACKER_LINK_MODEL", "on").lower() in ("0", "off", "false"):
        return {}, None
    model = load_model()
    if model is None:
        return {}, None
    return {
        id(a): model.score(features) for a, _, _, features in candidates(soup, source)
    }, model


def model_cache_tag():
    if os.environ.get("TRACKER_LINK_MODEL", "on").lower() in ("0", "off", "false"):
        return "rules-v3"
    if os.environ.get("TRACKER_LINK_MODEL", "on").lower() != "legacy":
        # Lazy import avoids the shared feature extractor's dependency cycle.
        from .context_model import active_context_model

        context = active_context_model()
        if context is not None:
            return (
                "context-v3:"
                + os.environ.get("TRACKER_LINK_MODEL", "on").lower()
                + ":"
                + context.model_id
            )
    model = load_model()
    return "rules-v3:" + (model.model_id if model else "fallback")
