"""Bounded, offline media classification and private search descriptors.

Media types describe the saved item; they never choose a scraping adapter.
"""

import re
import unicodedata
from collections import Counter
from typing import Literal, get_args
from urllib.parse import unquote, urlsplit

MediaOverride = Literal[
    "",
    "website",
    "blog",
    "comic",
    "novel",
    "youtube",
    "video",
    "podcast",
    "music",
    "events",
    "jobs",
    "software",
    "course",
    "research",
]
MEDIA_TYPES = set(get_args(MediaOverride)) - {""}
VERSION = 2
MAX_TAGS = 48
MAX_SUMMARY = 2400
MAX_SAMPLE = 32
PATTERNS = {
    "comic": r"\b(?:manga|manhwa|manhua|webtoons?|webcomics?|comics?)\b",
    "novel": r"\b(?:novels?|webnovels?|fiction|ebooks?|books?|serial fiction)\b",
    "blog": r"\b(?:blogs?|news|articles?|essays?|posts?|newsletter)\b",
    "video": r"\b(?:videos?|anime|watch|tv series|television|films?|movies?)\b",
    "podcast": r"\b(?:podcasts?|audio show)\b",
    "music": r"\b(?:albums?|discography|songs?|music|tracks?)\b",
    "events": r"\b(?:contests?|events?|conferences?|tournaments?|webinars?)\b",
    "jobs": r"\b(?:jobs?|careers?|vacancies|open positions|hiring|internships?|apply now)\b",
    "software": r"\b(?:releases?|changelog|release notes|software downloads)\b",
    "course": r"\b(?:courses?|lessons?|tutorials?|curriculum|lectures?)\b",
    "research": r"\b(?:research papers?|publications?|proceedings|preprints?|journals?)\b",
}
SIGNALS = {kind: re.compile(pattern) for kind, pattern in PATTERNS.items()}
CHAPTER = re.compile(r"\b(?:chapter|ch|chapters)\b")
STOP = set(
    """a an the of and or to for with from in on at by is it its as this that
    these those are be was were your you our we their they has have all more new
    latest online read reading quality free best top full available website home
    page pages chapter chapters episode episodes part parts volume volumes
    archive archives english translation translated scan scans manga comic comics
    novel novels blog blogs articles post posts links link content privacy terms
    sign login signup register cookie cookies follow share subscribe menu search
    comment comments previous next updated ago ago today yesterday none http https
    www com org net html php index new ongoing completed view views""".split()
)
STOP.update(
    """about above after again against also am any because been before being
    below between both but can could did do does doing down during each few further
    had having he her here hers herself him himself his how if into itself just me
    most much must myself no nor not now off once only other out over own same she
    should so some such than then there theirs them themselves through too under
    until up very what when where which while who whom why will would yours yourself
    yourselves according upon though don exactly immediately first still becomes
    beginning begins gives go going get gets good things time enjoy start fast
    fastest updating site translated summary end every since may might use using""".split()
)
# Retrieval aliases are concepts, not invented plot descriptions or inferred genres.
CONCEPTS = {
    "science fiction": r"\b(?:sci[ -]?fi|science fiction)\b",
    "fantasy": r"\b(?:fantasy|magic|magical|wizards?|witches?)\b",
    "comedy": r"\b(?:comedy|humou?r|humou?rous|funny)\b",
    "romance": r"\b(?:romance|romantic)\b",
    "horror": r"\b(?:horror|haunted)\b",
    "mystery": r"\b(?:mystery|detectives?)\b",
    "adventure": r"\badventures?\b",
    "slice of life": r"\bslice[ -]of[ -]life\b",
    "college university": r"\b(?:college|university|campus|freshman)\b",
    "diving scuba": r"\b(?:diving|scuba)\b",
    "programming development": r"\b(?:programming|software|coding|developer)\b",
    "machine learning": r"\b(?:machine learning|neural networks?|deep learning)\b",
    "jobs careers employment": PATTERNS["jobs"],
    "english": r"\b(?:english|en)\b",
    "japanese": r"\b(?:japanese|ja)\b",
    "korean": r"\b(?:korean|ko)\b",
    "spanish": r"\b(?:spanish|es)\b",
    "french": r"\b(?:french|fr)\b",
}
TYPE_TAGS = {
    "comic": ["manga comics", "illustrated stories"],
    "novel": ["novels books fiction", "reading"],
    "blog": ["blogs articles posts"],
    "youtube": ["youtube video"],
    "video": ["videos film television"],
    "podcast": ["podcasts audio"],
    "music": ["music audio"],
    "events": ["events contests"],
    "jobs": ["jobs careers employment"],
    "software": ["software releases changelog"],
    "course": ["courses learning education"],
    "research": ["research papers publications"],
}


def clean(value, limit=MAX_SUMMARY):
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")[: limit * 2]).split()
    )[:limit]


