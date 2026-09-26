import csv
import io
import zipfile
from unittest.mock import patch

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.tracker.csv_import import parse_csv
from app.tracker.file_import import parse_file
from app.tracker.limits import MAX_CSV_BYTES, MAX_LINKS
from app.tracker.urls import DiscoveryError

PROBLEMS = [
    (
        "3. Longest Substring Without Repeating Characters",
        "Sliding window + last-seen indices",
    ),
    ("56. Merge Intervals", "Sort by start, then merge"),
    ("347. Top K Frequent Elements", "Min-heap; then O(n) frequency buckets"),
    (
        "207. Course Schedule",
        "DFS with visiting/finished states—approach explained, no code",
    ),
    ("236. Lowest Common Ancestor of a Binary Tree", "Recursive DFS"),
    ("435. Non-overlapping Intervals", "Greedy selection by earliest end"),
    (
        "1438. Longest Continuous Subarray With Absolute Diff Less Than or Equal to Limit",
        "Sliding window with two monotonic deques or a multiset",
    ),
    (
        "84. Largest Rectangle in Histogram",
        "Monotonic stack tracking heights and starting indices",
    ),
    ("918. Maximum Sum Circular Subarray", "Maximum/minimum Kadane’s algorithm"),
    (
        "410. Split Array Largest Sum",
        "Binary search on the answer + greedy feasibility check",
    ),
    ("85. Maximal Rectangle", "Build a histogram per row and reuse problem 84"),
]
PROBLEM_TEXT = "".join(f"**{title}**{details}" for title, details in PROBLEMS)
REL = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def table(rows, delimiter=","):
    output = io.StringIO(newline="")
    csv.writer(output, delimiter=delimiter).writerows(rows)
    return output.getvalue().encode()


def archive(parts):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        for name, contents in parts.items():
            package.writestr(name, contents)
    return output.getvalue()


def assert_unlinked(entries):
    assert entries
    assert all(entry["url"] == "" and entry["source_id"] for entry in entries)
    assert len({entry["source_id"] for entry in entries}) == len(entries)


@pytest.mark.parametrize("filename", ["Pasted text.txt", "problems.md"])
def test_exact_concatenated_problem_list_keeps_each_title_and_its_details(filename):
    with patch("socket.create_connection", side_effect=AssertionError("No network")):
        result = parse_file(PROBLEM_TEXT.encode(), filename)
    entries = result["entries"]
    assert len(entries) == 11
    assert_unlinked(entries)
    assert [entry["title"] for entry in entries] == [title for title, _ in PROBLEMS]
    assert [entry["number"] for entry in entries] == [
        3,
        56,
        347,
        207,
        236,
        435,
        1438,
        84,
        918,
        410,
        85,
    ]
    for index, entry in enumerate(entries):
        assert PROBLEMS[index][1] in entry["context"]
        for other, (_, details) in enumerate(PROBLEMS):
            if other != index:
                assert details not in entry["context"]
    assert result["pages_scanned"] == 0
    assert not any(
        "No matching public links" in message for message in result["warnings"]
    )


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_link_free_tables_preserve_titles_dates_and_full_row_context(delimiter):
    result = parse_csv(
        table(
            [
                ["Title", "Notes", "Date"],
                [
                    "Merge intervals",
                    "Sort, then merge\nKeep the running end",
                    "2026-09-26",
                ],
                [
                    "Course schedule",
                    "DFS with visiting and finished states",
                    "2026-09-27",
                ],
            ],
            delimiter,
        ),
        "practice.csv",
    )
    entries = result["entries"]
    assert_unlinked(entries)
    assert result["csv"]["selected"]["url"] == -1
    assert result["csv"]["rows"] == 2 and result["csv"]["skipped"] == 0
    assert [entry["title"] for entry in entries] == [
        "Merge intervals",
        "Course schedule",
    ]
    assert "Sort, then merge\nKeep the running end" in entries[0]["context"]
    assert "visiting and finished" in entries[1]["context"]
    assert entries[0]["published_at"].startswith("2026-09-26")
    assert entries[1]["published_at"].startswith("2026-09-27")


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_pasted_delimited_text_uses_table_preview_without_requiring_urls(delimiter):
    result = parse_file(
        table(
            [
                ["Title", "Notes"],
                ["Merge intervals", "Sort first"],
                ["Course schedule", "DFS"],
            ],
            delimiter,
        ),
        "Pasted text.txt",
    )
    assert_unlinked(result["entries"])
    assert [entry["title"] for entry in result["entries"]] == [
        "Merge intervals",
        "Course schedule",
    ]
    assert result["csv"]["selected"]["url"] == -1
    assert result["csv"]["delimiter"] == delimiter


