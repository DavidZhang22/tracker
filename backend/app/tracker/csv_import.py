"""CSV records are authoritative boundaries; never evaluate cells or fetch their URLs."""

import csv
import hashlib
import io
import ipaddress
import json
import math
import re
from datetime import date
from itertools import islice
from urllib.parse import urlsplit

from .dates import DATE_TEXT, evidence
from .keywords import matches, terms
from .limits import MAX_CSV_BYTES, MAX_LINKS
from .models import Entry, Scan, sequence_value, utcnow
from .parser import merge_entries
from .urls import DiscoveryError, canonical_url

MAX_ROWS = 10_000
MAX_COLUMNS = 64
ALIASES = {
    "url": (
        "applicationurl",
        "applyurl",
        "applicationlink",
        "applylink",
        "contenturl",
        "url",
        "link",
        "href",
        "website",
        "address",
    ),
    "title": (
        "title",
        "roletitle",
        "jobtitle",
        "positiontitle",
        "role",
        "position",
        "name",
        "headline",
        "label",
    ),
    "company": (
        "company",
        "companyname",
        "employer",
        "organization",
        "author",
        "creator",
    ),
    "date": (
        "publishedat",
        "publisheddate",
        "publicationdate",
        "posteddate",
        "postedat",
        "postedorDeadline",
        "date",
        "published",
        "posted",
        "deadline",
        "closingdate",
        "updatedat",
        "updateddate",
    ),
    "number": (
        "chapter",
        "chapternumber",
        "episode",
        "episodenumber",
        "number",
        "sequence",
    ),
}


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def safe_link(value):
    try:
        url = canonical_url(value)
        host = urlsplit(url).hostname
        if host == "localhost" or host.endswith(
            (".localhost", ".local", ".internal", ".invalid")
        ):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or re.fullmatch(r"[\d.]+", host):
                return None
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                return None
        return url
    except (ValueError, UnicodeError):
        return None


