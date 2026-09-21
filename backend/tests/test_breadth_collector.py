import hashlib

import pytest

from app.tracker.cache import FetchCache
from app.tracker.documents import Document
from app.tracker.errors import DiscoveryError
from app.tracker.urls import SafeFetcher
from ml.collect_breadth import ListingFetcher, RobotsPolicy, collect, robots_policy


@pytest.mark.asyncio
async def test_robots_fetch_is_possible_before_installing_listing_policy(monkeypatch):
    calls = []

    async def request(self, url, *args):
        calls.append(url)
        return url, None, 0

    monkeypatch.setattr(SafeFetcher, "_request", request)
    fetcher = ListingFetcher("https://example.org/public", object())
    await fetcher._request("https://example.org/robots.txt")
    fetcher.install_robots("User-agent: *\nDisallow: /private")
    await fetcher._request("https://example.org/public")
    with pytest.raises(DiscoveryError, match="disallowed by robots"):
        await fetcher._request("https://example.org/private/list")
    assert calls == ["https://example.org/robots.txt", "https://example.org/public"]


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["example.org", "www.example.org"])
async def test_installed_rules_apply_to_allowed_host_aliases(monkeypatch, host):
    async def no_network(*args):
        pytest.fail("A denied URL must not reach the underlying request")

    monkeypatch.setattr(SafeFetcher, "_request", no_network)
    fetcher = ListingFetcher("https://example.org/public", object())
    fetcher.install_robots(
        "User-agent: Trackify\nDisallow: /private\nUser-agent: *\nAllow: /"
    )
    with pytest.raises(DiscoveryError, match="disallowed by robots"):
        await fetcher._request(f"https://{host}/private/list")


@pytest.mark.asyncio
async def test_collector_checks_robots_before_following_a_listing_redirect(
    tmp_path, monkeypatch
):
    from app.tracker import urls

    origin = "https://example.org"
    calls = []

    async def request(self, url, *args):
        calls.append(url)
        if url == origin + "/robots.txt":
            return url, Document.from_text("User-agent: *\nDisallow: /private"), 0
        if url == origin + "/public":
            return origin + "/private/list", None, 300
        pytest.fail("The redirected private listing must not be fetched")

    async def addresses(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(SafeFetcher, "_request", request)
    monkeypatch.setattr(urls, "public_addresses", addresses)
    row = {"id": "breadth-test-redirect", "url": origin + "/public"}
    await collect(row, tmp_path, FetchCache(tmp_path / "cache.sqlite3"))
    assert row["status"] == "unavailable"
    assert "disallowed by robots" in row["error"]
    assert calls == [origin + "/robots.txt", origin + "/public"]
    assert not (tmp_path / "breadth-test-redirect.html").exists()
    saved_robots = (tmp_path / "breadth-test-redirect-robots.txt").read_bytes()
    assert hashlib.sha256(saved_robots).hexdigest() == row["robots_sha256"]


@pytest.mark.parametrize(
    ("rule", "path", "allowed"),
    [
        ("/private/*", "/private/list", False),
        ("/private/*", "/public/list", True),
        ("/download/*.pdf$", "/download/report.pdf", False),
        ("/download/*.pdf$", "/download/report.pdf?preview=1", True),
        ("/private$", "/private", False),
        ("/private$", "/private/public", True),
        ("/a*b*c$", "/a-one-b-two-c", False),
        ("/a*b*c$", "/a-one-c-two-b", True),
        ("/a*ab$", "/a", True),
        ("/query?preview=*", "/query?preview=yes", False),
        ("/escaped/%2A", "/escaped/*", True),
        ("/escaped/%2A", "/escaped/%2a", False),
        ("/%70rivate", "/private", False),
        ("/private", "/%70rivate", False),
        ("/caf\u00e9", "/caf%C3%A9", False),
        ("/a%2Fb", "/a/b", True),
        ("/a%2Fb", "/a%2fb", False),
    ],
)
def test_robots_wildcards_end_anchor_and_percent_encoding(rule, path, allowed):
    policy = RobotsPolicy("User-agent: *\nDisallow: " + rule)
    assert policy.can_fetch("https://example.org" + path) is allowed


def test_longest_matching_rule_wins_and_allow_wins_equal_length():
    rules = "User-agent: *\nDisallow: /private\nAllow: /private/public\nDisallow: /private/public/admin"
    assert robots_policy(rules, "https://example.org/private/public/story")[0]
    assert not robots_policy(rules, "https://example.org/private/public/admin")[0]
    assert robots_policy(
        "User-agent: *\nDisallow: /same\nAllow: /same", "https://example.org/same"
    )[0]


def test_matching_product_groups_are_combined_and_use_strictest_rate():
    text = """User-agent: Trackify
Disallow: /private
Crawl-delay: 3

User-agent: TRACKIFY
Disallow: /hidden
Allow: /private/public
Request-rate: 1/12

User-agent: *
Disallow: /
Crawl-delay: 100
"""
    assert robots_policy(text, "https://example.org/private") == (False, 12)
    assert robots_policy(text, "https://example.org/hidden") == (False, 12)
    assert robots_policy(text, "https://example.org/private/public") == (True, 12)
    assert robots_policy(text, "https://example.org/list") == (True, 12)


def test_most_specific_product_group_overrides_less_specific_and_unrelated_agents():
    text = """User-agent: Track
Disallow: /
User-agent: UnrelatedBot
Disallow: /
User-agent: Trackify
Allow: /
Crawl-delay: 4.5
"""
    assert robots_policy(text, "https://example.org/list") == (True, 4.5)


def test_empty_rules_bom_and_comments_do_not_create_accidental_denials():
    assert (
        robots_policy(
            "\ufeffUser-agent: *\nDisallow: /private # comment",
            "https://example.org/private",
        )[0]
        is False
    )
    assert robots_policy(
        "User-agent: *\nDisallow:\nAllow:", "https://example.org/list"
    ) == (True, 2)
