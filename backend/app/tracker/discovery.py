import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

from .adapters import codeforces_endpoint, codeforces_scan, wetried_scan, wetried_series
from .fenrir import fenrir_endpoint, fenrir_scan
from .github import github_readme
from .limits import MAX_LINKS, bounded_scan
from .link_model import model_cache_tag
from .models import Entry, Scan, date_value, utcnow
from .parser import kind_for, merge_entries, parse_page, relevant
from .urls import (
    DiscoveryError,
    RequestBudget,
    SafeFetcher,
    canonical_url,
    content_key,
    request_budget,
)


def youtube_archive(url):
    """Use a maintained extractor for continuation tokens. No video downloads."""
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if parsed.hostname not in {"www.youtube.com", "youtube.com"}:
        raise DiscoveryError("Only YouTube source URLs can use the archive adapter.")
    if parsed.path == "/playlist" and re.fullmatch(
        r"[\w-]+", query.get("list", [""])[0]
    ):
        url = "https://www.youtube.com/playlist?list=" + query["list"][0]
    elif parsed.path == "/watch" and re.fullmatch(
        r"[\w-]{11}", query.get("v", [""])[0]
    ):
        url = "https://www.youtube.com/watch?v=" + query["v"][0]
    elif re.fullmatch(
        r"/(?:channel/UC[\w-]{22}|@[^/?#]+|(?:c|user)/[\w-]+)(?:/(?:videos|shorts|streams))?",
        parsed.path,
    ):
        url = "https://www.youtube.com" + parsed.path
    else:
        raise DiscoveryError(
            "Use a YouTube channel, playlist, video, or channel tab URL."
        )
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--ignore-config",
                "--no-cache-dir",
                "--flat-playlist",
                "--dump-single-json",
                "--skip-download",
                "--ignore-errors",
                "--no-warnings",
                "--socket-timeout",
                "15",
                "--retries",
                "1",
                "--extractor-retries",
                "1",
                "--playlist-end",
                str(MAX_LINKS + 1),
                "--sleep-requests",
                "2",
                url,
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise DiscoveryError(
            "YouTube archive lookup timed out. Only page/feed entries were scanned."
        ) from exc
    if process.returncode or not process.stdout.strip():
        raise DiscoveryError(
            "YouTube archive lookup failed. Only page/feed entries were scanned; retry later and keep yt-dlp updated."
        )
    try:
        data = json.loads(process.stdout)
    except ValueError as exc:
        raise DiscoveryError("YouTube returned an unreadable archive.") from exc
    entries = []

    def visit(obj):
        if not isinstance(obj, dict):
            return
        if obj.get("entries") is not None:
            for child in obj["entries"]:
                visit(child)
            return
        vid = obj.get("id", "")
        if not re.fullmatch(r"[\w-]{11}", vid):
            return
        entries.append(
            Entry(
                "https://www.youtube.com/watch?v=" + vid,
                obj.get("title") or vid,
                date_value(
                    obj.get("timestamp")
                    or obj.get("release_timestamp")
                    or (
                        obj.get("upload_date")
                        and f"{obj['upload_date'][:4]}-{obj['upload_date'][4:6]}-{obj['upload_date'][6:]}"
                    )
                ),
                position=len(entries),
                method="YouTube archive",
            )
        )

    visit(data)
    warnings = []
    if "ERROR:" in process.stderr:
        warnings.append(
            "YouTube could not load part of the archive. Some entries may be missing."
        )
    if len(entries) > MAX_LINKS:
        warnings.append(
            f"YouTube reached the {MAX_LINKS:,}-link limit. Older entries may be missing."
        )
    return data.get("title"), entries[:MAX_LINKS], warnings