def decode(data):
    if not data or len(data) > MAX_CSV_BYTES:
        raise DiscoveryError("Choose a non-empty CSV file up to 4 MB.")
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    try:
        text = data.decode(encoding)
    except UnicodeError:
        try:
            text = data.decode("windows-1252")
        except UnicodeError as exc:
            raise DiscoveryError("Save the file as UTF-8 CSV and try again.") from exc
    if "\x00" in text or re.search(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", text):
        raise DiscoveryError(
            "This file does not look like CSV text. Export it as CSV first."
        )
    return text


def read_table(data, delimiter="auto", header="auto"):
    text = decode(data)
    if match := re.match(r"^sep=([,;\t|])\r?\n", text, re.I):
        if delimiter == "auto":
            delimiter = match[1]
        text = text[match.end() :]
    if delimiter == "auto":

        def score(separator):
            try:
                sample = list(
                    islice(
                        csv.reader(
                            io.StringIO(text[:65_536], newline=""), delimiter=separator
                        ),
                        30,
                    )
                )
            except csv.Error:
                return 0
            sample = [row for row in sample if any(cell.strip() for cell in row)]
            if not sample or not 1 < len(sample[0]) <= MAX_COLUMNS:
                return 0
            return sum(len(row) == len(sample[0]) for row in sample) / len(sample)

        delimiter = max(",;\t|", key=score)
    rows = []
    try:
        reader = csv.reader(
            io.StringIO(text, newline=""), delimiter=delimiter, strict=True
        )
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            if len(rows) > MAX_ROWS or len(row) > MAX_COLUMNS:
                raise DiscoveryError(
                    "CSV imports support up to 10,000 rows and 64 columns."
                )
            if any(len(cell) > 16_384 for cell in row):
                raise DiscoveryError(
                    f"A cell near row {reader.line_num} is too long. Limit each cell to 16,384 characters."
                )
            rows.append([cell.strip() for cell in row])
    except csv.Error as exc:
        raise DiscoveryError(
            "The CSV has an oversized cell or invalid quoting. Check its delimiter and quoted fields."
        ) from exc
    if not rows:
        raise DiscoveryError("The CSV contains no rows.")
    aliases = {normalized(alias) for group in ALIASES.values() for alias in group}
    has_header = header == "yes" or (
        header == "auto"
        and not any(safe_link(c) for c in rows[0])
        and (
            any(normalized(c) in aliases for c in rows[0])
            or any(safe_link(c) for row in rows[1:6] for c in row)
        )
    )
    width = len(rows[0])
    labels = (
        [c[:100] or f"Column {i + 1}" for i, c in enumerate(rows.pop(0))]
        if has_header
        else [f"Column {i + 1}" for i in range(width)]
    )
    if not rows:
        raise DiscoveryError("The CSV has a header but no data rows.")
    if len(rows) > MAX_ROWS:
        raise DiscoveryError("CSV imports support up to 10,000 rows.")
    for i, row in enumerate(rows):
        if len(row) > width:
            raise DiscoveryError(
                f"Row {i + 1 + has_header} has more columns than the first row. Check the delimiter or quoting."
            )
        row.extend([""] * (width - len(row)))
    return labels, rows, delimiter, has_header


def detect_columns(labels, rows):
    names = [normalized(label) for label in labels]
    selected = {}
    for field, aliases in ALIASES.items():
        selected[field] = next(
            (names.index(normalized(a)) for a in aliases if normalized(a) in names), -1
        )
    counts = [
        sum(bool(safe_link(row[i])) for row in rows[:100]) for i in range(len(labels))
    ]
    if selected["url"] < 0 or not counts[selected["url"]]:
        selected["url"] = (
            max(range(len(labels)), key=lambda i: counts[i]) if any(counts) else -1
        )
    if selected["title"] < 0:
        selected["title"] = next(
            (
                i
                for i in range(len(labels))
                if i not in selected.values()
                and sum(
                    bool(re.search(r"[A-Za-z\u0080-\uffff]", row[i]))
                    and not safe_link(row[i])
                    for row in rows[:20]
                )
                > len(rows[:20]) / 2
            ),
            -1,
        )
    return selected, sum(n > 0 for n in counts)


def csv_date(value, label, date_order="auto"):
    if not value:
        return {}
    # Verification/checking dates describe the file, not the underlying content.
    if re.search(r"verified|checked|retrieved|accessed|scraped|imported", label, re.I):
        return {}
    candidates = []
    for match in DATE_TEXT.finditer(value):
        prefix = value[max(0, match.start() - 45) : match.start()].rsplit(";", 1)[-1]
        cue = (
            prefix
            if re.search(
                r"posted|publish|release|deadline|apply by|clos|updat|modifi",
                prefix,
                re.I,
            )
            else label
        )
        if re.search(r"posted|publish|release", cue, re.I):
            cue = "published"
        kind = (
            "deadline"
            if re.search(r"deadline|apply by|clos(?:e|es|ing)", cue, re.I)
            else "updated"
            if re.search(r"updat|modifi", cue, re.I)
            else "published"
        )
        if re.search(r"around|about|approx|expected", prefix, re.I):
            kind = "inferred"
        result = evidence(match[0], "CSV: " + label, kind)
        if result:
            candidates.append(result)
    if not candidates and (
        match := re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value.strip())
    ):
        a, b = int(match[1]), int(match[2])
        if a <= 12 and b <= 12 and a != b and date_order == "auto":
            return {}
        try:
            day_first = date_order == "day_first" or date_order == "auto" and a > 12
            parsed = date(int(match[3]), b if day_first else a, a if day_first else b)
            return csv_date(parsed.isoformat(), label)
        except (ValueError, OverflowError):
            return {}
    if not candidates:
        return {}
    priority = {"published": 4, "deadline": 3, "updated": 2, "inferred": 1}
    best = max(priority[d["date_kind"]] for d in candidates)
    found = {
        d["published_at"]: d for d in candidates if priority[d["date_kind"]] == best
    }
    return next(iter(found.values())) if len(found) == 1 else {}


