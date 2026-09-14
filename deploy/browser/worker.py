"""Unprivileged, networkless Chromium worker. Only the app brokers public GETs."""

import asyncio
import hashlib
import json
import os
import re
import socket
import struct
import threading
from pathlib import Path

from playwright.async_api import async_playwright

SOCKET = Path("/run/tracker-browser/worker.sock")
LIMIT = 16_000_000
MORE = re.compile(
    r"^\s*(?:(?:load|show|view)\s+more(?:\s+(?:posts?|news|chapters?|entries|items|results|articles|episodes))?|older\s+(?:posts?|entries|news))\s*[↓›»]*\s*$",
    re.I,
)
EXPAND = re.compile(
    r"^\s*(?:show\s+)?(?:all\s+)?(?:chapters|episodes|table of contents)\s*$", re.I
)
SNAPSHOT = """() => {
  const root = document.documentElement.cloneNode(true);
  root.querySelectorAll('script,style,iframe,object,embed,video,audio,canvas').forEach(n => n.remove());
  const html = root.outerHTML;
  return {html:html.slice(0,1000000), truncated:html.length>1000000};
}"""


async def send(writer, message):
    data = json.dumps(message, ensure_ascii=False).encode() + b"\n"
    if len(data) > LIMIT:
        raise ValueError("Message exceeds worker budget")
    writer.write(data)
    await writer.drain()


async def receive(reader):
    raw = await reader.readline()
    if not raw or len(raw) > LIMIT:
        raise ValueError("Invalid worker message")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid worker message")
    return data


async def render(reader, writer, url):
    io_lock = asyncio.Lock()
    requests = 0
    pending = 0
    snapshots, fingerprints = [], set()
    truncated = False
    blocked = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            chromium_sandbox=True,
            args=[
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-quic",
                "--disable-features=WebRtcHideLocalIpsWithMdns",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
        )
        try:
            context = await browser.new_context(
                service_workers="block",
                accept_downloads=False,
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            page.on("dialog", lambda dialog: dialog.dismiss())
            context.on("page", lambda popup: popup.close() if popup != page else None)
            await context.route_web_socket("**/*", lambda ws: ws.close())

            async def route(request_route):
                nonlocal requests, pending, blocked
                req = request_route.request
                if (
                    req.method != "GET"
                    or req.resource_type
                    not in {"document", "script", "stylesheet", "xhr", "fetch"}
                    or req.frame != page.main_frame
                    or (
                        req.is_navigation_request()
                        and req.url.rstrip("/") != url.rstrip("/")
                    )
                ):
                    blocked += 1
                    await request_route.abort()
                    return
                pending += 1
                try:
                    async with io_lock:
                        requests += 1
                        if requests > 40:
                            blocked += 1
                            await request_route.abort()
                            return
                        await send(
                            writer,
                            {
                                "type": "fetch",
                                "id": requests,
                                "url": req.url,
                                "method": req.method,
                                "resource": req.resource_type,
                            },
                        )
                        response = await receive(reader)
                        if response.get("id") != requests or response.get("error"):
                            blocked += 1
                            await request_route.abort()
                            return
                    body = response.get("body", "")
                    mime = {
                        "document": "text/html",
                        "script": "application/javascript",
                        "stylesheet": "text/css",
                    }.get(
                        req.resource_type,
                        "application/json"
                        if body.lstrip().startswith(("{", "["))
                        else "text/plain",
                    )
                    await request_route.fulfill(
                        status=200,
                        body=body,
                        content_type=mime,
                        headers={"Access-Control-Allow-Origin": "*"},
                    )
                except (ValueError, ConnectionError, asyncio.IncompleteReadError):
                    await request_route.abort()
                finally:
                    pending -= 1

            await context.route("**/*", route)

            async def settle():
                quiet = 0
                for _ in range(120):
                    await asyncio.sleep(0.25)
                    quiet = quiet + 1 if not pending else 0
                    if quiet >= 6:
                        return

            async def collect():
                nonlocal truncated
                snap = await page.evaluate(SNAPSHOT)
                truncated = truncated or snap["truncated"]
                digest = hashlib.sha256(snap["html"].encode()).hexdigest()
                if digest in fingerprints:
                    return False
                fingerprints.add(digest)
                snapshots.append(snap["html"])
                return True

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await settle()
            await page.locator("details:not([open])").evaluate_all(
                "nodes => nodes.slice(0,20).forEach(n => n.open = true)"
            )
            expand = page.get_by_role("button", name=EXPAND)
            if await expand.count() == 1 and await expand.is_visible():
                await expand.click(timeout=1500)
                await settle()
            await collect()
            unchanged = 0
            steps = 0
            for _ in range(4):
                more = page.locator(
                    "button,[role=button],a:not([href]),a[href='#']"
                ).filter(has_text=MORE)
                if (
                    await more.count() == 1
                    and await more.is_visible()
                    and await more.is_enabled()
                ):
                    # Avoid submitting forms even if their label resembles pagination.
                    if await more.evaluate("n => !!n.closest('form')"):
                        break
                    await more.click(timeout=1500)
                else:
                    await page.evaluate(
                        "window.scrollTo(0,document.documentElement.scrollHeight)"
                    )
                steps += 1
                await settle()
                unchanged = 0 if await collect() else unchanged + 1
                if unchanged >= 2:
                    break
            await send(
                writer,
                {
                    "type": "result",
                    "snapshots": snapshots,
                    "truncated": truncated,
                    "steps": steps,
                    "blocked": blocked,
                },
            )
        finally:
            await asyncio.wait_for(browser.close(), 3)


async def main():
    lock = asyncio.Lock()
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)

    async def handle(reader, writer):
        watchdog = None
        try:
            peer = writer.get_extra_info("socket").getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, 12
            )
            if struct.unpack("3i", peer)[1] != os.getuid():
                return
            if lock.locked():
                await send(
                    writer,
                    {
                        "type": "error",
                        "message": "The browser worker is busy. Try again shortly.",
                    },
                )
                return
            async with lock:
                # The container restarts if Chromium cannot be cancelled or cleaned up.
                watchdog = threading.Timer(65, lambda: os._exit(70))
                watchdog.daemon = True
                watchdog.start()
                async with asyncio.timeout(50):
                    message = await receive(reader)
                    url = message.get("url")
                    if (
                        not isinstance(url, str)
                        or len(url) > 4096
                        or not url.startswith(("https://", "http://"))
                    ):
                        raise ValueError("Invalid source")
                    await render(reader, writer, url)
        except Exception:
            try:
                await send(
                    writer,
                    {
                        "type": "error",
                        "message": "Browser scan could not finish within its limits. Saved links were kept.",
                    },
                )
            except Exception:
                pass
        finally:
            if watchdog:
                watchdog.cancel()
            writer.close()

    server = await asyncio.start_unix_server(handle, path=str(SOCKET), limit=LIMIT)
    SOCKET.chmod(0o600)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
