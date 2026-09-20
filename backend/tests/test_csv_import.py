import asyncio
import csv
import io
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.csv_import import csv_date, parse_csv, safe_link
from app.tracker.guards import ApiGuard
from app.tracker.limits import MAX_CSV_BYTES, MAX_LINKS
from tests.test_accounts import CONFIG, sign_up
from tests.test_accounts import client as account_client
from tests.test_store_api import FakeDiscoverer

SAMPLE = Path(__file__).parents[2] / "frontend/public/examples/links.csv"


def table(rows, delimiter=","):
    stream = io.StringIO(newline="")
    csv.writer(stream, delimiter=delimiter).writerows(rows)
    return stream.getvalue().encode()


def upload(client, data=None, **params):
    return client.post(
        "/api/scans/csv",
        params={"filename": "jobs.csv", **params},
        content=SAMPLE.read_bytes() if data is None else data,
        headers={"Content-Type": "text/csv"},
    )


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "csv.sqlite3", FakeDiscoverer())
    with TestClient(app) as session:
        yield session


def create(client, **kwargs):
    preview = upload(client, **kwargs)
    assert preview.status_code == 200, preview.text
    response = client.post(
        "/api/items", json={"scan_id": preview.json()["scan_id"], "read_indices": [0]}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_job_shape_preserves_row_context_without_inventing_dates_or_numbers():
    headers = [
        "Rank",
        "Priority",
        "Fit_Score_10",
        "Company",
        "Role",
        "Opportunity_Type",
        "Location",
        "Work_Mode",
        "Start_or_Season",
        "Dec_2026_Grad_Eligibility",
        "Key_Requirements",
        "Sponsorship_or_Other_Constraints",
        "Why_It_Matches_Your_Resume",
        "Main_Gap_or_Caveat",
        "Posted_or_Deadline",
        "Application_URL",
        "Verified_Date",
        "Application_Status",
        "Notes",
    ]
    rows = [
        [
            str(i),
            "High",
            "9",
            "Example Labs",
            "ML Engineer",
            "Graduate",
            "NY",
            "Remote",
            "2027",
            "Yes",
            "Python, APIs",
            "Unspecified",
            "ML experience",
            "None",
            date,
            f"https://example.org/jobs/{i}",
            "2026-09-15",
            "Not started",
            "Multiline\nnotes",
        ]
        for i, date in enumerate(
            [
                "Live; rolling/not stated",
                "Posted 2026-09-03; expected close about 2026-09-24",
                "Apply by 2026-09-30",
                "Updated 2026-09-04; live",
                "Posted around 2026-08-20; live",
            ],
            1,
        )
    ]
    result = parse_csv(table([headers, *rows]), "jobs.csv")
    assert result["csv"]["selected"] == {
        "url": 15,
        "title": 4,
        "company": 3,
        "date": 14,
        "number": -1,
    }
    entries = result["entries"]
    assert len(entries) == 5
    assert not entries[0]["published_at"]
    assert [entry["date_kind"] for entry in entries[1:]] == [
        "published",
        "deadline",
        "updated",
        "inferred",
    ]
    assert entries[1]["published_at"].startswith("2026-09-03")
    assert all(
        entry["title"] == "Example Labs · ML Engineer" and entry["number"] is None
        for entry in entries
    )
    assert all(
        all(cell in entry["context"] for j, cell in enumerate(row) if j != 15)
        for row, entry in zip(rows, entries, strict=True)
    )
    assert (
        parse_csv(table([headers, *rows]), "jobs.csv", keywords="remote, python")[
            "entries"
        ]
        == entries
    )
    assert (
        parse_csv(table([headers, *rows]), "jobs.csv", keywords="onsite")["entries"]
        == []
    )


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "windows-1252"])
def test_encodings_separators_and_quoted_newlines(delimiter, encoding):
    data = (
        table(
            [
                ["Title", "URL", "Published_Date", "Notes"],
                [
                    "Café, one",
                    "https://example.org/1",
                    "2026-09-01",
                    "Line one\nLine two",
                ],
            ],
            delimiter,
        )
        .decode()
        .encode(encoding)
    )
    scan = parse_csv(data, "sample.csv")
    assert scan["csv"]["delimiter"] == delimiter
    assert scan["entries"][0]["title"] == "Café, one"
    assert scan["entries"][0]["published_at"].startswith("2026-09-01")
    assert "Line one\nLine two" in scan["entries"][0]["context"]


def test_headerless_excel_separator_manual_columns_and_keywords():
    data = b"sep=;\nhttps://example.org/1;One;https://example.net/1\nhttps://example.org/2;Two;https://example.net/2"
    scan = parse_csv(data, "list.csv", columns={"url": 2}, keywords="two")
    assert not scan["csv"]["header"]
    assert scan["entries"][0]["url"] == "https://example.net/2"
    assert scan["entries"][0]["title"] == "Two"
    assert any("More than one" in warning for warning in scan["warnings"])


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://name:password@example.org/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/metadata",
        "http://10.0.0.1/",
        "https://localhost/",
        "https://test.internal/",
        '=HYPERLINK("https://example.org")',
    ],
)
def test_unsafe_links_rejected(value):
    assert safe_link(value) is None


