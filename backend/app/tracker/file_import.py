"""Normalize local documents into the same bounded link/context representation."""

import hashlib
import io
import json
import posixpath
import re
import zipfile
from datetime import datetime, timedelta
from html import escape
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString
from defusedxml import ElementTree as XML
from markdown_it import MarkdownIt

from .context_model import classify_context
from .csv_import import decode, parse_csv, safe_link
from .keywords import matches, terms
from .limits import MAX_CSV_BYTES, MAX_LINKS
from .models import Entry, Scan, sequence_value, utcnow
from .parser import merge_entries
from .record_context import RecordContext
from .urls import DiscoveryError

MAX_TEXT = 1_000_000
MAX_PART_BYTES = 8_000_000
MAX_EXPANDED_BYTES = 24_000_000
MAX_PARTS = 1500
MAX_PAGES = 200
MAX_RECORDS = 10_000
URL = re.compile(r"https?://[^\s<>\"\x00-\x20]+", re.I)
FORMATS = {
    ".txt",
    ".md",
    ".html",
    ".htm",
    ".csv",
    ".tsv",
    ".pdf",
    ".xlsx",
    ".pptx",
    ".docx",
}
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def external_link(value):
    value = value.strip().rstrip(".,;!?")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return safe_link(value)


def link_markup(url, label):
    return (
        f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'
        if safe_link(url)
        else escape(label)
    )


class Package:
    """Read selected XML parts in memory; never extract files or follow relationships."""

    def __init__(self, data):
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(data))
            info = self.archive.infolist()
            if len(info) > MAX_PARTS or len({p.filename for p in info}) != len(info):
                raise DiscoveryError(
                    "This document has too many or duplicate archive parts."
                )
            if sum(p.file_size for p in info) > MAX_EXPANDED_BYTES:
                raise DiscoveryError(
                    "This document expands beyond the 24 MB processing limit."
                )
            for part in info:
                path = PurePosixPath(part.filename)
                if (
                    part.flag_bits & 1
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in part.filename
                ):
                    raise DiscoveryError(
                        "Encrypted or unsafe document archives are not supported."
                    )
                if part.file_size > MAX_PART_BYTES:
                    raise DiscoveryError(
                        "A document part exceeds the 8 MB processing limit."
                    )
            self.names = {p.filename for p in info}
        except DiscoveryError:
            raise
        except (zipfile.BadZipFile, ValueError) as exc:
            raise DiscoveryError("This is not a readable Office document.") from exc

    def xml(self, name):
        if name not in self.names:
            raise DiscoveryError("A required document part is missing.")
        with self.archive.open(name) as part:
            data = part.read(MAX_PART_BYTES + 1)
        if len(data) > MAX_PART_BYTES:
            raise DiscoveryError("A document part is too large.")
        return XML.fromstring(
            data, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )

    def relationships(self, name):
        path = PurePosixPath(name)
        rel_path = str(path.parent / "_rels" / (path.name + ".rels"))
        if rel_path not in self.names:
            return {}
        return {r.get("Id"): r for r in self.xml(rel_path)}

    def internal_target(self, name, relation):
        if relation is None or relation.get("TargetMode") == "External":
            raise DiscoveryError("External document parts are not loaded.")
        target = relation.get("Target", "")
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
        if target.startswith("/"):
            resolved = target.lstrip("/")
        if resolved.startswith("../") or ":" in resolved or "\\" in resolved:
            raise DiscoveryError("Unsafe document relationship.")
        return resolved


def text_content(node):
    return " ".join(
        n.text or "" for n in node.iter() if local_name(n.tag) in {"t", "v"}
    ).strip()


def paragraphs(root, relationships):
    result = []
    for paragraph in root.iter():
        if local_name(paragraph.tag) != "p":
            continue
        pieces = []
        for child in paragraph:
            label = text_content(child)
            if not label:
                continue
            targets = [child.get(NS_REL + "id")]
            targets.extend(
                n.get(NS_REL + "id")
                for n in child.iter()
                if local_name(n.tag) == "hlinkClick"
            )
            href = next(
                (
                    relationships[t].get("Target")
                    for t in targets
                    if t in relationships
                    and relationships[t].get("TargetMode") == "External"
                ),
                None,
            )
            pieces.append(link_markup(href, label) if href else escape(label))
        if pieces:
            result.append("<p>" + " ".join(pieces) + "</p>")
        if len(result) > MAX_RECORDS:
            raise DiscoveryError(
                "This document contains more than 10,000 text records."
            )
    return "".join(result)


