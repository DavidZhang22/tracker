import gzip
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tracker.cache import FetchCache
from app.tracker.discovery import Discoverer
from app.tracker.models import Entry, Scan
from app.tracker.store import Store
from app.tracker.urls import DiscoveryError, RequestBudget, SafeFetcher, request_budget

ROOT = "https://blog.example/"
WP = "https://galactoidtetris.wordpress.com/"


class Listings:
    def __init__(self, handler):
        self.handler, self.calls, self.secrets = handler, [], []
        self.cache = FetchCache()

    async def get(self, url, **kwargs):
        self.calls.append(url)
        self.secrets.append(kwargs.get("secret_query"))
        p = urlsplit(url)
        value = self.handler(p, parse_qs(p.query))
        if isinstance(value, Exception):
            raise value
        return url, value if isinstance(value, str) else json.dumps(value)


def wp_row(i, host="galactoidtetris.wordpress.com"):
    return {
        "ID": i,
        "URL": f"https://{host}/post-{i}/",
        "title": f"Post {i}",
        "date": "2026-09-01T00:00:00Z",
        "type": "post",
        "tags": {"English": {}},
        "categories": {},
    }


async def test_wordpress_inventory_paginates_both_types_without_post_requests():
    def respond(p, q):
        assert p.hostname == "public-api.wordpress.com"
        assert q["type"] == ["any"] and q["number"] == ["100"]
        assert "content" not in q["fields"][0]
        if "page_handle" not in q:
            return {
                "found": 3,
                "posts": [wp_row(1), wp_row(2)],
                "meta": {"next_page": "next&safe=1"},
            }
        assert q["page_handle"] == ["next&safe=1"]
        return {"found": 3, "posts": [wp_row(3) | {"type": "page"}], "meta": {}}

    fetcher = Listings(respond)
    scanner = Discoverer(fetcher)
    result = await scanner.scan(WP, source_method="wordpress_com")
    assert len(result.entries) == 3 and result.pages_scanned == 2
    assert result.coverage == "complete" and all(
        e.published_at and e.source_id for e in result.entries
    )
    assert (await scanner.scan(WP, source_method="wordpress_com")).cached
    assert len(fetcher.calls) == 2


async def test_inventory_keywords_and_path_filters_work_without_models(monkeypatch):
    fetcher = Listings(lambda p, q: {"posts": [wp_row(1), wp_row(2)], "meta": {}})

    def unexpected(*args, **kwargs):
        pytest.fail("Inventory scanning should not run the HTML model")

    monkeypatch.setattr("app.tracker.discovery.parse_page", unexpected)
    monkeypatch.setattr("app.tracker.discovery.parser_version", unexpected)
    result = await Discoverer(fetcher).scan(
        WP, include_path="post-2", keywords="English", source_method="wordpress_com"
    )
    assert len(result.entries) == 1 and result.entries[0].url.endswith("post-2")


async def test_cache_is_separate_for_each_source_method():
    def respond(p, q):
        if p.hostname == "public-api.wordpress.com":
            return {"posts": [wp_row(1)], "meta": {}}
        if p.path == "/robots.txt":
            return "Sitemap: https://galactoidtetris.wordpress.com/sitemap.xml"
        if p.path == "/sitemap.xml":
            return sitemap("https://galactoidtetris.wordpress.com/post-2")
        pytest.fail(f"Unexpected content request {p.path}")

    fetcher = Listings(respond)
    scanner = Discoverer(fetcher)
    api = await scanner.scan(WP, source_method="wordpress_com")
    xml = await scanner.scan(WP, source_method="sitemap")
    assert api.entries[0].url != xml.entries[0].url and not xml.cached


