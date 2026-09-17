"""Source-grounded descriptions and stable inputs for the private search index."""

import hashlib
import json
import math
import re
import unicodedata

from .media_metadata import STOP, TYPE_TAGS, clean

MAX_DESCRIPTION = 700
PROFILE_VERSION = "profile-v1"
FIELDS = (
    "title",
    "kind",
    "source_summary",
    "search_tags",
    "description_override",
    "source_name",
)
PROMOTION = re.compile(
    r"\b(?:cookies?|privacy policy|terms of (?:use|service)|subscribe|sign up|log in|"
    r"highest quality|high quality|read .{0,100} (?:online|free)|download .{0,40}app|"
    r"all chapters|none chapters|you can read|chapters have been translated|\d[.,]\d+ rating|ignore (?:all |previous )?instructions)\b",
    re.I,
)


def fingerprint(row):
    return hashlib.sha256(
        json.dumps(
            {key: row.get(key) for key in FIELDS}, sort_keys=True, ensure_ascii=True
        ).encode()
    ).hexdigest()


def words(text):
    return re.findall(
        r"[^\W_]+", unicodedata.normalize("NFKC", str(text or "")).casefold()
    )


def description_sentences(source):
    source = clean(source)
    source = re.sub(r"(?:The )?Summary is\s*", "", source, flags=re.I)
    found = []
    seen = []
    for sentence in re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", source):
        sentence = sentence.strip(" \t•-")
        tokens = set(words(sentence)) - STOP
        if (
            not 40 <= len(sentence) <= 480
            or len(tokens) < 4
            or PROMOTION.search(sentence)
        ):
            continue
        if "..." in sentence or sentence.endswith("…"):
            continue
        if any(len(tokens & old) / max(1, len(tokens | old)) > 0.72 for old in seen):
            continue
        seen.append(tokens)
        found.append(sentence)
        if len(found) == 20:
            break
    return found


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def describe(title, source, model=None):
    sentences = description_sentences(source)
    if not sentences:
        return "", "unavailable"
    if model is None:
        chosen = [0]
        if (
            len(sentences) > 1
            and len(sentences[0]) + len(sentences[1]) + 1 <= MAX_DESCRIPTION
        ):
            chosen.append(1)
        return " ".join(sentences[i] for i in chosen), "source-excerpt"
    vectors = model.encode([clean(title, 300), *sentences])
    topic, vectors = vectors[0], vectors[1:]
    center = [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]
    norm = math.sqrt(dot(center, center)) or 1
    center = [value / norm for value in center]
    scores = [
        0.55 * dot(topic, vector) + 0.35 * dot(center, vector) + 0.1 / (i + 1)
        for i, vector in enumerate(vectors)
    ]
    chosen = []
    for _ in range(3):
        candidates = [
            i
            for i in range(len(sentences))
            if i not in chosen
            and sum(len(sentences[j]) + 1 for j in chosen) + len(sentences[i])
            <= MAX_DESCRIPTION
            and all(dot(vectors[i], vectors[j]) < 0.91 for j in chosen)
        ]
        if not candidates:
            break
        best = max(
            candidates,
            key=lambda i: (
                scores[i]
                - 0.25 * max((dot(vectors[i], vectors[j]) for j in chosen), default=0)
            ),
        )
        chosen.append(best)
        if sum(len(sentences[i]) for i in chosen) >= 400:
            break
    return " ".join(sentences[i] for i in sorted(chosen)), "semantic-extractive"


def document(row, description=None):
    if description is None:
        description = row.get("description_override")
        if description is None:
            description = row.get("description_auto", "")
    tags = row.get("search_tags", [])
    if isinstance(tags, str):
        tags = json.loads(tags)
    return "\n".join(
        [
            clean(row.get("title"), 300),
            " ".join(TYPE_TAGS.get(row.get("kind"), [])),
            clean(description or row.get("source_summary"), 1200),
            " ".join(tags[:12])[:300],
        ]
    )[:2000]
