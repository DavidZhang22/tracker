import json
from pathlib import Path

import pytest

from app.tracker.discovery import Discoverer
from app.tracker.parser import parse_page
from app.tracker.store import Store
from app.tracker.urls import DiscoveryError
from tests.test_discovery import FakeFetcher

ROOT = "https://github.com/SimplifyJobs/New-Grad-Positions"
BLOB = ROOT + "/blob/dev/README.md"
RAW = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md"
FIXTURE = Path(__file__).parent / "fixtures/jobs-table.html"


async def test_github_loads_only_advertised_readme_and_preserves_application_metadata(
    tmp_path,
):
    f = FakeFetcher(
        {
            ROOT: '<a href="/SimplifyJobs/New-Grad-Positions/blob/dev/README.md">README.md</a>',
            BLOB: '<script type="application/json">'
            + json.dumps(
                {
                    "payload": {"rawBlobUrl": RAW},
                    "blob": {"truncated": True},
                    "preview": {"richText": "<h1>Incomplete preview</h1>"},
                }
            )
            + "</script>",
            RAW: FIXTURE.read_text(encoding="utf8"),
        }
    )
    scan = await Discoverer(f).scan(ROOT)
    assert f.calls == [ROOT, BLOB, RAW]
    assert len(scan.entries) == 8
    assert all(
        e.number is None and " · " in e.title and "Age:" in e.summary
        for e in scan.entries
    )
    assert all(e.published_at is None for e in scan.entries)
    assert not any("/c/" in e.url for e in scan.entries)
    assert scan.order_hint == "source"
    store = Store(tmp_path / "jobs.sqlite3")
    item = store.create(store.save_scan(scan.to_dict()))
    assert item["latest_link"]["url"] == scan.entries[-1].url
    assert store.merge(item["id"], scan.to_dict()) == 0


def test_reordered_columns_images_continuations_and_closed_rows():
    html = """<h1>Careers</h1><nav><a href="/apply">Apply now</a></nav>
    <table><tr><th>Application</th><th>Role</th><th>Company</th><th>Posted</th></tr>
    <tr><td><a href="https://ats.example/jobs/search?id=123"><img alt="Apply"></a></td><td>Engineer 2027</td><td><a href="https://employer.example">Example</a></td><td>2026-09-01</td></tr>
    <tr><td><a href="https://ats.example/apply?id=456">Apply</a></td><td>Analyst</td><td>↳</td><td>2026-09-02</td></tr>
    <tr><td>Closed</td><td>Manager</td><td>Elsewhere</td><td>2026-08-01</td></tr></table>
    <footer><a href="https://promotion.example/apply">Apply</a></footer>"""
    scan, _, _ = parse_page(html, "https://different.example/careers")
    assert len(scan.entries) == 2
    assert [e.title for e in scan.entries] == [
        "Example · Engineer 2027",
        "Example · Analyst",
    ]
    assert all(e.published_at and e.number is None for e in scan.entries)
    assert "id=123" in scan.entries[0].url and "id=456" in scan.entries[1].url


async def test_github_does_not_follow_untrusted_raw_url_or_retry_refusals():
    f = FakeFetcher(
        {
            BLOB: '<script type="application/json">'
            + json.dumps({"rawBlobUrl": "http://169.254.169.254/private"})
            + "</script>"
        }
    )
    with pytest.raises(DiscoveryError):
        await Discoverer(f).scan(BLOB)
    assert f.calls == [BLOB]
    f = FakeFetcher({BLOB: DiscoveryError("Source refused access")})
    with pytest.raises(DiscoveryError):
        await Discoverer(f).scan(BLOB)
    assert f.calls == [BLOB]


async def test_rendered_readme_needs_no_extra_fetch_and_markdown_tables_work():
    f = FakeFetcher(
        {
            ROOT: '<article class="markdown-body"><h1>Articles</h1><h2><a href="https://publisher.example/story">A useful story</a></h2></article>'
        }
    )
    result = await Discoverer(f).scan(ROOT)
    assert len(result.entries) == 1 and f.calls == [ROOT]
    f = FakeFetcher(
        {
            BLOB: '<script type="application/json">'
            + json.dumps({"rawBlobUrl": RAW})
            + "</script>",
            RAW: "# Careers\n\n| Company | Role | Application | Date |\n|---|---|---|---|\n| Acme | Developer | [Apply](https://jobs.example/one) | 2026-09-09 |",
        }
    )
    result = await Discoverer(f).scan(BLOB)
    assert len(result.entries) == 1 and result.entries[0].title == "Acme · Developer"
