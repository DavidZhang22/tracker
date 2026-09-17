"""Select one explicit forward chain without guessing URLs or widening filters."""

import re
from itertools import chain
from urllib.parse import parse_qs, urlsplit

from .urls import DiscoveryError, canonical_url

POSITIONS = {
    "page",
    "p",
    "paged",
    "offset",
    "start",
    "start_index",
    "skip",
    "cursor",
    "after",
}
FORWARD = re.compile(
    r"(?:go\s*to\s+)?(?:next|older)(?:\s+(?:page|posts?|entries|results))?\s*[→»›]?",
    re.I,
)


def forward_pages(soup, source, fallback=(), *, same_path=False):
    base = urlsplit(source)
    query = parse_qs(base.query)
    filters = {k: v for k, v in query.items() if k not in POSITIONS}
    choices = {}
    for node in chain(
        soup.find_all("a", href=True), soup.find_all("link", href=True, rel="next")
    ):
        name = (node.get("aria-label") or node.get_text(" ", strip=True)).strip()
        explicit = "next" in node.get("rel", ()) or bool(FORWARD.fullmatch(name))
        pager = node.find_parent(
            class_=re.compile(r"pag(?:ing|ination|er)|page-numbers", re.I)
        )
        if not explicit and not (
            pager and re.fullmatch(r"\d+(?:\s*[-–]\s*\d+)?", name)
        ):
            continue
        try:
            url = canonical_url(node["href"], source)
        except (DiscoveryError, ValueError):
            continue
        p = urlsplit(url)
        if p.netloc != base.netloc or url == canonical_url(source):
            continue
        if same_path and (
            p.path.rstrip("/") != base.path.rstrip("/") or "review" in url.lower()
        ):
            continue
        incoming = parse_qs(p.query)
        if any(k in incoming and incoming[k] != value for k, value in filters.items()):
            continue
        preserved = all(incoming.get(k) == value for k, value in filters.items())
        if not preserved:
            continue
        increments = []
        for key in POSITIONS & incoming.keys():
            values = incoming[key]
            if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 9:
                continue
            current = query.get(key, ["1" if key in {"page", "p", "paged"} else "0"])
            if len(current) == 1 and current[0].isdigit():
                delta = int(values[0]) - int(current[0])
                if delta > 0:
                    increments.append(delta)
        if not explicit and (
            not increments or p.path.rstrip("/") != base.path.rstrip("/")
        ):
            continue
        choices[url] = (not preserved, not explicit, min(increments, default=1_000_000))
    if not choices:
        return [
            url
            for url in fallback
            if urlsplit(url).netloc == base.netloc
            and all(
                parse_qs(urlsplit(url).query).get(k) == value
                for k, value in filters.items()
            )
            and (
                not same_path
                or (
                    urlsplit(url).path.rstrip("/") == base.path.rstrip("/")
                    and "review" not in url.lower()
                )
            )
        ][:1]
    return [min(choices, key=choices.get)]
