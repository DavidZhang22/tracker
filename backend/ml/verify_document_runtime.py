"""Exercise bounded imports in the production image, with no network or user data."""

import asyncio
import io
import json
import resource
import statistics
import time
import zipfile

from app.tracker.csv_import import parse_csv
from app.tracker.import_pool import DocumentImporter


def workbook(count):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Reading" r:id="one"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="one" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet><sheetData>"
            + "".join(
                f'<row><c t="inlineStr"><is><t>Entry {i}</t></is></c><c t="inlineStr"><is><t>https://example.org/entry/{i}</t></is></c><c t="inlineStr"><is><t>2026-09-15</t></is></c></row>'
                for i in range(count)
            )
            + "</sheetData></worksheet>",
        )
    return output.getvalue()


async def main():
    importer = DocumentImporter()
    results = {}
    for count in (50, 1000, 4999):
        text = "".join(
            f"Entry {i} https://example.org/entry/{i} 2026-09-15\n"
            for i in range(count)
        ).encode()
        cases = [("text", text, "links.txt"), ("excel", workbook(count), "links.xlsx")]
        if count == 1000:
            html = (
                "<main>"
                + "".join(
                    f'<article><h2><a href="https://example.org/entry/{i}">Story {i}</a></h2><time>2026-09-15</time></article>'
                    for i in range(count)
                )
                + "</main>"
            ).encode()
            cases.append(("html_filtered", html, "links.html"))
        for name, data, filename in cases:
            times = []
            for _ in range(3):
                started = time.perf_counter()
                result = await importer.parse(
                    data,
                    filename,
                    link_filter="content" if name == "html_filtered" else "all",
                )
                times.append(time.perf_counter() - started)
                assert len(result["entries"]) == count, (
                    name,
                    count,
                    len(result["entries"]),
                )
                assert all(e["published_at"] for e in result["entries"])
            results[f"{name}_{count}"] = dict(
                bytes=len(data), median_seconds=round(statistics.median(times), 4)
            )
    csv = b"Title,URL,Date\n" + b"".join(
        f"Entry {i},https://example.org/entry/{i},2026-09-15\n".encode()
        for i in range(4999)
    )
    started = time.perf_counter()
    assert len(parse_csv(csv, "links.csv")["entries"]) == 4999
    results["csv_4999"] = dict(
        bytes=len(csv), seconds=round(time.perf_counter() - started, 4)
    )
    results["peak_child_rss_mib"] = round(
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 2
    )
    results["peak_parent_rss_mib"] = round(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2
    )
    assert results["peak_child_rss_mib"] < 384
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
