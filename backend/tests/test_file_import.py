import io
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.annotations import Link
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.main import create_app
from app.tracker.file_import import MAX_EXPANDED_BYTES, Package, parse_file
from app.tracker.import_pool import DocumentImporter
from app.tracker.urls import DiscoveryError
from tests.test_accounts import CONFIG, sign_up
from tests.test_accounts import client as account_client
from tests.test_store_api import FakeDiscoverer

REL = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def archive(parts):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in parts.items():
            package.writestr(name, content)
    return output.getvalue()


def xlsx():
    return archive(
        {
            "xl/workbook.xml": f'<workbook {REL}><sheets><sheet name="Jobs" r:id="one"/><sheet name="Reading" r:id="two"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="one" Target="worksheets/sheet1.xml"/><Relationship Id="two" Target="worksheets/sheet2.xml"/></Relationships>',
            "xl/styles.xml": '<styleSheet><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
            "xl/worksheets/sheet1.xml": f'<worksheet {REL}><sheetData><row><c r="A1" t="inlineStr"><is><t>Engineer</t></is></c><c r="B1" s="1"><v>46280</v></c></row></sheetData><hyperlinks><hyperlink ref="A1" r:id="job"/></hyperlinks></worksheet>',
            "xl/worksheets/_rels/sheet1.xml.rels": '<Relationships><Relationship Id="job" Target="https://example.org/job/1" TargetMode="External"/></Relationships>',
            "xl/worksheets/sheet2.xml": '<worksheet><sheetData><row><c r="A1"><f>HYPERLINK("https://example.org/book/2","Reading")</f><v>Reading</v></c></row></sheetData></worksheet>',
        }
    )


def pptx():
    return archive(
        {
            "ppt/presentation.xml": f'<presentation {REL}><sldIdLst><sldId r:id="s1"/></sldIdLst></presentation>',
            "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="s1" Target="slides/slide2.xml"/></Relationships>',
            "ppt/slides/slide2.xml": f'<sld {REL}><p><r><rPr><hlinkClick r:id="one"/></rPr><t>A lecture</t></r><r><t>2026-09-15</t></r></p></sld>',
            "ppt/slides/_rels/slide2.xml.rels": '<Relationships><Relationship Id="one" Target="https://example.org/lecture/1" TargetMode="External"/></Relationships>',
            "ppt/slides/slide99.xml": "<sld><p><r><t>https://example.org/unreferenced</t></r></p></sld>",
        }
    )


def docx():
    return archive(
        {
            "word/document.xml": f'<document {REL}><body><p><hyperlink r:id="one"><r><t>A book</t></r></hyperlink><r><t>2026-09-14</t></r></p></body></document>',
            "word/_rels/document.xml.rels": '<Relationships><Relationship Id="one" Target="https://example.org/book/1" TargetMode="External"/></Relationships>',
        }
    )


def pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=400)
    writer.add_annotation(
        0, Link(rect=(10, 10, 100, 30), url="https://example.org/pdf-link")
    )
    writer.add_annotation(0, Link(rect=(10, 50, 100, 70), url="javascript:alert(1)"))
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def text_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 20 300 Td (First lecture 2026-09-14) Tj 0 -40 Td (Second lecture 2026-09-15) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    for number, y in ((1, 300), (2, 260)):
        writer.add_annotation(
            0,
            Link(
                rect=(18, y - 2, 200, y + 14),
                url=f"https://example.org/lecture/{number}",
            ),
        )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_annotation_titles_and_dates_use_their_text_line():
    entries = parse_file(text_pdf(), "lectures.pdf")["entries"]
    assert len(entries) == 2
    assert entries[0]["title"].startswith("First lecture")
    assert entries[1]["title"].startswith("Second lecture")
    assert entries[0]["published_at"].startswith("2026-09-14")
    assert entries[1]["published_at"].startswith("2026-09-15")


def test_markdown_code_urls_are_data_not_executed():
    entries = parse_file(b"```text\nhttps://example.org/notes\n```", "notes.md")[
        "entries"
    ]
    assert entries[0]["url"] == "https://example.org/notes"


