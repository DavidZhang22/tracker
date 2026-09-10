"""Load a repository's advertised README, with no repository or job crawl."""

import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from .urls import DiscoveryError, canonical_url


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


async def github_readme(fetcher, source, html):
    parsed = urlsplit(source)
    match = re.fullmatch(
        r"/([\w.-]+)/([\w.-]+)(?:/blob/.+/README(?:\.[\w]+)?)?/?", parsed.path, re.I
    )
    if parsed.hostname != "github.com" or not match:
        return html, 0
    prefix = f"/{match[1]}/{match[2]}/"
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.markdown-body")
    if article:
        return str(article), 0
    extra = 0
    if "/blob/" not in parsed.path:
        target = next(
            (
                a.get("href")
                for a in soup.select("a[href]")
                if a.get("href", "").startswith(prefix + "blob/")
                and re.search(r"/README(?:\.[\w]+)?$", a.get("href", ""), re.I)
            ),
            None,
        )
        if not target:
            return html, 0
        final, html = await fetcher.get(canonical_url(target, source))
        if urlsplit(final).hostname != "github.com" or not urlsplit(
            final
        ).path.startswith(prefix):
            raise DiscoveryError("GitHub's README redirected outside its repository.")
        soup = BeautifulSoup(html, "html.parser")
        extra += 1
    article = soup.select_one("article.markdown-body")
    if article:
        return str(article), extra
    objects = list(embedded_objects(soup))
    rich = next(
        (
            obj.get("richText")
            for obj in objects
            if isinstance(obj.get("richText"), str)
            and obj.get("richText")
            and not obj.get("truncated")
        ),
        None,
    )
    if rich and not any(
        obj.get("truncated") or obj.get("isTruncated") for obj in objects
    ):
        return rich, extra
    raw = next(
        (
            obj.get("rawBlobUrl")
            for obj in objects
            if isinstance(obj.get("rawBlobUrl"), str)
        ),
        None,
    )
    if not raw:
        raise DiscoveryError(
            "GitHub did not provide its README content. Saved links were kept."
        )
    raw = canonical_url(raw, source)
    p = urlsplit(raw)
    if not (
        (p.hostname == "github.com" and p.path.startswith(prefix + "raw/"))
        or (p.hostname == "raw.githubusercontent.com" and p.path.startswith(prefix))
    ):
        raise DiscoveryError("GitHub's advertised README URL left its repository.")
    final, markdown = await fetcher.get(raw)
    p = urlsplit(final)
    if p.hostname not in {
        "github.com",
        "raw.githubusercontent.com",
    } or not p.path.startswith(prefix):
        raise DiscoveryError("GitHub's README redirected outside its repository.")
    # Rendering only creates parseable markup. No scripts, images or links execute.
    markdown = re.sub(r"</br\s*>", "<br>", markdown, flags=re.I)
    return MarkdownIt("commonmark", {"html": True}).enable("table").render(
        markdown
    ), extra + 1
