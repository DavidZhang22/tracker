"""Observed source recommendations with bounded, local TF-IDF profile ranking.

No crawler, generated URLs, cross-account data, or production ML dependency.
"""

import hashlib
import ipaddress
import math
import re
import unicodedata
from collections import Counter
from itertools import islice
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from .models import utcnow
from .urls import canonical_url

MAX_CANDIDATES = 1000
MAX_PER_SOURCE = 20
SOCIAL_HOSTS = {
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "discord.com",
    "discord.gg",
    "patreon.com",
    "paypal.com",
    "ko-fi.com",
    "t.me",
}
RELATED = re.compile(
    r"related|recommend|similar|you (?:may|might)|also (?:read|like)|more like|blogroll|friends|other (?:blogs|channels|series)",
    re.I,
)
COLLECTION = re.compile(
    r"^/(?:series|comics|manga|novel|novels|podcasts|shows|courses|collections)/[^/]+/?$|^/fiction/\d+/[^/]+/?$|^/title/[\da-f-]{36}(?:/[^/]+)?/?$|^/(?:channel/UC[\w-]{22}|@[^/]+)/?$",
    re.I,
)
STOP = set(
    "a an and are as at be by for from has in is it of on or the this to with your chapter chapters read more latest home next previous login sign up privacy terms donate all view cover image".split()
)


def source_key(url):
    url = canonical_url(url)
    p = urlsplit(url)
    return url.split("?")[0] if COLLECTION.fullmatch(p.path) else url


def safe_source(url, base=""):
    try:
        url = canonical_url(url, base)
        host = urlsplit(url).hostname
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return url if len(url) <= 2048 else None
    except (ValueError, TypeError, UnicodeError):
        return None


def observed_sources(soup, source, kind="website"):
    """Conservative collection roots and explicitly labeled blog/channel lists."""
    found = {}
    root = urlsplit(source)
    root_key = source_key(source)
    markers = {}
    for anchor in soup.find_all("a", href=True, limit=8000):
        url = safe_source(anchor.get("href"), source)
        if not url or source_key(url) == root_key:
            continue
        p = urlsplit(url)
        collection = bool(COLLECTION.fullmatch(p.path))
        blog_target = p.hostname != root.hostname and p.path.rstrip("/") in {
            "",
            "/blog",
            "/feed",
            "/rss",
            "/archive",
            "/posts",
        }
        repository_target = p.hostname == "github.com" and bool(
            re.fullmatch(r"/[^/]+/[^/]+", p.path)
        )
        # A chapter/article URL cannot be a suggested collection. Reject it
        # before examining ancestors, especially on long chapter indexes.
        if not (collection or blog_target or repository_target):
            continue
        if (
            p.query
            or p.hostname.removeprefix("www.") in SOCIAL_HOSTS
            or anchor.find_parent(["nav", "footer", "form", "header"])
        ):
            continue
        related = False
        for parent in islice(anchor.parents, 5):
            if parent.name in {"body", "html", "[document]"}:
                break
            if id(parent) not in markers:
                heading = parent.find(["h2", "h3", "h4"], recursive=False)
                marker = " ".join(
                    [
                        str(parent.get("id", "")),
                        " ".join(parent.get("class", [])),
                        str(parent.get("aria-label", "")),
                        heading.get_text(" ", strip=True)[:120] if heading else "",
                    ]
                )
                markers[id(parent)] = bool(RELATED.search(marker))
            related = related or markers[id(parent)]
        if p.path.rstrip("/").rsplit("/", 1)[-1].lower() in {
            "all",
            "ranking",
            "rankings",
            "search",
            "latest",
            "popular",
            "browse",
            "completed",
        }:
            continue
        blog = related and blog_target
        repository = related and repository_target
        if not (
            collection
            and (p.hostname == root.hostname or related)
            or blog
            or repository
        ):
            continue
        image = anchor.find("img", alt=True)
        heading = anchor.find(["h2", "h3", "h4", "strong"])
        image_title = (
            re.sub(
                r"\s+(?:cover|thumbnail|image)$", "", image.get("alt", ""), flags=re.I
            )
            if image
            else ""
        )
        if image_title.lower() in STOP:
            image_title = ""
        title = " ".join(
            (
                (heading.get_text(" ", strip=True) if heading else "")
                or anchor.get("title")
                or anchor.get("aria-label")
                or image_title
                or anchor.get_text(" ", strip=True)
                or ""
            ).split()
        )[:200]
        if (
            len(title) < 3
            or title.lower() in STOP
            or not any(w not in STOP for w in title.lower().split())
        ):
            continue
        record = anchor.find_parent(["article", "li"]) or anchor.parent
        description = ""
        if record and len(record.find_all("a", limit=5)) >= 5:
            record = anchor
        if record and len(record.find_all("a", limit=5)) < 5:
            description = " ".join(record.get_text(" ", strip=True).split())[:400]
            description = re.sub(
                r"\b(?:chapters|views):\s*[\d.,]+[KMB]?\b", "", description, flags=re.I
            )
            description = " ".join(description.replace(title, "", 1).split()).strip(
                " ·|:-"
            )
        candidate_kind = (
            "blog"
            if blog
            else "youtube"
            if p.hostname == "www.youtube.com"
            else "comic"
            if p.path.startswith(("/title/", "/comics/", "/manga/"))
            else "novel"
            if p.path.startswith(("/fiction/", "/novel"))
            else kind
        )
        found.setdefault(
            source_key(url),
            {
                "url": url,
                "title": title,
                "summary": description,
                "kind": candidate_kind,
            },
        )
        if len(found) == MAX_PER_SOURCE:
            break
    return list(found.values())


