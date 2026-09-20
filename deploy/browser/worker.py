"""Unprivileged, networkless Chromium worker. Only the app brokers public GETs."""

import asyncio
import hashlib
import json
import os
import re
import socket
import struct
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.async_api import async_playwright

# This standalone image uses the same public identity as app.tracker.http_identity.
USER_AGENT = (
    "Trackify/1.0 (+https://mediatrackify.duckdns.org; "
    "contact: mediatrackify@gmail.com)"
)

SOCKET = Path("/run/tracker-browser/worker.sock")
LIMIT = 16_000_000
MAX_STEPS = 20
EXPAND = re.compile(
    r"^\s*(?:show\s+)?(?:all\s+)?(?:chapters|episodes|table of contents)\s*$", re.I
)
SNAPSHOT = """() => {
  const root = document.documentElement.cloneNode(true);
  root.querySelectorAll('script,style,iframe,object,embed,video,audio,canvas').forEach(n => n.remove());
  const html = root.outerHTML;
  const links = [...document.querySelectorAll('a[href]')].map(n => [n.href,n.textContent.trim()]);
  return {html:html.slice(0,1000000), truncated:html.length>1000000, links};
}"""
CONTROL = r"""() => {
  const proseRegion = root => {
    if (/^(HTML|BODY|MAIN)$/.test(root.tagName)) return false;
    const nodes = [root];
    const walker = document.createTreeWalker(root, 1);
    while (walker.nextNode()) {
      if (nodes.length === 128) return false;
      nodes.push(walker.currentNode);
    }
    if (nodes.some(n => /^(UL|OL|TABLE|H[1-6])$/.test(n.tagName) ||
        /^(list|listitem|grid|feed|tree)$/.test(n.getAttribute('role') || '') ||
        /chapters?|episodes?|posts?|entries|results|articles|pagination|pager|(?:^|[\s_-])list(?:$|[\s_-])/i.test(`${n.id} ${n.getAttribute('class') || ''}`) ||
        (n.tagName === 'A' && (n.getAttribute('href') || '').trim() && !(n.getAttribute('href') || '').trim().startsWith('#')))) return false;
    const paragraphs = nodes.filter(n => n.tagName === 'P');
    const marked = nodes.some(n => !/^(BUTTON|A|SCRIPT|STYLE)$/.test(n.tagName) &&
      /description|synopsis|summary|abstract|biography|excerpt|overview|line-clamp/i.test(`${n.id} ${n.getAttribute('class') || ''} ${n.getAttribute('itemprop') || ''}`) &&
      n.textContent.trim().length >= 40);
    return marked || (paragraphs.length === 1 && paragraphs[0].textContent.trim().length >= 80);
  };
  const textExpansion = n => {
    const targets = (n.getAttribute('aria-controls') || '').split(/\s+/).filter(Boolean);
    for (const attribute of ['data-target', 'data-bs-target']) {
      const value = n.getAttribute(attribute) || '';
      if (/^#[\w-]+$/.test(value)) targets.push(value.slice(1));
    }
    if (targets.length) return targets.length <= 4 && targets.every(id => {
      const root = document.getElementById(id);
      return root && proseRegion(root);
    });
    let root = n.parentElement;
    for (let depth = 0; root && depth < 3; depth++, root = root.parentElement) {
      if (/^(HTML|BODY|MAIN)$/.test(root.tagName)) break;
      if (proseRegion(root)) return true;
    }
    return false;
  };
  const candidates = [...document.querySelectorAll('button,[role=button],a')].filter(n => {
    if (!n.checkVisibility() || n.disabled || n.getAttribute('aria-disabled')==='true' ||
        n.closest('form,[aria-roledescription=carousel],[class*=carousel],[class*=slider]')) return false;
    const text = (n.getAttribute('aria-label') || n.textContent).trim();
    if (/^(?:load|show|view)\s+more\s*[↓→›»+]*$/i.test(text) && textExpansion(n)) return false;
    const more = /^(?:(?:load|show|view)\s+more(?:\s+(?:posts?|news|chapters?|entries|items|results|articles|episodes))?|older\s+(?:posts?|entries|news))\s*[↓→›»+]*$/i.test(text);
    const next = /^(?:go\s*to\s+)?next\s+(?:page|posts?|results)\s*[→›»]*$/i.test(text) ||
      ((n.rel||'').split(/\s+/).includes('next')) ||
      (/^next\s*[→›»]*$/i.test(text) && n.closest('nav,[class*=pagin],[class*=pager],[aria-label*=agination]'));
    return more || next;
  });
  // Top and bottom pagers sometimes repeat an identical next link.
  if (candidates.length > 1 && !candidates.every(n => n.href && n.href === candidates[0].href)) return null;
  return candidates[0] || null;
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
    snapshot_urls = []
    snapshot_bytes = 0
    navigation = url
    started = time.monotonic()
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
                user_agent=USER_AGENT,
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
                        and req.url.rstrip("/") != navigation.rstrip("/")
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

            async def settle(quiet_steps=4):
                quiet = 0
                for _ in range(80):
                    await asyncio.sleep(0.25)
                    quiet = quiet + 1 if not pending else 0
                    if quiet >= quiet_steps or time.monotonic() - started > 43:
                        return

            async def collect():
                nonlocal truncated, snapshot_bytes
                snap = await page.evaluate(SNAPSHOT)
                truncated = truncated or snap["truncated"]
                digest = hashlib.sha256(json.dumps(snap["links"]).encode()).hexdigest()
                if digest in fingerprints:
                    return False
                size = len(snap["html"].encode())
                if snapshot_bytes + size > 5_000_000:
                    truncated = True
                    return False
                fingerprints.add(digest)
                snapshots.append(snap["html"])
                snapshot_urls.append(page.url)
                snapshot_bytes += size
                return True

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await settle()
            await page.locator("details:not([open])").evaluate_all(
                "nodes => nodes.slice(0,20).forEach(n => n.open = true)"
            )
            expand = page.get_by_role("button", name=EXPAND)
            if (
                await expand.count() == 1
                and await expand.is_visible()
                and not await expand.evaluate("n => !!n.closest('form')")
            ):
                await expand.click(timeout=1500)
                await settle()
            await collect()
            unchanged = 0
            steps = 0
            for _ in range(MAX_STEPS):
                if time.monotonic() - started > 43 or truncated or requests >= 40:
                    break
                handle = await page.evaluate_handle(CONTROL)
                more = handle.as_element()
                try:
                    if more:
                        target = await more.get_attribute("href")
                        if target and not target.startswith("#"):
                            target = urljoin(page.url, target)
                            if urlsplit(target).netloc != urlsplit(url).netloc:
                                break
                            async with io_lock:
                                await send(writer, {"type": "navigate", "url": target})
                                approval = await receive(reader)
                            if not approval.get("allowed"):
                                break
                            navigation = target
                        await more.click(timeout=1500)
                    else:
                        await page.evaluate(
                            "window.scrollTo(0,document.documentElement.scrollHeight)"
                        )
                except Exception:
                    break  # Preserve rows already rendered if a control disappears.
                finally:
                    await handle.dispose()
                steps += 1
                await settle(2)
                unchanged = 0 if await collect() else unchanged + 1
                if unchanged >= 2:
                    break
            result = {
                "type": "result",
                "snapshots": snapshots,
                "snapshot_urls": snapshot_urls,
                "truncated": truncated,
                "steps": steps,
                "blocked": blocked,
            }
        finally:
            await asyncio.wait_for(browser.close(), 3)
    await send(writer, result)


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
