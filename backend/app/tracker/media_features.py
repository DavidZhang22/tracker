"""Bounded, field-aware input for the media-format classifier."""

import re
from urllib.parse import unquote, urlsplit

# Features express format rather than a host name, author, or subject matter.
FORMAT_PATTERNS = {
    "comic": r"\b(?:manga|manhwa|manhua|webtoons?|webcomics?|comics?|comic strips?)\b",
    "novel": r"\b(?:novels?|webnovels?|fiction|ebooks?|books?|serial fiction)\b",
    "blog": r"\b(?:blogs?|weblogs?|news|articles?|essays?|posts?|newsletters?|stories|magazine)\b",
    "video": r"\b(?:videos?|anime|watch|tv series|television|films?|movies?|recordings?|technical sessions|presentations?)\b",
    "podcast": r"\b(?:podcasts?|audio show|earbuds|listen to|radio show)\b",
    "music": r"\b(?:albums?|discography|songs?|music|tracks?|singles?|record label)\b",
    "events": r"\b(?:contests?|events?|conferences?|tournaments?|webinars?|exhibitions?|competitions?|meetups?)\b",
    "jobs": r"\b(?:jobs?|careers?|vacancies|open positions|hiring|internships?|apply now|job board)\b",
    "software": r"\b(?:releases?|changelog|release notes|software downloads|releaselog)\b",
    "course": r"\b(?:courses?|lessons?|tutorials?|curriculum|lectures?|quizzes|exercises|syllabus|problem sets?)\b",
    "research": r"\b(?:research papers?|papers?|publications?|proceedings|preprints?|journals?|standards|drafts|doi)\b",
}
PATTERNS = {name: re.compile(pattern) for name, pattern in FORMAT_PATTERNS.items()}
PATHS = {
    "comic": {"comic", "comics", "manga", "manhwa", "webtoon"},
    "novel": {"novel", "novels", "fiction", "ebooks", "book", "books"},
    "blog": {
        "blog",
        "weblog",
        "news",
        "article",
        "articles",
        "post",
        "posts",
        "essays",
        "stories",
    },
    "video": {"video", "videos", "watch", "presentation", "recording", "talks"},
    "podcast": {"podcast", "podcasts", "audio"},
    "music": {"music", "track", "tracks", "album", "albums", "discography", "release"},
    "events": {"contest", "contests", "event", "events", "exhibitions", "meetups"},
    "jobs": {"job", "jobs", "careers", "vacancies", "positions", "apply"},
    "software": {"releases", "releaselog", "changelog", "downloads", "tags"},
    "course": {
        "course",
        "courses",
        "lesson",
        "lessons",
        "tutorial",
        "tutorials",
        "lectures",
        "weeks",
    },
    "research": {
        "papers",
        "paper",
        "publications",
        "abs",
        "proceedings",
        "preprints",
        "tr",
    },
}
KINDS = ("website", *FORMAT_PATTERNS, "youtube")
CLASS_OFFSETS = {kind: index * 6 for index, kind in enumerate(FORMAT_PATTERNS)}
CHAPTER_FEATURE = len(FORMAT_PATTERNS) * 6
EXTERNAL_LINK_FEATURE = CHAPTER_FEATURE + 5
FEATURE_COUNT = CHAPTER_FEATURE + 7 + len(KINDS)
CHAPTER = re.compile(r"\b(?:chapter|chapters|ch|chap|volume)\s*[.\d: -]")
EPISODE = re.compile(r"\b(?:episode|episodes|ep)\b")
VERSION = re.compile(r"(?<!\d)v?\d{1,3}\.\d{1,3}(?:\.\d{1,3})?(?!\d)")
DATE = re.compile(r"/\d{4}/(?:\d{1,2}|[a-z]{3,9})/")
AGGREGATE = re.compile(
    r"\b(?:aggregat\w*|link sharing|link discussion|community links|from .* blogs)\b"
)


def segments(value):
    return set(
        unquote(urlsplit(str(value or "")[:1800]).path).casefold().strip("/").split("/")
    )


def vector(scan, text, rows):
    """At most 32 entries and bounded source metadata; no network or encoder."""
    title, summary, path, combined = text
    summary = summary[:700]
    paths = [segments(row.get("url", "")) for row in rows]
    root_path = segments(scan.get("url", ""))
    row_titles = [str(row.get("title", ""))[:140].casefold() for row in rows]
    count = max(1, len(rows))
    result = []
    for kind, pattern in PATTERNS.items():
        vocabulary = PATHS[kind]
        result.extend(
            (
                float(bool(pattern.search(title))),
                float(bool(pattern.search(summary))),
                float(bool(pattern.search(path))),
                sum(bool(pattern.search(row)) for row in row_titles) / count,
                float(bool(vocabulary & root_path)),
                sum(bool(vocabulary & parts) for parts in paths) / count,
            )
        )
    host = urlsplit(str(scan.get("url", ""))[:1800]).hostname or ""
    external = (
        sum(
            bool(target := urlsplit(str(row.get("url", ""))[:1800]).hostname)
            and target != host
            for row in rows
        )
        / count
    )
    result.extend(
        (
            sum(bool(CHAPTER.search(row)) for row in combined) / count,
            sum(bool(EPISODE.search(row)) for row in combined) / count,
            sum(bool(VERSION.search(row)) for row in row_titles) / count,
            sum(bool(DATE.search(str(row.get("url", ""))[:1800])) for row in rows)
            / count,
            float(bool(AGGREGATE.search(summary))),
            external,
            min(1, len(rows) / 8),
        )
    )
    fallback = scan.get("detected_kind") or scan.get("kind", "website")
    result.extend(float(fallback == kind) for kind in KINDS)
    return result
