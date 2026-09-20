"""Profile cold page-load critical paths against an offline production build.

Uses visual-audit.py synthetic fixtures, never real accounts or remote APIs.
API latency is a fixed response delay, not a backend execution benchmark. Static
asset latency defaults to zero (loopback); set --asset-latency-ms to model RTT.
Run in the existing browser image with --network none, 1 GiB, and two CPUs.
"""

import argparse
import asyncio
import functools
import hashlib
import importlib.util
import json
import statistics
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

spec = importlib.util.spec_from_file_location(
    "visual_audit", Path(__file__).with_name("visual-audit.py")
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


PROBE = """() => {
  const probe = window.__loadProbe = {longTasks: [], paints: [], rowReady: null, rowsPainted: null};
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) probe.longTasks.push({start: entry.startTime, duration: entry.duration});
  }).observe({type: 'longtask', buffered: true});
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) probe.paints.push({name: entry.name, start: entry.startTime});
  }).observe({type: 'paint', buffered: true});
  const observer = new MutationObserver(() => {
    if (!document.querySelector('article.item-row, article.entry-row')) return;
    probe.rowReady = performance.now();
    observer.disconnect();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      probe.rowsPainted = performance.now();
    }));
  });
  observer.observe(document, {childList: true, subtree: true});
}"""


async def run_case(browser, args, route_name, count, latency, iteration):
    context = await browser.new_context(viewport={"width": 1440, "height": 1000})
    api = audit.API(delay=latency / 1000)
    api.items = api.items[:count]

    async def route(request_route):
        parsed = urlparse(request_route.request.url)
        if parsed.hostname != "127.0.0.1":
            await request_route.abort()
        elif parsed.path.startswith("/api/"):
            await api.route(request_route)
        else:
            if args.asset_latency_ms:
                await asyncio.sleep(args.asset_latency_ms / 1000)
            await request_route.continue_()

    await context.route("**/*", route)
    page = await context.new_page()
    await page.add_init_script(f"({PROBE})()")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cdp = await context.new_cdp_session(page)
    await cdp.send("Network.enable")
    await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    await cdp.send("Performance.enable")
    before = {
        x["name"]: x["value"]
        for x in (await cdp.send("Performance.getMetrics"))["metrics"]
    }
    requests = {}
    navigation_start = [None]

    def started(event):
        parsed = urlparse(event["request"]["url"])
        if event.get("type") == "Document" and navigation_start[0] is None:
            navigation_start[0] = event["timestamp"]
        requests[event["requestId"]] = {
            "url": parsed.path + ("?" + parsed.query if parsed.query else ""),
            "kind": event.get("type"),
            "start": event["timestamp"],
            "initiator": event.get("initiator", {}).get("type"),
            "method": event["request"]["method"],
        }

    def responded(event):
        if row := requests.get(event["requestId"]):
            row.update(response=event["timestamp"], status=event["response"]["status"])

    def finished(event):
        if row := requests.get(event["requestId"]):
            row.update(
                end=event["timestamp"], transferred_bytes=event["encodedDataLength"]
            )

    def failed(event):
        if row := requests.get(event["requestId"]):
            row.update(end=event["timestamp"], error=event["errorText"])

    cdp.on("Network.requestWillBeSent", started)
    cdp.on("Network.responseReceived", responded)
    cdp.on("Network.loadingFinished", finished)
    cdp.on("Network.loadingFailed", failed)
    path = "/" if route_name == "library" else "/items/one"
    await page.goto(
        f"http://127.0.0.1:{args.port}{path}", wait_until="domcontentloaded"
    )
    await page.wait_for_function(
        "window.__loadProbe?.rowsPainted != null", timeout=30000
    )
    ready_metrics = {
        x["name"]: x["value"]
        for x in (await cdp.send("Performance.getMetrics"))["metrics"]
    }
    # Settle only after measuring row readiness and renderer work; includes late chunks.
    await page.wait_for_timeout(100)
    probe = await page.evaluate("""() => ({
      ...window.__loadProbe,
      navigation: performance.getEntriesByType('navigation')[0].toJSON(),
      resources: performance.getEntriesByType('resource').map(x=>({url:new URL(x.name).pathname,start:x.startTime,duration:x.duration,bytes:x.transferSize})),
      domNodes: document.querySelectorAll('*').length,
      rows: document.querySelectorAll('article.item-row, article.entry-row').length
    })""")
    origin = navigation_start[0]
    waterfall = []
    for row in requests.values():
        for field in ("start", "response", "end"):
            if field in row:
                row[field + "_ms"] = round((row.pop(field) - origin) * 1000, 3)
        if "end_ms" in row:
            row["duration_ms"] = round(row["end_ms"] - row["start_ms"], 3)
        waterfall.append(row)
    duration_names = (
        "ScriptDuration",
        "LayoutDuration",
        "RecalcStyleDuration",
        "TaskDuration",
    )
    result = {
        "route": route_name,
        "item_count": count,
        "api_latency_ms": latency,
        "asset_latency_ms": args.asset_latency_ms,
        "iteration": iteration,
        "row_ready_ms": probe["rowReady"],
        "rows_painted_ms": probe["rowsPainted"],
        "html_ttfb_ms": probe["navigation"]["responseStart"],
        "dom_content_loaded_ms": probe["navigation"]["domContentLoadedEventEnd"],
        "fcp_ms": next(
            (
                x["start"]
                for x in probe["paints"]
                if x["name"] == "first-contentful-paint"
            ),
            None,
        ),
        "renderer_ms": {
            key: round((ready_metrics[key] - before.get(key, 0)) * 1000, 3)
            for key in duration_names
        },
        "heap_bytes_at_ready": ready_metrics["JSHeapUsedSize"],
        "long_tasks": [
            x for x in probe["longTasks"] if x["start"] <= probe["rowsPainted"]
        ],
        "dom_nodes": probe["domNodes"],
        "visible_row_count": probe["rows"],
        "requests": sorted(waterfall, key=lambda x: x["start_ms"]),
        "api_calls": api.calls,
        "errors": errors,
    }
    await context.close()
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "route",
                    "item_count",
                    "api_latency_ms",
                    "iteration",
                    "rows_painted_ms",
                    "errors",
                )
            }
        ),
        flush=True,
    )
    return result


