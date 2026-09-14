"""Public collection APIs with bounded pagination and no individual content requests."""

import html
import json
import os
import re
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .adapters import codeforces_endpoint, codeforces_scan
from .dates import evidence
from .limits import MAX_LINKS
from .mangadex import scan_mangadex
from .models import Entry, Scan
from .urls import DiscoveryError, canonical_url, content_key


def plain(value):
    return html.unescape(re.sub(r"<[^>]*>", " ", str(value or ""))).strip()[:1000]


def endpoint(base, **params):
    return base + "?" + urlencode(params)


def positive(value):
    try:
        value = int(value)
        return value if 0 <= value <= 10_000_000 else None
    except (ValueError, TypeError):
        return None


class Inventory:
    def __init__(self, fetcher, source, name, limit):
        self.fetcher, self.source, self.limit = fetcher, source, limit
        self.calls, self.seen, self.records = 0, set(), {}
        self.result = Scan(
            source,
            urlsplit(source).hostname,
            "blog",
            methods=[name + " API"],
            coverage="complete",
            analysis_mode="light",
        )

    async def get(self, url, secret=None):
        if self.calls >= self.limit:
            raise DiscoveryError(
                f"API discovery reached its {self.limit}-request limit."
            )
        if url in self.seen:
            raise DiscoveryError(
                "The API repeated a page. Scanning stopped to avoid extra requests."
            )
        self.seen.add(url)
        self.calls += 1
        _, text = await self.fetcher.get(
            url, **({"secret_query": {"key": secret}} if secret else {})
        )
        self.result.pages_scanned += 1
        try:
            data = json.loads(text)
            if not isinstance(data, (dict, list)):
                raise ValueError()
            return data
        except (ValueError, TypeError) as exc:
            raise DiscoveryError(
                "The API returned unreadable listing data. Try Automatic or Sitemap."
            ) from exc

    def add(
        self,
        url,
        title,
        date=None,
        *,
        source_id="",
        context="",
        language="",
        date_kind="published",
    ):
        try:
            url = canonical_url(url)
        except (DiscoveryError, ValueError):
            self.result.coverage = "partial"
            self.result.warnings.append(
                "Some API records had invalid URLs and were skipped."
            )
            return
        if url == canonical_url(self.source):
            return
        entry = Entry(
            url,
            plain(title) or url,
            method="public API",
            source_id=source_id[:300],
            context=plain(context),
            language=str(language or "")[:30],
            **evidence(date, self.result.methods[0], date_kind),
        )
        key = source_id or content_key(url)
        self.records[key] = entry
        if len(self.records) >= MAX_LINKS:
            raise DiscoveryError(f"API discovery reached the {MAX_LINKS:,}-link limit.")

    def rows(self, value):
        if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
            raise DiscoveryError(
                "The API returned an unexpected listing format. Try Automatic or Sitemap."
            )
        # Avoid unbounded processing even for a misbehaving API response.
        return value[: MAX_LINKS + 1]

    def progress(self, before, rows):
        if rows and len(self.records) == before:
            raise DiscoveryError(
                "The API returned no new usable links. Scanning stopped to avoid extra requests."
            )


async def wordpress_com(inv):
    host = urlsplit(inv.source).hostname
    base = f"https://public-api.wordpress.com/rest/v1.1/sites/{quote(host, safe='')}/posts/"
    params = dict(
        number=100,
        type="any",
        fields="ID,URL,title,date,modified,type,tags,categories",
        order="DESC",
        order_by="date",
    )
    received = 0
    while True:
        data = await inv.get(endpoint(base, **params))
        rows = inv.rows(data.get("posts"))
        received += len(rows)
        before = len(inv.records)
        for row in rows:
            labels = [
                *list((row.get("tags") or {}).keys()),
                *list((row.get("categories") or {}).keys()),
            ]
            inv.add(
                row.get("URL"),
                row.get("title"),
                row.get("date"),
                source_id=f"wp:{host}:{row['ID']}" if row.get("ID") else "",
                context=" ".join(labels),
            )
        token = (data.get("meta") or {}).get("next_page")
        if not token:
            if (positive(data.get("found")) or 0) > received:
                raise DiscoveryError(
                    "WordPress reported more entries but did not provide the next page. The listing may be incomplete."
                )
            break
        inv.progress(before, rows)
        params["page_handle"] = str(token)