async def test_repeating_api_pages_and_failed_next_page_stop_with_partial_results():
    for next_result in (
        {"posts": [wp_row(1)], "meta": {"next_page": "next"}},
        DiscoveryError("HTTP 429 paused"),
    ):
        fetcher = Listings(
            lambda p, q, next_result=next_result: (
                next_result
                if "page_handle" in q
                else {"posts": [wp_row(1)], "meta": {"next_page": "next"}}
            )
        )
        result = await Discoverer(fetcher).scan(WP, source_method="wordpress_com")
        assert result.coverage == "partial" and len(result.entries) == 1
        assert len(fetcher.calls) == 2 and result.warnings


async def test_api_page_and_link_limits(monkeypatch):
    fetcher = Listings(
        lambda p, q: {
            "posts": [wp_row(int(q.get("page_handle", ["1"])[0]))],
            "meta": {"next_page": str(int(q.get("page_handle", ["1"])[0]) + 1)},
        }
    )
    result = await Discoverer(fetcher, max_pages=2).scan(
        WP, source_method="wordpress_com"
    )
    assert (
        result.coverage == "partial" and len(fetcher.calls) == len(result.entries) == 2
    )
    monkeypatch.setattr("app.tracker.public_apis.MAX_LINKS", 3)
    fetcher = Listings(
        lambda p, q: {"posts": [wp_row(i) for i in range(10)], "meta": {}}
    )
    result = await Discoverer(fetcher).scan(WP, source_method="wordpress_com")
    assert len(result.entries) == 3 and result.coverage == "partial"


async def test_self_hosted_wordpress_uses_envelope_totals_and_includes_pages():
    def respond(p, q):
        assert p.path.startswith("/blog/wp-json/wp/v2/") and q["_envelope"] == ["1"]
        assert q["page"] == ["1"]
        rows = (
            [
                {
                    "id": i,
                    "link": f"https://blog.example/{i}",
                    "title": {"rendered": "A &amp; B"},
                    "date_gmt": "2026-09-01T01:00:00",
                }
                for i in range(100)
            ]
            if p.path.endswith("posts")
            else [
                {
                    "id": 101,
                    "link": "https://blog.example/about",
                    "title": {"rendered": "About"},
                }
            ]
        )
        return {"body": rows, "headers": {"X-WP-TotalPages": "1"}, "status": 200}

    f = Listings(respond)
    result = await Discoverer(f).scan(ROOT + "blog/wp-json/", source_method="wordpress")
    assert len(f.calls) == 2 and len(result.entries) == 101
    assert result.entries[0].title == "A & B"
    assert result.entries[0].published_at.endswith("+00:00")


@pytest.mark.parametrize(
    "method,source,payload",
    [
        (
            "devto",
            "https://dev.to/alice",
            [
                {
                    "id": 1,
                    "url": "https://dev.to/alice/a-post",
                    "title": "Post",
                    "published_at": "2026-09-01T00:00:00Z",
                    "tag_list": ["python"],
                }
            ],
        ),
        (
            "github",
            "https://github.com/owner/repo/releases",
            [
                {
                    "id": 1,
                    "html_url": "https://github.com/owner/repo/releases/tag/v1",
                    "name": "v1",
                    "published_at": "2026-09-01T00:00:00Z",
                    "draft": False,
                }
            ],
        ),
        (
            "codeforces",
            "https://codeforces.com/contests",
            {
                "status": "OK",
                "result": [{"id": 1, "name": "Round", "startTimeSeconds": 1700000000}],
            },
        ),
    ],
)
async def test_public_providers_return_dated_entries(method, source, payload):
    f = Listings(lambda p, q: payload)
    result = await Discoverer(f).scan(source, source_method=method)
    assert len(result.entries) == len(f.calls) == 1 and result.entries[0].published_at
    if method == "codeforces":
        assert result.entries[0].date_kind == "scheduled"


async def test_mastodon_looks_up_local_profile_then_lists_statuses():
    def respond(p, q):
        if p.path.endswith("lookup"):
            assert q["acct"] == ["alice"]
            return {"id": "42", "display_name": "Alice"}
        assert p.path == "/api/v1/accounts/42/statuses"
        return [
            {
                "id": "77",
                "url": "https://social.example/@alice/77",
                "content": "<p>Hello &amp; welcome</p>",
                "visibility": "public",
                "created_at": "2026-09-01T00:00:00Z",
                "language": "en",
            }
        ]

    f = Listings(respond)
    result = await Discoverer(f).scan(
        "https://social.example/@alice", source_method="mastodon"
    )
    assert len(f.calls) == 2 and result.entries[0].title == "Hello & welcome"
    assert result.entries[0].language == "en"