async def run(args):
    build = Path(args.build).resolve()
    if not (build / "index.html").is_file():
        raise SystemExit("--build must contain index.html")
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        functools.partial(audit.StaticHandler, directory=str(build)),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {
        "build_index_sha256": hashlib.sha256(
            (build / "index.html").read_bytes()
        ).hexdigest(),
        "scope": "Cold browser contexts; offline synthetic authenticated account; fixed API response delay; localhost static files; 1440x1000 viewport. Renderer timing stops at first row paint.",
        "runs": [],
    }
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True, chromium_sandbox=True
            )
            report["browser"] = browser.version
            for latency in args.api_latencies:
                for count in args.item_counts:
                    for route_name in ("library", "item"):
                        for iteration in range(args.repeats):
                            report["runs"].append(
                                await run_case(
                                    browser, args, route_name, count, latency, iteration
                                )
                            )
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    report["summaries"] = []
    for latency in args.api_latencies:
        for count in args.item_counts:
            for route_name in ("library", "item"):
                rows = [
                    row
                    for row in report["runs"]
                    if (row["api_latency_ms"], row["item_count"], row["route"])
                    == (latency, count, route_name)
                ]
                summary = {
                    "route": route_name,
                    "item_count": count,
                    "api_latency_ms": latency,
                    "asset_latency_ms": args.asset_latency_ms,
                }
                for key in (
                    "row_ready_ms",
                    "rows_painted_ms",
                    "html_ttfb_ms",
                    "dom_content_loaded_ms",
                    "heap_bytes_at_ready",
                    "dom_nodes",
                    "visible_row_count",
                ):
                    summary[key] = round(statistics.median(row[key] for row in rows), 3)
                summary["renderer_ms"] = {
                    key: round(
                        statistics.median(row["renderer_ms"][key] for row in rows), 3
                    )
                    for key in rows[0]["renderer_ms"]
                }
                summary["long_task_ms"] = round(
                    statistics.median(
                        sum(x["duration"] for x in row["long_tasks"]) for row in rows
                    ),
                    3,
                )
                summary["api_count"] = statistics.median(
                    len(row["api_calls"]) for row in rows
                )
                report["summaries"].append(summary)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps({"output": str(output), "summaries": report["summaries"]}),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--api-latencies",
        type=lambda value: [int(x) for x in value.split(",")],
        default=[80, 200],
    )
    parser.add_argument("--asset-latency-ms", type=int, default=0)
    parser.add_argument(
        "--item-counts",
        type=lambda value: [int(x) for x in value.split(",")],
        default=[25, 500],
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--port", type=int, default=8766)
    arguments = parser.parse_args()
    if (
        any(value < 0 or value > 5000 for value in arguments.api_latencies)
        or not 0 <= arguments.asset_latency_ms <= 5000
    ):
        parser.error("latencies must be between 0 and 5000 ms")
    if (
        any(not 1 <= count <= 500 for count in arguments.item_counts)
        or not 1 <= arguments.repeats <= 20
    ):
        parser.error("item counts must be 1–500 and repeats 1–20")
    asyncio.run(run(arguments))
