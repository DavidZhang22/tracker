"""Extract bounded listing records, including titles that have no link target."""

import re
from collections import Counter
from itertools import islice

from .dates import DATE_TEXT, node_dates
from .entry_identity import normalized, record_id
from .limits import MAX_LINKS
from .models import Entry, sequence_value
from .record_context import language_in_regions
from .urls import DiscoveryError, canonical_url, content_key

COLLECTION = re.compile(
    r"chapters?|episodes?|table.of.contents|catalog(?:ue)?|\btoc\b", re.I
)
ROW = re.compile(
    r"(?:chapter|episode|entry)[-_ ](?:row|item)|(?:chapter|episode)[-_ ]?\d+$", re.I
)
NAVIGATION = re.compile(
    r"(?:^|[-_ ])(?:nav|menu|pagination|pager|breadcrumb|social)(?:$|[-_ ])", re.I
)
NUMBERED = re.compile(r"^(?:chapter|episode|part|ch\.?|ep\.?)\s*[-:#]?\s*\d", re.I)
AGE = re.compile(
    r"\s*\b\d+\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago\s*$",
    re.I,
)
LABEL = (
    '[itemprop="name"],.chapter-title,.episode-title,.entry-title,.title,h2,h3,h4,h5'
)
ROWS = 'li,tr,article,[role="listitem"],[data-chapter-id],[data-episode-id]'
MAX_CANDIDATES = MAX_LINKS * 4


def safe_target(value, source):
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.lstrip().startswith("#")
    ):
        return ""
    try:
        return canonical_url(value, source)
    except (DiscoveryError, ValueError, TypeError):
        return ""


def label_text(row):
    node = row.select_one(LABEL) or row
    text = " ".join(
        value.strip()
        for value in islice(node.strings, 100)
        if not value.find_parent(["time", "relative-time"])
    )
    if not 2 <= len(text) <= 1000:
        return ""
    return AGE.sub("", DATE_TEXT.sub("", text)).strip(" ·|\n\t")


def scope_and_context(row):
    """Keep volume/season boundaries, but never use a list's changing position."""
    for node in islice(row.parents, 6):
        if node.name in {"html", "body", "main", "[document]"}:
            break
        heading = node.find(["h2", "h3", "h4", "h5"], recursive=False)
        if heading is None and node.name in {"ul", "ol", "table"}:
            previous = node.find_previous_sibling()
            if previous and previous.name in {"h2", "h3", "h4", "h5"}:
                heading = previous
        if heading:
            text = heading.get_text(" ", strip=True)[:200]
            match = re.search(r"\b(volume|season|book)\s*(\d+)\b", text, re.I)
            if match:
                return " ".join(match.groups()), text
    return "", ""


def candidates(soup, selector):
    if selector:
        selected = soup.select(selector)
        rows = []
        for node in selected[:MAX_CANDIDATES]:
            children = (
                node.select(ROWS)
                if node.name not in {"a", "li", "tr", "article"}
                else []
            )
            rows.extend(children or [node])
            if len(rows) >= MAX_CANDIDATES:
                break
        return rows[:MAX_CANDIDATES]
    rows = soup.select(ROWS)[:MAX_CANDIDATES]
    # Some JavaScript frontends use divs for every row, even in their rendered DOM.
    rows.extend(
        node
        for node in soup.find_all("div", limit=MAX_CANDIDATES)
        if ROW.search(" ".join(node.get("class", [])))
    )
    return rows[:MAX_CANDIDATES]