async def test_youtube_api_uses_uploads_playlist_and_video_publication_date(
    monkeypatch,
):
    monkeypatch.setenv("TRACKER_YOUTUBE_API_KEY", "secret-test-key")

    def respond(p, q):
        assert p.hostname == "www.googleapis.com"
        if p.path.endswith("channels"):
            assert q["forHandle"] == ["@alice"]
            return {
                "items": [
                    {
                        "snippet": {"title": "Alice"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UUtest"}},
                    }
                ]
            }
        assert q["playlistId"] == ["UUtest"]
        return {
            "items": [
                {
                    "snippet": {
                        "title": "Video",
                        "publishedAt": "2026-09-10T00:00:00Z",
                    },
                    "contentDetails": {
                        "videoId": "abcdefghijk",
                        "videoPublishedAt": "2026-09-01T00:00:00Z",
                    },
                }
            ]
        }

    f = Listings(respond)
    result = await Discoverer(f).scan(
        "https://www.youtube.com/@alice", source_method="youtube"
    )
    assert len(f.calls) == 2 and "secret-test-key" not in str(f.calls)
    assert all(secret == {"key": "secret-test-key"} for secret in f.secrets)
    assert result.entries[0].published_at.startswith("2026-09-01")


async def test_ghost_key_is_scoped_to_exact_host_and_posts_pages_are_included(
    monkeypatch,
):
    monkeypatch.setenv(
        "TRACKER_GHOST_CONTENT_KEYS", json.dumps({"blog.example": "ghost-secret"})
    )
    f = Listings(
        lambda p, q: {
            "posts" if p.path.endswith("posts/") else "pages": [
                {
                    "id": p.path,
                    "url": ROOT + p.path.split("/")[-2],
                    "title": "Content",
                    "published_at": "2026-09-01T00:00:00Z",
                }
            ],
            "meta": {"pagination": {"next": None}},
        }
    )
    result = await Discoverer(f).scan(ROOT, source_method="ghost")
    assert len(f.calls) == len(result.entries) == 2
    with pytest.raises(DiscoveryError, match="key"):
        await Discoverer(f).scan("https://other.example", source_method="ghost")
    assert len(f.calls) == 2


@pytest.mark.parametrize(
    "method,source",
    [
        ("youtube", "https://www.youtube.com/@alice"),
        ("ghost", ROOT),
        ("github", ROOT),
        ("devto", ROOT),
        ("mastodon", ROOT),
        ("codeforces", ROOT),
        ("mangadex", ROOT),
    ],
)
async def test_unavailable_or_wrong_api_never_falls_back_to_scraping(
    method, source, monkeypatch
):
    monkeypatch.delenv("TRACKER_YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("TRACKER_GHOST_CONTENT_KEYS", raising=False)
    f = Listings(lambda *args: pytest.fail("Unexpected request"))
    with pytest.raises(DiscoveryError):
        await Discoverer(f).scan(source, source_method=method)
    assert f.calls == []


def sitemap(*urls):
    return (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
        + "".join(
            f"<url><loc>{url}</loc><lastmod>2026-09-01</lastmod><image:image><image:loc>https://blog.example/image.png</image:loc></image:image></url>"
            for url in urls
        )
        + "</urlset>"
    )


async def test_sitemap_index_cycles_images_external_links_and_date_semantics():
    pages = {
        "/robots.txt": "Sitemap: https://blog.example/sitemap.xml\nUser-agent: *\nDisallow: /private/",
        "/sitemap.xml": "<sitemapindex><sitemap><loc>https://blog.example/posts.xml</loc></sitemap><sitemap><loc>https://blog.example/sitemap.xml</loc></sitemap></sitemapindex>",
        "/posts.xml": sitemap(
            ROOT,
            ROOT + "first-post",
            ROOT + "first-post#comments",
            ROOT + "private/secret",
            "https://other.example/post",
        ),
    }
    f = Listings(lambda p, q: pages[p.path])
    result = await Discoverer(f).scan(ROOT, source_method="sitemap")
    assert len(f.calls) == 3 and len(result.entries) == 1
    assert (
        result.entries[0].title == "first post"
        and result.entries[0].date_kind == "updated"
    )
    assert result.coverage == "complete"


async def test_direct_sitemap_is_one_request_and_unreadable_map_does_not_crawl():
    f = Listings(lambda p, q: sitemap(ROOT + "post"))
    assert (
        len(
            (
                await Discoverer(f).scan(ROOT + "sitemap.xml", source_method="sitemap")
            ).entries
        )
        == 1
    )
    assert len(f.calls) == 1
    f = Listings(lambda p, q: '<html><a href="/post">Do not crawl me</a></html>')
    with pytest.raises(DiscoveryError, match="sitemap"):
        await Discoverer(f).scan(ROOT + "sitemap.xml", source_method="sitemap")
    assert len(f.calls) == 1


async def test_sitemap_blocks_entities_and_caps_requests():
    f = Listings(lambda p, q: '<!DOCTYPE urlset [<!ENTITY test "secret">]><urlset/>')
    with pytest.raises(DiscoveryError, match="entities"):
        await Discoverer(f).scan(ROOT + "sitemap.xml", source_method="sitemap")
    f = Listings(
        lambda p, q: (
            "<sitemapindex><sitemap><loc>https://blog.example/next.xml</loc></sitemap></sitemapindex>"
            if p.path == "/sitemap.xml"
            else sitemap(ROOT + "post")
        )
    )
    with pytest.raises(DiscoveryError, match="limit"):
        await Discoverer(f, max_pages=1).scan(
            ROOT + "sitemap.xml", source_method="sitemap"
        )
    assert len(f.calls) == 1


async def test_credentialed_fetch_cache_is_redacted_and_redirects_do_not_forward_key():
    actual_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.params["key"] == "private-key"
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://other.example/"})
        return httpx.Response(200, json={"posts": []})

    cache = FetchCache()
    with (
        patch("app.tracker.urls.public_addresses", return_value=["93.184.216.34"]),
        patch(
            "app.tracker.urls.httpx.AsyncClient",
            side_effect=lambda **kw: actual_client(
                transport=httpx.MockTransport(handle), **kw
            ),
        ),
    ):
        f = SafeFetcher(cache, interval=0)
        for _ in range(2):
            final, _ = await f.get(ROOT + "api", secret_query={"key": "private-key"})
            assert "private-key" not in final
        assert len(requests) == 1 and "private-key" not in json.dumps(cache.memory)
        with pytest.raises(DiscoveryError, match="not forwarded"):
            await f.get(ROOT + "redirect", secret_query={"key": "private-key"})
        assert len(requests) == 2


async def test_gzip_sitemaps_have_decompression_and_request_budgets():
    actual_client = httpx.AsyncClient
    payload = gzip.compress(sitemap(ROOT + "post").encode())
    with (
        patch("app.tracker.urls.public_addresses", return_value=["93.184.216.34"]),
        patch(
            "app.tracker.urls.httpx.AsyncClient",
            side_effect=lambda **kw: actual_client(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, content=payload)
                ),
                **kw,
            ),
        ),
    ):
        f = SafeFetcher(interval=0)
        budget = RequestBudget(limit=1)
        token = request_budget.set(budget)
        try:
            _, text = await f.get(ROOT + "sitemap.xml.gz")
            assert "urlset" in text and budget.requests == 1
            payload = gzip.compress(b"x" * 8_000_001)
            with pytest.raises(DiscoveryError, match="8 MB"):
                request_budget.set(RequestBudget(limit=1))
                await f.get(ROOT + "large.xml.gz")
        finally:
            request_budget.reset(token)


