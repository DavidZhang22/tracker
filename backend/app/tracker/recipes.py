"""Validated, bounded DOM extraction recipes learned from a complete model scan.

Recipes contain data, never executable code or user-generated regular expressions.
Unknown shapes and evidence loss abandon the fast pass before anything is saved.
"""

import hashlib
import json
import re
import time
from itertools import islice
from urllib.parse import parse_qsl, urlsplit

from bs4 import Tag

from .dates import DATE_TEXT, node_dates
from .limits import MAX_LINKS
from .tables import anchor_label
from .urls import DiscoveryError, canonical_url, content_key

VERSION = 1
MAX_RECIPE_BYTES = 600_000
MAX_POLICIES = 2048
MAX_USES = 20
MAX_AGE = 7 * 86400


class RecipeMismatch(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def target(anchor, source):
    try:
        return canonical_url(anchor.get("href") or anchor.get("data-href"), source)
    except (DiscoveryError, ValueError, TypeError):
        return None


def url_shape(url):
    if not url:
        return None
    parsed = urlsplit(url)
    parts = parsed.path.split("/")
    for i, part in enumerate(parts):
        if re.fullmatch(r"\d+(?:\.\d+)?", part):
            parts[i] = "<number>"
        elif part and i == len(parts) - 1:
            # Literal parent paths and host remain part of the learned format.
            parts[i] = "<slug>"
    return [
        parsed.scheme,
        parsed.netloc,
        parts,
        [k for k, _ in parse_qsl(parsed.query)],
    ]


def tag_shape(node):
    return [
        node.name,
        sorted(node.get("class", [])),
        sorted(node.attrs),
        node.get("role"),
        node.get("type"),
        node.get("rel"),
        re.sub(r"\d+", "#", node.get("id", "")),
    ]


def signature(anchor, source, cache=None):
    return digest(
        [
            url_shape(target(anchor, source)),
            shape(anchor, cache),
            shape(anchor.find_parent(["tr", "article", "li"]) or anchor.parent, cache),
            bool(DATE_TEXT.search(anchor.get_text(" ", strip=True))),
            bool(
                re.fullmatch(
                    r"(?:log ?in|sign ?in|register|subscribe|privacy(?: policy)?|terms(?: of service)?|delete|contact|about|home|next|previous|older|newer)",
                    anchor_label(anchor),
                    re.I,
                )
            ),
            [
                tag_shape(t)
                for t in [anchor, *islice(anchor.parents, 8)]
                if isinstance(t, Tag)
            ],
        ]
    )


def shape(node, cache=None):
    if node is None:
        return None
    if cache is not None and id(node) in cache:
        return cache[id(node)]
    tags = [node, *(t for t in islice(node.descendants, 401) if isinstance(t, Tag))]
    value = digest([tag_shape(t) for t in tags])
    if cache is not None:
        cache[id(node)] = value
    return value


def auxiliary(soup):
    # Do not silently miss new script-provided records, changed table semantics,
    # or an index that moved into an unsupported hydration format.
    return digest(
        [[s.get("src"), s.get_text()] for s in soup.find_all("script", limit=128)]
        + [
            [n.name, n.get("props", "")]
            for n in soup.find_all("astro-island", limit=128)
            if '"chapters"' not in n.get("props", "")
        ]
        + [
            [t.get_text(" ", strip=True) for t in table.select("th")]
            for table in soup.find_all("table", limit=100)
        ]
    )


def route(anchor, node):
    if node is None:
        return None
    for up, parent in enumerate([anchor, *islice(anchor.parents, 8)]):
        if parent is node:
            return [up, []]
        path, current = [], node
        for _ in range(8):
            if current.parent is None:
                break
            siblings = current.parent.find_all(recursive=False)
            path.append(next(i for i, value in enumerate(siblings) if value is current))
            current = current.parent
            if current is parent:
                return [up, list(reversed(path))]
    raise RecipeMismatch("No bounded DOM route")


def locate(anchor, path):
    if path is None:
        return None
    if not isinstance(path, list) or len(path) != 2:
        raise RecipeMismatch("Invalid route")
    up, children = path
    if type(up) is not int or not 0 <= up <= 8 or len(children) > 8:
        raise RecipeMismatch("Invalid route bounds")
    node = anchor
    for _ in range(up):
        node = node.parent
        if node is None:
            raise RecipeMismatch("Missing ancestor")
    for index in children:
        nodes = node.find_all(recursive=False)
        if type(index) is not int or not 0 <= index < len(nodes):
            raise RecipeMismatch("Missing field")
        node = nodes[index]
    return node


def date_type(entry):
    return [
        bool(entry.published_at),
        entry.date_kind,
        entry.date_precision,
        entry.date_source if entry.published_at else "",
    ]


def bind_recipe(soup, source, recipe):
    if (
        recipe.get("version") != VERSION
        or recipe.get("source") != source
        or recipe.get("auxiliary") != auxiliary(soup)
    ):
        raise RecipeMismatch("Page envelope changed")
    bindings, fingerprints = {}, {}
    for a in soup.find_all("a", href=True):
        key = signature(a, source, fingerprints)
        if key not in recipe["policies"]:
            raise RecipeMismatch("Unfamiliar link format or layout")
        policy = recipe["policies"][key]
        if policy is None:
            bindings[id(a)] = None
            continue
        roots = (
            tuple(locate(a, p) for p in policy["regions"])
            if policy["regions"] is not None
            else None
        )
        if (
            roots is not None
            and [shape(n, fingerprints) for n in roots] != policy["region_shapes"]
        ):
            raise RecipeMismatch("Record or neighboring metadata changed")
        date_node = locate(a, policy["date_route"])
        if shape(date_node, fingerprints) != policy["date_shape"]:
            raise RecipeMismatch("Date layout changed")
        bindings[id(a)] = policy | {
            "regions": roots,
            "label_node": locate(a, policy["label_route"]),
            "date_node": date_node,
            "url": target(a, source),
        }
    return bindings


def validate_result(result, bindings, recipe):
    scan = result[0]
    expected = {content_key(p["url"]): p for p in bindings.values() if p}
    actual = {content_key(e.url): e for e in scan.entries}
    if set(actual) != set(expected) or not set(recipe["known_urls"]).issubset(actual):
        raise RecipeMismatch("Known links disappeared or selection changed")
    if not actual or len(actual) > MAX_LINKS:
        raise RecipeMismatch("Empty or oversized collection")
    for key, entry in actual.items():
        if date_type(entry) != expected[key]["date_type"]:
            raise RecipeMismatch("Publication evidence changed")


def learn_recipe(trace, result, source):
    from .parser import parse_page

    scan = result[0]
    if not trace or not 3 <= len(scan.entries) <= MAX_LINKS:
        return None
    if any(
        e.method in {"embedded data", "embedded chapter index", "feed"}
        for e in scan.entries
    ):
        return None  # These already have authoritative structured extraction.
    soup, context = trace["soup"], trace["context"]
    final = {content_key(e.url): e for e in scan.entries}
    policies, fingerprints = {}, {}
    try:
        for a in soup.find_all("a", href=True):
            key = signature(a, source, fingerprints)
            captured = trace["anchors"].get(id(a))
            entry = (
                final.get(content_key(target(a, source))) if target(a, source) else None
            )
            policy = None
            if captured and entry:
                _, raw, label, date = captured
                label_node = None
                if anchor_label(a) != label:
                    container = a.find_parent(["tr", "article", "li"]) or a
                    if not (
                        trace["tables"].get(id(a), {}).get("job")
                        and trace["tables"].get(id(a), {}).get("action")
                    ):
                        label_node = next(
                            (
                                n
                                for n in [
                                    container,
                                    *islice(container.descendants, 400),
                                ]
                                if isinstance(n, Tag) and anchor_label(n) == label
                            ),
                            None,
                        )
                        if label_node is None:
                            raise RecipeMismatch("Unsupported title derivation")
                roots = context.region(a)
                date_node = None
                if date:
                    date_node = next(
                        (
                            n
                            for n in [a, *islice(a.parents, 6)]
                            if n.name not in {"html", "body", "main", "[document]"}
                            and node_dates(n).get("published_at")
                            == date.get("published_at")
                        ),
                        None,
                    )
                policy = dict(
                    regions=[route(a, n) for n in roots],
                    region_shapes=[shape(n, fingerprints) for n in roots],
                    label_route=route(a, label_node),
                    date_route=route(a, date_node),
                    date_shape=shape(date_node, fingerprints),
                    date_skip=not date
                    and entry.date_source == "Astro chapter published_at",
                    date_metadata={
                        k: v
                        for k, v in date.items()
                        if k != "published_at"
                        and date_node is not None
                        and node_dates(date_node).get(k) != v
                    },
                    date_type=date_type(entry),
                    method=raw.method,
                )
            if key in policies and policies[key] != policy:
                differing = {
                    k for k in policy or {} if (policies[key] or {}).get(k) != policy[k]
                }
                if (
                    policies[key]
                    and policy
                    and differing <= {"regions", "region_shapes"}
                ):
                    # A repeated table shape can need content-dependent context.
                    # Keep its record model instead of guessing the neighborhood.
                    policy = policy | {"regions": None, "region_shapes": None}
                else:
                    raise RecipeMismatch("One layout has conflicting model decisions")
            policies[key] = policy
            if len(policies) > MAX_POLICIES:
                raise RecipeMismatch("Too many layout variants")
        recipe = dict(
            version=VERSION,
            source=source,
            policies=policies,
            auxiliary=auxiliary(soup),
            known_urls=list(final),
            created=time.time(),
            uses=0,
        )
        if len(json.dumps(recipe).encode()) > MAX_RECIPE_BYTES:
            return None
        # Every field, order, pagination link, warning, and suggestion must match.
        replay = parse_page(str(soup), source, learned=recipe)
        if replay != result:
            trace["recipe_rejection"] = "Shadow output differs"
            return None
        return recipe
    except (
        RecipeMismatch,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OverflowError,
    ) as exc:
        trace["recipe_rejection"] = str(exc)
        return None


def analyze(text, source, selector="", include_path="", recipe=None):
    from .parser import parse_page

    valid = False
    if isinstance(recipe, dict):
        created, uses = recipe.get("created"), recipe.get("uses")
        valid = (
            recipe.get("version") == VERSION
            and type(created) in (int, float)
            and 0 <= time.time() - created < MAX_AGE
            and type(uses) is int
            and 0 <= uses < MAX_USES
            and len(json.dumps(recipe).encode()) <= MAX_RECIPE_BYTES
        )
    if valid and not selector and not include_path:
        try:
            result = parse_page(text, source, learned=recipe)
            recipe = recipe | {
                "uses": recipe["uses"] + 1,
                "known_urls": [content_key(e.url) for e in result[0].entries],
            }
            return result, recipe, True
        except (
            RecipeMismatch,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OverflowError,
        ):
            pass  # Run the full parser on the same fetched document, never refetch.
    trace = {}
    result = parse_page(text, source, selector, include_path, trace=trace)
    learned = (
        learn_recipe(trace, result, source)
        if not selector and not include_path
        else None
    )
    return result, learned, False