@pytest.mark.parametrize(
    "filename, separator", [("notes.md", "\n"), ("notes.html", "<br>")]
)
def test_line_breaks_do_not_mix_record_dates(filename, separator):
    text = separator.join(
        [
            "First https://example.org/1 2026-09-14 French",
            "Second https://example.org/2 2026-09-15 English",
        ]
    )
    if filename.endswith("html"):
        text = "<p>" + text + "</p>"
    entries = parse_file(text.encode(), filename, keywords="English")["entries"]
    assert len(entries) == 1 and entries[0]["url"] == "https://example.org/2"
    assert entries[0]["published_at"].startswith("2026-09-15")


@pytest.mark.parametrize(
    "filename,data,expected",
    [
        (
            "notes.txt",
            b"Chapter 1 https://example.org/1 2026-09-14\nChapter 2 https://example.org/2 2026-09-15",
            2,
        ),
        (
            "notes.md",
            b"- [Chapter one](https://example.org/1) 2026-09-14\n- [Chapter two](https://example.org/2) 2026-09-15",
            2,
        ),
        (
            "notes.html",
            b'<script>https://example.org/private</script><p><a href="https://example.org/1">One</a> 2026-09-15</p>',
            1,
        ),
        ("slides.pptx", pptx(), 1),
        ("notes.docx", docx(), 1),
        ("jobs.xlsx", xlsx(), 2),
        ("reading.pdf", pdf(), 1),
    ],
)
def test_formats_keep_records_without_opening_any_link(filename, data, expected):
    with patch("socket.create_connection", side_effect=AssertionError("No network")):
        result = parse_file(data, filename)
    assert result["source_type"] == "document"
    assert len(result["entries"]) == expected
    assert result["pages_scanned"] == 0
    assert all(e["url"].startswith("https://example.org/") for e in result["entries"])
    if filename not in {"reading.pdf", "jobs.xlsx"}:
        assert all(e["published_at"] for e in result["entries"])


def test_rows_do_not_steal_neighboring_titles_dates_or_keywords():
    result = parse_file(
        b"First https://example.org/1 2026-09-14 French\nSecond https://example.org/2 2026-09-15 English",
        "links.txt",
        keywords="English",
    )
    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["url"] == "https://example.org/2"
    assert "Second" in entry["title"] and "First" not in entry["title"]
    assert entry["published_at"].startswith("2026-09-15")


def test_spreadsheet_hyperlinks_literal_formulas_and_dates_across_sheets():
    result = parse_file(xlsx(), "jobs.xlsx")
    assert result["document"]["units"] == 2
    assert result["entries"][0]["title"] == "Engineer"
    assert result["entries"][0]["published_at"].startswith("2026-09-15")
    assert result["entries"][1]["title"] == "Reading"


def test_pasted_spreadsheet_uses_column_detection():
    result = parse_file(
        b"Title\tURL\tDate\nA\thttps://example.org/a\t2026-09-15", "Pasted links.txt"
    )
    assert result["csv"]["selected"]["url"] == 1
    assert result["entries"][0]["title"] == "A"


def test_safety_dedup_and_explicit_all_links_mode():
    text = b'<nav><a href="https://example.org/">Home</a></nav><p><a href="https://example.org/1">One</a></p><p>https://example.org/1</p><a href="file:///etc/passwd">file</a><a href="http://127.0.0.1/private">local</a><a href="javascript:alert(1)">script</a>'
    result = parse_file(text, "links.html")
    assert len(result["entries"]) == 2
    assert result["document"]["duplicates"] == 1
    assert result["document"]["unsafe"] == 3
    filtered = parse_file(text, "links.html", link_filter="content")
    assert not any(e["url"] == "https://example.org/" for e in filtered["entries"])


@pytest.mark.parametrize(
    "filename,data",
    ids=lambda value: f"{len(value)} bytes" if isinstance(value, bytes) else value,
    argvalues=[
        ("x.exe", b"MZ"),
        ("x.ppt", b"old office"),
        ("x.pdf", b"not pdf"),
        ("x.xlsx", b"not zip"),
        ("x.txt", b""),
        ("x.txt", b"x" * 4_000_001),
    ],
)
def test_invalid_files_fail_closed(filename, data):
    with pytest.raises(DiscoveryError):
        parse_file(data, filename)