def office_html(data, extension):
    package = Package(data)
    if extension == ".docx":
        name = "word/document.xml"
        return paragraphs(package.xml(name), package.relationships(name)), {
            "format": "Word",
            "units": 1,
        }
    if extension == ".pptx":
        name = "ppt/presentation.xml"
        relations = package.relationships(name)
        slide_ids = [
            s for s in package.xml(name).iter() if local_name(s.tag) == "sldId"
        ]
        if len(slide_ids) > MAX_PAGES:
            raise DiscoveryError("Import up to 200 slides at a time.")
        slides = []
        for slide in slide_ids:
            path = package.internal_target(
                name, relations.get(slide.get(NS_REL + "id"))
            )
            slides.append(paragraphs(package.xml(path), package.relationships(path)))
        return "".join(
            f'<section data-unit="{i + 1}">{s}</section>' for i, s in enumerate(slides)
        ), {"format": "PowerPoint", "units": len(slides)}
    name = "xl/workbook.xml"
    workbook = package.xml(name)
    epoch = datetime(1899, 12, 30)
    if any(
        local_name(n.tag) == "workbookPr" and n.get("date1904") in {"1", "true"}
        for n in workbook
    ):
        epoch = datetime(1904, 1, 1)
    date_styles = set()
    if "xl/styles.xml" in package.names:
        styles = package.xml("xl/styles.xml")
        date_formats = set(range(14, 23)) | {45, 46, 47}
        for n in styles.iter():
            if local_name(n.tag) == "numFmt" and re.search(
                r"[dy]", re.sub(r'"[^"]*"|\\.', "", n.get("formatCode", "")), re.I
            ):
                date_formats.add(int(n.get("numFmtId", "0")))
        for n in styles:
            if local_name(n.tag) == "cellXfs":
                date_styles = {
                    i
                    for i, xf in enumerate(n)
                    if int(xf.get("numFmtId", "0")) in date_formats
                }
    relations = package.relationships(name)
    strings = []
    if "xl/sharedStrings.xml" in package.names:
        strings = [text_content(s) for s in package.xml("xl/sharedStrings.xml")]
    sheets = [s for s in workbook.iter() if local_name(s.tag) == "sheet"]
    if len(sheets) > 50:
        raise DiscoveryError("Import up to 50 worksheets at a time.")
    tables, row_count = [], 0
    for sheet in sheets:
        path = package.internal_target(name, relations.get(sheet.get(NS_REL + "id")))
        root = package.xml(path)
        relationships = package.relationships(path)
        hyperlinks = {}
        for node in root.iter():
            if local_name(node.tag) == "hyperlink":
                relation = relationships.get(node.get(NS_REL + "id"))
                if relation is not None and relation.get("TargetMode") == "External":
                    hyperlinks[node.get("ref", "")] = relation.get("Target", "")
        rows = []
        for row in root.iter():
            if local_name(row.tag) != "row":
                continue
            row_count += 1
            if row_count > MAX_RECORDS:
                raise DiscoveryError("Import up to 10,000 spreadsheet rows at a time.")
            cells = []
            for cell in row:
                if local_name(cell.tag) != "c":
                    continue
                value = text_content(cell)
                if cell.get("t") == "s":
                    try:
                        index = int(value)
                        if index < 0:
                            raise ValueError("Negative shared string index")
                        value = strings[index]
                    except (ValueError, IndexError) as exc:
                        raise DiscoveryError(
                            "The workbook has an invalid shared string."
                        ) from exc
                if (
                    cell.get("t") in {None, "n"}
                    and int(cell.get("s", "0")) in date_styles
                ):
                    try:
                        value = (
                            (epoch + timedelta(days=float(value))).date().isoformat()
                        )
                    except (ValueError, OverflowError):
                        pass
                # Cached formula results are data; formulas and external references never run.
                url = hyperlinks.get(cell.get("r", ""))
                for n in cell:
                    if local_name(n.tag) == "f" and (
                        literal := re.fullmatch(
                            r'\s*HYPERLINK\("(https?://[^"\r\n]+)"\s*[,;]\s*"([^"\r\n]*)"\)\s*',
                            n.text or "",
                            re.I,
                        )
                    ):
                        url, value = literal.groups()
                cells.append(
                    "<td>"
                    + (link_markup(url, value or url) if url else escape(value))
                    + "</td>"
                )
            if len(cells) > 64:
                raise DiscoveryError("Import up to 64 spreadsheet columns at a time.")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        tables.append(
            "<section><h2>"
            + escape(sheet.get("name", "Worksheet"))
            + "</h2><table>"
            + "".join(rows)
            + "</table></section>"
        )
    return "".join(tables), {"format": "Excel", "units": len(sheets), "rows": row_count}


