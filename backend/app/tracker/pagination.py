"""Select one explicit forward chain without guessing URLs or widening filters."""

import re
from itertools import chain, islice
from urllib.parse import parse_qs, urlsplit

from .limits import MAX_LINKS
from .urls import DiscoveryError, canonical_url, content_key

POSITIONS = {
    "page",
    "p",
    "paged",
    "offset",
    "start",
    "start_index",
    "skip",
    "cursor",
    "after",
}
FORWARD = re.compile(
    r"(?:go\s*to\s+)?(?:next|older)(?:\s+(?:page|posts?|entries|results))?\s*[→»›]?",
    re.I,
)
PROSE = re.compile(
    r"description|synopsis|summary|abstract|biography|excerpt|overview", re.I
)
LISTING = re.compile(
    r"chapters?|episodes?|posts?|entries|results|articles|pagination|pager|(?:^|[\s_-])list(?:$|[\s_-])",
    re.I,
)
AUXILIARY = re.compile(
    r"(?:^|[\s_-])(?:comments?|reviews?|discussion)(?:$|[\s_-])", re.I
)


def auxiliary_region(node):
    for region in islice(node.parents, 8):
        if region.name in {"html", "body", "main", "[document]"}:
            break
        label = " ".join(
            [
                region.get("id", ""),
                *region.get("class", []),
                region.get("aria-label", ""),
            ]
        )
        if AUXILIARY.search(label):
            return region
    return None


def tracked_link_locations(soup, source, entries):
    """Return bounded DOM evidence, or None when selection locations are unclear."""
    if not source or not entries or any(not entry.url for entry in entries):
        return None
    locations = {content_key(entry.url): set() for entry in entries}
    anchors = soup.find_all("a", href=True, limit=MAX_LINKS * 2 + 1)
    if len(anchors) > MAX_LINKS * 2:
        return None
    for anchor in anchors:
        try:
            key = content_key(canonical_url(anchor["href"], source))
        except (DiscoveryError, ValueError, TypeError):
            continue
        if key in locations:
            locations[key].add(id(anchor))
    return locations if all(locations.values()) else None


def tracked_links_outside(region, locations):
    if not locations:
        return False
    anchors = region.find_all("a", href=True, limit=513)
    if not anchors or len(anchors) > 512:
        return False
    # A bare pager does not establish the boundaries of the content it controls.
    if all(anchor.find_parent("nav") is not None for anchor in anchors):
        return False
    inside = {id(anchor) for anchor in anchors}
    # Chapter references can also occur in reviews. Require every tracked URL
    # outside the region, rather than mistaking those references for review rows.
    return all(nodes - inside for nodes in locations.values())


def text_expansion_control(node, soup):
    """Exclude local prose expanders, retaining ambiguous listing controls.

    A bare 'Show more' is common beside a truncated synopsis. Inspect only its
    explicit target or a small local container; never infer from page-wide text.
    Keep this conservative rule aligned with the browser worker's selector.
    """

    def prose_region(region):
        if region.name in {"html", "body", "main", "[document]"}:
            return False
        nodes = [region, *islice(region.find_all(True, limit=129), 129)]
        if len(nodes) > 128:
            return False
        for child in nodes:
            if (
                child.name in {"ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
                or child.get("role") in {"list", "listitem", "grid", "feed", "tree"}
                or LISTING.search(
                    " ".join([child.get("id", ""), *child.get("class", [])])
                )
                or child.name == "a"
                and child.get("href", "").strip()
                and not child.get("href", "").strip().startswith("#")
            ):
                return False
        paragraphs = [child for child in nodes if child.name == "p"]
        for child in nodes:
            label = " ".join(
                [
                    child.get("id", ""),
                    *child.get("class", []),
                    child.get("itemprop", ""),
                ]
            )
            marked = bool(PROSE.search(label) or "line-clamp" in label)
            if child.name in {"button", "a", "script", "style"}:
                continue
            if marked and len(child.get_text(" ", strip=True)) >= 40:
                return True
        return (
            len(paragraphs) == 1 and len(paragraphs[0].get_text(" ", strip=True)) >= 80
        )

    targets = node.get("aria-controls", "").split()
    for attribute in ("data-target", "data-bs-target"):
        value = node.get(attribute, "")
        if re.fullmatch(r"#[\w-]+", value):
            targets.append(value[1:])
    if targets:
        regions = [soup.find(id=target) for target in targets[:4]]
        # An explicit target takes precedence over unrelated neighboring prose.
        return len(targets) <= 4 and all(
            region is not None and prose_region(region) for region in regions
        )
    for parent in islice(node.parents, 3):
        if parent.name in {"html", "body", "main", "[document]"}:
            break
        if prose_region(parent):
            return True
    return False


def forward_pages(soup, source, fallback=(), *, same_path=False):
    base = urlsplit(source)
    query = parse_qs(base.query)
    filters = {k: v for k, v in query.items() if k not in POSITIONS}
    choices = {}
    for node in chain(
        soup.find_all("a", href=True), soup.find_all("link", href=True, rel="next")
    ):
        name = (node.get("aria-label") or node.get_text(" ", strip=True)).strip()
        explicit = "next" in node.get("rel", ()) or bool(FORWARD.fullmatch(name))
        pager = node.find_parent(
            class_=re.compile(r"pag(?:ing|ination|er)|page-numbers", re.I)
        )
        if not explicit and not (
            pager and re.fullmatch(r"\d+(?:\s*[-–]\s*\d+)?", name)
        ):
            continue
        try:
            url = canonical_url(node["href"], source)
        except (DiscoveryError, ValueError):
            continue
        p = urlsplit(url)
        if p.netloc != base.netloc or url == canonical_url(source):
            continue
        if same_path and (
            p.path.rstrip("/") != base.path.rstrip("/") or "review" in url.lower()
        ):
            continue
        incoming = parse_qs(p.query)
        if any(k in incoming and incoming[k] != value for k, value in filters.items()):
            continue
        preserved = all(incoming.get(k) == value for k, value in filters.items())
        if not preserved:
            continue
        increments = []
        for key in POSITIONS & incoming.keys():
            values = incoming[key]
            if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 9:
                continue
            current = query.get(key, ["1" if key in {"page", "p", "paged"} else "0"])
            if len(current) == 1 and current[0].isdigit():
                delta = int(values[0]) - int(current[0])
                if delta > 0:
                    increments.append(delta)
        if not explicit and (
            not increments or p.path.rstrip("/") != base.path.rstrip("/")
        ):
            continue
        choices[url] = (not preserved, not explicit, min(increments, default=1_000_000))
    if not choices:
        return [
            url
            for url in fallback
            if urlsplit(url).netloc == base.netloc
            and all(
                parse_qs(urlsplit(url).query).get(k) == value
                for k, value in filters.items()
            )
            and (
                not same_path
                or (
                    urlsplit(url).path.rstrip("/") == base.path.rstrip("/")
                    and "review" not in url.lower()
                )
            )
        ][:1]
    return [min(choices, key=choices.get)]
