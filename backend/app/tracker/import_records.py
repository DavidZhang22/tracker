"""Bounded, source-authored records for uploads with optional hyperlinks."""

import re
from collections import defaultdict

from markdown_it import MarkdownIt

from .entry_identity import normalized, record_id
from .models import Entry, sequence_value

BOLD = re.compile(r"\*\*([^*\n]{1,300})\*\*")
BLOCKS = {"tr", "li", "p", "dt", "h1", "h2", "h3", "h4", "h5", "h6"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
CHROME = {"nav", "footer", "header", "button", "select", "head"}
HEADER_WORDS = {
    "title",
    "name",
    "problem",
    "task",
    "item",
    "topic",
    "question",
    "exercise",
    "number",
    "id",
    "notes",
    "details",
    "description",
    "approach",
    "status",
    "date",
    "url",
    "link",
    "technique",
    "category",
    "priority",
    "deadline",
}


def text_markup(text):
    # Restore boundaries in rich-text pastes that lost newlines between bold titles.
    bold = list(BOLD.finditer(text))
    if len(bold) >= 2 and not text[: bold[0].start()].strip():
        numbered = all(re.match(r"^\d+[.)]\s+", m[1]) for m in bold)
        adjacent = all(
            m.start() and not text[m.start() - 1].isspace() for m in bold[1:]
        )
        if numbered or adjacent:
            starts = [0, *(m.start() for m in bold[1:]), len(text)]
            text = "\n\n".join(
                text[a:b] for a, b in zip(starts[:-1], starts[1:], strict=True)
            )
    # Markdown otherwise renumbers a non-consecutive problem/task list.
    text = re.sub(
        r"(?m)^(\s*)(\d+)([.)])\s+", lambda m: f"{m[1]}- {m[2]}\\{m[3]} ", text
    )
    return (
        MarkdownIt("commonmark", {"html": False, "breaks": True})
        .enable("table")
        .render(text)
    )


def assign_import_ids(entries):
    groups = defaultdict(list)
    for entry in entries:
        number = (
            entry.number if entry.number is not None else sequence_value(entry.title)
        )
        groups[(normalized(entry.title), number)].append(entry)
    for key, group in groups.items():
        variants = {(e.url, normalized(e.context)) for e in group}
        for entry in group:
            identity = repr(key)
            if len(variants) > 1:
                identity += "\n" + (entry.url or normalized(entry.context))
            entry.source_id = record_id("upload-entry", identity)


def plain_records(soup, page, consumed):
    """Use explicit document boundaries, never inventing a destination for text."""
    tables = {}
    result = []
    for node in soup.find_all(list(BLOCKS)):
        if node.find_parent(list(CHROME)) or id(node) in consumed:
            continue
        if node.find_parent(["tr", "li", "dt"]) or node.find(["tr", "li", "dt"]):
            continue
        if node.find("a", href=True):
            continue
        text = node.get_text(" ", strip=True)
        if not text or text in {"---", "***"}:
            continue
        if node.name in HEADINGS:
            # A heading that introduces a list/table is a section label.
            following = node.find_next_sibling()
            if following and following.name in {"ul", "ol", "table"}:
                continue
        label, context = text, text
        if node.name in HEADINGS:
            details = []
            for sibling in node.next_siblings:
                if getattr(sibling, "name", None) is None:
                    continue
                if sibling.name != "p" or sibling.find("a", href=True):
                    break
                details.append(sibling.get_text(" ", strip=True))
                consumed.add(id(sibling))
            context = "\n".join(details) or text
        if node.name == "tr":
            cells = node.find_all(["th", "td"], recursive=False)
            values = [c.get_text(" ", strip=True) for c in cells]
            table = node.find_parent("table")
            table_id = id(table)
            if table_id not in tables:
                tables[table_id] = []
                if any(c.name == "th" for c in cells) or (
                    len(values) > 1
                    and all(normalized(v) in HEADER_WORDS for v in values if v)
                ):
                    tables[table_id] = values
                    continue
            headings = tables[table_id]
            populated = [v for v in values if v]
            if not populated:
                continue
            label = populated[0]
            if label.isdigit() and len(populated) > 1:
                label += ". " + populated[1]
            context = "\n".join(
                f"{headings[i]}: {v}" if i < len(headings) and headings[i] else v
                for i, v in enumerate(values)
                if v
            )
        else:
            first = node.find(["strong", "b"])
            if first and text.startswith(first.get_text(" ", strip=True)):
                label = first.get_text(" ", strip=True)
                context = text[len(label) :].strip() or text
            elif node.name == "li":
                label = re.sub(r"^\[[ xX]\]\s*", "", label)
            if node.name == "dt":
                details = node.find_next_sibling("dd")
                if details:
                    context = details.get_text(" ", strip=True)
        result.append(
            (
                node,
                Entry(
                    "",
                    label[:300],
                    context=context[:8192],
                    number=sequence_value(label),
                    method="Document import",
                    **page.node_dates(node),
                ),
            )
        )
    return result