def pdf_html(data):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise DiscoveryError(
            "Password-protected PDFs are not supported. Export an unlocked copy."
        )
    if len(reader.pages) > MAX_PAGES:
        raise DiscoveryError("Import up to 200 PDF pages at a time.")
    pages, length, empty = [], 0, 0
    for page in reader.pages:
        fragments = []

        def collect(text, cm, tm, font, size, fragments=fragments):
            if text.strip() and len(fragments) < MAX_RECORDS:
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                fragments.append((x, y, max(1, abs(size)), text.strip()))

        text = page.extract_text(visitor_text=collect) or ""
        length += len(text)
        if length > MAX_TEXT:
            raise DiscoveryError(
                "The extracted document exceeds one million characters."
            )
        empty += not text.strip()
        records = [
            "<p>" + escape(line) + "</p>" for line in text.splitlines() if line.strip()
        ]
        for ref in page.get("/Annots", [])[:MAX_RECORDS]:
            annotation = ref.get_object()
            action = annotation.get("/A")
            if action is not None:
                action = action.get_object()
            if (
                action
                and action.get("/S") == "/URI"
                and (url := safe_link(str(action.get("/URI", ""))))
            ):
                nearby = ""
                rectangle = annotation.get("/Rect", [])
                if len(rectangle) == 4:
                    x1, y1, x2, y2 = map(float, rectangle)
                    hits = [
                        fragment
                        for fragment in fragments
                        if min(y1, y2) - fragment[2] <= fragment[1] <= max(y1, y2)
                        and min(x1, x2) - fragment[2] <= fragment[0] <= max(x1, x2)
                    ]
                    if hits:
                        # Use the same text line for dates, without borrowing from another row.
                        baseline = max(hits, key=lambda f: f[1])[1]
                        nearby = " ".join(
                            f[3]
                            for f in sorted(fragments)
                            if abs(f[1] - baseline) < min(f[2] * 0.4, 4)
                        )[:1500]
                label = str(annotation.get("/Contents", "")) or nearby or url
                records.append("<p>" + link_markup(url, label[:300]) + "</p>")
        pages.append("<section>" + "".join(records) + "</section>")
    return "".join(pages), {"format": "PDF", "units": len(pages), "image_pages": empty}


def autolink(soup):
    for node in list(soup.find_all(string=URL)):
        if node.find_parent(["a", "script", "style"]):
            continue
        text, start = str(node), 0
        for match in URL.finditer(text):
            url = external_link(match[0])
            if not url:
                continue
            node.insert_before(NavigableString(text[start : match.start()]))
            anchor = soup.new_tag("a", href=url)
            anchor.string = url
            node.insert_before(anchor)
            start = match.end()
        if start:
            node.replace_with(NavigableString(text[start:]))


def document_entries(markup, keywords, relevant=False):
    if len(markup) > 2 * MAX_TEXT:
        raise DiscoveryError(
            "The extracted document is too large. Split it into smaller files."
        )
    soup = BeautifulSoup(markup, "html.parser")
    for node in soup.select("script,style,iframe,object,embed,template"):
        node.decompose()
    for paragraph in soup.select("p"):
        if not paragraph.find("br", recursive=False):
            continue
        line = soup.new_tag("p")
        for child in list(paragraph.contents):
            if getattr(child, "name", None) == "br":
                paragraph.insert_before(line)
                line = soup.new_tag("p")
            else:
                line.append(child.extract())
        paragraph.insert_before(line)
        paragraph.decompose()
    for block in soup.select("pre"):
        for text in block.get_text().splitlines():
            line = soup.new_tag("p")
            line.string = text
            block.insert_before(line)
        block.decompose()
    if len(soup.get_text()) > MAX_TEXT or sum(1 for _ in soup.descendants) > 80_000:
        raise DiscoveryError(
            "The extracted document is too large. Split it into smaller files."
        )
    autolink(soup)
    anchors = soup.find_all("a", href=True, limit=MAX_RECORDS + 1)
    if len(anchors) > MAX_RECORDS:
        raise DiscoveryError("Import up to 10,000 candidate links at a time.")
    scores, labels, rejected, model = ({}, {}, set(), None)
    if relevant:
        if len(anchors) > 4000:
            raise DiscoveryError(
                "Content filtering supports up to 4,000 candidates. Use All links or split the file."
            )
        scores, labels, rejected, model = classify_context(
            soup, "https://document.invalid/import"
        )
    context = RecordContext(soup)
    entries, unsafe, removed = [], 0, 0
    for position, anchor in enumerate(anchors):
        url = safe_link(anchor["href"])
        if not url:
            unsafe += 1
            continue
        if relevant and (
            anchor.find_parent(["nav", "footer"]) or id(anchor) in rejected
        ):
            removed += 1
            continue
        record = anchor.find_parent(["tr", "li"])
        neighbor = None
        if record is None:
            record, neighbor = context.region(anchor)
        text = " ".join(
            n.get_text(" ", strip=True) for n in (record, neighbor) if n is not None
        )[:1500]
        label = labels.get(id(anchor)) or anchor.get_text(" ", strip=True)
        if not label or safe_link(label):
            without_url = URL.sub("", text).strip(" \t\n|:—–-")
            label = (
                without_url
                if without_url
                else urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
                or urlsplit(url).hostname
            )
        dates = context.page.node_dates(record)
        if not dates and neighbor is not None:
            dates = context.page.node_dates(neighbor)
        entry = Entry(
            url,
            label[:300],
            position=position,
            number=sequence_value(label),
            context=text,
            method="Document import",
            **dates,
        )
        if not keywords or matches(entry, keywords):
            entries.append(entry)
    merged = merge_entries(entries)
    return merged[:MAX_LINKS], {
        "candidates": len(anchors),
        "unsafe": unsafe,
        "filtered": removed,
        "duplicates": len(entries) - len(merged),
        "truncated": len(merged) > MAX_LINKS,
        "model": model.model_id if model else "record-context",
    }


