"""Official Steam announcements: listing metadata only, never article bodies."""

import re
from urllib.parse import urlsplit

from .urls import DiscoveryError


def app_id(source):
    p = urlsplit(source)
    match = re.fullmatch(r"/news/app/([1-9]\d{0,9})/?", p.path)
    if p.hostname == "store.steampowered.com" and match and int(match[1]) <= 2**32 - 1:
        return match[1]
    return None


async def steam(inv):
    from .public_apis import endpoint

    identity = app_id(inv.source)
    if not identity:
        raise DiscoveryError(
            "Use a Steam game news URL, such as store.steampowered.com/news/app/1623730."
        )
    inv.result.title = f"Steam news · {identity}"
    params = dict(
        appid=identity, count=100, maxlength=1, feeds="steam_community_announcements"
    )
    cursor = None
    while True:
        data = await inv.get(
            endpoint(
                "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/", **params
            )
        )
        news = data.get("appnews", {})
        if str(news.get("appid")) != identity:
            raise DiscoveryError("Steam returned news for a different game.")
        rows = inv.rows(news.get("newsitems"))
        before = len(inv.records)
        dates = []
        for row in rows:
            gid, date = str(row.get("gid", "")), row.get("date")
            if (
                not gid.isdigit()
                or str(row.get("appid")) != identity
                or row.get("feedname") != "steam_community_announcements"
                or type(date) is not int
                or not 0 < date < 2**32
            ):
                raise DiscoveryError(
                    "Steam returned unexpected announcement metadata. Saved links were kept."
                )
            dates.append(date)
            inv.add(
                row.get("url"),
                row.get("title"),
                date,
                source_id=f"steam:{identity}:{gid}",
                context="Official Steam announcement",
            )
        if len(rows) < 100:
            break
        inv.progress(before, rows)
        # Include boundary timestamps again: two announcements may share a second.
        next_cursor = min(dates) + 1
        if cursor is not None and next_cursor >= cursor:
            raise DiscoveryError(
                "Steam repeated a date boundary. The archive may be incomplete."
            )
        params["enddate"] = cursor = next_cursor
