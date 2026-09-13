import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from dateutil import parser


def utcnow():
    return datetime.now(UTC).isoformat()


def date_rank(entry):
    """Prefer publication/start metadata over modification or URL guesses."""
    if not isinstance(entry, dict):
        entry = dict(entry) if hasattr(entry, "keys") else asdict(entry)
    if not entry.get("published_at"):
        return 0
    return (
        {
            "published": 400,
            "scheduled": 400,
            "listed": 300,
            "updated": 200,
            "inferred": 100,
        }.get(entry.get("date_kind", "published"), 100)
        + (20 if entry.get("method") in {"public API", "feed"} else 0)
        + (1 if entry.get("date_precision") == "time" else 0)
    )


def date_value(value):
    if not value:
        return None
    try:
        if (
            isinstance(value, (int, float))
            or str(value).isdigit()
            and len(str(value)) in (10, 13)
        ):
            value = float(value)
            return datetime.fromtimestamp(
                value / 1000 if value > 1e11 else value, UTC
            ).isoformat()
        # Relative dates and bare numbers must not acquire an invented publication date.
        if not re.search(r"\b(?:19|20)\d{2}\b", str(value)) or not re.search(
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|[A-Za-z]{3,}.*\d{1,2}.*\d{4}|\d{1,2}.*[A-Za-z]{3,}.*\d{4}",
            str(value),
        ):
            return None
        d = parser.parse(str(value), fuzzy=False)
        return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def sequence_value(title, url=""):
    if re.match(r"^\s*v?\d+\.\d+\.\d+(?:\b|[-+])", title):
        return None  # Semantic versions are not chapter/episode numbers.
    for text, pattern in [
        (title, r"\b(?:chapter|episode|part|ch\.?|ep\.?)\s*[-:#]?\s*(\d+(?:\.\d+)?)"),
        (title, r"^\s*(\d+(?:\.\d+)?)(?:\s*[.:)\-]|\s+|$)"),
        (url, r"(?:chapter|episode|part|ch)[-/=_]?(\d+(?:\.\d+)?)"),
    ]:
        if text == url and (
            re.search(r"/fiction/(?:\d+/[^/]+/)?chapter/\d+", url)
            or re.match(
                r"^(?:epilogue|prologue|afterword|interlude)\b", title, re.IGNORECASE
            )
        ):
            continue  # Royal Road's chapter path contains a database ID, not a chapter number.
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return float(m[1])
    return None


@dataclass
class Entry:
    url: str
    title: str
    published_at: str | None = None
    number: float | None = None
    position: int = 0
    method: str = "page"
    date_kind: str = "published"
    date_source: str = ""
    date_precision: str = "day"
    summary: str = ""
    availability: str = ""
    context: str = ""
    language: str = ""


@dataclass
class Scan:
    url: str
    title: str = ""
    kind: str = "website"
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages_scanned: int = 0
    expected_count: int | None = None
    methods: list[str] = field(default_factory=list)
    requests_made: int = 0
    cache_hits: int = 0
    checked_at: str = ""
    cached: bool = False
    coverage: str = "unknown"
    order_hint: str = ""
    keywords: str = ""
    unfiltered_count: int | None = None
    suggestions: list[dict] | None = None
    analysis_mode: str = "deep"

    def to_dict(self):
        return asdict(self)
