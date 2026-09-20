"""Offline Chromium UI audit using synthetic accounts and a production build.

Run in the existing Playwright browser image with --network none; never connect
this harness to production APIs. Screenshots and raw reports belong in ignored data.
"""

import argparse
import asyncio
import functools
import json
import statistics
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright


class StaticHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not Path(self.translate_path(self.path)).is_file():
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, *_args):
        pass


def fixtures():
    links = [
        {
            "id": f"link{i}",
            "title": f"Chapter {i}: An unexpected discovery",
            "url": f"https://example.org/chapter/{i}",
            "number": i,
            "read": i < 80,
            "favorite": i % 17 == 0,
            "ignored": False,
            "is_new": i > 195,
            "published_at": "2026-09-19T13:30:00Z" if i % 3 else None,
            "date_kind": "published",
            "date_source": "Series list",
            "date_precision": "day",
            "context": "Company: Northstar Labs; Location: Remote (United States); Role: Software Engineer — Infrastructure and Machine Learning; Application: https://example.org/careers/engineering/2026/graduate-program-with-a-long-application-address",
            "summary": "A short update with source details available below.",
            "link_count": 1,
        }
        for i in range(1, 201)
    ]
    links[199]["title"] = (
        "Northstar Labs — Graduate software engineer, infrastructure and machine learning (2027 start)"
    )
    links[198]["title"] = "Chapter 199: A second chance"
    links[197]["title"] = "Episode 198: Exploring the spaces between stars"
    base = {
        "id": "one",
        "title": "Climbing the Tower with Time-Stop Ability",
        "kind": "novel",
        "url": "https://example.org/series/time-stop",
        "favorite": True,
        "ignored": False,
        "auto_read": True,
        "error": "",
        "warnings": [],
        "methods": ["page", "link classifier"],
        "pages_scanned": 2,
        "created_at": "2026-09-19T08:00:00Z",
        "last_checked_at": "2026-09-20T08:00:00Z",
        "selector": "",
        "include_path": "",
        "keywords": "",
        "dated_count": 134,
        "total_count": 200,
        "ignored_count": 0,
        "read_count": 79,
        "unread_count": 121,
        "new_count": 5,
        "latest_link": links[-1],
        "next_unread_link": links[79],
        "continue_link": links[79],
        "source_type": "web",
        "kind_override": "",
        "kind_auto": "novel",
        "description": "A traveler climbs a mysterious tower, using the ability to stop time to navigate its changing trials.",
        "description_auto": "A traveler climbs a mysterious tower, using the ability to stop time to navigate its changing trials.",
        "description_override": "",
        "description_method": "source",
        "description_suppressed": False,
        "search_tags": ["fiction", "fantasy", "time travel"],
    }
    titles = [
        base["title"],
        "Stories about the natural world, science, and the places we visit",
        "音楽と物語 — a collection across languages",
        "Research notes: machine learning and distributed systems",
        "Summer internship and graduate engineering opportunities",
        "A podcast about design and everyday things",
        "Comics: a collection of small discoveries",
    ]
    kinds = ["novel", "blog", "youtube", "research", "jobs", "podcast", "comic"]
    items = [
        {
            **base,
            "id": "one" if i == 0 else f"item{i}",
            "title": titles[i % 7] + (f" · Collection {i + 1}" if i >= 7 else ""),
            "kind": kinds[i % 7],
            "favorite": i < 3,
            "ignored": i == 2 or i % 37 == 0 and i > 0,
            "new_count": 5 if i % 3 else 0,
            "description": base["description"]
            if i % 7 == 0
            else "A collection of recent releases and updates, organized for later reading.",
        }
        for i in range(500)
    ]
    return items, links


