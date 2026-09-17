"""Correlate dates with one record; never borrow a date from a neighboring record."""

import re
from itertools import islice

from bs4 import Tag

from .models import date_value

DATE_TEXT = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T ]\d{2}:\d{2}(?::[\d.]+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?[ /]+\d{1,2}[ ,/]+\d{4}(?: \d{2}:\d{2})?"
    r"|\d{1,2} (?:Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*) \d{4}",
    re.I,
)


def evidence(raw, source, kind="published"):
    value = date_value(raw)
    if not value:
        return {}
    precise = isinstance(raw, (float, int)) or bool(
        re.search(r"\d:\d|^\d{10,13}$", str(raw))
    )
    if precise and not (
        isinstance(raw, (float, int))
        or re.fullmatch(r"\d{10,13}", str(raw))
        or re.search(r"(?:Z|[+-]\d{2}:?\d{2}|UTC|GMT)\s*$", str(raw), re.I)
    ):
        # A clock without a time zone cannot be compared globally as an instant.
        value, precise = value[:10] + "T00:00:00+00:00", False
        source += " (time zone unspecified; date only)"
    return dict(
        published_at=value,
        date_kind=kind,
        date_source=source,
        date_precision="time" if precise else "day",
    )


def node_dates(node):
    found = []
    labeled = []
    candidates = [node] + [
        t
        for t in node.descendants
        if isinstance(t, Tag)
        and (
            t.name in {"time", "relative-time"}
            or any(
                attr in t.attrs
                for attr in ("datetime", "data-date", "data-timestamp", "title")
            )
        )
    ]
    for t in candidates:
        for attr in ("datetime", "data-date", "data-timestamp", "title"):
            if d := evidence(t.get(attr), f"{t.name}[{attr}]"):
                found.append(d)
                nearby = ""
                if t.parent and t.name in {"time", "relative-time"}:
                    strings = (
                        t.parent.stripped_strings
                        if len(t.parent.find_all(["time", "relative-time"], limit=2))
                        == 1
                        else t.parent.find_all(string=True, recursive=False)
                    )
                    nearby = " ".join(islice(strings, 30))[:301]
                if len(nearby) < 300 and re.search(
                    r"\b(?:published|released|posted)\b", nearby, re.I
                ):
                    labeled.append(d)
                break
        else:
            if t.name in {"time", "relative-time"}:
                if d := evidence(t.get_text(" ", strip=True), t.name):
                    found.append(d)
    if not found:
        # Publication metadata outranks dates mentioned in a headline or summary.
        for tag in [node, *node.find_all(["span", "p", "div"], limit=100)]:
            text = tag.get_text(" ", strip=True)
            if len(text) > 350:
                continue
            if DATE_TEXT.fullmatch(text.strip(" .,:;()")):
                if d := evidence(text.strip(" .,:;()"), "record date"):
                    labeled.append(d)
                continue
            classes = " ".join(tag.get("class", []))
            if not (
                re.search(r"\b(?:published|released|posted)\b", text, re.I)
                or re.search(
                    r"(?:^|[-_ ])(?:pubdate|published|release-date|post-date)(?:$|[-_ ])",
                    classes,
                    re.I,
                )
            ):
                continue
            candidates = [
                evidence(m[0], "publication metadata") for m in DATE_TEXT.finditer(text)
            ]
            unique_labeled = {d["published_at"]: d for d in candidates if d}
            if len(unique_labeled) == 1:
                labeled.extend(unique_labeled.values())
        for m in DATE_TEXT.finditer(node.get_text(" ", strip=True)):
            if d := evidence(m[0], "record text"):
                found.append(d)
    # A card with a publication and modification date is ambiguous unless labeled.
    unique = {d["published_at"]: d for d in labeled or found}
    return next(iter(unique.values())) if len(unique) == 1 else {}


def link_date(anchor, context=None):
    from .page_context import PageContext

    context = context or PageContext()
    dates = context.node_dates
    # Archive anchors often carry their own date (e.g. xkcd).
    own = dates(anchor)
    if own and own.get("date_source") != "record text":
        return own
    row = anchor.find_parent("tr")
    if row:
        if d := dates(row):
            return d
        # Paired title/metadata rows are a common news-board layout.
        next_row = row.find_next_sibling("tr")
        if (
            row.get("id")
            and next_row
            and not next_row.get("id")
            and anchor.find_parent(class_=re.compile("titleline|headline"))
        ):
            if d := dates(next_row):
                return d | {
                    "date_kind": "listed",
                    "date_source": "following metadata row",
                }
        return own or {}
    for depth, parent in enumerate(anchor.parents):
        if depth > 5 or parent.name in {"body", "html", "main", "table"}:
            break
        if context.heading_count(parent) > 1:
            break
        # Do not cross from one list/card into its siblings.
        if len(parent.find_all(["article", "li"], recursive=False)) > 1:
            break
        if d := dates(parent):
            if d.get("date_source") != "record text":
                return d
            own = own or d
        if parent.name in {"article", "li"}:
            break
        # Repeated cards are boundaries even when one card has no date.
        if context.sibling_boundary(parent):
            break
    return own or {}
