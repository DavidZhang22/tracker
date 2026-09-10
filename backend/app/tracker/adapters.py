"""Small adapters for public collection APIs; all requests share the fetch budget."""

import json
import re
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup

from .dates import evidence
from .models import Entry, Scan, sequence_value
from .parser import json_objects, walk
from .urls import DiscoveryError, canonical_url


def codeforces_endpoint(url):
    p = urlsplit(url)
    if (
        p.hostname in {"codeforces.com", "www.codeforces.com"}
        and p.path.rstrip("/") == "/contests"
    ):
        return "https://codeforces.com/api/contest.list?gym=false"
    return None


def codeforces_scan(text, source):
    try:
        data = json.loads(text)
        if data.get("status") != "OK" or not isinstance(data.get("result"), list):
            raise ValueError()
        result = Scan(
            source,
            "Codeforces contests",
            "events",
            methods=["public API"],
            coverage="complete",
        )
        for obj in data["result"]:
            if not isinstance(obj.get("id"), int) or not isinstance(
                obj.get("name"), str
            ):
                continue
            minutes = obj.get("durationSeconds", 0) // 60
            phase = {
                "BEFORE": "Upcoming",
                "CODING": "Live",
                "FINISHED": "Finished",
            }.get(obj.get("phase"), "In progress")
            result.entries.append(
                Entry(
                    f"https://codeforces.com/contest/{obj['id']}",
                    obj["name"],
                    method="public API",
                    summary=f"{phase} · {minutes // 60}h {minutes % 60:02d}m",
                    **evidence(
                        obj.get("startTimeSeconds"),
                        "Codeforces startTimeSeconds (UTC)",
                        "scheduled",
                    ),
                )
            )
        result.expected_count = len(data["result"])
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise DiscoveryError(
            "Codeforces returned an unreadable contest list. Retry later."
        ) from exc


def wetried_series(text, source):
    p = urlsplit(source)
    if p.hostname not in {"wetriedtls.com", "www.wetriedtls.com"} or not re.fullmatch(
        r"/series/[\w-]+/?", p.path
    ):
        return None
    slug = p.path.strip("/").split("/")[-1]
    for script in BeautifulSoup(text, "html.parser").select("script"):
        for root in json_objects(script.string or script.get_text()):
            for obj in walk(root):
                if obj.get("series_slug") == slug and isinstance(
                    obj.get("series_id"), int
                ):
                    return obj["series_id"]
    raise DiscoveryError(
        "The chapter list's series identifier was not found. The site may have changed."
    )


def chapter_entries(data, source, series_id):
    entries = []
    for obj in data.get("data", []):
        # Only metadata explicitly belonging to this series is accepted.
        slug = obj.get("chapter_slug", "")
        if obj.get("series_id") != series_id or not re.fullmatch(r"[\w.-]+", slug):
            continue
        title = obj.get("chapter_name") or slug
        if obj.get("chapter_title"):
            title += ": " + obj["chapter_title"]
        entries.append(
            Entry(
                canonical_url(source.rstrip("/") + "/" + slug),
                title,
                number=sequence_value(title, slug),
                method="public API",
                availability="paid" if obj.get("price", 0) else "free",
                **evidence(
                    obj.get("created_at"), "We Tried TLS chapter created_at", "listed"
                ),
            )
        )
    return entries


async def wetried_scan(fetcher, source, series_id, result, max_pages):
    result.entries = []
    result.methods = ["page", "public API"]
    result.expected_count = 0
    result.coverage = "complete"
    remaining = max_pages
    for paid in (False, True):
        page, last = 1, 1
        while page <= last:
            if remaining <= 0:
                result.coverage = "partial"
                result.warnings.append(
                    "Chapter lookup reached the page limit. More chapters may remain."
                )
                return result
            query = {"query": "", "order": "desc"}
            if not paid or page > 1:
                query.update(page=str(page), perPage="30")
            url = (
                f"https://api.wetriedtls.com/chapters/{series_id}"
                + ("/paid" if paid else "")
                + "?"
                + urlencode(query)
            )
            remaining -= 1
            try:
                _, text = await fetcher.get(url)
                data = json.loads(text)
                meta = data["meta"]
                if (
                    not isinstance(data.get("data"), list)
                    or int(meta["current_page"]) != page
                ):
                    raise ValueError()
                last = max(1, int(meta["last_page"]))
                if page == 1:
                    result.expected_count += int(meta["total"])
                incoming = chapter_entries(data, source, series_id)
                if not incoming and page < last:
                    raise ValueError()
                result.entries.extend(incoming)
                result.pages_scanned += 1
                page += 1
            except (DiscoveryError, ValueError, KeyError, TypeError) as exc:
                result.coverage = "partial"
                result.warnings.append(
                    f"Could not finish the {'paid' if paid else 'free'} chapter list: {str(exc) or 'unexpected API response'}."
                )
                break
    return result