def sample_entries(entries):
    # Sample the whole list so one promoted/newest entry cannot dominate its type.
    count = len(entries)
    return (
        [
            entries[i * (count - 1) // min(count - 1, MAX_SAMPLE - 1)]
            for i in range(min(count, MAX_SAMPLE))
        ]
        if count > 1
        else list(entries)
    )


def source_summary(soup):
    fragments = []
    for node in soup.find_all("meta", limit=64):
        if (
            node.get("name") == "description"
            or node.get("property") == "og:description"
        ):
            value = clean(node.get("content"), 600)
            if value and value not in fragments:
                fragments.append(value)
            if len(fragments) >= 6:
                break
    # Reuse the parsed DOM. Skip menus, huge chapter lists and linked promotional cards.
    root = soup.find("body") or soup
    heading = soup.title or soup.find("h1")
    title_words = (
        set(
            re.findall(
                r"[^\W\d_]{3,}",
                clean(heading.get_text() if heading else "", 300).casefold(),
            )
        )
        - STOP
    )
    for node in root.find_all("p", limit=48):
        if node.find_parent(["nav", "header", "footer", "form"]):
            continue
        text = clean(node.get_text(" ", strip=True), 1201)
        if not 60 <= len(text) <= 1200:
            continue
        if (
            node.find_parent("aside")
            and len(title_words & set(re.findall(r"[^\W\d_]{3,}", text.casefold()))) < 2
        ):
            continue
        linked = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        if linked > len(text) * 0.25:
            continue
        fragments.append(text)
        if sum(map(len, fragments)) >= MAX_SUMMARY:
            break
    return clean(" ".join(dict.fromkeys(fragments)))


def evidence_text(scan, entries=None):
    if entries is None:
        entries = sample_entries(scan.get("entries", []))
    title = clean(scan.get("title"), 300).casefold()
    summary = clean(scan.get("source_summary") or scan.get("summary")).casefold()
    path = (
        unquote(urlsplit(scan.get("url", "")).path)[:400].replace("-", " ").casefold()
    )
    rows = [
        clean(e.get("title"), 140).casefold()
        + " "
        + unquote(urlsplit(e.get("url", "")).path)[-160:].replace("-", " ").casefold()
        for e in entries
    ]
    return title, summary, path, rows


def features(scan, *, evidence=None):
    title, summary, path, rows = (
        evidence if evidence is not None else evidence_text(scan)
    )
    result = []
    for signal in SIGNALS.values():
        result.extend(
            [
                float(bool(signal.search(title))),
                float(bool(signal.search(summary))),
                float(bool(signal.search(path))),
                sum(bool(signal.search(row)) for row in rows) / max(1, len(rows)),
            ]
        )
    result.append(sum(bool(CHAPTER.search(row)) for row in rows) / max(1, len(rows)))
    return result


def baseline_classify(scan, *, evidence=None):
    """Conservative baseline; uncertain evidence keeps the acquisition type."""
    values = features(scan, evidence=evidence)
    scores = {}
    for i, kind in enumerate(SIGNALS):
        title, summary, path, entries = values[i * 4 : i * 4 + 4]
        scores[kind] = 4 * title + 2 * summary + 2 * path + 5 * entries
    chapters = values[-1]
    # RSS and Article describe transport/markup. Chapter sequences describe content.
    if chapters >= 0.4:
        for kind in ("comic", "novel"):
            if scores[kind] >= 2:
                scores[kind] += 5 * chapters
        scores["blog"] *= 0.4
    fallback = scan.get("detected_kind") or scan.get("kind", "website")
    if fallback not in MEDIA_TYPES:
        fallback = "website"
    if fallback == "youtube":
        return fallback
    ranked = sorted(scores, key=scores.get, reverse=True)
    winner = ranked[0]
    if scores[winner] >= 4 and scores[winner] - scores[ranked[1]] >= 1.5:
        return winner
    return fallback


def classify(scan, *, evidence=None, entries=None):
    from .media_classifier import classify as learned_classify

    if entries is None:
        entries = sample_entries(scan.get("entries", []))
    if evidence is None:
        evidence = evidence_text(scan, entries)
    baseline = baseline_classify(scan, evidence=evidence)
    return learned_classify(scan, evidence, entries, baseline)


def search_tags(scan, kind, *, evidence=None, entries=None):
    if entries is None:
        entries = sample_entries(scan.get("entries", []))
    title, summary, _, _ = (
        evidence if evidence is not None else evidence_text(scan, entries)
    )
    fragments = [title, summary, clean(scan.get("keywords"), 300).casefold()]
    fragments += [clean(e.get("title"), 140).casefold() for e in entries[:12]]
    fragments += [clean(e.get("language"), 40).casefold() for e in entries]
    text = " ".join(fragments)
    tags = list(TYPE_TAGS.get(kind, []))
    tags += [tag for tag, pattern in CONCEPTS.items() if re.search(pattern, text)]
    weights = Counter()
    for position, fragment in enumerate(fragments):
        fragment = re.sub(r"(?:https?://|www\.)\S+", " ", fragment)
        words = re.findall(r"[^\W\d_]{3,}", fragment)
        weight = 5 if position == 0 else 2 if position == 1 else 1
        # Count presence per fragment; SEO repetition cannot increase its weight.
        terms = {w for w in words if w not in STOP and len(w) <= 36}
        terms |= {
            a + " " + b
            for a, b in zip(words, words[1:], strict=False)
            if a not in STOP and b not in STOP and len(a + b) <= 47
        }
        weights.update({term: weight for term in terms})
    tags += sorted(weights, key=lambda term: (-weights[term], term))
    return list(dict.fromkeys(tags))[:MAX_TAGS]


def annotate(scan, override=""):
    if override not in get_args(MediaOverride):
        raise ValueError("Choose a supported media type.")
    entries = sample_entries(scan.get("entries", []))
    evidence = evidence_text(scan, entries)
    detected = classify(scan, evidence=evidence, entries=entries)
    kind = override or detected
    return scan | {
        "kind": kind,
        "detected_kind": detected,
        "source_summary": clean(scan.get("source_summary") or scan.get("summary")),
        "search_tags": search_tags(scan, kind, evidence=evidence, entries=entries),
    }