def save_observations(db, iid, candidates):
    """Called in the same transaction as the source scan; feedback survives refresh."""
    if candidates is None:
        return
    db.execute("DELETE FROM suggestion_sources WHERE item_id=?", (iid,))
    db.execute(
        "DELETE FROM suggestions WHERE dismissed=0 AND id NOT IN (SELECT suggestion_id FROM suggestion_sources)"
    )
    count = db.execute("SELECT count(*) FROM suggestions").fetchone()[0]
    for candidate in candidates[:MAX_PER_SOURCE]:
        url = safe_source(candidate.get("url"))
        if not url:
            continue
        url = source_key(url)
        sid = hashlib.sha256(url.encode()).hexdigest()[:32]
        exists = db.execute("SELECT id FROM suggestions WHERE id=?", (sid,)).fetchone()
        if not exists and count >= MAX_CANDIDATES:
            continue
        db.execute(
            """INSERT INTO suggestions(id,url,title,summary,kind,found_at) VALUES (?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title,summary=excluded.summary,found_at=excluded.found_at""",
            (
                sid,
                url,
                str(candidate.get("title", ""))[:200],
                str(candidate.get("summary", ""))[:400],
                str(candidate.get("kind", "website"))[:30],
                utcnow(),
            ),
        )
        count += not bool(exists)
        db.execute("INSERT OR IGNORE INTO suggestion_sources VALUES (?,?)", (sid, iid))


def tokens(text):
    words = [
        w
        for w in re.findall(
            r"[^\W\d_]{2,}",
            unicodedata.normalize("NFKC", unquote(text[:2000])).casefold(),
        )
        if w not in STOP
    ][:80]
    return set(words) | {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)}


def normalize(vector):
    length = math.sqrt(sum(v * v for v in vector.values())) or 1
    return {k: v / length for k, v in vector.items()}


def dot(a, b):
    return sum(v * b.get(k, 0) for k, v in a.items())