class API:
    def __init__(self, signed_in=True, delay=0.08):
        self.signed_in = signed_in
        self.items, self.links = fixtures()
        self.calls = []
        self.delay = delay

    async def route(self, route):
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path.removeprefix("/api")
        query = parse_qs(parsed.query)
        self.calls.append({"path": path, "method": request.method})
        await asyncio.sleep(self.delay)
        status = 200
        if path == "/auth/status":
            data = {
                "required": True,
                "registration": True,
                "invite_required": False,
                "user": {"username": "visual_reader"} if self.signed_in else None,
            }
        elif path == "/settings":
            data = {
                "link_sort": "auto",
                "link_direction": "desc",
                "library_sort": "recent",
                "auto_read": True,
                "refresh_mode": "light",
                "source_method": "auto",
            }
        elif path == "/items":
            data = [] if query.get("trash") else self.items
        elif path == "/search":
            body = request.post_data_json or {}
            key = body.get("query", "").casefold()
            data = {
                "scores": [
                    {"id": row["id"], "score": 1}
                    for row in self.items
                    if key in row["title"].casefold()
                ]
            }
        elif path.startswith("/items/") and path.endswith("/links"):
            needle = query.get("search", [""])[0].casefold()
            matching = [
                link
                for link in reversed(self.links)
                if needle in link["title"].casefold()
            ]
            offset = int(query.get("offset", [0])[0])
            data = {
                "links": matching[offset : offset + 50],
                "total": len(matching),
                "sort_used": "number",
            }
        elif path.startswith("/items/"):
            data = {
                **self.items[0],
                "source_type": "csv",
                "source_name": "engineering-opportunities.csv",
            }
        elif path in ("/scans/import", "/scans"):
            data = {
                **self.items[0],
                "scan_id": "offline-preview",
                "source_type": "csv",
                "source_name": "engineering-opportunities.csv",
                "url": "csv:offline",
                "title": "Engineering opportunities",
                "entries": list(reversed(self.links[:12])),
                "csv": {
                    "rows": 12,
                    "columns": [
                        {"index": i, "label": label}
                        for i, label in enumerate(
                            ["URL", "Title", "Company", "Date", "Location"]
                        )
                    ],
                    "selected": {
                        "url": 0,
                        "title": 1,
                        "company": 2,
                        "date": 3,
                        "number": -1,
                    },
                    "delimiter": ",",
                    "header": True,
                },
            }
        elif path.startswith("/auth/recovery") or path == "/auth/email":
            data = {
                "available": False,
                "delivery_available": False,
                "email": None,
                "verified": False,
            }
        elif path == "/source-method":
            data = {"method": "auto", "label": "Automatic"}
        else:
            data = {}
        await route.fulfill(
            status=status, content_type="application/json", body=json.dumps(data)
        )


async def snapshot(page, output, name):
    await page.screenshot(path=str(output / f"{name}.png"), full_page=False)
    return await page.evaluate("""() => ({
      width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      overflow: document.documentElement.scrollWidth > innerWidth,
      nodes: document.querySelectorAll('*').length,
      buttons: [...document.querySelectorAll('button,input,select')].filter(x=>x.getBoundingClientRect().width).map(x=>({label:x.getAttribute('aria-label')||x.textContent?.trim().slice(0,70),height:Math.round(x.getBoundingClientRect().height)})),
    })""")


async def measure(page, cdp):
    await cdp.send("HeapProfiler.collectGarbage")
    metrics = await cdp.send("Performance.getMetrics")
    data = {
        m["name"]: m["value"]
        for m in metrics["metrics"]
        if m["name"]
        in (
            "JSHeapUsedSize",
            "JSHeapTotalSize",
            "Nodes",
            "Documents",
            "LayoutDuration",
            "ScriptDuration",
            "TaskDuration",
        )
    }
    data["worker_count"] = len(page.workers)
    data["worker_urls"] = [worker.url.rsplit("/", 1)[-1] for worker in page.workers]
    data["dom"] = await page.evaluate("document.querySelectorAll('*').length")
    data["resources"] = await page.evaluate(
        "performance.getEntriesByType('resource').filter(x=>x.name.includes('/static/')).map(x=>({name:x.name.split('/').pop(),bytes:x.decodedBodySize,duration:x.duration}))"
    )
    data["navigation"] = await page.evaluate(
        "performance.getEntriesByType('navigation').map(x=>({domContentLoaded:x.domContentLoadedEventEnd,load:x.loadEventEnd}))"
    )
    return data


