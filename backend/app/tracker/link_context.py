"""Bounded record context and tokenization shared by training and inference."""

import re
import unicodedata
from collections import Counter
from itertools import islice
from urllib.parse import parse_qsl, unquote, urlsplit

from bs4 import Tag

from .dom import HEADINGS, first_tag, tags
from .link_model import candidates as base_candidates
from .tables import TABLE_FEATURES, table_context

EXTRA_FEATURES = (
    "own_date_attribute",
    "root_target",
    "has_fragment",
    "heading_level",
    "preceding_heading_level",
    "heading_distance",
    "heading_text_overlap",
    "record_heading_count",
    "record_words",
    "paragraph_words",
    "inline_text_fraction",
    "sentence_punctuation",
    "record_first_link",
    "same_url_count",
    "same_url_has_heading",
    "same_url_has_date",
    "same_url_max_label",
    "record_primary_url",
    "record_other_primary",
    "path_date_index",
    "rel_author",
    "rel_tag",
    "inside_list_record",
    "inside_content_body",
    "heading_link_fraction",
    "preceding_heading_linked",
    "next_link_same_record",
    "prev_link_same_record",
    "same_template_count",
) + TABLE_FEATURES
STOP = frozenset(
    "a an the and or of for to in on at by is are with from this that as it be you your our".split()
)
GENERIC = re.compile(
    r"^(?:read(?: more| the full article)?|full story|continue reading|view(?: details)?|enter|comments?(?:\s*[:(].*)?|#|\d+(?::\d+)?(?:\s*[ap]m)?)$",
    re.I,
)


def words(value):
    value = unicodedata.normalize("NFKC", unquote(str(value)[:1500]))
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    value = value.casefold()
    return [
        w
        for w in re.findall(r"[^\W\d_]+|\d+", value, re.UNICODE)[:100]
        if (2 <= len(w) <= 25 and w not in STOP) or w.isdigit()
    ]


def tokens(label, url, ancestry, heading):
    path = urlsplit(url)
    fields = {"t": label, "u": path.path, "c": ancestry, "h": heading}
    result = []
    for channel, value in fields.items():
        pieces = ["num" if w.isdigit() else w for w in words(value)]
        result.extend(channel + ":" + w for w in pieces)
        if channel in ("t", "u"):
            result.extend(
                channel + ":" + a + "_" + b
                for a, b in zip(pieces, pieces[1:], strict=False)
            )
    # Query values and hostnames are deliberately never vectorized.
    result.extend("q:" + w for key, _ in parse_qsl(path.query) for w in words(key))
    return sorted(set(result))[:220]


