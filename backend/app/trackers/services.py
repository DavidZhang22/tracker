from dataclasses import dataclass
from html import unescape
import re
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from django.utils import timezone

from .models import Entry, Tracker


@dataclass(frozen=True)
class LinkCandidate:
    title: str
    url: str
    summary: str = ""


def fetch_page(url):
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; MediaTracker/1.0; "
                "+https://github.com/DavidZhang22/tracker)"
            )
        },
    )
    with urlopen(request, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
    return body


def extract_links(page_url, html):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        absolute_url = urljoin(page_url, anchor.get("href", "").strip())

        if not _is_trackable_link(page_url, absolute_url, title):
            continue
        if absolute_url in seen:
            continue

        seen.add(absolute_url)
        candidates.append(
            LinkCandidate(
                title=(unescape(title) or absolute_url)[:500],
                url=absolute_url,
                summary=_nearby_text(anchor),
            )
        )

    for candidate in _extract_embedded_links(page_url, html):
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        candidates.append(candidate)

    if _should_infer_chapters(soup, candidates):
        for candidate in _infer_numbered_chapters(page_url, soup, candidates):
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            candidates.append(candidate)

    return candidates


def refresh_tracker(tracker):
    html = fetch_page(tracker.target_url)
    created_entries = []

    for candidate in extract_links(tracker.target_url, html):
        entry, created = Entry.objects.get_or_create(
            tracker=tracker,
            url=candidate.url,
            defaults={
                "title": candidate.title,
                "summary": candidate.summary,
                "is_new": True,
            },
        )
        if created:
            created_entries.append(entry)
            continue

        updates = []
        if candidate.title and entry.title != candidate.title:
            entry.title = candidate.title
            updates.append("title")
        if candidate.summary and entry.summary != candidate.summary:
            entry.summary = candidate.summary
            updates.append("summary")
        if updates:
            entry.save(update_fields=updates)

    tracker.last_checked_at = timezone.now()
    tracker.save(update_fields=["last_checked_at", "updated_at"])
    return created_entries


def refresh_due_trackers():
    refreshed = []
    for tracker in Tracker.objects.all():
        if tracker.is_due:
            refreshed.append((tracker, refresh_tracker(tracker)))
    return refreshed


def _is_trackable_link(page_url, absolute_url, title):
    parsed = urlparse(absolute_url)
    page = urlparse(page_url)

    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc != page.netloc:
        return False
    if len(title) < 3:
        return False

    lowered = absolute_url.lower()
    if any(
        lowered.endswith(ext)
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf", ".zip")
    ):
        return False

    return _looks_like_release_url(parsed)


def _nearby_text(anchor):
    container = anchor.find_parent(["article", "li", "section", "div"])
    if not container:
        return ""
    text = " ".join(container.get_text(" ", strip=True).split())
    if len(text) <= 40:
        return ""
    return text[:500]


def _infer_numbered_chapters(page_url, soup, candidates):
    chapter_patterns = {}

    for candidate in candidates:
        match = re.search(r"^(?P<prefix>.+/chapter-)(?P<number>\d+)(?P<suffix>/?(?:[?#].*)?)$", candidate.url)
        if not match:
            continue

        key = (match.group("prefix"), match.group("suffix"))
        chapter_patterns.setdefault(key, set()).add(int(match.group("number")))

    inferred = []
    chapter_total = _chapter_total(soup)

    for (prefix, suffix), observed_numbers in chapter_patterns.items():
        if len(observed_numbers) < 2:
            continue

        highest_observed = max(observed_numbers)
        highest_chapter = chapter_total or highest_observed
        if highest_chapter <= 0 or highest_chapter > 2000:
            continue

        for chapter_number in range(1, highest_chapter + 1):
            if chapter_number in observed_numbers:
                continue
            url = f"{prefix}{chapter_number}{suffix}"
            if not _is_same_site(page_url, url):
                continue
            inferred.append(
                LinkCandidate(
                    title=f"Chapter {chapter_number}",
                    url=url,
                    summary="Inferred from numbered chapter URL pattern.",
                )
            )

    return inferred


def _extract_embedded_links(page_url, html):
    embedded_candidates = []
    seen = set()
    page = urlparse(page_url)
    same_site_path = re.escape(f"{page.scheme}://{page.netloc}")
    normalized_html = html.replace("\\/", "/")
    patterns = [
        rf"{same_site_path}/[^\s\"'<>\\]+",
        r"/(?:series|fiction|news|en|[12]\d{3})/[^\s\"'<>\\]+",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, normalized_html):
            raw_url = match.group(0)
            if raw_url.startswith("/") and _is_path_inside_external_url(normalized_html, match.start()):
                continue
            absolute_url = urljoin(page_url, unescape(raw_url))
            title = _title_from_url(absolute_url)

            if absolute_url in seen:
                continue
            if not _is_trackable_link(page_url, absolute_url, title):
                continue

            seen.add(absolute_url)
            embedded_candidates.append(
                LinkCandidate(
                    title=title,
                    url=absolute_url,
                    summary="Found in embedded page data.",
                )
            )

    return embedded_candidates


def _should_infer_chapters(soup, candidates):
    chapter_total = _chapter_total(soup)
    if not chapter_total:
        return False

    chapter_links = [
        candidate
        for candidate in candidates
        if re.search(r"/chapter-\d+(?:\.\d+)?(?:/?(?:[?#].*)?)$", candidate.url)
    ]
    if len(chapter_links) >= chapter_total:
        return False

    page_text = " ".join(soup.get_text(" ", strip=True).split()).lower()
    has_expand_signal = any(
        phrase in page_text
        for phrase in ("show all", "load more", "view all", "all chapters")
    )
    return has_expand_signal and len(chapter_links) >= 2


def _chapter_total(soup):
    text = " ".join(soup.get_text(" ", strip=True).split())
    match = re.search(r"\b(\d{1,4})\s+chapters?\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _title_from_url(url):
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    chapter_match = re.search(r"chapter-(\d+(?:\.\d+)?)", slug, flags=re.IGNORECASE)
    if chapter_match:
        return f"Chapter {chapter_match.group(1)}"
    return slug.replace("-", " ").title()[:500] or url


def _is_same_site(page_url, absolute_url):
    parsed = urlparse(absolute_url)
    page = urlparse(page_url)
    return parsed.scheme in {"http", "https"} and parsed.netloc == page.netloc


def _is_path_inside_external_url(html, path_start):
    prefix = html[max(0, path_start - 120):path_start]
    last_delimiter = max(prefix.rfind('"'), prefix.rfind("'"), prefix.rfind("<"), prefix.rfind(">"), prefix.rfind(" "))
    current_token_prefix = prefix[last_delimiter + 1:]
    return "://" in current_token_prefix


def _looks_like_release_url(parsed):
    path = parsed.path.rstrip("/")
    lower_path = path.lower()
    segments = [segment for segment in lower_path.split("/") if segment]

    if not segments:
        return False

    if any(segment in {"tag", "tags", "category", "categories", "author", "authors"} for segment in segments):
        return False

    if re.search(r"/chapter-\d+(?:\.\d+)?$", lower_path):
        return True

    if re.search(r"/fiction/\d+/.+/chapter/\d+/.+$", lower_path):
        return True

    if lower_path.endswith("/viewer"):
        query = parse_qs(parsed.query)
        if "episode_no" in query and "title_no" in query:
            return True

    if re.search(r"/(?:news/)?[12]\d{3}/\d{2}(?:/\d{2})?/[^/]+$", lower_path):
        return True

    if parsed.netloc.endswith("medium.com") and re.search(r"/[^/]+/[^/]+-[0-9a-f]{8,}$", lower_path):
        return True

    if re.search(r"/[^/]+-[0-9a-f]{8,}$", lower_path):
        return True

    return False