@pytest.mark.parametrize("filename", ["practice.csv", "practice.tsv"])
def test_headerless_unlinked_rows_keep_first_row_and_allow_manual_column_mapping(
    filename,
):
    data = table([["Sort first", "Merge intervals"], ["DFS", "Course schedule"]], "\t")
    result = parse_file(data, filename, header="no", columns={"title": 1, "url": -1})
    assert not result["csv"]["header"]
    assert [entry["title"] for entry in result["entries"]] == [
        "Merge intervals",
        "Course schedule",
    ]
    assert "Sort first" in result["entries"][0]["context"]
    assert_unlinked(result["entries"])


@pytest.mark.parametrize(
    "filename,text",
    [
        ("practice.txt", "Merge intervals\nCourse schedule\nMaximum subarray"),
        ("practice.md", "- Merge intervals\n- Course schedule\n- Maximum subarray"),
        ("practice.txt", "- Merge intervals\n- Course schedule\n- Maximum subarray"),
        (
            "practice.html",
            "<ul><li>Merge intervals</li><li>Course schedule</li><li>Maximum subarray</li></ul>",
        ),
    ],
)
def test_plain_lines_and_list_items_are_records(filename, text):
    result = parse_file(text.encode(), filename)
    assert len(result["entries"]) == 3
    assert_unlinked(result["entries"])
    for entry, title in zip(
        result["entries"],
        ["Merge intervals", "Course schedule", "Maximum subarray"],
        strict=True,
    ):
        assert title in entry["title"]
    assert all(not entry["published_at"] for entry in result["entries"])


def test_html_table_keeps_row_details_without_importing_column_headers():
    result = parse_file(
        b"<table><thead><tr><th>Title</th><th>Notes</th></tr></thead><tbody><tr><td>Merge intervals</td><td>Sort by start</td></tr><tr><td>Course schedule</td><td>Use DFS</td></tr></tbody></table>",
        "practice.html",
    )
    entries = result["entries"]
    assert len(entries) == 2
    assert_unlinked(entries)
    assert "Merge intervals" in entries[0]["title"]
    assert (
        "Sort by start" in entries[0]["context"]
        and "Use DFS" not in entries[0]["context"]
    )
    assert "Course schedule" in entries[1]["title"]
    assert (
        "Use DFS" in entries[1]["context"]
        and "Sort by start" not in entries[1]["context"]
    )


def test_mixed_csv_keeps_blank_url_rows_and_rejects_explicit_unsafe_urls():
    result = parse_csv(
        table(
            [
                ["Title", "URL", "Notes"],
                [
                    "A linked exercise",
                    "https://example.org/practice/1",
                    "Read the source",
                ],
                ["An offline exercise", "", "Solve on paper"],
                ["Unsafe script", "javascript:alert(1)", "Do not import"],
                ["Private address", "http://127.0.0.1/secret", "Do not import"],
            ]
        ),
        "practice.csv",
    )
    entries = result["entries"]
    assert [entry["title"] for entry in entries] == [
        "A linked exercise",
        "An offline exercise",
    ]
    assert entries[0]["url"] == "https://example.org/practice/1"
    assert_unlinked(entries[1:])
    assert result["csv"]["skipped"] == 2


def test_mixed_document_does_not_duplicate_the_text_of_linked_records():
    result = parse_file(
        b"<ul><li><a href='https://example.org/practice/1'>Linked exercise</a> Notes for linked exercise</li><li>Offline exercise</li></ul>",
        "practice.html",
    )
    entries = result["entries"]
    assert len(entries) == 2
    linked = next(entry for entry in entries if entry["url"])
    unlinked = next(entry for entry in entries if not entry["url"])
    assert linked["url"] == "https://example.org/practice/1"
    assert "Notes for linked exercise" in linked["context"]
    assert "Offline exercise" in unlinked["title"]
    assert_unlinked([unlinked])