def context_candidates(soup, source, limit=4000, *, tables=None):
    base = list(base_candidates(soup, source, limit))
    if tables is None:
        tables = table_context(soup)
    if not base:
        return
    node_order, heading_before, previous_heading = {}, {}, None
    count = 0
    for node in soup.descendants:
        if not isinstance(node, Tag):
            continue
        node_order[id(node)] = count
        if node.name == "a":
            heading_before[id(node)] = previous_heading
        if node.name in HEADINGS:
            previous_heading = node
        count += 1
    info, url_info, template_counts, record_info = [], {}, Counter(), {}
    for a, url, label, features in base:
        parents = list(islice(a.parents, 10))
        heading = first_tag(a, HEADINGS) or next(
            (p for p in parents if p.name in HEADINGS), None
        )
        before = heading_before.get(id(a))
        record = next(
            (
                p
                for p in parents[:6]
                if p.name in ("article", "li", "tr")
                or re.search(
                    r"blurb|entry|post|card|episode|story",
                    " ".join(p.get("class", [])),
                    re.I,
                )
            ),
            a.parent or a,
        )
        if id(record) not in record_info:
            heads = list(tags(record, HEADINGS, limit=12))
            primary = next(
                (
                    link
                    for h in heads
                    if (link := first_tag(h, {"a"}, attribute="href")) is not None
                ),
                None,
            )
            first = first_tag(record, {"a"}, attribute="href")
            record_info[id(record)] = (
                len(heads),
                len(" ".join(islice(record.stripped_strings, 160)).split()),
                primary.get("href") if primary else None,
                id(first) if first else 0,
            )
        attrs = " ".join(
            str(p.get("id", "")) + " " + " ".join(p.get("class", []))
            for p in [a] + parents[:6]
        )[:1000]
        template = tuple((p.name, tuple(p.get("class", [])[:3])) for p in parents[:3])
        template_counts[template] += 1
        own_date = any(
            re.search(r"\b(?:19|20)\d{2}\b", str(a.get(k, "")))
            for k in ("title", "datetime", "data-date")
        )
        shared = url_info.setdefault(url, [0, False, False, 0])
        shared[0] += 1
        shared[1] |= heading is not None
        shared[2] |= own_date
        shared[3] = max(shared[3], len(label))
        info.append(
            (
                a,
                url,
                label,
                features,
                parents,
                heading,
                before,
                record,
                attrs,
                template,
                own_date,
            )
        )
    for index, (
        a,
        url,
        label,
        features,
        parents,
        heading,
        before,
        record,
        attrs,
        template,
        own_date,
    ) in enumerate(info):
        path = urlsplit(url)
        paragraph = next((p for p in parents[:4] if p.name == "p"), None)
        ptext = (
            " ".join(islice(paragraph.stripped_strings, 140))[:1600]
            if paragraph
            else ""
        )
        htext = (
            (heading or before).get_text(" ", strip=True)[:220]
            if (heading is not None or before is not None)
            else ""
        )
        distance = (
            max(0, node_order.get(id(a), 0) - node_order.get(id(before), 0))
            if before is not None
            else 500
        )
        heading_count, record_words, primary, first_id = record_info[id(record)]
        shared = url_info[url]
        hwords, lwords = set(words(htext)), set(words(label))
        rel = set(a.get("rel", []))
        extra = [
            own_date,
            path.path in ("", "/"),
            bool(urlsplit(a.get("href", "")).fragment),
            int(heading.name[1]) / 6 if heading else 0,
            int(before.name[1]) / 6 if before else 0,
            min(distance / 80, 1),
            len(hwords & lwords) / max(len(lwords), 1),
            min(heading_count / 6, 1),
            min(record_words / 200, 1),
            min(len(ptext.split()) / 100, 1),
            len(label) / max(len(ptext), len(label), 1),
            min(len(re.findall(r"[.!?;]", ptext)) / 12, 1),
            id(a) == first_id,
            min(shared[0] / 5, 1),
            shared[1],
            shared[2],
            min(shared[3] / 150, 1),
            primary == a.get("href"),
            bool(primary and primary != a.get("href")),
            bool(
                re.fullmatch(
                    r".*/(?:19|20)\d{2}(?:/(?:\d{1,2}|[a-z]{3,9}))?/?", path.path
                )
            ),
            "author" in rel,
            "tag" in rel,
            record.name in ("li", "tr"),
            bool(
                re.search(
                    r"(?:entry|post|article|story)[-_ ]?(?:content|body)|markdown-body",
                    attrs,
                    re.I,
                )
            ),
            min(len(heading.get_text()) / max(len(htext), 1), 1) if heading else 0,
            bool(before is not None and first_tag(before, {"a"}, attribute="href")),
            index + 1 < len(info) and id(info[index + 1][7]) == id(record),
            index > 0 and id(info[index - 1][7]) == id(record),
            min(template_counts[template] / 30, 1),
        ]
        display = (
            htext if GENERIC.fullmatch(label) and htext and distance < 80 else label
        )
        yield dict(
            anchor=a,
            url=url,
            label=display or label,
            original_label=label,
            features=features
            + [float(x) for x in extra]
            + tables.get(id(a), {}).get("features", [0.0] * len(TABLE_FEATURES)),
            tokens=tokens(label, url, attrs, htext),
            record_id=id(record),
        )