def test_source_method_persists_across_add_refresh_and_settings(tmp_path):
    class Scanner:
        def __init__(self):
            self.methods = []

        async def scan(self, url, *args, **kwargs):
            self.methods.append(kwargs.get("source_method", "auto"))
            return Scan(url, "Test", entries=[Entry(ROOT + "post", "Post")])

    scanner = Scanner()
    app = create_app(tmp_path / "library.db", scanner)
    with TestClient(app) as c:
        for invalid in ("unlisted", "http://evil.example"):
            assert (
                c.post(
                    "/api/scans", json={"url": ROOT, "source_method": invalid}
                ).status_code
                == 422
            )
        sid = c.post(
            "/api/scans", json={"url": ROOT, "source_method": "sitemap"}
        ).json()["scan_id"]
        item = c.post("/api/items", json={"scan_id": sid, "mark_read": True}).json()
        assert item["source_method"] == "sitemap"
        c.post(f"/api/items/{item['id']}/refresh?deep=true")
        assert (
            c.patch(
                f"/api/items/{item['id']}", json={"source_method": "wordpress"}
            ).status_code
            == 200
        )
        c.post(f"/api/items/{item['id']}/refresh")
        assert scanner.methods == ["sitemap", "sitemap", "wordpress"]
        assert c.get(f"/api/items/{item['id']}").json()["read_count"] == 1
        c.patch("/api/settings", json={"source_method": "sitemap"})
        assert Store(app.state.store.path).settings()["source_method"] == "sitemap"


