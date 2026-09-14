"""Source choices and conservative URL-only API detection (no network requests)."""

import json
import os
import re
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from .urls import DiscoveryError, canonical_url

SourceMethod = Literal[
    "auto",
    "sitemap",
    "wordpress_com",
    "wordpress",
    "devto",
    "github",
    "codeforces",
    "mastodon",
    "youtube",
    "ghost",
    "mangadex",
    "steam",
    "browser",
]


def detect_source_method(url):
    """Choose only collection APIs whose scope matches the supplied URL.

    Never probe arbitrary hosts, guess from an @username alone, or replace a
    filtered archive with an entire site's inventory. The fetcher still validates
    and pins public DNS addresses when the user actually requests a scan.
    """
    result = {"source_method": "auto", "note": ""}
    try:
        p = urlsplit(canonical_url(url))
    except (DiscoveryError, ValueError, UnicodeError):
        return result
    host, path, query = (
        p.hostname,
        p.path.rstrip("/"),
        parse_qs(p.query, keep_blank_values=True),
    )
    method = "auto"
    if host == "store.steampowered.com" and not query:
        from .steam import app_id

        if app_id(url):
            return {"source_method": "steam", "note": ""}
    if not query:
        if (
            re.fullmatch(r"[a-z0-9-]+\.wordpress\.com", host)
            and host.split(".")[0]
            not in {"www", "public-api", "developer", "apps", "login", "api", "support"}
            and not path
        ):
            method = "wordpress_com"
        elif re.fullmatch(r"(?:/[\w.-]+)*/wp-json(?:/wp/v2)?", path):
            method = "wordpress"
        elif host in {"dev.to", "www.dev.to"} and re.fullmatch(r"/[\w-]+", path):
            if path[1:].lower() not in {
                "about",
                "admin",
                "api",
                "contact",
                "dashboard",
                "enter",
                "faq",
                "latest",
                "new",
                "pod",
                "podcasts",
                "privacy",
                "readinglist",
                "search",
                "settings",
                "tags",
                "terms",
                "top",
                "videos",
            }:
                method = "devto"
        elif host in {"github.com", "www.github.com"} and re.fullmatch(
            r"/[\w.-]+/[\w.-]+/releases", path
        ):
            # Bare repositories can be README job tables rather than releases.
            method = "github"
        elif host in {"mastodon.social", "mastodon.online"} and re.fullmatch(
            r"/(?:@|users/)[\w.-]+", path
        ):
            method = "mastodon"
        elif not path and p.scheme == "https":
            try:
                keys = json.loads(os.environ.get("TRACKER_GHOST_CONTENT_KEYS", "{}"))
            except ValueError:
                keys = None
            if (
                isinstance(keys, dict)
                and isinstance(keys.get(host), str)
                and keys[host].strip()
            ):
                method = "ghost"
    if (
        host in {"codeforces.com", "www.codeforces.com"}
        and path == "/contests"
        and query.keys() <= {"locale"}
    ):
        method = "codeforces"
    if host in {"mangadex.org", "www.mangadex.org"} and query.keys() <= {
        "tab",
        "order",
    }:
        from .mangadex import title_id

        if title_id(url):
            method = "mangadex"
    if host == "www.youtube.com":
        channel = not query and re.fullmatch(
            r"/(?:channel/UC[\w-]{22}|@[^/]+|user/[\w-]+)", path
        )
        playlist = (
            path == "/playlist"
            and query.keys() == {"list"}
            and len(query["list"]) == 1
            and re.fullmatch(r"[\w-]+", query["list"][0])
        )
        if channel or playlist:
            if os.environ.get("TRACKER_YOUTUBE_API_KEY", "").strip():
                method = "youtube"
            else:
                result["note"] = (
                    "YouTube detected. Using the page and feed scanner because a server API key is not configured."
                )
    result["source_method"] = method
    return result