@pytest.mark.parametrize("filename", ["practice.csv", "Pasted text.txt"])
def test_keywords_match_unlinked_details_without_borrowing_other_rows(filename):
    result = parse_file(
        table(
            [["Title", "Notes"], ["One", "monotonic stack"], ["Two", "recursive DFS"]]
        ),
        filename,
        keywords="recursive",
    )
    assert len(result["entries"]) == 1
    assert_unlinked(result["entries"])
    assert result["entries"][0]["title"] == "Two"
    assert "monotonic" not in result["entries"][0]["context"]


def test_identical_rows_deduplicate_but_shared_titles_do_not():
    data = table(
        [
            ["Title", "Notes"],
            ["Review", "Practice recursion"],
            ["Review", "Practice dynamic programming"],
            ["Review", "Practice recursion"],
        ]
    )
    first = parse_csv(data, "practice.csv")
    entries = first["entries"]
    assert len(entries) == 2 and first["csv"]["duplicates"] == 1
    assert_unlinked(entries)
    assert all(entry["title"] == "Review" for entry in entries)
    reordered = parse_csv(
        table(
            [
                ["Title", "Notes"],
                ["Review", "Practice dynamic programming"],
                ["Review", "Practice recursion"],
            ]
        ),
        "renamed.csv",
    )
    assert {entry["context"]: entry["source_id"] for entry in entries} == {
        entry["context"]: entry["source_id"] for entry in reordered["entries"]
    }


def test_document_records_with_shared_titles_retain_distinct_details():
    result = parse_file(
        b"**Review**Practice recursion**Review**Practice dynamic programming",
        "practice.txt",
    )
    entries = result["entries"]
    assert len(entries) == 2
    assert_unlinked(entries)
    assert all(entry["title"] == "Review" for entry in entries)
    assert "recursion" in entries[0]["context"]
    assert "dynamic programming" in entries[1]["context"]


def test_authored_unlinked_rows_are_not_removed_by_link_classifier_mode():
    for mode in ("all", "content"):
        result = parse_file(PROBLEM_TEXT.encode(), "practice.md", link_filter=mode)
        assert len(result["entries"]) == 11
        assert_unlinked(result["entries"])


def test_link_free_office_documents_share_record_extraction():
    files = {
        "practice.docx": archive(
            {
                "word/document.xml": "<document><body><p><r><t>Merge intervals</t></r></p><p><r><t>Course schedule</t></r></p></body></document>",
            }
        ),
        "practice.pptx": archive(
            {
                "ppt/presentation.xml": f'<presentation {REL}><sldIdLst><sldId r:id="one"/></sldIdLst></presentation>',
                "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="one" Target="slides/slide1.xml"/></Relationships>',
                "ppt/slides/slide1.xml": "<sld><p><r><t>Merge intervals</t></r></p><p><r><t>Course schedule</t></r></p></sld>",
            }
        ),
        "practice.xlsx": archive(
            {
                "xl/workbook.xml": f'<workbook {REL}><sheets><sheet name="Practice" r:id="one"/></sheets></workbook>',
                "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="one" Target="worksheets/sheet1.xml"/></Relationships>',
                "xl/worksheets/sheet1.xml": '<worksheet><sheetData><row><c r="A1" t="inlineStr"><is><t>Merge intervals</t></is></c><c r="B1" t="inlineStr"><is><t>Sort first</t></is></c></row><row><c r="A2" t="inlineStr"><is><t>Course schedule</t></is></c><c r="B2" t="inlineStr"><is><t>DFS</t></is></c></row></sheetData></worksheet>',
            }
        ),
    }
    for filename, data in files.items():
        result = parse_file(data, filename)
        entries = result["entries"]
        assert len(entries) == 2, filename
        assert_unlinked(entries)
        assert "Merge intervals" in entries[0]["title"]
        assert "Course schedule" in entries[1]["title"]