def test_zip_expansion_and_path_controls():
    with pytest.raises(DiscoveryError, match="unsafe"):
        Package(archive({"../outside.xml": "x"}))
    with pytest.raises(DiscoveryError, match="24 MB"):
        Package(archive({"huge.xml": "x" * (MAX_EXPANDED_BYTES + 1)}))


def test_xml_entities_never_expand_or_fetch():
    data = archive(
        {
            "word/document.xml": '<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><document><p><t>&leak;</t></p></document>'
        }
    )
    with pytest.raises(Exception, match="Forbidden"):
        parse_file(data, "x.docx")


@pytest.mark.asyncio
async def test_worker_returns_errors_recovers_after_timeout_and_limits_concurrency():
    importer = DocumentImporter(timeout=0.001)
    with pytest.raises(DiscoveryError, match="too long"):
        await importer.parse(b"https://example.org/one", "links.txt")
    assert not importer.busy
    importer.timeout = 20
    result = await importer.parse(docx(), "links.docx")
    assert result["entries"][0]["title"] == "A book"
    bad = archive({"word/document.xml": "bad XML"})
    with pytest.raises(DiscoveryError, match="could not be read"):
        await importer.parse(bad, "links.docx")
    importer.busy = True
    with pytest.raises(Exception) as error:
        await importer.parse(b"https://example.org/one", "links.txt")
    assert error.value.status_code == 429


def test_document_api_save_reimport_keeps_progress_and_skips_bulk_refresh(tmp_path):
    app = create_app(
        tmp_path / "library.db", FakeDiscoverer(), auth_config={"required": False}
    )
    with TestClient(app) as client:

        def upload(data, **options):
            return client.post(
                "/api/scans/import",
                params={"filename": "links.txt", **options},
                content=data,
            )

        first = upload(b"One https://example.org/1 2026-09-15")
        assert first.status_code == 200, first.text
        saved = client.post(
            "/api/items", json={"scan_id": first.json()["scan_id"], "mark_read": True}
        )
        assert saved.status_code == 201, saved.text
        item = saved.json()
        assert item["read_count"] == 1
        assert not app.state.store.refresh_sources()
        second = upload(
            b"One revised https://example.org/1 2026-09-15\nTwo https://example.org/2",
            item_id=item["id"],
        )
        assert second.status_code == 200, second.text
        updated = client.post(
            f"/api/items/{item['id']}/import",
            json={"scan_id": second.json()["scan_id"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["read_count"] == 1 and updated.json()["total_count"] == 2
        large = upload(b"x" * 4_000_001)
        assert large.status_code == 413


def test_import_requires_session_origin_and_preview_ownership(tmp_path):
    app = create_app(tmp_path / "accounts.db", FakeDiscoverer(), CONFIG)
    with account_client(app) as alice, account_client(app) as bob:

        def upload(client, **options):
            return client.post(
                "/api/scans/import",
                params={"filename": "links.txt", **options},
                content=b"https://example.org/one",
            )

        assert upload(alice).status_code == 401
        sign_up(alice, "alice")
        sign_up(bob, "bob")
        preview = upload(alice).json()
        assert (
            bob.post("/api/items", json={"scan_id": preview["scan_id"]}).status_code
            == 422
        )
        saved = alice.post("/api/items", json={"scan_id": preview["scan_id"]})
        assert saved.status_code == 201
        assert upload(bob, item_id=saved.json()["id"]).status_code == 404
        assert (
            alice.post(
                "/api/scans/import?filename=links.txt",
                content=b"https://example.org/one",
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )


@pytest.mark.asyncio
async def test_cancelled_import_releases_worker_and_admission():
    import asyncio

    importer = DocumentImporter()
    running = asyncio.create_task(importer.parse(docx(), "links.docx"))
    await asyncio.sleep(0.01)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert not importer.busy
    assert (await importer.parse(docx(), "links.docx"))["entries"]
