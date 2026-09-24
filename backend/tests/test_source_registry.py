import copy
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.errors import DiscoveryError
from app.tracker.source_registry import SourceRegistry, public_url


def observation(now=None):
    now = time.time() if now is None else now
    checked = datetime.fromtimestamp(now, timezone.utc).isoformat()
    return dict(
        checked_url="https://news.example.org/",
        status="access_blocked",
        checked_at=checked,
        alternatives=[
            dict(
                url="https://feeds.example.org/news.xml",
                label="Publisher headlines",
                kind="feed",
                relationship="official",
                evidence_url="https://news.example.org/rss",
                verified_at=checked,
            )
        ],
    )


def test_shared_registry_has_exact_host_scope_and_no_network(tmp_path, monkeypatch):
    registry = SourceRegistry(tmp_path / "sources.sqlite3", seed=None)
    registry.import_rows([observation()])
    other = SourceRegistry(tmp_path / "sources.sqlite3", seed=None)
    assert (
        other.lookup("https://www.news.example.org/archive")["status"]
        == "access_blocked"
    )
    for url in [
        "https://news.example.org.evil.org/",
        "https://other.news.example.org/",
        "https://feeds.example.org/",
        "http://127.0.0.1/",
        "not a url",
    ]:
        assert other.lookup(url) is None
    assert (
        other.lookup("https://news.example.org/")["alternatives"][0]["relationship"]
        == "official"
    )


def test_stale_checks_expire_alternatives_and_never_override_newer_data(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.sqlite3", seed=None)
    registry.import_rows([observation(1_700_000_000)])
    assert registry.lookup("https://news.example.org", now=1_700_090_000)["stale"]
    assert not registry.lookup("https://news.example.org", now=1_703_000_000)[
        "alternatives"
    ]
    fresh = observation(1_700_010_000) | {"status": "accessible"}
    registry.import_rows([fresh])
    registry.import_rows([observation(1_700_000_000)])
    assert registry.lookup("https://news.example.org") is None


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///tmp/a",
        "https://alice:secret@example.org/",
        "http://127.1/",
        "http://127.0.0.1/",
        "http://169.254.169.254/",
        "https://example.org:8000/",
        "http://localhost/",
        "https://service.local/",
        "https://bad..org/",
        "https://example.org/\nheader",
    ],
)
def test_unsafe_alternatives_are_rejected(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_invalid_import_is_atomic_and_registry_outage_is_advisory(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.sqlite3", seed=None)
    invalid = copy.deepcopy(observation())
    invalid["alternatives"][0]["relationship"] = "unverified"
    with pytest.raises(ValueError):
        registry.import_rows([observation(), invalid])
    assert registry.lookup("https://news.example.org/") is None
    registry.import_rows([observation()])
    with sqlite3.connect(registry.path) as db:
        db.execute(
            "UPDATE sources SET observation=?",
            (json.dumps(observation() | {"checked_at": None}),),
        )
    db.close()
    assert registry.lookup("https://news.example.org/") is None
    registry.path.unlink()
    assert registry.lookup("https://news.example.org/") is None
    assert not registry.path.exists()


class RefusingScanner:
    calls = 0

    async def scan(self, *args, **kwargs):
        self.calls += 1
        raise DiscoveryError("Source refused access (HTTP 403).")


def test_detection_is_read_only_and_scan_failure_returns_guidance(tmp_path):
    scanner = RefusingScanner()
    app = create_app(tmp_path / "library.db", scanner)
    app.state.source_registry.import_rows([observation()])
    with TestClient(app) as client:
        detected = client.post(
            "/api/source-method/detect", json={"url": "https://news.example.org/"}
        )
        assert detected.status_code == 200
        assert detected.json()["source_status"]["alternatives"][0]["kind"] == "feed"
        assert scanner.calls == 0
        # An advisory observation never silently changes the selected source or denies a scan.
        response = client.post("/api/scans", json={"url": "https://news.example.org/"})
        assert response.status_code == 422
        assert response.json()["source_status"]["status"] == "access_blocked"
        assert scanner.calls == 1
        assert client.post("/api/source-status", json=observation()).status_code in {
            404,
            405,
        }