def test_api_id_url_changes_and_switching_methods_preserve_one_link(tmp_path):
    store = Store(tmp_path / "library.db")

    def scan(url, source_id=""):
        return Scan(
            ROOT, "Blog", entries=[Entry(url, "Post", source_id=source_id)]
        ).to_dict()

    item = store.create(store.save_scan(scan(ROOT + "old")), mark_read=True)
    first = store.links(item["id"])["links"][0]
    assert store.merge(item["id"], scan(ROOT + "old", "wp:blog.example:7")) == 0
    assert store.merge(item["id"], scan(ROOT + "renamed", "wp:blog.example:7")) == 0
    assert store.merge(item["id"], scan(ROOT + "renamed")) == 0
    assert store.item(item["id"])["total_count"] == 1

    # A sitemap can discover a second alias before the API supplies its ID.
    assert store.merge(item["id"], scan(ROOT + "renamed-again")) == 1
    newest = next(
        r
        for r in store.links(item["id"])["links"]
        if r["url"].endswith("renamed-again")
    )
    store.update("links", newest["id"], {"favorite": True})
    assert (
        store.merge(item["id"], scan(ROOT + "renamed-again", "wp:blog.example:7")) == 0
    )
    row = store.links(item["id"])["links"][0]
    assert row["id"] == first["id"] and row["read"] and row["favorite"]
    assert store.item(item["id"])["total_count"] == 1


def test_sitemap_refresh_retains_real_titles_publication_dates_and_context(tmp_path):
    store = Store(tmp_path / "library.db")
    original = Entry(
        ROOT + "post",
        "A real title",
        "2026-09-01T00:00:00Z",
        method="public API",
        context="English tutorial",
    )
    item = store.create(
        store.save_scan(Scan(ROOT, "Blog", entries=[original]).to_dict())
    )
    sitemap_row = Entry(
        ROOT + "post",
        "post",
        "2026-09-14T00:00:00Z",
        method="sitemap",
        date_kind="updated",
    )
    for _ in range(2):
        assert (
            store.merge(item["id"], Scan(ROOT, "Blog", entries=[sitemap_row]).to_dict())
            == 0
        )
        row = store.links(item["id"])["links"][0]
        assert (
            row["title"] == original.title
            and row["published_at"] == original.published_at
        )
        assert row["context"] == original.context and row["date_kind"] == "published"