class Discoverer:
    def __init__(self, fetcher=None, max_pages=40, youtube_loader=youtube_archive):
        self.fetcher = fetcher or SafeFetcher()
        self.max_pages = max_pages
        self.youtube_loader = youtube_loader
        self.scan_locks = [asyncio.Lock() for _ in range(64)]

    async def scan(self, url, selector="", include_path=""):
        url = canonical_url(url, preserve_slash=True)
        async with self.scan_locks[hash(url) % 64]:
            return await self._cached_scan(url, selector, include_path)

    async def _cached_scan(self, url, selector, include_path):
        cache = getattr(self.fetcher, "cache", None)
        key = (
            "scan:"
            + hashlib.sha256(
                json.dumps(
                    [
                        url,
                        selector,
                        include_path,
                        model_cache_tag(),
                        "collection-api-v4",
                    ]
                ).encode()
            ).hexdigest()
        )
        cached = cache.get(key) if cache else None
        if cached and time.time() - cached["checked"] < 600:
            data = bounded_scan(cached["scan"])
            result = Scan(**(data | {"entries": [Entry(**e) for e in data["entries"]]}))
            result.cached, result.requests_made, result.cache_hits = True, 0, 1
            return result
        budget = RequestBudget(self.max_pages)
        token = request_budget.set(budget)
        try:
            async with asyncio.timeout(180):
                result = await self._scan(url, selector, include_path)
                result.requests_made, result.cache_hits = budget.requests, budget.hits
                result.checked_at = utcnow()
                if cache and result.entries:
                    cache.put(key, {"scan": result.to_dict()})
                return result
        except TimeoutError as exc:
            raise DiscoveryError(
                "Scan exceeded three minutes. Use a smaller archive or a feed URL."
            ) from exc
        finally:
            request_budget.reset(token)

    async def _scan(self, url, selector, include_path):
        first_error = None
        endpoint = codeforces_endpoint(url) if not selector else None
        chapter_endpoint = fenrir_endpoint(url) if not selector else None
        try:
            final, text = await self.fetcher.get(endpoint or chapter_endpoint or url)
        except DiscoveryError as exc:
            if kind_for(url) != "youtube":
                raise
            first_error = str(exc)
            final, text = url, "<title>YouTube channel</title>"
        if chapter_endpoint:
            final = url
            result, pages, feeds = fenrir_scan(text, url), [], []
        elif endpoint:
            final = url
            result, pages, feeds = codeforces_scan(text, url), [], []
        else:
            text, readme_pages = await github_readme(self.fetcher, final, text)
            result, pages, feeds = parse_page(text, final, selector, include_path)
        result.pages_scanned = 0 if first_error else 1
        if not endpoint and not chapter_endpoint:
            result.pages_scanned += readme_pages
            if readme_pages:
                result.methods.append("GitHub README")
        if not selector and (series_id := wetried_series(text, final)) is not None:
            result = await wetried_scan(
                self.fetcher, final, series_id, result, self.max_pages - 1
            )
            pages, feeds = [], []
        need_feed = not result.entries or any(
            not e.published_at or e.date_kind == "inferred" for e in result.entries
        )
        queue = [(u, False) for u in pages] + [(u, True) for u in feeds if need_feed]
        seen = {url, final}
        if result.kind == "youtube":
            channel = re.search(r"/channel/(UC[\w-]{22})", final) or re.search(
                r'"(?:channelId|externalId)"\s*:\s*"(UC[\w-]{22})"', text
            )
            queue = []
            if channel and (
                re.fullmatch(r"/channel/UC[\w-]{22}", urlsplit(final).path.rstrip("/"))
                or re.fullmatch(r"/@[^/]+", urlsplit(final).path.rstrip("/"))
            ):
                queue.append(
                    (
                        "https://www.youtube.com/feeds/videos.xml?channel_id="
                        + channel[1],
                        True,
                    )
                )
        attempted = result.pages_scanned
        while queue and attempted < self.max_pages and len(result.entries) < MAX_LINKS:
            target, is_feed = queue.pop(0)
            if target in seen:
                continue
            seen.add(target)
            attempted += 1
            # Only discovered same-origin pagination is followed. Feeds may use a CDN.
            if not is_feed and urlsplit(target).hostname != urlsplit(final).hostname:
                result.warnings.append(
                    "A pagination link left the source host and was skipped."
                )
                continue
            try:
                actual, html = await self.fetcher.get(target)
                seen.add(actual)
                part, more, _ = parse_page(
                    html, actual, "" if is_feed else selector, include_path
                )
                if (
                    not is_feed
                    and urlsplit(actual).hostname != urlsplit(final).hostname
                ):
                    raise DiscoveryError("A page redirected outside the source host.")
                if is_feed and "feed" not in part.methods:
                    raise DiscoveryError(
                        "The advertised feed did not return a supported RSS or Atom feed."
                    )
                result.pages_scanned += 1
                incoming = part.entries
                if result.kind in {"novel", "comic"}:
                    fiction = re.search(r"/fiction/(\d+)", final)

                    def trusted_feed_alias(
                        entry, is_feed=is_feed, fiction=fiction, target=target
                    ):
                        return bool(
                            is_feed
                            and fiction
                            and urlsplit(target).path == f"/syndication/{fiction[1]}"
                            and urlsplit(target).hostname == urlsplit(final).hostname
                            and urlsplit(entry.url).hostname == urlsplit(final).hostname
                            and re.fullmatch(
                                r"/fiction/chapter/\d+", urlsplit(entry.url).path
                            )
                        )

                    incoming = [
                        e
                        for e in incoming
                        if relevant(e.url, final, e.title, result.kind)
                        or trusted_feed_alias(e)
                    ]
                if include_path:
                    incoming = [e for e in incoming if include_path in e.url]
                result.entries = merge_entries(result.entries + incoming)
                result.methods = list(dict.fromkeys(result.methods + part.methods))
                result.warnings += part.warnings
                queue.extend((u, is_feed) for u in more if u not in seen)
            except DiscoveryError as exc:
                result.coverage = "partial"
                result.warnings.append(f"Could not scan {target}: {exc}")
        if any(u not in seen for u, _ in queue):
            result.coverage = "partial"
            result.warnings.append(
                f"Scan stopped at the {self.max_pages}-page limit. More pages may remain."
            )
        if result.kind == "youtube":
            try:
                # Channel tabs group shorts/streams separately. The uploads playlist
                # gives one ordered sequence across all public upload types.
                root_path = urlsplit(final).path.rstrip("/")
                archive_url = final
                if channel and (
                    re.fullmatch(r"/channel/UC[\w-]{22}", root_path)
                    or re.fullmatch(r"/@[^/]+", root_path)
                ):
                    archive_url = (
                        "https://www.youtube.com/playlist?list=UU" + channel[1][2:]
                    )
                title, entries, warnings = await asyncio.to_thread(
                    self.youtube_loader, archive_url
                )
                if entries:
                    archive_keys = {content_key(e.url) for e in entries}
                    metadata = [
                        e
                        for e in result.entries
                        if e.method == "feed" or content_key(e.url) in archive_keys
                    ]
                    result.entries = merge_entries(entries + metadata)
                    result.title = (
                        title.removeprefix("Uploads from ") if title else result.title
                    )
                    result.methods.append("YouTube archive")
                else:
                    result.warnings.append(
                        "YouTube archive returned no videos. The list may be incomplete."
                    )
                result.warnings += warnings
            except DiscoveryError as exc:
                result.warnings.append(str(exc))
            if first_error:
                result.warnings.append(first_error)
        result.entries = merge_entries(result.entries)
        if include_path:
            result.entries = [e for e in result.entries if include_path in e.url]
        if len(result.entries) > MAX_LINKS:
            result.entries = result.entries[:MAX_LINKS]
            result.warnings.append(f"Scan reached the {MAX_LINKS:,}-link limit.")
        if result.expected_count and len(result.entries) < result.expected_count:
            result.coverage = "partial"
            result.warnings.append(
                f"The source reports {result.expected_count} entries; found {len(result.entries)}. Some links may be missing."
            )
        elif result.expected_count and result.coverage != "partial":
            result.coverage = "complete"
        if not result.entries:
            result.warnings.append(
                "No content links found. Try a feed/archive URL or adjust the link selector. Links loaded only by JavaScript may be unavailable."
            )
        if result.kind in {"blog", "website"} and result.methods == ["feed"]:
            result.warnings.append(
                "Feeds can contain only recent releases. The full historical archive is not guaranteed."
            )
        # Never infer ordering from database IDs or unrelated URL numbers.
        if result.order_hint == "source":
            pass  # The public index explicitly orders groups, chapter parts, and extras.
        elif result.entries and all(e.number is not None for e in result.entries):
            result.entries.sort(key=lambda e: e.number)
        elif result.entries and all(e.published_at for e in result.entries):
            result.entries.sort(key=lambda e: (e.published_at, e.position))
        else:
            dated = [e for e in result.entries if e.published_at]
            numbered = [e for e in result.entries if e.number is not None]
            if (
                (len(dated) > 1 and dated[0].published_at > dated[-1].published_at)
                or (len(numbered) > 1 and numbered[0].number > numbered[-1].number)
                or (result.kind == "youtube" and urlsplit(final).path != "/playlist")
            ):
                result.entries.reverse()
            if any(not e.published_at for e in result.entries):
                result.warnings.append(
                    "Some dates are unavailable. Source order is used where needed; individual content pages are not fetched for dates."
                )
        for i, e in enumerate(result.entries):
            e.position = i
        result.warnings = list(dict.fromkeys(result.warnings))
        return result