def test_duplicate_canonical_urls_and_text_are_not_executed():
    scan = parse_csv(
        table(
            [
                ["URL", "Title"],
                ["https://example.org/1?utm_source=x", "=SUM(1+1)"],
                ["https://example.org/1/", "<script>alert(1)</script>"],
                ["javascript:alert(1)", "Wrong"],
            ]
        ),
        "test.csv",
    )
    assert len(scan["entries"]) == 1
    assert scan["entries"][0]["url"] == "https://example.org/1"
    assert scan["csv"]["duplicates"] == 1 and scan["csv"]["skipped"] == 1


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\0binary",
        b'URL,Title\nhttps://example.org,"unclosed',
        b"URL,Title\nhttps://example.org,Title,extra",
        b"URL\n" + b"a" * 16385,
        b"a" * (MAX_CSV_BYTES + 1),
        table([[str(i) for i in range(65)]]),
        b"URL\n" + b"https://example.org/\n" * 10001,
    ],
    ids=["empty", "binary", "quoting", "columns", "cell", "file", "width", "rows"],
)
def test_malformed_or_oversized_csv_is_rejected(data):
    with pytest.raises(ValueError):
        parse_csv(data, "test.csv")


def test_link_cap_deduplicates_before_counting():
    scan = parse_csv(
        table(
            [
                ["URL"],
                *[[f"https://example.org/{i}"] for i in range(MAX_LINKS + 2)],
                ["https://example.org/1/"],
            ]
        ),
        "large.csv",
    )
    assert len(scan["entries"]) == MAX_LINKS and scan["coverage"] == "partial"
    assert scan["csv"]["duplicates"] == 1


def test_ambiguous_dates_require_explicit_order_and_verified_never_becomes_published():
    assert csv_date("04/05/2026", "Date") == {}
    assert csv_date("04/05/2026", "Date", "day_first")["published_at"].startswith(
        "2026-05-04"
    )
    assert csv_date("04/05/2026", "Date", "month_first")["published_at"].startswith(
        "2026-04-05"
    )
    assert csv_date("2026-09-15", "Verified_Date") == {}
    assert csv_date("14/05/2026", "Date", "month_first") == {}
    assert csv_date("09/30/2026", "Deadline")["date_kind"] == "deadline"
    assert csv_date("2026-09-01 and 2026-09-02", "Date") == {}
    assert (
        csv_date("2026-09-01T10:00:00-04:00", "Posted")["published_at"]
        == "2026-09-01T14:00:00+00:00"
    )


def test_one_item_import_reupload_retains_state_and_missing_links_without_fetches(
    client,
):
    item = create(client)
    iid = item["id"]
    assert (
        item["total_count"] == 2
        and item["read_count"] == 1
        and item["source_type"] == "csv"
    )
    links = client.get(f"/api/items/{iid}/links?sort=source&direction=asc").json()[
        "links"
    ]
    first, second = links
    client.patch(f"/api/links/{first['id']}", json={"favorite": True, "ignored": True})
    assert (
        client.post(
            "/api/links/bulk",
            json={"ids": [second["id"]], "action": "delete", "item_id": iid},
        ).status_code
        == 200
    )
    revised = table(
        [
            ["URL", "Title", "Notes"],
            [first["url"], "Updated title", "Changed details"],
            ["https://example.net/new", "New", "Extra"],
        ]
    )
    preview = upload(client, revised, item_id=iid).json()
    assert (
        client.post("/api/items", json={"scan_id": preview["scan_id"]}).status_code
        == 422
    )
    response = client.post(
        f"/api/items/{iid}/import", json={"scan_id": preview["scan_id"]}
    )
    assert response.status_code == 200, response.text
    with client.app.state.store.connection() as db:
        saved = dict(
            db.execute("SELECT * FROM links WHERE id=?", (first["id"],)).fetchone()
        )
        assert (
            saved["read"]
            and saved["favorite"]
            and saved["ignored"]
            and saved["title"] == "Updated title"
        )
        assert db.execute(
            "SELECT deleted FROM links WHERE id=?", (second["id"],)
        ).fetchone()[0]
        assert db.execute("SELECT count(*) FROM links").fetchone()[0] == 3
    assert len(client.get("/api/items").json()) == 1
    assert (
        client.post(
            f"/api/items/{iid}/import", json={"scan_id": preview["scan_id"]}
        ).status_code
        == 422
    )
    assert client.post(f"/api/items/{iid}/refresh").status_code == 422
    assert client.post("/api/refresh").json()["checked"] == 0
    assert client.get("/api/suggestions").status_code == 200
    assert client.post("/api/suggestions/rebuild").status_code == 200
    assert client.app.state.discoverer.calls == []
    assert (
        client.get(f"/api/items/{iid}/links?filter=ignored&search=Changed").json()[
            "total"
        ]
        == 1
    )


