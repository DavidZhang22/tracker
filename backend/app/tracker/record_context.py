"""Learned DOM record boundaries. No page scripts, remote model, or ML runtime."""

import json
import math
import re
from collections import Counter
from functools import lru_cache
from itertools import islice
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import Tag

from .dom import first_parent, first_tag
from .keywords import language_codes, language_text
from .limits import MAX_LINKS
from .page_context import PageContext

FEATURES = (
    "distance",
    "is_anchor",
    "semantic_record",
    "is_page",
    "record_class",
    "unique_links",
    "same_route_links",
    "other_links",
    "text_length",
    "link_fraction",
    "text_outside_link",
    "metadata_nodes",
    "date_nodes",
    "heading_nodes",
    "tag_count",
    "sibling_records",
    "parent_same_route_links",
    "has_navigation",
    "is_paragraph",
    "is_heading",
    "single_target",
    "single_route",
    "empty_siblings",
    "joined_sibling",
    "sibling_following",
    "sibling_links",
    "sibling_heading",
)
MODEL_PATH = Path(__file__).with_name("record-context-model.json")
EXCLUDED = {
    "script",
    "style",
    "template",
    "noscript",
    "nav",
    "footer",
    "select",
    "button",
}
RECORD = re.compile(
    r"(?:^|[-_ ])(?:row|card|entry|chapter|episode|post|release|result|item)(?:$|[-_ ])",
    re.I,
)
META = re.compile(
    r"lang|badge|meta|tag|genre|location|category|format|group|status", re.I
)


def route(href):
    try:
        path = urlsplit(href[:2048]).path
        return re.sub(r"/[^/]*$", "/*", path)
    except (TypeError, ValueError):
        return ""


def snippets(node, limit=1500):
    parts = []
    size = 0
    for child in islice(node.descendants, 500):
        previous = len(parts)
        if isinstance(child, Tag):
            if child.name in EXCLUDED:
                continue
            for attr in ("title", "alt", "aria-label", "data-language"):
                value = child.get(attr)
                if isinstance(value, str):
                    parts.append(
                        language_text(value)[:160]
                        if attr == "data-language"
                        else value[:160]
                    )
            if child.has_attr("lang") and child.name not in {"html", "body"}:
                parts.append(language_text(child["lang"]))
        elif child.strip() and not any(
            p.name in EXCLUDED for p in islice(child.parents, 10)
        ):
            parts.append(str(child).strip()[:240])
        size += sum(len(part) for part in parts[previous:])
        if size > limit:
            break
    for attr in ("title", "alt", "aria-label", "data-language"):
        if isinstance(node.get(attr), str):
            parts.append(
                language_text(node[attr])[:160]
                if attr == "data-language"
                else node[attr][:160]
            )
    if node.has_attr("lang") and node.name not in {"html", "body"}:
        parts.append(language_text(node["lang"]))
    return " ".join(dict.fromkeys(parts))[:limit]


def language_in_regions(roots):
    languages = set()
    for root in roots:
        if root is None:
            continue
        nodes = [root] + list(islice(root.descendants, 200))
        for node in nodes:
            if (
                not isinstance(node, Tag)
                or node.name in EXCLUDED
                or node.name
                in {"html", "body", "a", "h1", "h2", "h3", "h4", "h5", "h6"}
            ):
                continue
            values = [
                node.get(attr, "")
                for attr in ("lang", "data-language", "alt", "title", "aria-label")
            ]
            if node.name in {"span", "small", "div", "td"} and not node.find("a"):
                values.append(node.get_text(" ", strip=True)[:100])
            for value in values:
                if isinstance(value, str) and (codes := language_codes(value)):
                    languages.add(codes[0])
    return languages.pop() if len(languages) == 1 else ""


