from . import browser_client
from .limits import MAX_LINKS
from .listing_recipes import cached_listing, learn
from .models import Scan
from .parser import merge_entries, parse_page
from .urls import DiscoveryError
from .workers import run_blocking


async def scan_browser(
    discoverer,
    source,
    *,
    initial=None,
    selector="",
    include_path="",
    keywords="",
    deep=False,
):
    if not deep and not selector and not keywords:
        if cached := await cached_listing(
            discoverer.fetcher, source, discoverer.max_pages
        ):
            return cached
    if initial is None:
        source, initial = await discoverer.fetcher.get(source)
    rendered = await browser_client.render(discoverer.fetcher, source, initial)
    result = Scan(source, methods=["Browser JavaScript"], coverage="partial")
    first = None
    for snapshot, location in zip(
        rendered["snapshots"],
        rendered.get("snapshot_urls", [source] * len(rendered["snapshots"])),
        strict=True,
    ):
        if discoverer.analyzer:
            part, _, _ = await discoverer.analyzer.analyze(
                snapshot, location, selector, include_path
            )
        else:
            part, _, _ = await run_blocking(
                parse_page, snapshot, location, selector, include_path
            )
        if first is None:
            first = part
        result.title, result.kind = part.title or result.title, part.kind
        result.entries = merge_entries(result.entries + part.entries)[:MAX_LINKS]
        result.pages_scanned += 1
    if not result.entries:
        raise DiscoveryError(
            "The browser did not find content links. Try a public API, feed, or a more specific listing URL."
        )
    if not selector and not keywords and first:
        validated = await learn(
            discoverer.fetcher,
            source,
            rendered["captured"],
            first,
            discoverer.max_pages,
        )
        if validated:
            # Keep context recovered from neighboring DOM elements for keyword filters.
            validated.entries = merge_entries(result.entries + validated.entries)
            validated.methods.insert(0, "Browser JavaScript")
            return validated
    result.warnings.append(
        "Browser scanning followed a limited number of pagination steps. Older entries may remain."
    )
    if rendered.get("truncated"):
        result.warnings.append("The rendered page exceeded the browser snapshot limit.")
    return result