def extract_list_entries(soup, source, selector="", include_path=""):
    """Explicit selectors may choose arbitrary records; automatic lists need evidence.

    A missing URL remains empty. Fragment/JavaScript handlers are never turned into
    guessed URLs, and record text/date lookup stops at the selected row boundary.
    """
    records, seen, scopes = [], set(), {}
    for row in candidates(soup, selector):
        if id(row) in seen:
            continue
        seen.add(id(row))
        ancestors = [row, *islice(row.parents, 8)]
        if any(
            n.name
            in {
                "nav",
                "footer",
                "header",
                "form",
                "select",
                "script",
                "style",
                "template",
            }
            or NAVIGATION.search(" ".join(n.get("class", [])) + " " + n.get("id", ""))
            or n.get("role") in {"navigation", "menu", "menubar", "tablist", "listbox"}
            for n in ancestors
        ):
            continue
        if not selector and any(
            re.search(
                r"(?:^|[-_ ])(?:comments?|reviews?|related|recommendations?)(?:$|[-_ ])",
                " ".join(n.get("class", [])) + " " + n.get("id", ""),
                re.I,
            )
            for n in ancestors
        ):
            continue
        if row.name in {"li", "tr", "article"} and row.find(row.name):
            continue  # An enclosing volume/list is not itself a chapter.
        if row.name == "tr" and not row.find("td", recursive=False):
            continue
        title = label_text(row)
        if not title or re.fullmatch(
            r"(?:read|unlock|next|previous|load|show)(?:\s+more)?|\d+", title, re.I
        ):
            continue
        semantic = any(
            COLLECTION.search(" ".join(n.get("class", [])) + " " + n.get("id", ""))
            or n.get("data-chapter-id")
            or n.get("data-episode-id")
            for n in ancestors[:5]
        )
        # A plain ordered list headed "Chapters" is as meaningful as .chapter-list.
        parent = row.parent
        previous = parent.find_previous_sibling() if parent else None
        if previous and previous.name in {"h2", "h3", "h4", "h5"}:
            semantic = semantic or bool(
                COLLECTION.search(previous.get_text(" ", strip=True))
            )
        number = sequence_value(title)
        date = node_dates(row)
        numbered = bool(NUMBERED.match(title))
        article = row.name == "article" and row.select_one(LABEL) and date
        if not selector and not (semantic or numbered or article):
            continue
        anchor_nodes = [row] if row.name == "a" else row.find_all("a", limit=20)
        targets = {
            u
            for a in anchor_nodes
            if (u := safe_target(a.get("href") or a.get("data-href"), source))
        }
        direct = safe_target(row.get("data-href"), source)
        if direct:
            targets.add(direct)
        # Multi-link cards remain the link classifier's responsibility.
        if len(targets) > 1:
            continue
        url = next(iter(targets), "")
        if include_path and (not url or include_path not in url):
            continue
        group = id(parent)
        if group not in scopes:
            scopes[group] = scope_and_context(row)
        scope, heading = scopes[group]
        identifier = next(
            (
                row.get(k)
                for k in (
                    "data-chapter-id",
                    "data-episode-id",
                    "data-entry-id",
                    "data-id",
                )
                if row.get(k)
            ),
            "",
        )
        lang_node = next((n for n in ancestors[:5] if n.get("lang")), None)
        language = language_in_regions([row]) or (
            str(lang_node.get("lang", ""))[:30] if lang_node else ""
        )
        context = " ".join(islice(row.stripped_strings, 120))[:1400]
        records.append(
            {
                "group": id(parent),
                "scope": scope,
                "identifier": identifier,
                "numbered": numbered,
                "semantic": semantic,
                "entry": Entry(
                    url,
                    title,
                    number=number,
                    method="content list",
                    language=language,
                    context=(heading + " " + context).strip(),
                    **date,
                ),
            }
        )

    groups = Counter(r["group"] for r in records)
    explicit_numbers = Counter(r["group"] for r in records if r["numbered"])

    def identity_slot(record):
        entry = record["entry"]
        return (
            record["scope"],
            entry.number if entry.number is not None else normalized(entry.title),
            entry.language,
        )

    identities, targets = {}, {}
    for r in records:
        e = r["entry"]
        identities.setdefault(identity_slot(r), set()).add(normalized(e.title))
        if e.url:
            targets.setdefault(identity_slot(r), set()).add(content_key(e.url))
    result = []
    for r in records:
        entry = r["entry"]
        if not selector and not (
            r["semantic"]
            or explicit_numbers[r["group"]] >= 2
            or groups[r["group"]] >= 2
            and entry.published_at
        ):
            continue
        value = (
            "id:" + str(r["identifier"])
            if r["identifier"]
            else (
                f"number:{entry.number:g}"
                if entry.number is not None and len(identities[identity_slot(r)]) == 1
                else "title:" + normalized(entry.title)
            )
        )
        if not r["identifier"] and len(targets.get(identity_slot(r), ())) > 1:
            # Identical labels can be different translations/editions. A title or
            # chapter number alone must never coalesce distinct published URLs.
            value = (
                "url:" + content_key(entry.url)
                if entry.url
                else "title:" + normalized(entry.title)
            )
        entry.source_id = record_id(r["scope"] + " " + entry.language, value)
        result.append(entry)
        if len(result) > MAX_LINKS:
            break
    return result