def test_duplicate_files_cooldown_and_empty_import(client):
    create(client)
    sid = upload(client).json()["scan_id"]
    assert client.post("/api/items", json={"scan_id": sid}).status_code == 409
    sid = upload(client, b"URL\nhttps://example.net/new").json()["scan_id"]
    assert client.post("/api/items", json={"scan_id": sid}).status_code == 429
    sid = upload(client, keywords="unmatched").json()["scan_id"]
    assert client.post("/api/items", json={"scan_id": sid}).status_code == 422


def test_reimport_is_target_bound_and_blocked_after_trashing(client):
    item = create(client)
    sid = upload(client).json()["scan_id"]
    assert (
        client.post(
            f"/api/items/{item['id']}/import", json={"scan_id": sid}
        ).status_code
        == 422
    )
    sid = upload(client, item_id=item["id"]).json()["scan_id"]
    client.post("/api/items/bulk", json={"ids": [item["id"]], "action": "delete"})
    assert (
        client.post(
            f"/api/items/{item['id']}/import", json={"scan_id": sid}
        ).status_code
        == 422
    )
    assert upload(client, item_id=item["id"]).status_code == 422


def test_reimport_keeps_groups_and_trashed_links(client):
    item = create(client)
    iid = item["id"]
    links = client.get(f"/api/items/{iid}/links?sort=source&direction=asc").json()[
        "links"
    ]
    response = client.post(
        f"/api/items/{iid}/link-groups",
        json={
            "ids": [links[1]["id"]],
            "action": "merge",
            "sort": "source",
            "direction": "asc",
        },
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            "/api/links/bulk",
            json={"ids": [links[0]["id"]], "item_id": iid, "action": "delete"},
        ).status_code
        == 200
    )
    sid = upload(client, item_id=iid).json()["scan_id"]
    assert (
        client.post(f"/api/items/{iid}/import", json={"scan_id": sid}).status_code
        == 200
    )
    saved = client.get(f"/api/items/{iid}/links?filter=trash").json()["links"]
    assert len(saved) == 1 and saved[0]["link_count"] == 2


def test_import_rolls_back_on_database_failure(client):
    item = create(client)
    sid = upload(client, item_id=item["id"]).json()["scan_id"]
    with patch(
        "app.tracker.store.Store._merge",
        side_effect=sqlite3.OperationalError("disk full"),
    ):
        assert (
            client.post(
                f"/api/items/{item['id']}/import", json={"scan_id": sid}
            ).status_code
            == 503
        )
    assert client.get(f"/api/items/{item['id']}").json()["total_count"] == 2
    assert (
        client.post(
            f"/api/items/{item['id']}/import", json={"scan_id": sid}
        ).status_code
        == 200
    )


def test_csv_upload_limits_and_content_type(client):
    data = table(
        [
            ["URL", "Notes"],
            *[[f"https://example.org/{i}", "x" * 1000] for i in range(100)],
        ]
    )
    assert len(data) > 65536 and upload(client, data).status_code == 200
    assert client.post("/api/scans", content=data).status_code == 413
    assert upload(client, b"x" * (MAX_CSV_BYTES + 1)).status_code == 413
    assert (
        client.post("/api/scans/csv", json={"url": "https://example.org"}).status_code
        == 415
    )
    assert upload(client, url_column=63).status_code == 422
    assert upload(client, delimiter="xx").status_code == 422
    assert upload(client, title_column=0, url_column=0).status_code == 422


def test_account_scope_and_csrf(tmp_path):
    app = create_app(tmp_path / "accounts.sqlite3", FakeDiscoverer(), CONFIG)
    with account_client(app) as alice, account_client(app) as bob:
        assert upload(alice).status_code == 401
        sign_up(alice, "alice")
        sign_up(bob, "bob")
        item = create(alice)
        assert upload(bob, item_id=item["id"]).status_code == 404
        preview = upload(alice, item_id=item["id"]).json()
        assert (
            bob.post(
                f"/api/items/{item['id']}/import", json={"scan_id": preview["scan_id"]}
            ).status_code
            == 404
        )
        assert (
            alice.post(
                "/api/scans/csv",
                content=SAMPLE.read_bytes(),
                headers={"Content-Type": "text/csv", "Origin": "https://evil.example"},
            ).status_code
            == 403
        )


async def test_csv_bodies_have_bounded_concurrency_before_buffering():
    gate, started = asyncio.Event(), asyncio.Event()
    entered = 0

    async def application(scope, receive, send):
        nonlocal entered
        entered += 1
        if entered == 3:
            started.set()
        await gate.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guard = ApiGuard(application)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guard), base_url="http://test"
    ) as session:
        pending = [
            asyncio.create_task(session.post("/api/scans/csv", content=b"csv"))
            for _ in range(3)
        ]
        await asyncio.wait_for(started.wait(), 5)
        assert (await session.post("/api/scans/csv", content=b"csv")).status_code == 429
        gate.set()
        assert all(r.status_code == 200 for r in await asyncio.gather(*pending))
        assert guard.uploads == guard.active == 0