@lru_cache(maxsize=1)
def load_record_model():
    try:
        if MODEL_PATH.stat().st_size > 500_000:
            return None
        data = json.loads(MODEL_PATH.read_text(encoding="utf8"))
        if data["features"] != list(FEATURES) or data["version"] != 1:
            return None
        if (
            not isinstance(data.get("model_id"), str)
            or not 1 <= len(data["model_id"]) <= 100
        ):
            return None
        if not 0.5 <= data["threshold"] <= 0.99:
            return None
        if "layers" in data:
            width = len(FEATURES)
            if not 1 <= len(data["layers"]) <= 3:
                return None
            for layer in data["layers"]:
                count = len(layer["bias"])
                if (
                    not 1 <= count <= 64
                    or len(layer["weights"]) != count
                    or any(len(row) != width for row in layer["weights"])
                ):
                    return None
                if not all(
                    math.isfinite(v) and abs(v) < 1000
                    for row in layer["weights"] + [layer["bias"]]
                    for v in row
                ):
                    return None
                width = count
            return data if width == 1 else None
        if not 1 <= len(data["trees"]) <= 120:
            return None
        for tree in data["trees"]:
            if not 1 <= len(tree) <= 63:
                return None
            for i, (feature, threshold, left, right, value) in enumerate(tree):
                if (
                    not all(
                        math.isfinite(v)
                        for v in (feature, threshold, left, right, value)
                    )
                    or abs(value) > 100
                ):
                    return None
                if feature == -2 and left == right == -1:
                    continue
                if (
                    not isinstance(feature, int)
                    or not 0 <= feature < len(FEATURES)
                    or not all(
                        isinstance(j, int) and i < j < len(tree) for j in (left, right)
                    )
                ):
                    return None
        if not math.isfinite(data["intercept"]) or abs(data["intercept"]) > 100:
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None


def predict(data, features):
    if "layers" in data:
        values = features
        for i, layer in enumerate(data["layers"]):
            values = [
                bias + math.sumprod(weights, values)
                for weights, bias in zip(layer["weights"], layer["bias"], strict=True)
            ]
            if i + 1 < len(data["layers"]):
                values = [max(v, 0) for v in values]
        return 1 / (1 + math.exp(-max(-60, min(60, values[0]))))
    score = data["intercept"]
    for tree in data["trees"]:
        index = 0
        while tree[index][0] != -2:
            f, threshold, left, right, _ = tree[index]
            index = left if features[f] <= threshold else right
        score += tree[index][4]
    return 1 / (1 + math.exp(-max(-60, min(60, score))))


