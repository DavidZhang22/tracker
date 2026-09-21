import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.discovery import Discoverer
from app.tracker.models import Entry, Scan
from app.tracker.source_methods import detect_source_method

WP = "https://galactoidtetris.wordpress.com/"
TITLE = "https://mangadex.org/title/d8a959f7-648e-4c8d-8f23-f1f3f8e129f3/one-punch-man"


@pytest.mark.parametrize(
    "url,method",
    [
        (WP, "wordpress_com"),
        (WP + "?utm_source=test#posts", "wordpress_com"),
        ("https://example.org/wp-json/", "wordpress"),
        ("https://example.org/blog/wp-json/wp/v2/", "wordpress"),
        ("https://DEV.TO/alice/", "devto"),
        ("https://dev.to/my-org", "devto"),
        ("https://github.com/python/cpython/releases/", "github"),
        ("https://codeforces.com/contests?locale=en", "codeforces"),
        ("https://www.codeforces.com/contests/", "codeforces"),
        (TITLE + "?tab=chapters&order=asc", "mangadex"),
        ("https://mastodon.social/@alice", "mastodon"),
        ("https://mastodon.online/users/alice", "mastodon"),
    ],
)
def test_collection_urls_select_the_matching_api(url, method):
    assert detect_source_method(url)["source_method"] == method


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/SimplifyJobs/New-Grad-Positions",
        "https://github.com/python/cpython/releases/tag/v3.14.0",
        "https://github.com/python/cpython/releases?q=stable",
        WP + "2026/09/14/post/",
        WP + "category/tetris/",
        WP + "?s=tetris",
        "https://dev.to/alice/my-post",
        "https://dev.to/tags",
        "https://dev.to/top",
        "https://dev.to/search",
        "https://codeforces.com/contest/100",
        "https://codeforces.com/contests?filter=upcoming",
        TITLE + "?group=some-group",
        "https://mastodon.social/@alice/123",
        "https://unknown.example/@alice",
        "https://example.org/",
        "https://example.org/wp-json/wp/v2/posts?categories=2",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://dev.to.evil.example/alice",
        "https://notwordpress.com/",
        "https://www.wordpress.com/",
        "https://public-api.wordpress.com/",
        "https://wordpress.com.evil.example/",
        "https://github.com@evil.example/a/b/releases",
        "https://github.com/a/b/releases\\evil",
        "https://github.com:8000/a/b/releases",
        "file:///etc/passwd",
        "http://[broken",
        "not a URL",
    ],
)
def test_ambiguous_filtered_and_unsafe_urls_do_not_change_scope(url):
    assert detect_source_method(url) == {"source_method": "auto", "note": ""}


def test_keys_gate_detection_without_exposing_credentials(monkeypatch):
    monkeypatch.delenv("TRACKER_YOUTUBE_API_KEY", raising=False)
    missing = detect_source_method("https://youtube.com/@alice")
    assert missing["source_method"] == "auto" and "not configured" in missing["note"]
    monkeypatch.setenv("TRACKER_YOUTUBE_API_KEY", "private-test-youtube-key")
    for url in (
        "https://youtube.com/@alice",
        "https://m.youtube.com/user/alice",
        "https://youtube.com/channel/UCRIgIJQWuBJ0Cv_VlU3USNA",
        "https://www.youtube.com/playlist?list=PLabc123",
    ):
        assert detect_source_method(url) == {"source_method": "youtube", "note": ""}
    monkeypatch.setenv(
        "TRACKER_GHOST_CONTENT_KEYS",
        json.dumps({"blog.example": "private-test-ghost-key"}),
    )
    assert detect_source_method("https://blog.example/")["source_method"] == "ghost"
    for url in (
        "http://blog.example",
        "https://blog.example/post",
        "https://blog.example.evil/",
        "https://other.example",
    ):
        assert detect_source_method(url)["source_method"] == "auto"
    for config in (
        "broken-json",
        "[]",
        '{"blog.example": 123}',
        '{"blog.example": ""}',
    ):
        monkeypatch.setenv("TRACKER_GHOST_CONTENT_KEYS", config)
        assert detect_source_method("https://blog.example")["source_method"] == "auto"