async def wordpress(inv):
    p = urlsplit(inv.source)
    # A /wp-json/ URL also supports WordPress installed in a subdirectory.
    prefix = p.path.split("/wp-json", 1)[0] if "/wp-json" in p.path else ""
    base = f"{p.scheme}://{p.netloc}{prefix}/wp-json/wp/v2"
    for kind in ("posts", "pages"):
        page = 1
        while True:
            data = await inv.get(
                endpoint(
                    base + "/" + kind,
                    per_page=100,
                    page=page,
                    _envelope=1,
                    _fields="id,link,title,date_gmt,date,modified_gmt,type",
                    orderby="id",
                    order="asc",
                )
            )
            headers = data.get("headers", {}) if isinstance(data, dict) else {}
            if isinstance(data, dict) and data.get("status", 200) >= 400:
                raise DiscoveryError(
                    "WordPress refused the public listing request. Try Automatic or Sitemap."
                )
            rows = inv.rows(data.get("body") if isinstance(data, dict) else data)
            before = len(inv.records)
            for row in rows:
                date = (row["date_gmt"] + "Z") if row.get("date_gmt") else None
                inv.add(
                    row.get("link"),
                    (row.get("title") or {}).get("rendered"),
                    date,
                    source_id=f"wp:{p.hostname}:{row['id']}" if row.get("id") else "",
                )
            total_pages = next(
                (
                    positive(v)
                    for k, v in headers.items()
                    if k.lower() == "x-wp-totalpages"
                ),
                None,
            )
            if len(rows) < 100 or (total_pages is not None and page >= total_pages):
                break
            inv.progress(before, rows)
            page += 1


async def devto(inv):
    p = urlsplit(inv.source)
    match = re.fullmatch(r"/([\w-]+)/?", p.path)
    if p.hostname not in {"dev.to", "www.dev.to"} or not match:
        raise DiscoveryError(
            "Use a DEV.to author or organization URL, such as https://dev.to/username."
        )
    page = 1
    while True:
        rows = inv.rows(
            await inv.get(
                endpoint(
                    "https://dev.to/api/articles",
                    username=match[1],
                    per_page=100,
                    page=page,
                )
            )
        )
        before = len(inv.records)
        for row in rows:
            inv.add(
                row.get("url"),
                row.get("title"),
                row.get("published_at"),
                source_id=f"devto:{row['id']}" if row.get("id") else "",
                context=" ".join(row.get("tag_list") or []),
            )
        if len(rows) < 100:
            break
        inv.progress(before, rows)
        page += 1


async def github(inv):
    p = urlsplit(inv.source)
    match = re.fullmatch(r"/([\w.-]+)/([\w.-]+)(?:/releases)?/?", p.path)
    if p.hostname not in {"github.com", "www.github.com"} or not match:
        raise DiscoveryError(
            "Use a GitHub repository or releases URL. For README job listings, choose Automatic."
        )
    repo = match[1] + "/" + match[2].removesuffix(".git")
    inv.result.title, inv.result.kind = repo + " releases", "website"
    page = 1
    while True:
        rows = inv.rows(
            await inv.get(
                endpoint(
                    f"https://api.github.com/repos/{repo}/releases",
                    per_page=100,
                    page=page,
                )
            )
        )
        before = len(inv.records)
        for row in rows:
            if row.get("draft"):
                continue
            inv.add(
                row.get("html_url"),
                row.get("name") or row.get("tag_name"),
                row.get("published_at"),
                source_id=f"github:{repo}:release:{row['id']}" if row.get("id") else "",
                context="Prerelease" if row.get("prerelease") else "Release",
            )
        if len(rows) < 100:
            break
        inv.progress(before, rows)
        page += 1