def parse_file(data, filename, *, keywords="", link_filter="all", **csv_options):
    if not data or len(data) > MAX_CSV_BYTES:
        raise DiscoveryError("Choose a non-empty file up to 4 MB.")
    if link_filter not in {"all", "content"}:
        raise DiscoveryError("Choose All links or Content links.")
    terms(keywords)
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    extension = PurePosixPath(filename.lower()).suffix
    if extension not in FORMATS:
        raise DiscoveryError(
            "Use CSV, TSV, TXT, Markdown, HTML, PDF, XLSX, PPTX, or DOCX. Export older Office files to a modern format first."
        )
    if extension in {".csv", ".tsv"}:
        return parse_csv(data, filename, keywords=keywords, **csv_options)
    if extension in {".xlsx", ".pptx", ".docx"}:
        markup, metadata = office_html(data, extension)
    elif extension == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise DiscoveryError("This file is not a PDF.")
        markup, metadata = pdf_html(data)
    else:
        text = decode(data)
        if len(text) > MAX_TEXT:
            raise DiscoveryError("Import up to one million text characters at a time.")
        if extension in {".html", ".htm"}:
            markup = text
        elif extension == ".txt":
            if "\t" in text and "\n" in text:
                try:
                    return parse_csv(data, filename, keywords=keywords, **csv_options)
                except DiscoveryError:
                    pass
            markup = "".join(
                "<p>" + escape(line) + "</p>"
                for line in text.splitlines()
                if line.strip()
            )
        else:
            markup = MarkdownIt("commonmark", {"html": False, "breaks": True}).render(
                text
            )
        metadata = {"format": extension[1:].upper(), "units": 1}
    entries, counts = document_entries(markup, keywords, link_filter == "content")
    scan = Scan(
        "document:"
        + hashlib.sha256(
            data + json.dumps([filename, keywords, link_filter]).encode()
        ).hexdigest(),
        PurePosixPath(filename).stem[:300],
        entries=entries,
        methods=["Document import"],
        checked_at=utcnow(),
        coverage="partial" if counts["truncated"] else "complete",
        order_hint="source",
        keywords=keywords,
    )
    if counts["unsafe"]:
        scan.warnings.append(
            f"Skipped {counts['unsafe']} links without a complete public HTTP or HTTPS address."
        )
    if counts["duplicates"]:
        scan.warnings.append(f"Combined {counts['duplicates']} duplicate links.")
    if counts["truncated"]:
        scan.warnings.append(f"Kept the first {MAX_LINKS:,} unique links.")
    if metadata.get("image_pages"):
        scan.warnings.append(
            "Some PDF pages have no selectable text. Image-only text needs OCR before import; embedded hyperlinks are still included."
        )
    if not entries:
        scan.warnings.append(
            "No matching public links found. Try All links, remove keywords, or paste text containing complete URLs."
        )
    return scan.to_dict() | {
        "source_type": "document",
        "source_name": filename,
        "source_method": "auto",
        "document": metadata | counts,
    }
