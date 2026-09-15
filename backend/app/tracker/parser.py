"""Pure parsers. Never execute page scripts or crawl article bodies."""

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .asura import enrich_asura_dates
from .context_model import classify_context
from .dates import DATE_TEXT, evidence, link_date
from .documents import unpack
from .embedded_series import embedded_series
from .keywords import language_text
from .link_model import load_model, page_scores
from .models import Entry, Scan, date_rank, date_value, sequence_value
from .record_context import RecordContext
from .suggestions import observed_sources
from .tables import anchor_label, table_context
from .urls import DiscoveryError, canonical_url, content_key

SKIP = re.compile(
    r"(?:^|/)(?:login|sign-?up|register|privacy|terms|contact|about|search|tag|category|author|user|members|forum|reviews?|comments?|donate|shop|cart)(?:/|$)",
    re.IGNORECASE,
)


def kind_for(url):
    host = urlsplit(url).hostname
    if host in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
        return "youtube"
    if (
        host == "wetriedtls.com"
        or host.endswith(".wetriedtls.com")
        or host in {"fenrirealm.com", "www.fenrirealm.com"}
    ):
        return "novel"
    if host == "royalroad.com" or host.endswith(".royalroad.com"):
        return "novel"
    if host == "asurascans.com" or host.endswith(".asurascans.com"):
        return "comic"
    return "website"


def candidate_url(value, base):
    try:
        return canonical_url(value, base)
    except (ValueError, TypeError):
        return None


def relevant(
    url, source, title, kind, selector=False, external=False, application=False
):
    p, root = urlsplit(url), urlsplit(source)
    if (
        url == source
        or (p.hostname != root.hostname and not external)
        or (
            SKIP.search(p.path)
            and not application
            and not re.search(r"/releases/tag/", p.path)
        )
    ):
        return False
    if re.search(
        r"\.(?:png|jpg|jpeg|gif|svg|css|js|ico|zip|gz|xz|exe|sha256|sha512|json)$",
        p.path,
        re.IGNORECASE,
    ):
        return False
    if kind == "novel":
        if root.hostname in {
            "wetriedtls.com",
            "www.wetriedtls.com",
            "fenrirealm.com",
            "www.fenrirealm.com",
        }:
            return p.path.startswith(root.path.rstrip("/") + "/")
        fiction = re.search(r"/fiction/(\d+)", root.path)
        return bool(
            fiction and re.match(rf"/fiction/{fiction[1]}/[^/]+/chapter/\d+", p.path)
        )
    if kind == "comic":
        slug = root.path.rstrip("/").split("/")[-1]
        return (
            p.path.startswith(root.path.rstrip("/") + "/")
            and bool(re.search(r"/chapter[-/]", p.path))
        ) or (slug in p.path and bool(re.search(r"chapter-\d", p.path)))
    if kind == "youtube":
        return p.path == "/watch" and bool(parse_qs(p.query).get("v"))
    if selector:
        return True
    if re.search(r"/releases/latest/?$", p.path):
        return False  # Moving alias, not a distinct historical release.
    return not (
        len(title.strip()) < 3
        or re.fullmatch(
            r"(?:read more|next|previous|older|newer|\d+|home)",
            title.strip(),
            re.IGNORECASE,
        )
    )


def merge_entries(entries):
    merged = {}
    for entry in entries:
        key = content_key(entry.url)
        old = merged.get(key)
        if old is None:
            entry.position = len(merged)
            merged[key] = entry
        else:
            old.url = entry.url  # Keep the latest observed target for this identity.
            if old.title.lower() in {
                "first chapter",
                "start reading",
                "read more",
            } or re.search(r"\bago$", old.title):
                old.title = entry.title
                old.number = entry.number
            if entry.published_at and date_rank(entry) >= date_rank(old):
                for field in (
                    "published_at",
                    "date_kind",
                    "date_source",
                    "date_precision",
                ):
                    setattr(old, field, getattr(entry, field))
            old.summary = old.summary or entry.summary
            old.language = entry.language or old.language
            old.context = " ".join(dict.fromkeys((old.context, entry.context))).strip()[
                :1800
            ]
            old.availability = entry.availability or old.availability
            old.number = old.number if old.number is not None else entry.number
    return list(merged.values())