async def mastodon(inv):
    p = urlsplit(inv.source)
    match = re.fullmatch(r"/(?:@|users/)([\w.-]+)/?", p.path)
    if not match:
        raise DiscoveryError(
            "Use a Mastodon profile URL, such as https://mastodon.social/@username."
        )
    origin = f"{p.scheme}://{p.netloc}"
    account = await inv.get(endpoint(origin + "/api/v1/accounts/lookup", acct=match[1]))
    aid = str(account.get("id", ""))
    if not re.fullmatch(r"\d+", aid):
        raise DiscoveryError(
            "The Mastodon account could not be resolved through its public API."
        )
    inv.result.title = plain(account.get("display_name") or match[1])
    params = dict(limit=40, exclude_reblogs="true")
    while True:
        rows = inv.rows(
            await inv.get(
                endpoint(origin + f"/api/v1/accounts/{aid}/statuses", **params)
            )
        )
        before = len(inv.records)
        for row in rows:
            if row.get("reblog") or row.get("visibility") not in {"public", "unlisted"}:
                continue
            inv.add(
                row.get("url"),
                plain(row.get("spoiler_text") or row.get("content"))[:180],
                row.get("created_at"),
                source_id=f"mastodon:{p.hostname}:{row['id']}" if row.get("id") else "",
                language=row.get("language"),
            )
        if len(rows) < 40:
            break
        inv.progress(before, rows)
        token = str(rows[-1].get("id", ""))
        if not token.isdigit():
            raise DiscoveryError("Mastodon returned an invalid pagination cursor.")
        params["max_id"] = token


async def youtube(inv):
    key = os.environ.get("TRACKER_YOUTUBE_API_KEY", "")
    if not key:
        raise DiscoveryError(
            "YouTube API needs a server API key. Choose Automatic for now, or configure TRACKER_YOUTUBE_API_KEY using the deployment guide."
        )
    p = urlsplit(inv.source)
    if p.hostname not in {"youtube.com", "www.youtube.com"}:
        raise DiscoveryError("Use a YouTube channel or playlist URL.")
    playlist = parse_qs(p.query).get("list", [""])[0] if p.path == "/playlist" else ""
    base = "https://www.googleapis.com/youtube/v3/"
    if not playlist:
        path = p.path.rstrip("/")
        if re.fullmatch(r"/channel/UC[\w-]{22}", path):
            who = {"id": path.split("/")[-1]}
        elif re.fullmatch(r"/@[^/]+", path):
            who = {"forHandle": path[1:]}
        elif re.fullmatch(r"/user/[\w-]+", path):
            who = {"forUsername": path.split("/")[-1]}
        else:
            raise DiscoveryError(
                "YouTube API supports channel IDs, @handles, legacy user URLs, and playlists."
            )
        data = await inv.get(
            endpoint(base + "channels", part="snippet,contentDetails", **who), key
        )
        rows = inv.rows(data.get("items"))
        if not rows:
            raise DiscoveryError("The YouTube API could not find that channel.")
        playlist = (
            rows[0]
            .get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads", "")
        )
        inv.result.title = plain(rows[0].get("snippet", {}).get("title"))
    if not re.fullmatch(r"[\w-]+", playlist):
        raise DiscoveryError("The YouTube uploads playlist is unavailable.")
    inv.result.kind = "youtube"
    params = dict(
        part="snippet,contentDetails",
        playlistId=playlist,
        maxResults=50,
        fields="nextPageToken,items(contentDetails(videoId,videoPublishedAt),snippet(title,resourceId(videoId)))",
    )
    while True:
        data = await inv.get(endpoint(base + "playlistItems", **params), key)
        rows = inv.rows(data.get("items"))
        before = len(inv.records)
        for row in rows:
            details, snippet = row.get("contentDetails", {}), row.get("snippet", {})
            vid = str(details.get("videoId", ""))
            if not re.fullmatch(r"[\w-]{11}", vid) or snippet.get("title") in {
                "Private video",
                "Deleted video",
            }:
                continue
            inv.add(
                "https://www.youtube.com/watch?v=" + vid,
                snippet.get("title"),
                details.get("videoPublishedAt"),
                source_id="youtube:" + vid,
            )
        if not data.get("nextPageToken"):
            break
        inv.progress(before, rows)
        params["pageToken"] = str(data["nextPageToken"])


