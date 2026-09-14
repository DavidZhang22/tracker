"""Dispatch explicit inventories separately from HTML/model extraction."""

from .public_apis import scan_api
from .sitemaps import scan_sitemap


async def scan_inventory(fetcher, source, method, max_pages, keywords=""):
    if method == "sitemap":
        return await scan_sitemap(fetcher, source, max_pages)
    return await scan_api(fetcher, source, method, max_pages, keywords)
