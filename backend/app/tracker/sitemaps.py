"""Bounded sitemap inventories. Never fetch a listed content page."""

import re
from collections import deque
from urllib.parse import unquote, urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

from .dates import evidence
from .limits import MAX_LINKS
from .models import Entry, Scan
from .urls import DiscoveryError, canonical_url

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def elements(root, name):
    prefix = "{" + NS + "}" if root.tag.startswith("{" + NS + "}") else ""
    return root.findall(prefix + name), prefix


def document(text):
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
        raise DiscoveryError(
            "Sitemaps containing document types or entities are not supported."
        )
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DiscoveryError(
            "The source did not return a readable XML sitemap."
        ) from exc
    if root.tag not in {
        "urlset",
        "sitemapindex",
        "{" + NS + "}urlset",
        "{" + NS + "}sitemapindex",
    }:
        raise DiscoveryError("The source did not return a sitemap or sitemap index.")
    return root


async def scan_sitemap(fetcher, source, max_pages):
    parts = urlsplit(source)
    origin = f"{parts.scheme}://{parts.netloc}"
    direct = bool(re.search(r"(?:\.xml(?:\.gz)?|/sitemap)/?$", parts.path, re.I))
    result = Scan(
        source,
        parts.hostname + " pages",
        "website",
        methods=["sitemap"],
        coverage="complete",
        analysis_mode="light",
    )
    queue, allowed = deque(), {parts.hostname}
    robots = None
    attempted = 0
    if direct:
        queue.append(source)
    else:
        attempted += 1
        try:
            _, text = await fetcher.get(origin + "/robots.txt")
            result.pages_scanned += 1
            robots = RobotFileParser()
            robots.parse(text.splitlines())
            for value in re.findall(r"^\s*Sitemap\s*:\s*(\S+)", text, re.I | re.M)[
                :100
            ]:
                try:
                    url = canonical_url(value, origin, preserve_slash=True)
                    allowed.add(urlsplit(url).hostname)
                    queue.append(url)
                except DiscoveryError:
                    continue
        except DiscoveryError:
            result.warnings.append(
                "Sitemap discovery could not read robots.txt; trying the standard sitemap address."
            )
        if not queue:
            queue.append(origin + "/sitemap.xml")
    seen, entries = set(), {}
    while queue and attempted < max_pages and len(entries) < MAX_LINKS:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        if urlsplit(url).hostname not in allowed:
            result.coverage = "partial"
            result.warnings.append(
                "A sitemap index left the declared sitemap hosts and was skipped."
            )
            continue
        if (
            robots
            and urlsplit(url).hostname == parts.hostname
            and not robots.can_fetch("MediaTracker", url)
        ):
            result.coverage = "partial"
            result.warnings.append("A sitemap was excluded by robots.txt.")
            continue
        attempted += 1
        try:
            final, text = await fetcher.get(url)
            if urlsplit(final).hostname not in allowed:
                raise DiscoveryError("A sitemap redirected outside its declared hosts.")
            seen.add(final)
            root = document(text)
            result.pages_scanned += 1
        except DiscoveryError as exc:
            result.coverage = "partial"
            result.warnings.append(str(exc))
            continue
        is_index = root.tag.endswith("sitemapindex")
        nodes, prefix = elements(root, "sitemap" if is_index else "url")
        for node in nodes:
            try:
                target = canonical_url(
                    node.findtext(prefix + "loc"), final, preserve_slash=is_index
                )
            except DiscoveryError:
                continue
            if is_index:
                if target not in seen and len(queue) < 200:
                    queue.append(target)
                elif len(queue) >= 200:
                    result.coverage = "partial"
                continue
            # Only direct sitemap loc fields count; image/video loc fields are not pages.
            if urlsplit(target).hostname != parts.hostname:
                continue
            path = urlsplit(target).path
            if canonical_url(target) == origin + "/" or re.search(
                r"\.(?:png|jpe?g|gif|webp|svg|ico|mp4|mp3|zip|pdf)$", path, re.I
            ):
                continue
            if robots and not robots.can_fetch("MediaTracker", target):
                continue
            label = (
                unquote(path.rstrip("/").rsplit("/", 1)[-1])
                .replace("-", " ")
                .replace("_", " ")
                or target
            )
            entries[target] = Entry(
                target,
                label[:1000],
                method="sitemap",
                **evidence(
                    node.findtext(prefix + "lastmod"), "Sitemap lastmod", "updated"
                ),
            )
            if len(entries) >= MAX_LINKS:
                result.coverage = "partial"
                break
    if any(u not in seen for u in queue) or len(entries) >= MAX_LINKS:
        result.coverage = "partial"
        result.warnings.append(
            f"Sitemap discovery reached its limit of {max_pages} requests or {MAX_LINKS:,} links."
        )
    result.entries = list(entries.values())
    result.expected_count = len(entries) if result.coverage == "complete" else None
    result.warnings = list(dict.fromkeys(result.warnings))
    if not result.entries and result.coverage == "partial":
        raise DiscoveryError(
            "No sitemap pages could be read. " + " ".join(result.warnings)
        )
    return result