async def ghost(inv):
    p = urlsplit(inv.source)
    try:
        keys = json.loads(os.environ.get("TRACKER_GHOST_CONTENT_KEYS", "{}"))
        key = keys.get(p.hostname) if isinstance(keys, dict) else None
    except ValueError:
        key = None
    if not isinstance(key, str) or not key:
        raise DiscoveryError(
            "Ghost API needs a Content API key configured for this hostname. Choose Sitemap or configure TRACKER_GHOST_CONTENT_KEYS using the deployment guide."
        )
    if p.scheme != "https":
        raise DiscoveryError("Use HTTPS for a Ghost API source.")
    base = f"https://{p.netloc}/ghost/api/content"
    for kind in ("posts", "pages"):
        page = 1
        while True:
            data = await inv.get(
                endpoint(
                    base + "/" + kind + "/",
                    limit=100,
                    page=page,
                    fields="id,uuid,title,url,published_at,updated_at",
                    order="published_at desc",
                ),
                key,
            )
            rows = inv.rows(data.get(kind))
            before = len(inv.records)
            for row in rows:
                inv.add(
                    row.get("url"),
                    row.get("title"),
                    row.get("published_at"),
                    source_id=f"ghost:{p.hostname}:{row['id']}"
                    if row.get("id")
                    else "",
                )
            next_page = (data.get("meta") or {}).get("pagination", {}).get("next")
            if next_page is None:
                break
            inv.progress(before, rows)
            if positive(next_page) is None or int(next_page) <= page:
                raise DiscoveryError("Ghost returned an invalid pagination cursor.")
            page = int(next_page)


async def scan_api(fetcher, source, method, max_pages, keywords=""):
    if method == "mangadex":
        return await scan_mangadex(fetcher, source, max_pages, keywords)
    if method == "codeforces":
        target = codeforces_endpoint(source)
        if not target:
            raise DiscoveryError(
                "Use https://codeforces.com/contests with the Codeforces API."
            )
        _, text = await fetcher.get(target)
        result = codeforces_scan(text, source)
        result.pages_scanned, result.analysis_mode = 1, "light"
        return result
    providers = {
        "wordpress_com": (wordpress_com, "WordPress.com"),
        "wordpress": (wordpress, "WordPress"),
        "devto": (devto, "DEV.to"),
        "github": (github, "GitHub releases"),
        "mastodon": (mastodon, "Mastodon"),
        "youtube": (youtube, "YouTube"),
        "ghost": (ghost, "Ghost"),
    }
    if method not in providers:
        raise DiscoveryError("Choose a supported API.")
    provider, label = providers[method]
    inv = Inventory(fetcher, source, label, max_pages)
    try:
        await provider(inv)
    except (DiscoveryError, AttributeError, TypeError, KeyError, ValueError) as exc:
        message = (
            str(exc)
            if isinstance(exc, DiscoveryError)
            else "The API returned an unexpected listing format. Try Automatic or Sitemap."
        )
        if not inv.records:
            raise DiscoveryError(message) from exc
        inv.result.coverage = "partial"
        inv.result.warnings.append(message)
    inv.result.entries = list(inv.records.values())
    inv.result.expected_count = (
        len(inv.records) if inv.result.coverage == "complete" else None
    )
    inv.result.warnings = list(dict.fromkeys(inv.result.warnings))
    return inv.result