async def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 8765), functools.partial(StaticHandler, directory=args.build)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {
        "fixture": {"items": 500, "links_per_item": 200, "api_delay_ms": 80},
        "views": {},
        "runs": [],
        "errors": [],
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, chromium_sandbox=True)
        for width, height in [
            (int(pair.split("x")[0]), int(pair.split("x")[1]))
            for pair in args.sizes.split(",")
            if pair
        ]:
            context = await browser.new_context(
                viewport={"width": width, "height": height}, device_scale_factor=1
            )
            api = API()
            await context.route("**/api/**", api.route)
            page = await context.new_page()
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            await page.goto("http://127.0.0.1:8765/")
            await page.locator("article").first.wait_for()
            report["views"][f"library-{width}"] = await snapshot(
                page, output, f"library-{width}"
            )
            await page.goto("http://127.0.0.1:8765/items/one")
            await page.locator(".csv-details summary").first.click()
            report["views"][f"item-{width}"] = await snapshot(
                page, output, f"item-{width}"
            )
            await page.locator(".csv-details").first.scroll_into_view_if_needed()
            report["views"][f"details-{width}"] = await snapshot(
                page, output, f"details-{width}"
            )
            if not await page.get_by_label(
                "Selection options", exact=True
            ).is_visible():
                await page.get_by_role(
                    "button", name="Select links", exact=True
                ).click()
            await page.get_by_label("Selection options", exact=True).select_option(
                "pattern"
            )
            await page.locator(".selection-pattern").scroll_into_view_if_needed()
            report["views"][f"pattern-{width}"] = await snapshot(
                page, output, f"pattern-{width}"
            )
            await page.goto("http://127.0.0.1:8765/add")
            await page.get_by_role("button", name="File or text", exact=True).wait_for()
            report["views"][f"add-{width}"] = await snapshot(
                page, output, f"add-{width}"
            )
            await page.get_by_role("button", name="File or text", exact=True).click()
            await page.get_by_label("Import file", exact=True).set_input_files(
                {
                    "name": "engineering-opportunities.csv",
                    "mimeType": "text/csv",
                    "buffer": b"URL,Title\nhttps://example.org/job,Engineer\n",
                }
            )
            await page.get_by_role("button", name="Preview links", exact=True).click()
            await page.get_by_role(
                "button", name="Add to library", exact=True
            ).wait_for()
            report["views"][f"import-{width}"] = await snapshot(
                page, output, f"import-{width}"
            )
            await page.get_by_role(
                "button", name="Add to library", exact=True
            ).scroll_into_view_if_needed()
            await page.locator(
                ".csv-details summary"
            ).first.scroll_into_view_if_needed()
            await page.locator(".csv-details summary").first.click()
            report["views"][f"preview-{width}"] = await snapshot(
                page, output, f"preview-{width}"
            )
            await page.goto("http://127.0.0.1:8765/settings")
            await page.get_by_role(
                "heading", name="Reading & library", exact=True
            ).wait_for()
            report["views"][f"settings-{width}"] = await snapshot(
                page, output, f"settings-{width}"
            )
            await page.locator("#account").scroll_into_view_if_needed()
            report["views"][f"account-{width}"] = await snapshot(
                page, output, f"account-{width}"
            )
            api.signed_in = False
            await page.goto("http://127.0.0.1:8765/")
            await page.get_by_role("heading", name="Sign in", exact=True).wait_for()
            report["views"][f"signin-{width}"] = await snapshot(
                page, output, f"signin-{width}"
            )
            await context.close()
        for _ in range(args.repeats):
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1000}
            )
            api = API()
            await context.route("**/api/**", api.route)
            page = await context.new_page()
            cdp = await context.new_cdp_session(page)
            await cdp.send("Performance.enable")
            start = time.perf_counter()
            await page.goto("http://127.0.0.1:8765/")
            await page.locator("article").first.wait_for()
            cold_ms = (time.perf_counter() - start) * 1000
            initial = await measure(page, cdp)
            search = page.get_by_role("searchbox", name="Search library", exact=True)
            cycles = []
            for _cycle in range(3):
                for word in ["research", "podcast", "xyzabsent", ""]:
                    started = time.perf_counter()
                    await search.fill(word)
                    await page.wait_for_timeout(450)
                    cycles.append((time.perf_counter() - started) * 1000)
                await page.get_by_role("link", name="Settings", exact=True).click()
                await page.get_by_role(
                    "heading", name="Reading & library", exact=True
                ).wait_for()
                await page.get_by_role("link", name="Library", exact=True).first.click()
                await page.locator("article").first.wait_for()
            final = await measure(page, cdp)
            report["runs"].append(
                {
                    "cold_ready_ms": cold_ms,
                    "initial": initial,
                    "after_cycles": final,
                    "api_calls": len(api.calls),
                }
            )
            await context.close()
        if args.check_failure:
            context = await browser.new_context(viewport={"width": 390, "height": 844})
            api = API()
            await context.route("**/api/**", api.route)
            await context.route(
                "**/static/SettingsPage-*.js", lambda route: route.abort()
            )
            page = await context.new_page()
            await page.goto("http://127.0.0.1:8765/")
            await page.locator("article").first.wait_for()
            await page.get_by_role("button", name="Menu", exact=True).click()
            await page.get_by_role("link", name="Settings", exact=True).click()
            await page.get_by_role(
                "heading", name="Page unavailable", exact=True
            ).wait_for()
            await page.get_by_role("button", name="Reload page", exact=True).wait_for()
            report["views"]["route-failure-390"] = await snapshot(
                page, output, "route-failure-390"
            )
            await page.get_by_role("button", name="Menu", exact=True).click()
            await page.get_by_role("link", name="Library", exact=True).click()
            await page.locator("article").first.wait_for()
            report["route_failure_recovery"] = True
            await context.close()
        await browser.close()
    server.shutdown()
    if report["runs"]:
        report["median"] = {
            "cold_ready_ms": statistics.median(
                x["cold_ready_ms"] for x in report["runs"]
            ),
            "initial_heap_bytes": statistics.median(
                x["initial"]["JSHeapUsedSize"] for x in report["runs"]
            ),
            "after_cycles_heap_bytes": statistics.median(
                x["after_cycles"]["JSHeapUsedSize"] for x in report["runs"]
            ),
            "initial_dom": statistics.median(
                x["initial"]["dom"] for x in report["runs"]
            ),
        }
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "output": str(output),
                "median": report.get("median"),
                "errors": report["errors"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--check-failure", action="store_true")
    parser.add_argument("--sizes", default="1440x1000,900x900,390x844,320x780")
    asyncio.run(run(parser.parse_args()))
