"""Readable, bounded plain text from a selected record's DOM region."""

import re

from bs4 import NavigableString, Tag

from .keywords import language_codes, language_text

MAX_NODES = 1000
MAX_DEPTH = 64
EXCLUDED = {
    "script",
    "style",
    "template",
    "noscript",
    "nav",
    "footer",
    "select",
    "button",
    "head",
    "iframe",
    "object",
    "embed",
    "input",
    "textarea",
}
BLOCKS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "caption",
    "dd",
    "details",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tbody",
    "tfoot",
    "thead",
    "tr",
    "ul",
}
SPACE = re.compile(r"\s+")
CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HIDDEN_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))"
    r"\s*(?:!important\s*)?(?:;|$)",
    re.I,
)


def details_text(node, limit=1500):
    """Keep visible wording and record structure ahead of tooltip metadata.

    The caller selects the region. This never follows links or leaves that
    subtree, and both traversal and attribute/text processing are bounded.
    Accessible names are fallbacks for empty elements. Tooltips attached to
    visible labels are not detached from their owners and appended as facts.
    """
    if not isinstance(node, Tag) or limit <= 0:
        return ""
    parts, metadata, languages = [], {}, {}
    size = metadata_size = language_size = visited = text_nodes = 0
    remaining_work = max(4096, limit * 4)
    pending = ""

    def fallback_metadata(values):
        nonlocal metadata_size
        for key, value in values:
            if key not in metadata and metadata_size + len(value) <= limit:
                metadata[key] = value
                metadata_size += len(value)

    stack = [(iter((node,)), False, 0, ())]
    while stack and visited < MAX_NODES and size < limit and remaining_work > 0:
        children, close_block, started_at, fallback = stack[-1]
        try:
            child = next(children)
        except StopIteration:
            stack.pop()
            if started_at == text_nodes:
                fallback_metadata(fallback)
            if close_block and parts:
                pending = "\n"
            continue
        visited += 1
        if isinstance(child, Tag):
            attrs = child.attrs
            aria_hidden = attrs.get("aria-hidden")
            style = attrs.get("style")
            if (
                child.name in EXCLUDED
                or "hidden" in attrs
                or (isinstance(aria_hidden, str) and aria_hidden[:16].lower() == "true")
                or (isinstance(style, str) and HIDDEN_STYLE.search(style[:512]))
            ):
                continue
            fallback = []
            for attr in (
                ("data-language", "lang", "title", "alt", "aria-label") if attrs else ()
            ):
                value = attrs.get(attr)
                is_language = attr in {"lang", "data-language"}
                if not isinstance(value, str) or (
                    attr == "lang" and child.name in {"html", "body"}
                ):
                    continue
                value = value[:160]
                value = SPACE.sub(" ", CONTROLS.sub("", value)).strip()
                if not is_language and (codes := language_codes(value)):
                    is_language = True
                    value = codes[0]
                if is_language:
                    value = language_text(value)
                key = value.casefold()
                if not value:
                    continue
                if is_language:
                    if key not in languages and language_size + len(value) <= 320:
                        languages[key] = value
                        language_size += len(value)
                else:
                    fallback.append((key, value))
            is_block = child.name in BLOCKS
            if parts and (is_block or child.name == "br"):
                pending = "\n"
            elif parts and child.name in {"td", "th"} and pending != "\n":
                pending = " | "
            if (
                child.name not in {"svg", "canvas", "br", "hr"}
                and len(stack) < MAX_DEPTH
            ):
                stack.append((iter(child.children), is_block, text_nodes, fallback))
            elif child.name in {"svg", "canvas", "br", "hr"}:
                fallback_metadata(fallback)
        elif type(child) is NavigableString:
            # Do not stringify comments, processing instructions, or script data.
            text = child[:remaining_work]
            remaining_work -= len(text)
            text = SPACE.sub(" ", CONTROLS.sub("", text))
            if pending or not parts or parts[-1][-1:].isspace():
                text = text.lstrip()
            if not text:
                continue
            text = ((pending if parts else "") + text)[: limit - size]
            parts.append(text)
            text_nodes += bool(text.strip())
            size += len(text)
            pending = ""
    visible = re.sub(r" *\n *", "\n", "".join(parts)).strip()
    result = [visible] if visible else []
    size = len(visible)
    seen = visible.casefold()
    for key, value in (languages | metadata).items():
        if key in seen or size >= limit:
            continue
        value = value[: limit - size - bool(result)]
        if not value:
            break
        result.append(value)
        size += len(value) + (len(result) > 1)
        seen += "\n" + key
    return "\n".join(result)
