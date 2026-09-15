"""Learn data-only GET/JSON listings with explicit next-URL pagination.

No executable expressions, guessed offsets, authentication, or arbitrary replayed
headers. An observed listing must match rendered links and finish its next chain.
"""

import hashlib
import json
from urllib.parse import urlsplit

from .browser_client import allowed_request
from .dates import evidence
from .limits import MAX_LINKS
from .models import Entry, Scan
from .urls import DiscoveryError, canonical_url, content_key
from .workers import run_blocking


def cache_key(source):
    return (
        "listing-recipe-v1:"
        + hashlib.sha256(canonical_url(source).encode()).hexdigest()
    )


def at(data, path):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            raise DiscoveryError("The listing API changed its response layout.")
        data = data[key]
    return data


def objects(data, path=()):
    if len(path) > 6 or not isinstance(data, dict):
        return
    yield path, data
    for key, value in list(data.items())[:100]:
        if isinstance(key, str) and len(key) <= 100 and isinstance(value, dict):
            yield from objects(value, (*path, key))


def safe_endpoint(url, source, base=None):
    url = canonical_url(url, base or source, preserve_slash=True)
    if urlsplit(url).netloc != urlsplit(source).netloc:
        raise DiscoveryError("Learned listing pagination must stay on the source host.")
    if not allowed_request({"method": "GET", "resource": "fetch", "url": url}, source):
        raise DiscoveryError("The listing request is not a reusable public GET.")
    return url


def entries(data, recipe, source):
    rows = at(data, recipe["rows"])
    if not isinstance(rows, list) or len(rows) > MAX_LINKS:
        raise DiscoveryError("The listing API returned an invalid record collection.")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise DiscoveryError("The listing API changed its records.")
        url, title = row.get(recipe["url_field"]), row.get(recipe["title_field"])
        if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
            raise DiscoveryError("The listing API no longer supplies titles and URLs.")
        url = canonical_url(url, source)
        if urlsplit(url).netloc != urlsplit(source).netloc or content_key(
            url
        ) == content_key(source):
            raise DiscoveryError("The listing API returned links outside this source.")
        date = evidence(row.get(recipe.get("date_field")), "Learned public listing API")
        if recipe.get("date_field") and not date:
            raise DiscoveryError("The listing API changed its publication dates.")
        context = []
        for key in ("language", "category", "tags", "type"):
            value = row.get(key)
            if isinstance(value, (str, int)):
                context.append(str(value)[:300])
            elif isinstance(value, list):
                context.extend(v[:100] for v in value[:20] if isinstance(v, str))
        result.append(
            Entry(
                url,
                title[:500],
                method="public API",
                context=" ".join(context)[:1000],
                language=str(row.get("language") or "")[:30],
                **date,
            )
        )
    return result


def infer(observation, source, rendered):
    try:
        endpoint = safe_endpoint(observation["url"], source)
        data = observation["data"]
        next_paths = []
        for path, obj in objects(data):
            for key in ("next", "next_url", "next_page_url"):
                if key in obj and (obj[key] is None or isinstance(obj[key], str)):
                    next_paths.append([*path, key])
        if len(next_paths) != 1:
            return None
        if any(not e.url for e in rendered):
            return None
        wanted = {content_key(e.url): e for e in rendered}
        if len(wanted) < 2:
            return None
        for path, obj in objects(data):
            for name, rows in list(obj.items())[:100]:
                if (
                    not isinstance(rows, list)
                    or len(rows) < 2
                    or not isinstance(rows[0], dict)
                ):
                    continue
                first = rows[0]
                url_field = next(
                    (
                        k
                        for k in ("url", "href", "link", "permalink")
                        if isinstance(first.get(k), str)
                    ),
                    None,
                )
                title_field = next(
                    (
                        k
                        for k in ("title", "name", "headline")
                        if isinstance(first.get(k), str)
                    ),
                    None,
                )
                if not url_field or not title_field:
                    continue
                date_field = next(
                    (
                        k
                        for k in (
                            "published_at",
                            "publishedAt",
                            "published",
                            "date",
                            "created_at",
                        )
                        if evidence(first.get(k), "API")
                    ),
                    None,
                )
                recipe = {
                    "endpoint": endpoint,
                    "rows": [*path, name],
                    "next": next_paths[0],
                    "url_field": url_field,
                    "title_field": title_field,
                    "date_field": date_field,
                }
                incoming = entries(data, recipe, source)
                matching = {content_key(e.url) for e in incoming} & wanted.keys()
                # Compare to the initial rendered list: later DOM windows may have
                # been virtualized away, while the API may include extra records.
                if len(matching) >= 2 and len(matching) >= len(wanted) * 0.8:
                    if any(wanted[k].published_at for k in matching) and not date_field:
                        continue
                    return recipe
    except (ValueError, KeyError, TypeError, UnicodeError):
        pass
    return None


async def replay(fetcher, source, recipe, max_pages=40, first=None):
    endpoint = safe_endpoint(recipe["endpoint"], source)
    origin_path = urlsplit(endpoint).path
    seen, records = set(), {}
    result = Scan(
        source,
        recipe.get("title") or urlsplit(source).hostname,
        recipe.get("kind", "website"),
        methods=["Learned listing API"],
        coverage="complete",
        analysis_mode="light",
    )
    for _ in range(max_pages):
        if endpoint in seen:
            raise DiscoveryError("The listing API repeated a pagination URL.")
        seen.add(endpoint)
        if first is not None:
            data, first = first, None
        else:
            _, text = await fetcher.get(endpoint)
            if len(text) > 1_000_000:
                raise DiscoveryError(
                    "The listing API exceeded its metadata size limit."
                )
            try:
                data = json.loads(text)
            except (ValueError, RecursionError) as exc:
                raise DiscoveryError("The listing API returned invalid JSON.") from exc
        result.pages_scanned += 1
        incoming = entries(data, recipe, source)
        before = len(records)
        records.update((content_key(e.url), e) for e in incoming)
        if len(records) > MAX_LINKS:
            raise DiscoveryError("The listing API exceeded the link limit.")
        following = at(data, recipe["next"])
        if following is None or following == "":
            result.entries = list(records.values())
            result.expected_count = len(records)
            return result
        if not isinstance(following, str) or not incoming or len(records) == before:
            raise DiscoveryError("The listing API did not advance its records.")
        following = safe_endpoint(following, source, endpoint)
        if urlsplit(following).path != origin_path:
            raise DiscoveryError("The listing API changed its pagination endpoint.")
        endpoint = following
    raise DiscoveryError("The listing API reached the scan's page limit.")


async def cached_listing(fetcher, source, max_pages):
    cache = getattr(fetcher, "cache", None)
    saved = await run_blocking(cache.get, cache_key(source)) if cache else None
    recipe = (saved or {}).get("recipe")
    if recipe:
        try:
            return await replay(fetcher, source, recipe, max_pages)
        except (DiscoveryError, ValueError, TypeError, KeyError):
            await run_blocking(cache.put, cache_key(source), {})
    return None


async def learn(fetcher, source, observations, rendered, max_pages):
    cache = getattr(fetcher, "cache", None)
    if not cache:
        return None
    for observation in observations:
        recipe = await run_blocking(infer, observation, source, rendered.entries)
        if not recipe:
            continue
        recipe.update(title=rendered.title, kind=rendered.kind)
        try:
            result = await replay(
                fetcher, source, recipe, max_pages, first=observation["data"]
            )
            await run_blocking(cache.put, cache_key(source), {"recipe": recipe})
            return result
        except (DiscoveryError, ValueError, TypeError, KeyError):
            pass
    return None