def ranked_suggestions(store, limit=40):
    items = store.items() + store.items(trash=True)
    active = {i["id"]: i for i in items if not i["ignored"] and not i["deleted"]}
    known = {source_key(i["url"]) for i in items}
    with store.connection() as db:
        candidates = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM suggestions ORDER BY found_at DESC,id LIMIT ?",
                (MAX_CANDIDATES,),
            )
        ]
        sources = {}
        for row in db.execute("SELECT * FROM suggestion_sources"):
            if row["item_id"] in active:
                sources.setdefault(row["suggestion_id"], []).append(row["item_id"])
    documents = [
        tokens(i["title"] + " " + i.get("keywords", "")) for i in active.values()
    ]
    documents += [tokens(c["title"] + " " + c["summary"]) for c in candidates]
    frequency = Counter(t for doc in documents for t in doc)
    vocabulary = {t for t, _ in frequency.most_common(4096)}
    vectors = [
        normalize(
            {
                t: 1 + math.log((1 + len(documents)) / (1 + frequency[t]))
                for t in doc
                if t in vocabulary
            }
        )
        for doc in documents
    ]
    profile, negative = Counter(), Counter()
    for item, vector in zip(active.values(), vectors, strict=False):
        weight = (
            1
            + 2 * item["favorite"]
            + min(1, item["read_count"] / max(1, item["total_count"]))
        )
        profile.update({t: weight * v for t, v in vector.items()})
    candidate_vectors = vectors[len(active) :]
    for candidate, vector in zip(candidates, candidate_vectors, strict=True):
        if candidate["dismissed"]:
            negative.update(vector)
    profile, negative = normalize(profile), normalize(negative)
    ranked = []
    for candidate, vector in zip(candidates, candidate_vectors, strict=True):
        origins = sources.get(candidate["id"], [])
        if candidate["dismissed"] or candidate["url"] in known or not origins:
            continue
        origin = max(
            (active[i] for i in origins),
            key=lambda i: (i["favorite"], i["read_count"], i["id"]),
        )
        affinity = (
            0.25
            + 0.35 * origin["favorite"]
            + 0.15 * min(1, origin["read_count"] / max(1, origin["total_count"]))
        )
        score = affinity + 0.6 * dot(vector, profile) - 0.3 * dot(vector, negative)
        ranked.append(
            (
                score,
                vector,
                {
                    k: candidate[k]
                    for k in ("id", "url", "title", "summary", "kind", "found_at")
                }
                | {
                    "source_id": origin["id"],
                    "source_title": origin["title"],
                    "reason": "From a favorite source"
                    if origin["favorite"]
                    else "Found on a source you track",
                },
            )
        )
    selected, hosts, similarity = [], Counter(), Counter()
    while ranked and len(selected) < limit:
        best = max(
            range(len(ranked)),
            key=lambda j: (
                ranked[j][0]
                - 0.12 * hosts[urlsplit(ranked[j][2]["url"]).hostname]
                - 0.2 * similarity[ranked[j][2]["id"]],
                ranked[j][2]["id"],
            ),
        )
        record = ranked.pop(best)
        selected.append(record)
        hosts[urlsplit(record[2]["url"]).hostname] += 1
        for _, vector, candidate in ranked:
            similarity[candidate["id"]] = max(
                similarity[candidate["id"]], dot(vector, record[1])
            )
    return {
        "suggestions": [row for _, _, row in selected],
        "total": len(selected) + len(ranked),
        "model": "tfidf-profile-v1",
    }


def collect_cached(store, cache):
    """Bounded cache backfill for existing libraries; deliberately never calls get on a fetcher."""
    checked, reused, byte_count = 0, 0, 0
    if cache is None:
        return {"pages_used": 0}
    for item in store.items():
        if item["ignored"] or item["deleted"]:
            continue
        cached = cache.get(canonical_url(item["url"], preserve_slash=True))
        if not cached or not isinstance(cached.get("body"), str) or cached.get("error"):
            continue
        stamp = float(cached.get("checked", 0))
        if item.get("suggestions_checked", 0) >= stamp:
            reused += 1
            continue
        body = cached["body"]
        if len(body) > 2_000_000 or not re.search(
            r"<(?:html|body|a)\b", body[:10000], re.I
        ):
            continue
        if checked >= 8 or byte_count + len(body.encode()) > 8_000_000:
            break
        candidates = observed_sources(
            BeautifulSoup(body, "html.parser"),
            cached.get("final", item["url"]),
            item["kind"],
        )
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT ignored,deleted FROM items WHERE id=?", (item["id"],)
            ).fetchone()
            if current and not current["ignored"] and not current["deleted"]:
                save_observations(db, item["id"], candidates)
                db.execute(
                    "UPDATE items SET suggestions_checked=? WHERE id=?",
                    (stamp, item["id"]),
                )
        checked += 1
        byte_count += len(body.encode())
    return {"pages_used": checked, "pages_reused": reused}
