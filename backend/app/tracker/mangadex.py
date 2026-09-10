"""Public chapter metadata only. Never request images or chapter bodies."""

import json
import math
import re
from urllib.parse import urlencode, urlsplit

from .dates import evidence
from .keywords import language_codes, language_text, terms
from .limits import MAX_LINKS
from .models import Entry, Scan
from .urls import DiscoveryError

UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


def title_id(url):
    parts = urlsplit(url)
    match = re.match(rf"/title/({UUID})(?:/|$)", parts.path)
    return (
        match[1].lower()
        if parts.hostname in {"mangadex.org", "www.mangadex.org"} and match
        else None
    )


def feed_url(identity, offset=0, languages=()):
    params = [
        ("limit", "500"),
        ("offset", str(offset)),
        ("order[chapter]", "asc"),
        ("includes[]", "scanlation_group"),
        ("includeUnavailable", "0"),
        ("includeFuturePublishAt", "0"),
        ("includeFutureUpdates", "0"),
    ]
    params += [("translatedLanguage[]", code) for code in languages]
    params += [
        ("contentRating[]", rating)
        for rating in ("safe", "suggestive", "erotica", "pornographic")
    ]
    return f"https://api.mangadex.org/manga/{identity}/feed?" + urlencode(params)


def collection(text):
    try:
        data = json.loads(text)
        if data.get("result") != "ok" or not isinstance(data.get("data"), list):
            raise ValueError()
        if type(data.get("total")) is not int or not 0 <= data["total"] <= 1_000_000:
            raise ValueError()
        return data
    except (ValueError, TypeError, AttributeError) as exc:
        raise DiscoveryError(
            "MangaDex returned unreadable chapter metadata. Saved links were kept."
        ) from exc


async def scan_mangadex(fetcher, source, max_pages, keywords=""):
    identity = title_id(source)
    if not identity:
        raise DiscoveryError("Use a MangaDex title URL.")
    # Exact recognized language terms can be sent to the API, reducing pages fetched.
    languages = next(
        (codes for term in terms(keywords) if (codes := language_codes(term))), []
    )
    slug = urlsplit(source).path.rstrip("/").split("/")[-1]
    result = Scan(
        source,
        slug.replace("-", " ").title()
        if slug.lower() != identity
        else "MangaDex title",
        "comic",
        methods=["MangaDex API"],
    )
    entries, offset, total = {}, 0, None
    while result.pages_scanned < max_pages and len(entries) < MAX_LINKS:
        try:
            _, text = await fetcher.get(feed_url(identity, offset, languages))
            payload = collection(text)
        except DiscoveryError as exc:
            if not result.pages_scanned:
                raise
            result.coverage = "partial"
            result.warnings.append(str(exc))
            break
        result.pages_scanned += 1
        if payload.get("offset") != offset:
            result.coverage = "partial"
            result.warnings.append(
                "MangaDex repeated a page. Scanning stopped to avoid extra requests."
            )
            break
        total = payload["total"]
        rows = payload["data"][:500]
        added = 0
        for row in rows:
            if (
                not isinstance(row, dict)
                or row.get("type") != "chapter"
                or not re.fullmatch(UUID, str(row.get("id", "")))
            ):
                continue
            attrs = row.get("attributes", {})
            relationships = row.get("relationships", [])
            if not isinstance(attrs, dict) or not isinstance(relationships, list):
                continue
            if not any(
                r.get("type") == "manga" and str(r.get("id", "")).lower() == identity
                for r in relationships
                if isinstance(r, dict)
            ):
                continue
            lang = attrs.get("translatedLanguage", "")
            if (
                not isinstance(lang, str)
                or (languages and lang not in languages)
                or attrs.get("isUnavailable")
            ):
                continue
            raw_number = attrs.get("chapter")
            try:
                number = float(raw_number)
                if not math.isfinite(number) or not 0 <= number <= 1_000_000:
                    number = None
            except (TypeError, ValueError):
                number = None
            label = f"Chapter {raw_number}" if raw_number is not None else "Oneshot"
            if attrs.get("title"):
                label += ": " + str(attrs["title"])
            groups = [
                str(r.get("attributes", {}).get("name", ""))[:160]
                for r in relationships
                if isinstance(r, dict)
                and r.get("type") == "scanlation_group"
                and isinstance(r.get("attributes"), dict)
            ]
            context = " · ".join(filter(None, [language_text(lang), *groups]))[:1000]
            url = "https://mangadex.org/chapter/" + row["id"].lower()
            if url not in entries:
                entries[url] = Entry(
                    url,
                    label[:1000],
                    number=number,
                    position=len(entries),
                    method="MangaDex API",
                    summary=context,
                    context=context,
                    language=lang,
                    **evidence(attrs.get("publishAt"), "MangaDex publishAt"),
                )
                added += 1
            if len(entries) >= MAX_LINKS:
                break
        offset += len(rows)
        if offset >= total:
            break
        if not rows or not added:
            result.coverage = "partial"
            result.warnings.append(
                "MangaDex returned no new usable records. Scanning stopped to avoid extra requests."
            )
            break
    result.entries = list(entries.values())
    result.expected_count = total
    if total is not None and len(entries) == total and result.coverage != "partial":
        result.coverage = "complete"
    else:
        result.coverage = "partial"
        result.warnings.append(
            f"MangaDex reports {total or 0:,} entries; recovered {len(entries):,}. The scan is bounded to {max_pages} pages and {MAX_LINKS:,} links."
        )
    if not entries:
        result.warnings.append(
            "MangaDex currently lists no available chapters"
            + (
                " for " + ", ".join(language_text(code) for code in languages)
                if languages
                else ""
            )
            + ". Other languages were not substituted."
        )
    return result