def json_objects(text):
    """Read JSON documents/assignment values, including serialized Next flight data."""
    decoder = json.JSONDecoder()
    end, attempt = 0, 0
    for match in re.finditer(r"[\[{]", text):
        if match.start() < end:
            continue
        attempt += 1
        if attempt > 256:
            return
        try:
            value, end = decoder.raw_decode(text, match.start())
            yield value
        except (ValueError, RecursionError):
            continue


def walk(value, depth=0):
    if depth > 35:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child, depth + 1)
    elif isinstance(value, str) and ('"' in value and ("{" in value or "[" in value)):
        for obj in json_objects(value):
            yield from walk(obj, depth + 1)


def parse_feed(text, source):
    text = text.lstrip("\ufeff \t\r\n")
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict) or not str(data.get("version", "")).startswith(
            "https://jsonfeed.org/version/"
        ):
            return None
        scan = Scan(source, data.get("title") or "Feed", "blog", methods=["feed"])
        for item in data.get("items", []):
            target = item.get("url") or item.get("external_url")
            if not target and item.get("attachments"):
                target = item["attachments"][0].get("url")
            u = candidate_url(target, source)
            if not u:
                continue
            title = item.get("title") or item.get("summary") or "Untitled"
            scan.entries.append(
                Entry(
                    u,
                    title,
                    context=" ".join(
                        (
                            BeautifulSoup(
                                str(item.get("summary", ""))[:1500], "html.parser"
                            ).get_text(" ", strip=True),
                            " ".join(str(v)[:80] for v in item.get("tags", [])[:20]),
                            language_text(
                                item.get("language") or data.get("language", "")
                            ),
                        )
                    )[:1800],
                    number=sequence_value(title, u),
                    method="feed",
                    **evidence(
                        item.get("date_published") or item.get("date_modified"),
                        "JSON feed",
                        "published" if item.get("date_published") else "updated",
                    ),
                )
            )
        next_url = candidate_url(data.get("next_url"), source)
        scan.entries = merge_entries(scan.entries)
        return scan, [next_url] if next_url else []
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.IGNORECASE):
        raise DiscoveryError("Feeds containing document entities are not supported.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    if local(root.tag) not in {"rss", "feed", "RDF"}:
        return None
    scan = Scan(source, kind="blog", methods=["feed"])
    links = []
    for node in root.iter():
        tag = local(node.tag)
        if tag == "title" and not scan.title:
            scan.title = node.text or ""
        if tag == "link" and node.get("rel") == "next":
            if u := candidate_url(node.get("href"), source):
                links.append(u)
        if tag not in {"item", "entry"}:
            continue
        children = {local(c.tag): c for c in node}
        title = (
            "".join(children["title"].itertext()).strip()
            if "title" in children
            else "Untitled"
        )
        target = None
        for child in node:
            if (
                local(child.tag) == "link"
                and child.get("rel", "alternate") == "alternate"
            ):
                target = child.get("href") or child.text
                break
        if (
            not target
            and "guid" in children
            and children["guid"].get("isPermaLink", "true") == "true"
        ):
            target = children["guid"].text
        if not target:
            for child in node:
                if local(child.tag) == "enclosure":
                    target = child.get("url")
                    break
        u = candidate_url(target, source)
        if not u:
            continue
        date_key = next(
            (
                k
                for k in ["published", "pubDate", "date", "updated"]
                if k in children and date_value(children[k].text)
            ),
            None,
        )
        date = children[date_key].text if date_key else None
        scan.entries.append(
            Entry(
                u,
                title,
                context=" ".join(
                    BeautifulSoup(
                        " ".join(c.itertext())[:1200], "html.parser"
                    ).get_text(" ", strip=True)
                    for c in list(node)
                    if local(c.tag)
                    in {"category", "summary", "description", "language"}
                )[:1800],
                number=sequence_value(title, u),
                method="feed",
                **evidence(
                    date,
                    "RSS / Atom",
                    "updated" if date_key == "updated" else "published",
                ),
            )
        )
    scan.entries = merge_entries(scan.entries)
    return scan, links


def parse_page(text, source, selector="", include_path="", *, learned=None, trace=None):
    text = unpack(text)
    text = text.lstrip("\ufeff \t\r\n")
    if text.lstrip().startswith("{"):
        feed = parse_feed(text, source)
        if feed:
            return feed[0], feed[1], []
    if text.lstrip().startswith(("<", "<?xml")) and re.search(
        r"<(?:rss|feed|rdf:RDF)\b", text[:1000]
    ):
        feed = parse_feed(text, source)
        if feed:
            return feed[0], feed[1], []
    soup = BeautifulSoup(text, "html.parser")
    del text
    try:
        return _parse_html(soup, source, selector, include_path, learned, trace)
    finally:
        if trace is None or trace.get("soup") is not soup:
            soup.clear(decompose=True)
            soup.decompose()


def _parse_html(soup, source, selector, include_path, learned, trace):
    bindings = None
    if learned is not None:
        from .recipes import bind_recipe

        bindings = bind_recipe(soup, source, learned)
    kind = kind_for(source)
    heading = (
        soup.select_one('meta[property="og:title"]')
        or soup.select_one("h1")
        or soup.title
    )
    title = (
        (heading.get("content") or heading.get_text(" ", strip=True))
        if heading
        else urlsplit(source).hostname
    )
    scan = Scan(source, title, kind, methods=["page"])
    scan.suggestions = observed_sources(soup, source, kind)
    if not selector and (embedded := embedded_series(soup, source, title)):
        embedded.suggestions = scan.suggestions
        if include_path:
            embedded.entries = [e for e in embedded.entries if include_path in e.url]
            if len(embedded.entries) < embedded.expected_count:
                embedded.coverage = "partial"
        return embedded, [], []
    tables = table_context(soup)
    job_anchors = {
        key for key, value in tables.items() if value["job"] and value["action"]
    }
    if job_anchors and not selector:
        scan.order_hint = "source"
    for heading in soup.select("h2, h3, .chapter-count"):
        m = re.search(
            r"([\d,]+)\s+Chapters", heading.get_text(" ", strip=True), re.IGNORECASE
        )
        if m:
            scan.expected_count = int(m[1].replace(",", ""))
            break
    if kind == "novel" and not scan.expected_count:
        m = re.search(r"([\d,]+)\s+Chapters", soup.get_text(" ", strip=True))
        if m:
            scan.expected_count = int(m[1].replace(",", ""))
    pages, feeds, raw = [], [], []
    for link in soup.select("link[href]"):
        u = candidate_url(link.get("href"), source)
        if not u:
            continue
        if "next" in link.get("rel", []):
            pages.append(u)
        if "alternate" in link.get("rel", []) and any(
            t in link.get("type", "") for t in ("rss", "atom", "feed+json")
        ):
            feeds.append(u)
    try:
        anchors = soup.select(selector) if selector else soup.select("a[href]")
    except Exception as exc:
        raise DiscoveryError("The link selector is not valid CSS.") from exc
    # Classify generic anchors before clustering. Structured sources and explicit
    # selectors retain authority. A model decision never creates a crawl request.
    scores, labels, rejected, model = ({}, {}, set(), None)
    fallback_scores, fallback_model = ({}, None)
    if bindings is not None and kind == "website":
        model = SimpleNamespace(primary=True, upper=0.5, lower=0.1)
        scores = {key: 1.0 for key, value in bindings.items() if value}
        rejected = {key for key, value in bindings.items() if not value}
    elif not selector and kind == "website":
        scores, labels, rejected, model = classify_context(
            soup, source, fallback_scores=fallback_scores, tables=tables
        )
        if model is None:
            scores, model = page_scores(soup, source)
        else:
            fallback_model = load_model()
    context_model = bool(getattr(model, "primary", False))
    all_anchors = anchors
    if bindings is None and not selector and kind == "website":
        prefix = urlsplit(source).path.rstrip("/") + "/"
        scoped = []
        for a in anchors:
            u = candidate_url(a.get("href"), source)
            if (
                u
                and prefix != "/"
                and urlsplit(u).path.startswith(prefix)
                and relevant(u, source, a.get_text(" ", strip=True), kind)
            ):
                scoped.append((a, re.sub(r"/[^/]+/?$", "/*", urlsplit(u).path)))
        shapes = Counter(
            shape
            for _, shape in {
                (candidate_url(a.get("href"), source), shape) for a, shape in scoped
            }
        )
        collection = [a for a, shape in scoped if shapes[shape] >= 2]
        if len(collection) >= 2:
            anchors = collection
        else:
            headlines = soup.select(".titleline > a[href],.headline > a[href]")
            if len(headlines) >= 3:
                anchors = headlines
    if job_anchors and not selector:
        anchors = [a for a in all_anchors if id(a) in job_anchors]
    if model:
        selected = {id(a) for a in anchors}
        anchors = [
            a
            for a in all_anchors
            if id(a) in selected or scores.get(id(a), 0) >= model.upper
        ]
    if bindings is not None:
        anchors = [a for a in anchors if bindings.get(id(a))]
    # Pagination is independent of a custom content selector.
    for a in soup.select("a[href]"):
        u = candidate_url(a.get("href"), source)
        if not u or urlsplit(u).hostname != urlsplit(source).hostname:
            continue
        label = a.get_text(" ", strip=True)
        is_pager = bool(a.find_parent(class_=re.compile(r"pag(?:ination|er)")))
        if "next" in a.get("rel", []) or (
            is_pager
            and (
                label.isdigit()
                or re.search(r"next|older|last|[»›]", label, re.IGNORECASE)
            )
        ):
            if kind != "novel" or (
                "review" not in u.lower()
                and urlsplit(u).path.rstrip("/") == urlsplit(source).path.rstrip("/")
            ):
                pages.append(u)
        elif re.fullmatch(
            r"(?:older|next)(?:\s+(?:posts?|page|entries))?\s*[→»›]?",
            label,
            re.IGNORECASE,
        ):
            pages.append(u)
    record_cache = {}
    record_context = RecordContext(
        soup,
        {
            key: value["regions"]
            for key, value in bindings.items()
            if value and value["regions"] is not None
        }
        if bindings is not None
        else None,
    )
    traced = {}
    assisted_urls = set()
    has_chapters = kind == "novel" and soup.select_one("#chapters") is not None
    for a in anchors:
        table = tables.get(id(a), {})
        if job_anchors and not selector and id(a) not in job_anchors:
            continue
        model_accepts = bool(model and scores.get(id(a), 0) >= model.upper)
        if id(a) in rejected and not (table.get("job") and table.get("action")):
            continue
        href = a.get("href") or a.get("data-href")
        u = candidate_url(href, source)
        label = anchor_label(a)
        policy = bindings.get(id(a)) if bindings is not None else None
        if policy and policy["label_node"] is not None:
            label = anchor_label(policy["label_node"])
        if context_model and model_accepts:
            label = labels.get(id(a)) or label
        if table.get("job") and table.get("action"):
            label = table["title"]
            model_accepts = (
                True  # Explicit application-column semantics are authoritative.
            )
        if re.fullmatch(
            r"\d+\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago",
            label,
            re.IGNORECASE,
        ):
            continue
        container = a.find_parent(["tr", "article", "li"]) or a
        primary = bool(
            a.find_parent(["h2", "h3"])
            or a.find_parent(class_=re.compile(r"titleline|headline|entry-title"))
        )
        if not selector and kind == "website" and not (context_model and model_accepts):
            if id(container) not in record_cache:
                record_cache[id(container)] = (
                    container.select_one(
                        ".titleline > a[href], .headline > a[href], h2 a[href], h3 a[href]"
                    ),
                    bool(container.select_one(".subtext,.subline,.age"))
                    if container.name == "tr"
                    else False,
                )
            main_link, metadata_row = record_cache[id(container)]
            if main_link and a.get("href") != main_link.get("href"):
                continue
            if not main_link and metadata_row:
                continue
        generic_label = bool(
            re.fullmatch(r"Enter|Read(?: more)?|View(?: details)?", label, re.I)
        )
        if generic_label:
            heading = container.select_one("h2,h3,td")
            if heading:
                label = heading.get_text(" ", strip=True)
                primary = True
        if (
            not u
            or u in pages
            or not relevant(
                u,
                source,
                label,
                kind,
                bool(selector),
                external=primary or bool(selector) or model_accepts,
                application=bool(table.get("job") and table.get("action")),
            )
        ):
            continue
        if include_path and include_path not in u:
            continue
        if (
            not selector
            and not (context_model and model_accepts)
            and (
                a.find_parent(["nav", "footer", "aside"])
                or (a.find_parent("header") and not a.find_parent("article"))
            )
        ):
            continue
        if has_chapters and not a.find_parent(id="chapters"):
            continue
        if policy and policy["date_skip"]:
            date = {}  # Verified Astro publication metadata is applied below.
        elif policy and policy["date_node"] is not None:
            from .dates import node_dates

            date = node_dates(policy["date_node"])
            if date:
                date.update(policy["date_metadata"])
        else:
            date = link_date(a)
        if not date:
            dated_path = re.search(
                r"/((?:19|20)\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|[-_])",
                urlsplit(u).path,
            )
            if dated_path:
                date = evidence(
                    "-".join(dated_path.groups()), "URL path (inferred)", "inferred"
                )
        clean = DATE_TEXT.sub("", label).strip()
        e = Entry(
            u,
            clean or label,
            number=sequence_value(clean, u),
            context=record_context.text(a),
            language=record_context.language(a),
            **date,
        )
        if table.get("job") and table.get("action"):
            e.number = None
            e.summary = table["summary"]
            e.method = "application table"
        # Strong semantic evidence; remaining generic links are clustered below.
        strong = bool(
            selector
            or (table.get("job") and table.get("action"))
            or primary
            or kind != "website"
            or a.find_parent("article")
            or a.find_parent(["h2", "h3"])
            or date
            or e.number is not None
            or re.search(
                r"/(?:posts?|articles?|blog)/|/\d{4}/\d{1,2}/|[?&](?:p|id)=\d+", u
            )
        )
        score = scores.get(id(a))
        if context_model and not model_accepts and fallback_model:
            score = fallback_scores.get(id(a))
            if not strong and score is not None and score < fallback_model.lower:
                continue
            if score is not None and score >= fallback_model.upper:
                assisted_urls.add(u)
                e.method = "page + classifier"
                strong = True
            score = None
        if model and score is not None:
            if not strong and score < model.lower:
                continue
            if score >= model.upper:
                assisted_urls.add(u)
                e.method = "page + classifier"
                strong = True
        if policy:
            e.method = policy["method"]
            strong = True
        raw.append((e, strong))
        if trace is not None:
            traced[id(a)] = (a, e, label, dict(date))

    def shape(e):
        p = urlsplit(e.url)
        return p.hostname + re.sub(r"/[^/]+/?$", "/*", p.path)

    shapes = Counter(shape(e) for e in {e.url: e for e, _ in raw}.values())
    # Prefer a coherent child collection when the source URL scopes it (releases,
    # series, etc.). Otherwise use semantic records before loose path clusters.
    prefix = urlsplit(source).path.rstrip("/") + "/"
    children = [
        e
        for e, _ in raw
        if prefix != "/"
        and urlsplit(e.url).path.startswith(prefix)
        and shapes[shape(e)] >= 2
    ]
    scoped_shapes = {shape(e) for e in children}
    strong_count = sum(strong for _, strong in raw)
    scan.entries = [
        e
        for e, strong in raw
        if (
            shape(e) in scoped_shapes or e.url in assisted_urls
            if len(children) >= 2
            else strong or (strong_count < 3 and shapes[shape(e)] >= 3)
        )
    ]
    for script in [] if selector or job_anchors else soup.select("script"):
        content = script.string or script.get_text()
        if not content or len(content) > 4_000_000:
            continue
        for root in json_objects(content):
            for obj in walk(root):
                for key in ("nextPageUrl", "next_page_url"):
                    if isinstance(obj.get(key), str):
                        next_url = candidate_url(obj[key], source)
                        if (
                            next_url
                            and urlsplit(next_url).hostname == urlsplit(source).hostname
                        ):
                            pages.append(next_url)
                video = obj.get("videoId")
                if (
                    kind == "youtube"
                    and isinstance(video, str)
                    and re.fullmatch(r"[\w-]{11}", video)
                ):
                    t = obj.get("title", {})
                    if isinstance(t, dict):
                        t = t.get("simpleText") or "".join(
                            r.get("text", "") for r in t.get("runs", [])
                        )
                    if t:
                        scan.entries.append(
                            Entry(
                                "https://www.youtube.com/watch?v=" + video,
                                str(t),
                                method="embedded data",
                            )
                        )
                    continue
                u = candidate_url(
                    obj.get("url") or obj.get("href") or obj.get("@id"), source
                )
                t = obj.get("headline") or obj.get("name") or obj.get("title")
                if (
                    not u
                    or not isinstance(t, str)
                    or not relevant(u, source, t, kind, bool(selector))
                ):
                    continue
                if include_path and include_path not in u:
                    continue
                typ = obj.get("@type", "")
                if kind == "website" and not (
                    typ
                    in [
                        "BlogPosting",
                        "Article",
                        "NewsArticle",
                        "PodcastEpisode",
                        "VideoObject",
                    ]
                    or obj.get("datePublished")
                ):
                    continue
                scan.entries.append(
                    Entry(
                        u,
                        t,
                        number=sequence_value(t, u),
                        method="embedded data",
                        **evidence(
                            obj.get("datePublished")
                            or obj.get("published_at")
                            or obj.get("publishedAt"),
                            "embedded publication date",
                        ),
                    )
                )
    scan.entries = merge_entries(scan.entries)
    if enrich_asura_dates(soup, source, scan.entries):
        scan.methods.append("embedded chapter dates")
    if job_anchors and not selector:
        scan.methods.append("application table")
        # Ages order rows across category tables without inventing absolute dates.
        ages = {}
        for a in all_anchors:
            record = tables.get(id(a), {})
            if record.get("job") and record.get("action"):
                age = re.search(r"\bAge: (\d+)\s*(d|w|mo|y)\b", record["summary"], re.I)
                if age:
                    ages[candidate_url(a.get("href"), source)] = (
                        int(age[1])
                        * {"d": 1, "w": 7, "mo": 30, "y": 365}[age[2].lower()]
                    )
        if scan.entries and all(e.url in ages for e in scan.entries):
            scan.entries.sort(key=lambda e: (-ages[e.url], -e.position))
        elif not ages:
            scan.order_hint = ""  # Fully dated tables can use normal date ordering.
        if ages and any(not e.published_at for e in scan.entries):
            scan.warnings.append(
                "This source lists relative ages, not publication dates. Ages are kept with each role; dates remain unknown."
            )
        for index, entry in enumerate(scan.entries):
            entry.position = index
    if any(e.method == "page + classifier" for e in scan.entries):
        scan.methods.append("link classifier")
    if any(e.method == "embedded data" for e in scan.entries):
        scan.methods.append("embedded data")
    if scan.kind == "website" and (feeds or soup.select_one("article")):
        scan.kind = "blog"
    if not pages and re.search(
        r"load\s+more|show\s+more\s+chapters",
        soup.get_text(" ", strip=True),
        re.IGNORECASE,
    ):
        scan.warnings.append(
            "This page has a load-more control. Content fetched only after a click may be missing; use a feed or a more complete archive URL."
        )
    # Follow one forward chain when available, rather than every numbered button.
    next_anchors = soup.select('a[rel~="next"],link[rel~="next"]')
    forward = [candidate_url(a.get("href"), source) for a in next_anchors]
    if not forward:
        forward = [
            candidate_url(a.get("href"), source)
            for a in soup.select("a[href]")
            if re.fullmatch(
                r"(?:Next|Older)(?:\s+(?:page|posts?))?\s*[→»›]?",
                a.get_text(" ", strip=True),
                re.I,
            )
        ]
    if any(u in pages for u in forward):
        pages = [u for u in forward if u in pages]
    if len(feeds) > 1:
        matched = [
            u
            for u in feeds
            if urlsplit(u).path.startswith(urlsplit(source).path.rstrip("/"))
        ]
        feeds = (matched or feeds)[
            :1
        ]  # RSS and Atom usually represent the same collection.
    result = scan, list(dict.fromkeys(pages)), list(dict.fromkeys(feeds))
    if bindings is not None:
        from .recipes import validate_result

        validate_result(result, bindings, learned)
    if trace is not None:
        trace.update(soup=soup, anchors=traced, context=record_context, tables=tables)
    return result
