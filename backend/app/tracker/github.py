"""Load a repository's advertised README, with no repository or job crawl."""

import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from .documents import Document, unpack
from .urls import DiscoveryError, canonical_url, fetch_document
from .workers import run_blocking


def embedded_objects(soup):
    for script in soup.select('script[type="application/json"]'):
        try:
            root = json.loads(script.string or "")
        except (ValueError, RecursionError):
            continue
        stack = [root]
        for _ in range(10000):
            if not stack:
                break
            obj = stack.pop()
            if isinstance(obj, dict):
                yield obj
                stack.extend(v for v in obj.values() if isinstance(v, (list, dict)))
            elif isinstance(obj, list):
                stack.extend(v for v in obj if isinstance(v, (list, dict)))


def readme_part(html, prefix, find_blob=False):
    soup = BeautifulSoup(unpack(html), "html.parser")
    try:
        article = soup.select_one("article.markdown-body")
        if article:
            return "html", Document.from_text(str(article))
        if find_blob:
            target = next(
                (
                    a.get("href")
                    for a in soup.select("a[href]")
                    if a.get("href", "").startswith(prefix + "blob/")
                    and re.search(r"/README(?:\.[\w]+)?$", a.get("href", ""), re.I)
                ),
                None,
            )
            return ("blob", target) if target else ("html", html)
        rich, raw, truncated = None, None, False
        for obj in embedded_objects(soup):
            truncated = truncated or bool(
                obj.get("truncated") or obj.get("isTruncated")
            )
            if (
                rich is None
                and isinstance(obj.get("richText"), str)
                and obj["richText"]
                and not obj.get("truncated")
            ):
                rich = obj["richText"]
            if raw is None and isinstance(obj.get("rawBlobUrl"), str):
                raw = obj["rawBlobUrl"]
        if rich and not truncated:
            return "html", Document.from_text(rich)
        if raw:
            return "raw", raw
        raise DiscoveryError(
            "GitHub did not provide its README content. Saved links were kept."
        )
    finally:
        soup.clear(decompose=True)
        soup.decompose()


def render_markdown(markdown):
    # Rendering creates markup for analysis; no scripts, images or links execute.
    markdown = re.sub(r"</br\s*>", "<br>", unpack(markdown), flags=re.I)
    return Document.from_text(
        MarkdownIt("commonmark", {"html": True}).enable("table").render(markdown)
    )


async def github_readme(fetcher, source, html, analyzer=None):
    parsed = urlsplit(source)
    match = re.fullmatch(
        r"/([\w.-]+)/([\w.-]+)(?:/blob/.+/README(?:\.[\w]+)?)?/?", parsed.path, re.I
    )
    if parsed.hostname != "github.com" or not match:
        return html, 0

    async def prepare(body, prefix, find_blob=False):
        if analyzer is not None and hasattr(analyzer, "prepare_readme"):
            return await analyzer.prepare_readme(body, prefix, find_blob)
        return await run_blocking(readme_part, body, prefix, find_blob)

    prefix = f"/{match[1]}/{match[2]}/"
    kind, content = await prepare(html, prefix, "/blob/" not in parsed.path)
    extra = 0
    if kind == "blob":
        final, html = await fetch_document(fetcher, canonical_url(content, source))
        if urlsplit(final).hostname != "github.com" or not urlsplit(
            final
        ).path.startswith(prefix):
            raise DiscoveryError("GitHub's README redirected outside its repository.")
        kind, content = await prepare(html, prefix)
        extra = 1
    if kind == "html":
        return content, extra
    raw = canonical_url(content, source)
    p = urlsplit(raw)
    if not (
        (p.hostname == "github.com" and p.path.startswith(prefix + "raw/"))
        or (p.hostname == "raw.githubusercontent.com" and p.path.startswith(prefix))
    ):
        raise DiscoveryError("GitHub's advertised README URL left its repository.")
    final, markdown = await fetch_document(fetcher, raw)
    p = urlsplit(final)
    if p.hostname not in {
        "github.com",
        "raw.githubusercontent.com",
    } or not p.path.startswith(prefix):
        raise DiscoveryError("GitHub's README redirected outside its repository.")
    if analyzer is not None and hasattr(analyzer, "prepare_markdown"):
        return await analyzer.prepare_markdown(markdown), extra + 1
    return await run_blocking(render_markdown, markdown), extra + 1