def parse_csv(
    data,
    filename,
    *,
    columns=None,
    delimiter="auto",
    header="auto",
    date_order="auto",
    keywords="",
):
    terms(keywords)
    labels, rows, delimiter, has_header = read_table(data, delimiter, header)
    chosen, url_columns = detect_columns(labels, rows)
    for field, index in (columns or {}).items():
        if (
            field not in chosen
            or type(index) is not int
            or not -1 <= index < len(labels)
        ):
            raise DiscoveryError(
                "A selected column is not in this CSV. Check the column choices."
            )
        chosen[field] = index
    if chosen["url"] < 0:
        raise DiscoveryError(
            "No link column was found. Include complete http:// or https:// links in a CSV column."
        )
    if len({v for v in chosen.values() if v >= 0}) != sum(
        v >= 0 for v in chosen.values()
    ):
        raise DiscoveryError("Choose a different column for each field.")
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    title = re.sub(r"[_.]+", " ", re.sub(r"\.(?:csv|tsv)$", "", filename, flags=re.I))[
        :300
    ]
    key = hashlib.sha256(
        data
        + json.dumps(
            [chosen, delimiter, has_header, date_order, keywords], sort_keys=True
        ).encode()
    ).hexdigest()
    scan = Scan(
        "csv:" + key,
        title or "CSV import",
        methods=["CSV import"],
        checked_at=utcnow(),
        coverage="complete",
        order_hint="source",
        keywords=keywords,
    )
    skipped, clipped, undated = 0, 0, 0
    imported = []
    for position, row in enumerate(rows):
        url = safe_link(row[chosen["url"]])
        if not url:
            skipped += 1
            continue

        def value(field, row=row):
            return row[chosen[field]] if chosen[field] >= 0 else ""

        label = (
            value("title")
            or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
            or urlsplit(url).hostname
        )
        company = value("company")
        if company and company.casefold() not in label.casefold():
            label = company + " · " + label
        context = "\n".join(
            f"{labels[i]}: {cell}"
            for i, cell in enumerate(row)
            if cell and i != chosen["url"]
        )
        clipped += len(context) > 8192 or len(label) > 300
        date = (
            csv_date(value("date"), labels[chosen["date"]], date_order)
            if chosen["date"] >= 0
            else {}
        )
        undated += bool(value("date")) and not date
        number = None
        if value("number"):
            try:
                number = float(value("number"))
                if not math.isfinite(number) or not 0 <= number <= 1_000_000:
                    number = None
            except ValueError:
                number = sequence_value(value("number"))
        entry = Entry(
            url,
            label[:300],
            number=number,
            position=position,
            method="CSV import",
            context=context[:8192],
            **date,
        )
        if not keywords or matches(entry, keywords):
            imported.append(entry)
    merged = merge_entries(imported)
    duplicates = len(imported) - len(merged)
    scan.entries = merged[:MAX_LINKS]
    scan.unfiltered_count = len(rows) - skipped
    if skipped:
        scan.warnings.append(
            f"Skipped {skipped} rows with missing or unsafe links. Only complete public HTTP or HTTPS links are imported."
        )
    if duplicates:
        scan.warnings.append(f"Combined {duplicates} duplicate links.")
    if undated:
        scan.warnings.append(
            f"{undated} date cells did not contain a clear content date. Their original text is kept in link details."
        )
    if clipped:
        scan.warnings.append(f"Shortened long text in {clipped} rows.")
    if len(merged) > MAX_LINKS:
        scan.coverage = "partial"
        scan.warnings.append(
            f"Kept the first {MAX_LINKS:,} unique links. Split larger collections into separate files."
        )
    if url_columns > 1:
        scan.warnings.append(
            "More than one column contains links. Review the link column before saving."
        )
    for i, entry in enumerate(scan.entries):
        entry.position = i
    payload = scan.to_dict() | {
        "source_type": "csv",
        "source_name": filename,
        "source_method": "auto",
        "csv": {
            "columns": [{"index": i, "label": label} for i, label in enumerate(labels)],
            "selected": chosen,
            "delimiter": delimiter,
            "header": has_header,
            "date_order": date_order,
            "rows": len(rows),
            "skipped": skipped,
            "duplicates": duplicates,
        },
    }
    return payload
