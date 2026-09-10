import json
from pathlib import Path

from bs4 import BeautifulSoup

from app.tracker.discovery import Discoverer
from app.tracker.flight import FlightData
from app.tracker.parser import parse_page
from app.tracker.store import Store
from tests.test_discovery import FakeFetcher

SOURCE = "https://novelshaven.com/series/the-galgame-martial-saint"
FIXTURE = Path(__file__).parent / "fixtures/novelshaven-chapters.json"


def page(rows=None, slug="the-galgame-martial-saint", split=False):
    rows = rows if rows is not None else json.loads(FIXTURE.read_text(encoding="utf8"))
    element = ["$", "$L1", None, {"firstChapter": rows[0]}]
    props = {
        "seriesId": "fixture-series",
        "slug": slug,
        "chapters": ["$a:2:props:firstChapter", *rows[1:]],
    }
    stream = (
        "a:" + json.dumps([None, None, element]) + "\nb:" + json.dumps(props) + "\n"
    )
    pieces = [stream[:123], stream[123:]] if split else [stream]
    scripts = "".join(
        "<script>self.__next_f.push(" + json.dumps([1, part]) + ")</script>"
        for part in pieces
    )
    anchors = "".join(
        f'<a href="/series/the-galgame-martial-saint/chapter-{i}">Chapter {i}</a>'
        for i in range(min(90, len(rows)))
    )
    return (
        "<h1>The Galgame Martial Saint</h1><details hidden>"
        + anchors
        + "</details><p>Showing 90 of 221 chapters</p><button>Load 131 more</button>"
        + scripts
    )


async def test_full_embedded_index_in_one_fetch_including_reference_and_dates(tmp_path):
    f = FakeFetcher({SOURCE: page(split=True)})
    scan = await Discoverer(f).scan(SOURCE)
    assert f.calls == [SOURCE] and scan.pages_scanned == 1
    assert len(scan.entries) == scan.expected_count == 221
    assert scan.coverage == "complete" and not scan.warnings
    assert [e.number for e in scan.entries] == list(range(221))
    assert all(e.published_at and e.date_precision == "day" for e in scan.entries)
    assert scan.entries[0].title == "Chapter 0: Illustrations"
    assert scan.entries[-1].published_at.startswith("2026-06-21")
    store = Store(tmp_path / "series.sqlite3")
    item = store.create(store.save_scan(scan.to_dict()))
    first = store.links(item["id"])["links"][0]
    store.update("links", first["id"], {"read": True, "favorite": True})
    assert store.merge(item["id"], scan.to_dict()) == 0
    saved = store.links(item["id"])["links"][0]
    assert saved["id"] == first["id"] and saved["read"] and saved["favorite"]
    assert store.item(item["id"])["latest_link"]["number"] == 220


def test_missing_records_report_partial_and_other_series_are_excluded():
    rows = json.loads(FIXTURE.read_text(encoding="utf8"))
    rows[-1] = {**rows[-1], "chapter_num": "../../private"}
    scan, _, _ = parse_page(page(rows), SOURCE)
    assert len(scan.entries) == 220 and scan.coverage == "partial" and scan.warnings
    scan, _, _ = parse_page(page(slug="unrelated-story"), SOURCE)
    assert len(scan.entries) == 90
    assert all("chapter-" in e.url for e in scan.entries)


def test_explicit_selector_still_limits_embedded_source():
    scan, _, _ = parse_page(page(), SOURCE, 'a[href$="chapter-1"]')
    assert len(scan.entries) == 1 and scan.entries[0].number == 1


def test_reference_cycles_and_executable_text_are_not_evaluated():
    script = '<script>self.__next_f.push([1,"a:\\"$b\\"\\nb:\\"$a\\"\\n"]);throw Error("must not execute")</script>'
    data = FlightData(BeautifulSoup(script, "html.parser"))
    assert data.frames == {"a": "$b", "b": "$a"}
    assert data.resolve("$a") is None
    assert data.resolve("$dead:props") is None
