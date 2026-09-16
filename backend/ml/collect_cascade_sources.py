"""Opt-in, one-index-per-host source collection; no detail-page crawling."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from build_v2_dataset import annotated_index

from app.tracker.urls import SafeFetcher

SOURCES = [
    (
        "eff",
        "https://www.eff.org/updates",
        "test",
        "policy",
        r"^/deeplinks/\d{4}/\d{2}/[^/]+/?$",
    ),
    (
        "planetary",
        "https://www.planetary.org/articles",
        "test",
        "space",
        r"^/articles/[^/]+/?$",
    ),
    (
        "freecodecamp",
        "https://www.freecodecamp.org/news/",
        "test",
        "tutorials",
        r"^/news/[^/]+/?$",
    ),
    (
        "mozilla",
        "https://www.mozilla.org/en-US/firefox/releases/",
        "test",
        "releases",
        r"^/en-US/firefox/[\d.]+/releasenotes/?$",
    ),
    (
        "sqlite",
        "https://www.sqlite.org/chronology.html",
        "train",
        "releases",
        r"^/releaselog/.*\.html$",
    ),
    (
        "debian",
        "https://www.debian.org/News/",
        "train",
        "releases",
        r"^/News/\d{4}/\d+",
    ),
    ("arch", "https://archlinux.org/news/", "train", "releases", r"^/news/[^/]+/?$"),
    (
        "usenix",
        "https://www.usenix.org/conference/osdi25/technical-sessions",
        "train",
        "talks",
        r"^/conference/osdi25/presentation/[^/]+/?$",
    ),
    (
        "quanta",
        "https://www.quantamagazine.org/archive/",
        "train",
        "science",
        r"^/[^/]+-\d{8}/?$",
    ),
    ("aeon", "https://aeon.co/essays", "train", "essays", r"^/essays/[^/]+/?$"),
    (
        "atlas",
        "https://www.atlasobscura.com/articles",
        "train",
        "travel",
        r"^/articles/[^/]+/?$",
    ),
    (
        "laphams",
        "https://www.laphamsquarterly.org/roundtable",
        "train",
        "essays",
        r"^/roundtable/[^/]+/?$",
    ),
    (
        "met",
        "https://www.metmuseum.org/exhibitions",
        "train",
        "exhibitions",
        r"^/exhibitions/[^/]+/?$",
    ),
    (
        "mitocw",
        "https://ocw.mit.edu/courses/",
        "train",
        "courses",
        r"^/courses/[^/]+/?$",
    ),
    (
        "smithsonian",
        "https://www.smithsonianmag.com/category/science-nature/",
        "train",
        "science",
        r"^/science-nature/[^/]+/?$",
    ),
    (
        "raspberrypi",
        "https://www.raspberrypi.com/news/",
        "validation",
        "projects",
        r"^/news/[^/]+/?$",
    ),
    (
        "blender",
        "https://www.blender.org/news/",
        "validation",
        "releases",
        r"^/news/[^/]+/?$",
    ),
    (
        "who",
        "https://www.who.int/news",
        "validation",
        "health",
        r"^/news/item/[^/]+/?$",
    ),
    (
        "imperial",
        "https://www.imperial.ac.uk/news/",
        "validation",
        "science",
        r"^/news/articles/(?:[^/]+/)*\d{4}/[^/]+/?$",
    ),
    (
        "nationalgallery",
        "https://www.nationalgallery.org.uk/exhibitions",
        "validation",
        "exhibitions",
        r"^/exhibitions/[^/]+/?$",
    ),
    (
        "nist",
        "https://www.nist.gov/news-events/news",
        "test",
        "science",
        r"^/news-events/news/\d{4}/\d{2}/[^/]+/?$",
    ),
    (
        "cisa",
        "https://www.cisa.gov/news-events/alerts",
        "test",
        "alerts",
        r"^/news-events/alerts/\d{4}/\d{2}/\d{2}/[^/]+/?$",
    ),
    ("fsf", "https://www.fsf.org/news", "test", "announcements", r"^/news/[^/]+/?$"),
    (
        "britishmuseum",
        "https://www.britishmuseum.org/exhibitions-events",
        "test",
        "exhibitions",
        r"^/exhibitions/[^/]+/?$",
    ),
    ("jpl", "https://www.jpl.nasa.gov/news/", "test", "science", r"^/news/[^/]+/?$"),
    ("loc", "https://blogs.loc.gov/", "test", "blogs", r"^/[^/]+/\d{4}/\d{2}/[^/]+/?$"),
]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Make one bounded listing request per source, preserving existing limits and backoff",
    )
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--prefix", default="cascade")
    args = parser.parse_args()
    if not args.prefix.replace("-", "").isalnum():
        parser.error("Prefix must contain only letters, numbers and hyphens")
    directory = ROOT / "data/cascade-sources"
    directory.mkdir(parents=True, exist_ok=True)
    fetcher = SafeFetcher()
    manifest, rows = [], []
    # Do not introduce train/test overlap with existing domain partitions.
    old_hosts = {
        urlsplit(json.loads(line)["source"]).hostname
        for name in ("v3-dataset.jsonl", "generalization.jsonl")
        for line in (ROOT / "ml/datasets" / name)
        .read_text(encoding="utf8")
        .splitlines()
    }
    for name, url, split, family, positive in SOURCES:
        if args.only and name not in args.only:
            continue
        source = dict(
            id="cascade-" + name,
            url=url,
            split=split,
            family=family,
            positive=positive,
            reason="Publisher index scope annotated by URL role; weak labels, not independent human gold.",
        )
        path = directory / (name + ".html")
        if name == "blender":
            source.update(
                positive=".",
                positive_selector=".cards-item-content a[href]",
                external=True,
            )
        elif name in {"eff", "planetary", "freecodecamp"}:
            source.update(
                positive=".",
                positive_selector={
                    "eff": ".node__title a[href]",
                    "planetary": "main h2 a[href]",
                    "freecodecamp": ".post-card-title a[href]",
                }[name],
            )
            source["reason"] = (
                "Reviewed primary collection cards; excludes footer guides and inline references, includes mixed-path publication types. Scope corrected after baseline replay, before candidate comparison; not a pristine blind gold test."
            )
        elif name == "nationalgallery":
            source["positive_selector"] = ".exhibition-card a[href]"
        elif name == "britishmuseum":
            source.update(
                positive=r"^/(?:exhibitions|events)/[^/]+/?$",
                positive_selector=".teaser__title a[href]",
            )
        host = urlsplit(url).hostname.removeprefix("www.")
        if any(
            host == old.removeprefix("www.")
            or host.endswith("." + old.removeprefix("www."))
            or old.removeprefix("www.").endswith("." + host)
            for old in old_hosts
            if old
        ):
            manifest.append(
                source | {"status": "excluded: existing domain/platform family"}
            )
            continue
        try:
            if not path.exists() and args.fetch:
                final, html = await fetcher.get(url)
                if urlsplit(final).hostname != urlsplit(url).hostname:
                    raise ValueError(
                        "Redirected to another host; annotation needs review"
                    )
                path.write_text(html, encoding="utf8")
            html = path.read_text(encoding="utf8")
            soup = BeautifulSoup(html, "html.parser")
            annotated, positives = annotated_index(soup, source)
            rows.extend(annotated)
            manifest.append(
                source
                | dict(
                    status="captured",
                    file="data/cascade-sources/" + path.name,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    rows=len(annotated),
                    expected_urls=sorted(positives),
                    samples=[
                        {"url": r["url"], "text": r["text"], "label": r["label"]}
                        for r in annotated
                        if r["label"]
                    ][:5],
                )
            )
            print(name, split, len(annotated), len(positives), flush=True)
            soup.decompose()
        except Exception as exc:
            manifest.append(
                source
                | {
                    "status": "unavailable",
                    "reason": "No saved capture"
                    if isinstance(exc, FileNotFoundError)
                    else str(exc)[:250],
                }
            )
            print(name, type(exc).__name__, str(exc)[:120], flush=True)
    (ROOT / "ml/datasets" / (args.prefix + "-sources.json")).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf8"
    )
    (ROOT / "ml/datasets" / (args.prefix + "-dataset.jsonl")).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf8"
    )


if __name__ == "__main__":
    asyncio.run(main())