class RecordContext:
    def __init__(self, soup, regions=None, *, page=None):
        self.soup = soup
        self.page = page or PageContext()
        self.languages = {}
        self.stats = {}
        self.selected = dict(regions or {})
        self.sibling_stats = {}
        self.references = None
        model = load_record_model()
        self.model = model
        # Repeated layouts often produce identical feature vectors. Keep the
        # exact prediction, bounded to this page; no weights/thresholds change.
        self.score = lru_cache(maxsize=2048)(lambda features: predict(model, features))

    def siblings(self, node):
        parent = node.parent
        if parent is None:
            return 0, 0
        key = id(parent)
        if key not in self.sibling_stats:
            repeated, linked, empty = Counter(), set(), set()
            for sibling in islice(parent.children, 80):
                if not isinstance(sibling, Tag):
                    continue
                if first_tag(sibling, {"a"}, attribute="href"):
                    repeated[sibling.name] += 1
                    linked.add(id(sibling))
                elif sibling.name != "a":
                    empty.add(id(sibling))
            self.sibling_stats[key] = repeated, linked, empty
        repeated, linked, empty = self.sibling_stats[key]
        # Exclude this node only when it was within the original 80-child window.
        return repeated[node.name] - (id(node) in linked), len(empty) - (
            id(node) in empty
        )

    def info(self, node):
        key = id(node)
        if key not in self.stats:
            descendants = list(islice(node.descendants, 401))
            tags = [node, *descendants[:400]]
            tags = [t for t in tags if isinstance(t, Tag)]
            anchors = [t for t in tags if t.name == "a" and t.get("href")]
            hrefs = {a["href"] for a in anchors}
            self.stats[key] = dict(
                complete=len(descendants) <= 400,
                hrefs=hrefs,
                routes=Counter(route(u) for u in hrefs),
                text=self.page.snippet(node),
                metadata=sum(
                    bool(META.search(" ".join(t.get("class", []))))
                    or any(
                        t.has_attr(k)
                        for k in ("lang", "data-language", "aria-label", "alt")
                    )
                    for t in tags
                ),
                dates=sum(t.name == "time" or t.has_attr("datetime") for t in tags),
                headings=sum(t.name in {"h2", "h3", "h4", "h5", "h6"} for t in tags),
                navigation=any(t.name in {"nav", "footer", "select"} for t in tags),
                count=len(tags),
            )
        return self.stats[key]

    def candidates(self, anchor):
        shape = route(anchor.get("href", ""))
        label = anchor.get_text(" ", strip=True)
        parents = [anchor] + list(islice(anchor.parents, 8))
        for distance, node in enumerate(parents):
            if not isinstance(node, Tag) or node.name == "[document]":
                break
            info = self.info(node)
            repeated, empty = self.siblings(node)
            parent_routes = (
                self.info(node.parent)["routes"][shape]
                if isinstance(node.parent, Tag)
                else 0
            )
            same = info["routes"][shape]
            text_len = len(info["text"])
            features = [
                distance / 8,
                node is anchor,
                node.name in {"article", "li", "tr"},
                node.name in {"html", "body", "main"},
                bool(RECORD.search(" ".join(node.get("class", [])))),
                min(len(info["hrefs"]) / 12, 1),
                min(same / 6, 1),
                min((len(info["hrefs"]) - same) / 8, 1),
                min(text_len / 1500, 1),
                min(len(label) / max(text_len, 1), 1),
                min(max(0, text_len - len(label)) / 300, 1),
                min(info["metadata"] / 8, 1),
                min(info["dates"] / 5, 1),
                min(info["headings"] / 5, 1),
                min(info["count"] / 80, 1),
                min(repeated / 8, 1),
                min(parent_routes / 6, 1),
                info["navigation"],
                node.name == "p",
                node.name in {"h1", "h2", "h3", "h4", "h5", "h6"},
                len(info["hrefs"]) == 1,
                same == 1,
                min(empty / 8, 1),
                0,
                0,
                0,
                0,
            ]
            yield node, [float(f) for f in features]
            if node.name in {"body", "html"}:
                break

    def regions(self, anchor):
        shape = route(anchor.get("href", ""))
        for node, features in self.candidates(anchor):
            yield node, None, features
            if node.name in {"html", "body", "main"}:
                continue
            own = self.info(node)
            if own["count"] > 80:
                continue
            for following in (True, False):
                siblings = node.next_siblings if following else node.previous_siblings
                neighbor = next(
                    (s for s in islice(siblings, 8) if isinstance(s, Tag)), None
                )
                if (
                    neighbor is None
                    or neighbor.name in EXCLUDED
                    or neighbor.name in {"html", "body", "main"}
                ):
                    continue
                other = self.info(neighbor)
                if other["count"] > 80:
                    continue
                combined = own["hrefs"] | other["hrefs"]
                routes = Counter(route(u) for u in combined)
                length = len(own["text"]) + len(other["text"])
                label = len(anchor.get_text(" ", strip=True))
                updates = {
                    "unique_links": min(len(combined) / 12, 1),
                    "same_route_links": min(routes[shape] / 6, 1),
                    "other_links": min((len(combined) - routes[shape]) / 8, 1),
                    "text_length": min(length / 1500, 1),
                    "link_fraction": min(label / max(length, 1), 1),
                    "text_outside_link": min(max(0, length - label) / 300, 1),
                    "metadata_nodes": min((own["metadata"] + other["metadata"]) / 8, 1),
                    "date_nodes": min((own["dates"] + other["dates"]) / 5, 1),
                    "heading_nodes": min((own["headings"] + other["headings"]) / 5, 1),
                    "tag_count": min((own["count"] + other["count"]) / 80, 1),
                    "has_navigation": own["navigation"] or other["navigation"],
                    "single_target": len(combined) == 1,
                    "single_route": routes[shape] == 1,
                    "joined_sibling": 1,
                    "sibling_following": following,
                    "sibling_links": min(len(other["hrefs"]) / 8, 1),
                    "sibling_heading": neighbor.name
                    in {"h1", "h2", "h3", "h4", "h5", "h6"},
                }
                yield (
                    node,
                    neighbor,
                    [
                        float(updates.get(name, value))
                        for name, value in zip(FEATURES, features, strict=True)
                    ],
                )

    def region(self, anchor):
        if id(anchor) in self.selected:
            return self.selected[id(anchor)]
        if len(self.selected) >= MAX_LINKS:
            return anchor, None
        self.selected[id(anchor)] = self._region(anchor)
        return self.selected[id(anchor)]

    def _region(self, anchor):
        model = self.model
        choices = list(self.regions(anchor))
        if model:
            ranked = [
                (self.score(tuple(f)), -i, node, neighbor)
                for i, (node, neighbor, f) in enumerate(choices)
                if node.name not in {"html", "body", "main"}
            ]
            if ranked:
                probability, _, node, neighbor = max(ranked, key=lambda t: t[:2])
                if probability >= model["threshold"]:
                    return node, neighbor
            if record := self._single_target_record(anchor, choices):
                return record, None
        # A missing or invalid model still fails closed to the anchor.
        return anchor, None

    def _single_target_record(self, anchor, choices):
        if not (first_parent(anchor, {"h2", "h3"}) or first_tag(anchor, {"h2", "h3"})):
            return None
        for node, neighbor, _ in choices:
            if neighbor is not None or node is anchor:
                continue
            semantic = node.name in {"article", "li", "tr"}
            if not semantic and not RECORD.search(" ".join(node.get("class", []))):
                continue
            if node.name in {
                "html",
                "body",
                "main",
                "nav",
                "header",
                "footer",
                "aside",
            }:
                continue
            info = self.info(node)
            if (
                info["complete"]
                and info["hrefs"] == {anchor.get("href")}
                and not info["navigation"]
                and (semantic or self.siblings(node)[0] > 0)
            ):
                return node
        return None

    def record(self, anchor):
        return self.region(anchor)[0]

    def language(self, anchor):
        region = self.region(anchor)
        key = tuple(id(node) for node in region)
        if key not in self.languages:
            self.languages[key] = language_in_regions(region)
        return self.languages[key]

    def text(self, anchor):
        record, neighbor = self.region(anchor)
        if id(anchor) not in self.selected:
            return self.page.details(anchor, 240)
        parts = [self.page.details(record)]
        if neighbor is not None:
            parts.append(self.page.details(neighbor))
        # Explicit accessible references are stronger than physical proximity.
        for ref in (
            anchor.get("aria-describedby", "")
            + " "
            + record.get("aria-describedby", "")
        ).split()[:4]:
            if self.references is None:
                self.references = {}
                for node in self.soup.find_all(id=True):
                    self.references.setdefault(node["id"], node)
            if linked := self.references.get(ref):
                parts.append(self.page.details(linked, 240))
        # Section headings can describe a whole group; never take a neighboring row's label.
        for parent in [record] + list(islice(record.parents, 3)):
            if parent.name in {"body", "html", "main", "[document]"}:
                break
            for previous in islice(parent.previous_siblings, 30):
                if isinstance(previous, Tag) and previous.name in {
                    "h2",
                    "h3",
                    "h4",
                    "h5",
                    "h6",
                }:
                    parts.append(self.page.details(previous, 160))
                    break
            else:
                continue
            break
        return "\n".join(dict.fromkeys(part for part in parts if part))[:1800]
