"""Bounded multi-source refresh using the shared scanner/cache and network admission."""

import asyncio
from dataclasses import replace
from urllib.parse import urlsplit

from .entry_identity import entry_key, record_id
from .limits import bounded_scan
from .models import Scan, utcnow
from .parser import merge_entries
from .source_methods import detect_source_method
from .urls import DiscoveryError, content_key


def source_urls(item):
    if item.get("source_urls") is not None:
        return item["source_urls"]
    return [] if item.get("source_type") in {"csv", "document"} else [item["url"]]


async def scan_sources(item, app, store, deep=False):
    urls = source_urls(item)

    async def one(index, url):
        try:
            selector = item.get("selector", "") if index == 0 else ""
            method = item.get("source_method", "auto") if index == 0 else "auto"
            if method == "auto" and not selector:
                method = detect_source_method(url)["source_method"]
            async with app.state.scan_semaphore:
                store.check_active()
                result = await app.state.discoverer.scan(
                    url,
                    selector,
                    item.get("include_path", "") if index == 0 else "",
                    **({"keywords": item["keywords"]} if item.get("keywords") else {}),
                    **({"deep": True} if deep else {}),
                    **({"source_method": method} if method != "auto" else {}),
                )
            original_url = item["url"]
            original = original_url.startswith(("http://", "https://")) and content_key(
                url
            ) == content_key(original_url)
            if not original:
                result = replace(
                    result,
                    entries=[
                        replace(
                            entry,
                            source_id=record_id(
                                "source:" + content_key(url),
                                entry.source_id or entry_key(entry),
                            ),
                        )
                        if entry.source_id or not entry.url
                        else replace(entry)
                        for entry in result.entries
                    ],
                )
            if not result.entries and not (
                result.unfiltered_count
                and result.keywords
                or result.coverage == "complete"
                and result.expected_count == 0
            ):
                raise DiscoveryError(
                    "No content found during refresh. Saved links and progress were kept. "
                    + " ".join(result.warnings)
                )
            return result, None
        except DiscoveryError as exc:
            prefix = f"{urlsplit(url).hostname}: " if len(urls) > 1 else ""
            return None, prefix + str(exc)

    tasks = [asyncio.create_task(one(i, url)) for i, url in enumerate(urls)]
    try:
        outcomes = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    results = [result for result, _ in outcomes if result is not None]
    errors = [error for _, error in outcomes if error]
    if not results:
        raise DiscoveryError(" ".join(errors))
    if len(urls) == 1:
        return results[0].to_dict(), errors
    # Copy entries: cached scanner results must not be mutated by cross-source merging.
    copies = [replace(entry) for result in results for entry in result.entries]
    identities = {}
    for entry in copies:
        if entry.url and entry.source_id:
            identities.setdefault(content_key(entry.url), entry.source_id)
    entries = merge_entries(copies)
    for entry in entries:
        if entry.url and (identity := identities.get(content_key(entry.url))):
            entry.source_id = identity
    warnings = list(
        dict.fromkeys(warning for result in results for warning in result.warnings)
    )
    warnings.extend(errors)
    complete = not errors and all(r.coverage == "complete" for r in results)
    combined = Scan(
        item["url"],
        item["title"],
        kind=results[0].kind,
        entries=entries,
        warnings=warnings,
        pages_scanned=sum(r.pages_scanned for r in results),
        methods=list(
            dict.fromkeys(method for result in results for method in result.methods)
        ),
        requests_made=sum(r.requests_made for r in results),
        cache_hits=sum(r.cache_hits for r in results),
        checked_at=max(
            (r.checked_at for r in results if r.checked_at), default=utcnow()
        ),
        cached=all(r.cached for r in results),
        coverage="complete" if complete else "partial",
        expected_count=len(entries) if complete else None,
        order_hint="source" if all(r.order_hint == "source" for r in results) else "",
        keywords=item.get("keywords", ""),
        analysis_mode="deep"
        if any(r.analysis_mode == "deep" for r in results)
        else "light",
        source_summary=results[0].source_summary,
    )
    return bounded_scan(combined.to_dict()), errors