def test_text_only_pdf_imports_each_text_line_without_links():
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
        b"BT /F1 12 Tf 20 300 Td (Merge intervals) Tj 0 -40 Td (Course schedule) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    entries = parse_file(output.getvalue(), "practice.pdf")["entries"]
    assert len(entries) == 2
    assert_unlinked(entries)
    assert "Merge intervals" in entries[0]["title"]
    assert "Course schedule" in entries[1]["title"]


def test_unlinked_import_is_bounded_before_saving():
    result = parse_csv(
        table([["Title"], *[[f"Practice {index}"] for index in range(MAX_LINKS + 2)]]),
        "practice.csv",
    )
    assert len(result["entries"]) == MAX_LINKS
    assert result["coverage"] == "partial"
    assert result["warnings"]
    assert_unlinked(result["entries"])
    with pytest.raises(DiscoveryError):
        parse_file(b"x" * (MAX_CSV_BYTES + 1), "practice.txt")
    with pytest.raises(DiscoveryError):
        parse_csv(
            table([["Title"], *[[f"Practice {index}"] for index in range(10_001)]]),
            "practice.csv",
        )


def test_unique_title_identity_survives_notes_edits_and_row_reordering():
    first = parse_csv(
        table(
            [
                ["Title", "Notes"],
                ["Merge intervals", "Sort first"],
                ["Course schedule", "DFS"],
            ]
        ),
        "practice.csv",
    )["entries"]
    revised = parse_csv(
        table(
            [
                ["Title", "Notes"],
                ["Course schedule", "BFS also works"],
                ["Merge intervals", "Sort and merge overlapping ranges"],
            ]
        ),
        "renamed.csv",
    )["entries"]
    assert {entry["title"]: entry["source_id"] for entry in first} == {
        entry["title"]: entry["source_id"] for entry in revised
    }
    assert all(entry["source_id"] for entry in first)


def test_different_link_targets_with_the_same_title_remain_distinct():
    entries = parse_csv(
        table(
            [
                ["Title", "URL"],
                ["Practice", "https://example.org/one"],
                ["Practice", "https://example.org/two"],
            ]
        ),
        "practice.csv",
    )["entries"]
    assert len(entries) == 2
    assert {entry["url"] for entry in entries} == {
        "https://example.org/one",
        "https://example.org/two",
    }


def test_empty_url_column_is_optional_even_when_every_row_is_unlinked():
    result = parse_csv(
        table(
            [
                ["Title", "URL", "Notes"],
                ["Merge intervals", "", "Sort first"],
                ["Course schedule", "", "DFS"],
            ]
        ),
        "practice.csv",
    )
    assert len(result["entries"]) == 2
    assert_unlinked(result["entries"])
    assert result["csv"]["skipped"] == 0


def test_explicit_unsafe_url_column_is_not_reinterpreted_as_offline_content():
    result = parse_csv(
        table(
            [
                ["Title", "URL", "Notes"],
                ["Script", "javascript:alert(1)", "Unsafe URL"],
                ["Private", "http://127.0.0.1/", "Private URL"],
            ]
        ),
        "practice.csv",
    )
    assert result["entries"] == []
    assert result["csv"]["skipped"] == 2


def test_numbered_text_preserves_nonconsecutive_numbers():
    entries = parse_file(
        b"3. Longest substring\n56. Merge intervals\n347. Top K", "practice.txt"
    )["entries"]
    assert [e["number"] for e in entries] == [3, 56, 347]
    assert [e["title"] for e in entries] == [
        "3. Longest substring",
        "56. Merge intervals",
        "347. Top K",
    ]


def test_document_headings_keep_their_notes_as_one_entry():
    entries = parse_file(
        b"## Merge intervals\nSort by start.\n\nThen merge.\n\n## Course schedule\nUse DFS.",
        "practice.md",
    )["entries"]
    assert len(entries) == 2
    assert entries[0]["title"] == "Merge intervals"
    assert entries[0]["context"] == "Sort by start.\nThen merge."
    assert entries[1]["title"] == "Course schedule"
    assert entries[1]["context"] == "Use DFS."
