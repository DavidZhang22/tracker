"""Bounded Atom metadata discovery through arXiv's public API."""

from datetime import datetime

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from .arxiv_urls import paper_id, translate
from .dates import evidence
from .errors import DiscoveryError
from .limits import MAX_LINKS
from .models import Entry, Scan
from .workers import run_blocking

ATOM = "{http://www.w3.org/2005/Atom}"
SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"


def text(node, tag, limit=1800):
    return " ".join((node.findtext(ATOM + tag) or "").split())[:limit]


def parse(text_body, start, size):
    try:
        root = ET.fromstring(text_body, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException, ValueError) as exc:
        raise DiscoveryError("arXiv returned unreadable Atom metadata.") from exc
    if root.tag != ATOM + "feed":
        raise DiscoveryError("arXiv did not return an Atom feed.")
    rows = root.findall(ATOM + "entry")
    for row in rows:
        if "/api/errors" in text(row, "id") or text(row, "title").lower() == "error":
            raise DiscoveryError(
                "arXiv rejected the query: " + text(row, "summary", 250)
            )
    try:
        total = int(root.findtext(SEARCH + "totalResults"))
        offset = int(root.findtext(SEARCH + "startIndex"))
        if total < 0 or offset != start or len(rows) > size:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise DiscoveryError(
            "arXiv returned inconsistent pagination metadata."
        ) from exc
    entries = []
    for row in rows:
        identity = paper_id(text(row, "id"))
        title = text(row, "title", 500)
        if not identity or not title:
            continue
        authors = [
            text(author, "name", 150) for author in row.findall(ATOM + "author")[:30]
        ]
        categories = [
            node.get("term", "")[:80] for node in row.findall(ATOM + "category")[:30]
        ]
        published, updated = text(row, "published"), text(row, "updated")
        entries.append(
            Entry(
                "https://arxiv.org/abs/" + identity,
                title,
                method="public API",
                source_id="arxiv:" + identity,
                summary=text(row, "summary", 1000),
                context=(" ".join(authors + categories) + " " + text(row, "summary"))[
                    :1800
                ],
                **evidence(
                    published or updated,
                    "arXiv API",
                    "published" if published else "updated",
                ),
            )
        )
    return total, len(rows), entries


async def scan_arxiv(fetcher, source, max_pages):
    query = translate(source)
    result = Scan(
        source,
        query.title,
        "website",
        methods=["arXiv API"],
        coverage="complete",
        analysis_mode="light",
        warnings=list(query.notes),
    )
    start, size = query.params["start"], query.params["max_results"]
    if start:
        result.coverage = "partial"
        result.warnings.append(
            f"The source starts at result {start + 1:,}; earlier matches were not requested."
        )
    records = {}
    try:
        for _ in range(min(max_pages, 25)):
            _, body = await fetcher.get(query.url(start))
            result.pages_scanned += 1
            total, count, entries = await run_blocking(parse, body, start, size)
            result.expected_count = total
            if len(entries) != count:
                result.coverage = "partial"
                result.warnings.append(
                    "Some arXiv records had invalid paper IDs or titles and were skipped."
                )
            before = len(records)
            for entry in entries:
                if entry.source_id not in records and len(records) >= MAX_LINKS:
                    raise DiscoveryError(
                        f"arXiv reached the {MAX_LINKS:,}-link limit. Narrow the search for more complete coverage."
                    )
                records[entry.source_id] = entry
            if start + count >= total:
                break
            if not count or len(records) == before:
                raise DiscoveryError(
                    "arXiv repeated or omitted a result page. Remaining matches were not requested."
                )
            if count < size:
                raise DiscoveryError(
                    "arXiv returned fewer records than expected. The listing is incomplete."
                )
            start += count
            if start >= 30000:
                raise DiscoveryError(
                    "arXiv limits access to the first 30,000 matches. Narrow the search."
                )
        else:
            raise DiscoveryError(
                f"arXiv reached the {min(max_pages, 25)}-page request limit. Narrow the search for more complete coverage."
            )
    except DiscoveryError as exc:
        if not records:
            raise
        result.coverage = "partial"
        result.warnings.append(str(exc))
    result.entries = list(records.values())
    if result.coverage == "complete" and result.expected_count != len(records):
        result.coverage = "partial"
        result.warnings.append(
            "Duplicate or changing arXiv results prevented complete coverage."
        )
    if query.date_bounds:
        lower, upper = query.date_bounds
        result.unfiltered_count = len(result.entries)
        if any(
            not entry.published_at or entry.date_kind != "published"
            for entry in result.entries
        ):
            result.coverage = "partial"
            result.warnings.append(
                "Papers without an original submission date were excluded from the date-filtered preview."
            )
        result.entries = [
            entry
            for entry in result.entries
            if entry.published_at
            and entry.date_kind == "published"
            and lower <= datetime.fromisoformat(entry.published_at) < upper
        ]
        result.expected_count = (
            len(result.entries) if result.coverage == "complete" else None
        )
    result.warnings = list(dict.fromkeys(result.warnings))
    return result
