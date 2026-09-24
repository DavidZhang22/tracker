"""Select a reproducible category-ranked sample, preserving source ranks."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
MULTIPART = {
    "co.uk",
    "org.uk",
    "com.br",
    "com.au",
    "co.jp",
    "co.in",
    "com.cn",
    "co.nz",
    "co.za",
    "com.sg",
    "com.mx",
    "com.ar",
    "com.tr",
    "net.au",
    "org.au",
    "com.tw",
    "com.hk",
    "co.kr",
}


def family(url):
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    count = 3 if ".".join(parts[-2:]) in MULTIPART else 2
    key = ".".join(parts[-count:])
    return {"bbc.com": "bbc.co.uk"}.get(key, key)


def existing_sources(directory):
    ids = {}
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf8"))
        except ValueError:
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            if (
                isinstance(row, dict)
                and row.get("id")
                and isinstance(row.get("url"), str)
            ):
                ids[row["id"]] = row["url"]
    seen = {}
    for path in sorted(directory.glob("*.jsonl")):
        if path.name.startswith("media200-"):
            continue
        with path.open(encoding="utf8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                values = [
                    row.get(k, "")
                    for k in ("source", "source_url", "page_url", "source_id")
                ]
                for value in values:
                    if not isinstance(value, str):
                        continue
                    url = (
                        value
                        if value.startswith(("http://", "https://"))
                        else ids.get(value, "")
                    )
                    if not url:
                        continue
                    info = seen.setdefault(
                        family(url), {"datasets": set(), "splits": set()}
                    )
                    info["datasets"].add(path.name)
                    info["splits"].add(row.get("split", "unspecified"))
    return {
        key: {field: sorted(values) for field, values in info.items()}
        for key, info in seen.items()
    }


def ranked_lists(directory):
    provenance = {
        r["id"]: r
        for r in json.loads((directory / "sources.json").read_text(encoding="utf8"))
    }
    groups = {}
    for key in (
        "news",
        "blogs",
        "entertainment",
        "technology",
        "gaming",
        "music",
        "books",
        "food",
    ):
        raw = (directory / f"ranking-{key}.html").read_bytes()
        soup = BeautifulSoup(raw.decode("utf8"), "html.parser")
        if key == "news":
            listings = []
            for script in soup.select('script[type="application/ld+json"]'):
                data = json.loads(script.get_text())
                for node in data.get("@graph", []):
                    if node.get("@type") == "ItemList":
                        listings = [
                            (r["url"], r["name"], r["position"])
                            for r in node["itemListElement"]
                        ]
            vintage = "June 2026"
        else:
            anchors = soup.select(".blog-url a[href], .ranking-url a[href]")
            if not anchors and key == "gaming":
                anchors = [
                    a
                    for a in soup.select("table td a[href]")
                    if a.find_previous_sibling("img") or a.parent.name == "img"
                ]
            listings = [
                (a["href"], a.get_text(" ", strip=True), i + 1)
                for i, a in enumerate(anchors)
            ]
            text = soup.get_text(" ", strip=True)
            match = re.search(r"Last updated:\s*(.{1,65}?)\.", text)
            vintage = match[1].strip() if match else "Not stated"
        if not listings:
            raise ValueError("No ranking rows for " + key)
        groups[key] = [
            dict(
                url=url,
                name=name,
                category=key,
                ranking_url=provenance["ranking-" + key]["url"],
                category_rank=rank,
                ranking_vintage=vintage,
                ranking_capture_sha256=hashlib.sha256(raw).hexdigest(),
            )
            for url, name, rank in listings
        ]
    groups["media"] = [
        dict(
            url="https://" + domain + "/",
            name=domain,
            category="media",
            ranking_url="https://www.similarweb.com/top-websites/arts-and-entertainment/",
            category_rank=i + 1,
            ranking_vintage="August 2026",
        )
        for i, domain in enumerate(
            ("youtube.com", "netflix.com", "bilibili.com", "fandom.com", "imdb.com")
        )
    ]
    return groups


def main():
    directory = ROOT / "data/media200/rankings"
    groups = ranked_lists(directory)
    known = existing_sources(ROOT / "ml/datasets")
    order = groups["news"] + groups["media"]
    # Round-robin within category rank avoids letting the longest list dominate.
    for index in range(max(map(len, groups.values()))):
        for key in (
            "blogs",
            "entertainment",
            "technology",
            "gaming",
            "music",
            "books",
            "food",
        ):
            if index < len(groups[key]):
                order.append(groups[key][index])
    selected = {}
    for source in order:
        key = family(source["url"])
        if key in selected:
            selected[key]["ranking_references"].append(
                {
                    k: v
                    for k, v in source.items()
                    if k.startswith("ranking_") or k in ("category", "category_rank")
                }
            )
            continue
        if len(selected) == 200:
            continue
        bucket = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 10
        selected[key] = dict(
            id="media200-" + str(len(selected) + 1).zfill(3),
            site_family=key,
            url=source["url"],
            name=source["name"],
            category=source["category"],
            ranking_references=[
                {
                    k: v
                    for k, v in source.items()
                    if k.startswith("ranking_") or k in ("category", "category_rank")
                }
            ],
            existing=known.get(key),
            split="existing"
            if key in known
            else "train"
            if bucket < 7
            else "validation"
            if bucket == 7
            else "test",
            status="pending",
        )
    assert len(selected) == 200
    rows = list(selected.values())
    target = ROOT / "ml/datasets/media200-inventory.json"
    target.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf8",
        newline="\n",
    )
    card = dict(
        created_at=datetime.now(timezone.utc).isoformat(),
        scope="200 distinct domain families from public category rankings; not a fabricated single global top-200 rank.",
        selection="All unique families from the public top-100 News/Media list, five leading Arts/Entertainment domains, then round-robin category ranks from public blog lists until 200 families. Ranking provenance and displayed vintage remain attached.",
        news_source="https://onelittleweb.com/digital-market-intelligence/popular-websites/news-and-media/",
        blog_sources={
            key: values[0]["ranking_url"]
            for key, values in groups.items()
            if key not in ("news", "media")
        },
        source_counts={key: len(rows) for key, rows in groups.items()},
        sites=len(rows),
        existing_sites=sum(bool(row["existing"]) for row in rows),
        split_policy="Existing source families are not duplicated or reassigned. New families get a deterministic SHA256-based 70/10/20 train/validation/test assignment before acquisition or predictions. Subdomain families are conservative reviewed public-suffix groupings; no claim of publisher/template independence.",
        limitations=[
            "There is no single public media/content/blog ranking. The mixture is category-stratified, not an absolute traffic-ordered global top 200.",
            "Some ranking pages have older displayed update dates despite a 2026 title. The actual vintage is retained.",
            "Absence from recorded source metadata cannot prove absence from historical synthetic or unrecorded training data.",
        ],
    )
    (ROOT / "ml/datasets/media200-card.json").write_text(
        json.dumps(card, indent=2) + "\n", encoding="utf8", newline="\n"
    )
    print(
        json.dumps(
            {
                "sites": len(rows),
                "existing": card["existing_sites"],
                "groups": card["source_counts"],
            }
        )
    )


if __name__ == "__main__":
    main()
