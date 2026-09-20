"""Capture footer layouts with public synthetic fixtures and an offline build."""

import argparse
import asyncio
import functools
import hashlib
import importlib.util
import json
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

CASES = [
    ("library", "/", "article.item-row", True, 16),
    ("empty-library", "/", ".empty .button", True, 0),
    ("item", "/items/one", "article.entry-row", True, 16),
    ("add", "/add", "input[type=url]", True, 16),
    ("settings", "/settings", ".recovery-email input[type=email]", True, 16),
    ("privacy", "/privacy", ".legal-page h1", True, 16),
    ("terms", "/terms", ".legal-page h1", True, 16),
    ("signin", "/", ".auth-card input[type=password]", False, 16),
    ("recovery", "/account/recover", ".recovery-card h1", False, 16),
    ("verify-email", "/account/verify-email", ".recovery-card h1", False, 16),
    ("shell-failure", "/", ".page-load-error", True, 16),
]

METRICS = """() => {
  const rect = selector => {
    const node = document.querySelector(selector);
    if (!node) return null;
    const b = node.getBoundingClientRect(), s = getComputedStyle(node);
    return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,width:b.width,height:b.height,
      paddingLeft:s.paddingLeft,paddingRight:s.paddingRight,paddingTop:s.paddingTop,paddingBottom:s.paddingBottom,
      display:s.display,background:s.backgroundColor};
  };
  return {width:innerWidth,height:innerHeight,scrollY,scrollHeight:document.documentElement.scrollHeight,
    overflow:document.documentElement.scrollWidth>innerWidth,
    footerCount:document.querySelectorAll('footer').length,
    capacityLabel:document.querySelector('.heading-count')?.textContent.trim() || null,
    oldLimitVisible:document.body.textContent.includes('Up to 500 items per account'),
    footer:rect('.site-footer'),footerContent:rect('.footer-content'),sidebar:rect('.sidebar'),
    failureControls:rect('.page-load-error .button'),
    main:rect('main'),panel:rect('.collection-panel'),legal:rect('.legal-page'),auth:rect('.auth-card'),
    links:[...document.querySelectorAll('.site-footer a')].map(node=>{
      const r=node.getBoundingClientRect();
      return {text:node.textContent,left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height};
    })};
}"""


async def run(args):
    build, output = Path(args.build), Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        functools.partial(audit.StaticHandler, directory=str(build)),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {
        "build_index_sha256": hashlib.sha256(
            (build / "index.html").read_bytes()
        ).hexdigest(),
        "fixture": "16 synthetic items; 200 links per item; separate empty account; 80 ms API delay; no remote APIs",
        "views": {},
        "errors": [],
        "expected_errors": [],
    }
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, chromium_sandbox=True)
            for width, height in [
                tuple(map(int, pair.split("x"))) for pair in args.sizes.split(",")
            ]:
                for name, path, ready, signed_in, count in CASES:
                    if args.cases and name not in args.cases.split(","):
                        continue
                    context = await browser.new_context(
                        viewport={"width": width, "height": height},
                        is_mobile=width < 800,
                        has_touch=width < 800,
                    )
                    api = audit.API(signed_in=signed_in)
                    api.items = api.items[:count]

                    async def route(request_route, *, api=api, case=name):
                        parsed = urlparse(request_route.request.url)
                        if parsed.hostname != "127.0.0.1" or (
                            case == "shell-failure"
                            and "/static/Shell-" in parsed.path
                            and parsed.path.endswith(".js")
                        ):
                            await request_route.abort()
                        elif parsed.path.startswith("/api/"):
                            await api.route(request_route)
                        else:
                            await request_route.continue_()

                    await context.route("**/*", route)
                    page = await context.new_page()
                    page.on(
                        "pageerror",
                        lambda error, expected=name == "shell-failure": report[
                            "expected_errors" if expected else "errors"
                        ].append(str(error)),
                    )
                    await page.goto(f"http://127.0.0.1:{args.port}{path}")
                    await page.wait_for_selector(ready, state="attached")
                    await page.wait_for_load_state("networkidle")
                    await page.evaluate(
                        "window.scrollTo(0,document.documentElement.scrollHeight)"
                    )
                    await page.wait_for_timeout(60)
                    key = f"{name}-{width}"
                    report["views"][key] = await page.evaluate(METRICS)
                    await page.screenshot(path=str(output / f"{key}.png"))
                    await context.close()
                    print(
                        json.dumps(
                            {"view": key, "overflow": report["views"][key]["overflow"]}
                        ),
                        flush=True,
                    )
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "views": len(report["views"]),
                "errors": report["errors"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sizes", default="1440x1000,900x900,390x844,320x780")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument(
        "--cases", default="", help="Optional comma-separated case names"
    )
    asyncio.run(run(parser.parse_args()))
