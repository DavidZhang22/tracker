"""Record semantics that do not depend on a publisher's hostname or URL route."""

import re
from urllib.parse import unquote, urlsplit

from .dates import DATE_TEXT, evidence, node_dates
from .limits import MAX_LINKS
from .models import Entry
from .tables import anchor_label
from .urls import DiscoveryError, canonical_url, content_key

GENERIC_TITLE = re.compile(
    r"(?:read|view|open|show)(?:\s+(?:more|details|paper|article|post|entry|record|content))?",
    re.I,
)
FORMAT_LINK = re.compile(r"(?:pdf|html|xml|other(?: formats)?|download(?: pdf)?)", re.I)
NAVIGATION = re.compile(
    r"(?:^|[-_\s])(?:nav|navigation|menu|menubar|submenu|sidebar)(?:$|[-_\s])", re.I
)


def navigation_links(soup):
    excluded = set()
    for node in soup.find_all(True):
        if (
            node.name in {"nav", "footer", "aside"}
            or node.get("role")
            in {"navigation", "menu", "menubar", "banner", "contentinfo"}
            or (node.name == "header" and not node.find_parent("article"))
            or (
                NAVIGATION.search(
                    " ".join(node.get("class", [])) + " " + node.get("id", "")
                )
                and not node.find_parent("main")
                and not node.find_parent(attrs={"role": "main"})
                and not node.select_one("main,[role=main]")
                and node.name != "main"
                and node.get("role") != "main"
            )
        ):
            excluded.update(id(a) for a in node.find_all("a"))
    return excluded


def repeated_cards(soup, source, visible, context, excluded):
    """Extend a collection only when the model already accepts most sibling records."""
    groups, seen = {}, set()
    candidates = {}
    for node in soup.find_all(True):
        if (
            node.name == "a"
            and node.get("href")
            and re.search(
                r"(?:^|[-_ ])(?:heading|title)(?:$|[-_ ])",
                " ".join(node.get("class", [])),
                re.I,
            )
        ):
            candidates[id(node)] = node, node
        elif node.name in {"h2", "h3", "h4"} or node.get("role") == "heading":
            anchors = node.find_all("a", href=True)
            if not anchors and (anchor := node.find_parent("a", href=True)):
                anchors = [anchor]
            for anchor in anchors:
                candidates[id(anchor)] = anchor, node
    for anchor, heading in candidates.values():
        if id(anchor) in excluded:
            continue
        try:
            url = canonical_url(anchor["href"], source)
        except (DiscoveryError, ValueError):
            continue
        for depth, card in enumerate(anchor.parents):
            if depth > 5 or card.name in {"body", "html", "main"}:
                break
            if context.heading_count(card) > 1:
                break
            if context.sibling_boundary(card):
                if id(card) in seen:
                    break
                seen.add(id(card))
                date = context.node_dates(card)
                title = heading.get_text(" ", strip=True)
                if 4 <= len(title) <= 500:
                    key = id(card.parent), card.name, tuple(card.get("class", []))
                    groups.setdefault(key, []).append(
                        Entry(url, title, method="repeated cards", **date)
                    )
                break
    known = {content_key(e.url) for e in visible if e.url}
    return [
        e
        for group in groups.values()
        if len({content_key(e.url) for e in group} & known)
        >= max(2, (len(group) + 1) // 2)
        for e in group
    ][:MAX_LINKS]


def definition_entries(soup, source):
    result = []
    for listing in soup.find_all("dl", limit=100):
        group_date, term, records = {}, None, []
        for node in listing.find_all(recursive=False, limit=MAX_LINKS * 3):
            if node.name in {"h2", "h3", "h4", "h5"}:
                dates = [
                    evidence(m[0], "listing date heading", "listed")
                    for m in DATE_TEXT.finditer(node.get_text(" ", strip=True))
                ]
                group_date = dates[0] if len(dates) == 1 else {}
                term = None
            elif node.name == "dt":
                term = node
            elif node.name == "dd" and term is not None:
                title = node.select_one(
                    '[itemprop="name"],h2,h3,h4,.title,[class$="-title"],.list-title'
                )
                if title is None:
                    term = None
                    continue
                text = re.sub(
                    r"^\s*(?:title|name)\s*:\s*",
                    "",
                    title.get_text(" ", strip=True),
                    flags=re.I,
                )
                choices = []
                for anchor in term.select("a[href]"):
                    if FORMAT_LINK.fullmatch(anchor_label(anchor)) or re.search(
                        r"\.(?:pdf|xml|zip)$", anchor.get("href", ""), re.I
                    ):
                        continue
                    try:
                        url = canonical_url(anchor["href"], source)
                    except (DiscoveryError, ValueError):
                        continue
                    if urlsplit(url).netloc == urlsplit(source).netloc:
                        choices.append(url)
                choices = list(dict.fromkeys(choices))
                if len(choices) == 1 and len(text) >= 4:
                    date = node_dates(node)
                    if date.get("date_source") == "record text":
                        date = {}
                    records.append(
                        Entry(
                            choices[0],
                            text[:500],
                            context=node.get_text(" ", strip=True)[:1800],
                            method="definition list",
                            **(date or group_date),
                        )
                    )
                term = None
        if len(records) >= 2:
            result.extend(records)
        if len(result) >= MAX_LINKS:
            return result[:MAX_LINKS]
    return result


def observed_identifiers(entries):
    result = {}
    for entry in entries:
        leaf = unquote(urlsplit(entry.url).path.rstrip("/").rsplit("/", 1)[-1])
        result.setdefault(leaf, set()).add(entry.url)
    return result


def joined_metadata(record, identifiers):
    """Join metadata only to URLs already observed; never manufacture an ID route."""
    title = record.get("title") or record.get("headline") or record.get("name")
    if not isinstance(title, str) or len(title.strip()) < 4:
        return None
    identifier = record.get("id") or record.get("slug") or record.get("identifier")
    if not isinstance(identifier, (str, int)):
        return None
    urls = identifiers.get(str(identifier), ())
    if len(urls) != 1:
        return None
    date = next(
        (
            d
            for k in ("datePublished", "published_at", "publishedAt", "publicationDate")
            if (d := evidence(record.get(k), "embedded record metadata"))
        ),
        {},
    )
    if not date:
        return None
    return Entry(next(iter(urls)), title.strip()[:500], method="embedded data", **date)