class RecordingScanner:
    def __init__(self):
        self.calls = []

    async def scan(self, url, *args, **kwargs):
        self.calls.append(kwargs.get("source_method", "auto"))
        return Scan(url, "Test", entries=[Entry(WP + "post", "Post")])


def test_server_resolves_default_and_saves_method_for_both_refreshes(tmp_path):
    scanner = RecordingScanner()
    app = create_app(tmp_path / "library.db", scanner)
    with TestClient(app) as c:
        assert (
            c.post("/api/source-method/detect", json={"url": WP}).json()[
                "source_method"
            ]
            == "wordpress_com"
        )
        assert scanner.calls == []  # Suggestions never scan or spend source quota.
        preview = c.post("/api/scans", json={"url": WP}).json()
        assert preview["source_method"] == "wordpress_com"
        created = c.post(
            "/api/items", json={"scan_id": preview["scan_id"], "mark_read": True}
        )
        assert created.status_code == 201, created.text
        item = created.json()
        assert item["source_method"] == "wordpress_com"
        for suffix in ("", "?deep=true"):
            assert c.post(f"/api/items/{item['id']}/refresh{suffix}").status_code == 200
        assert scanner.calls == ["wordpress_com"] * 3
        assert c.get(f"/api/items/{item['id']}").json()["read_count"] == 1


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"source_method": "sitemap"}, "sitemap"),
        ({"source_method": "auto", "detect_api": False}, "auto"),
        ({"selector": "article a"}, "auto"),
        ({"selector": "  "}, "wordpress_com"),
    ],
)
def test_manual_choices_and_css_selectors_override_detection(
    tmp_path, values, expected
):
    scanner = RecordingScanner()
    with TestClient(create_app(tmp_path / "library.db", scanner)) as c:
        response = c.post("/api/scans", json={"url": WP, **values})
        assert response.status_code == 200, response.text
        assert response.json()["source_method"] == expected
        assert scanner.calls == [expected]


def test_auto_api_uses_only_listing_request_with_publication_dates(tmp_path):
    class Fetcher:
        calls = []

        async def get(self, url, **kwargs):
            self.calls.append(url)
            assert url.startswith(
                "https://public-api.wordpress.com/rest/v1.1/sites/galactoidtetris.wordpress.com/posts/?"
            )
            return url, json.dumps(
                {
                    "found": 1,
                    "posts": [
                        {
                            "ID": 12,
                            "URL": WP + "post",
                            "title": "Post",
                            "date": "2026-09-14T12:00:00Z",
                        }
                    ],
                }
            )

    fetcher = Fetcher()
    with TestClient(create_app(tmp_path / "library.db", Discoverer(fetcher))) as c:
        result = c.post("/api/scans", json={"url": WP})
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["source_method"] == "wordpress_com"
        assert data["entries"][0]["date_kind"] == "published"
        assert data["entries"][0]["published_at"].startswith("2026-09-14")
        assert len(fetcher.calls) == 1


def test_arxiv_api_selection_is_saved_and_reused(tmp_path):
    scanner = RecordingScanner()
    source = "https://arxiv.org/search/cs?query=Hoffmann+et+al.+2022&searchtype=all"
    with TestClient(create_app(tmp_path / "library.db", scanner)) as client:
        assert (
            client.post("/api/source-method/detect", json={"url": source}).json()[
                "source_method"
            ]
            == "arxiv"
        )
        preview = client.post("/api/scans", json={"url": source}).json()
        assert preview["source_method"] == "arxiv"
        item = client.post("/api/items", json={"scan_id": preview["scan_id"]}).json()
        assert item["source_method"] == "arxiv"
        for suffix in ("", "?deep=true"):
            assert (
                client.post(f"/api/items/{item['id']}/refresh{suffix}").status_code
                == 200
            )
        assert scanner.calls == ["arxiv"] * 3
