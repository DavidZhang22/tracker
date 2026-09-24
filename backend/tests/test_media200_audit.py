import gzip
import json
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from bs4 import BeautifulSoup

from app.tracker.context_model import NUMERIC_FEATURES
from app.tracker.link_context import context_candidates
from app.tracker.parser import parse_feed
from app.tracker.source_registry import SourceRegistry, timestamp
from ml.artifacts import read_json
from ml.media_audit.collect import classify, run
from ml.media_audit.corpus import fingerprint, weak_label
from ml.media_audit.inventory import family

ROOT = Path(__file__).resolve().parents[1]


def test_inventory_splits_and_compressed_rows_are_consistent():
    inventory = read_json(ROOT / "ml/datasets/media200-inventory.json")
    assert len(inventory) == len({r["site_family"] for r in inventory}) == 200
    sources = {r["id"]: r for r in inventory}
    urls = set()
    features = set()
    splits = defaultdict(set)
    with gzip.open(
        ROOT / "ml/datasets/media200-links.jsonl.gz", "rt", encoding="utf8"
    ) as stream:
        for line in stream:
            row = json.loads(line)
            source = sources[row["source_id"]]
            assert not source["existing"]
            assert row["split"] == source["split"]
            assert row["site_family"] == family(source["url"])
            assert len(row["features"]) == len(NUMERIC_FEATURES)
            assert row["label_quality"].startswith("weak")
            assert row["url"] not in urls and fingerprint(row) not in features
            urls.add(row["url"])
            features.add(fingerprint(row))
            splits[row["site_family"]].add(row["split"])
    assert all(len(values) == 1 for values in splits.values())
    assert len(splits) > 100


def test_weak_labels_abstain_on_ambiguous_links():
    soup = BeautifulSoup(
        '<nav><a href="/news">News</a></nav><article><h2><a href="/posts/long-public-story">A long example article headline</a></h2><a href="/authors/test">Some Person</a></article>',
        "html.parser",
    )
    rows = list(context_candidates(soup, "https://publisher.example.org/"))
    labels = {r["url"]: weak_label(r, "https://publisher.example.org/") for r in rows}
    assert labels["https://publisher.example.org/news"][0] == 0
    assert labels["https://publisher.example.org/posts/long-public-story"][0] == 1
    assert labels["https://publisher.example.org/authors/test"] is None


def test_successful_feed_alternatives_have_offline_parser_replays(tmp_path):
    fixtures = read_json(ROOT / "ml/datasets/media200-feeds.json.gz")
    assert len(fixtures) == 8
    registry = SourceRegistry(tmp_path / "registry.sqlite3")
    observed = {
        row["host"]: timestamp(row["checked_at"]) + 600
        for row in read_json(ROOT / "app/tracker/source_status.json")
    }
    for fixture in fixtures:
        root = ET.Element("rss", version="2.0")
        channel = ET.SubElement(root, "channel")
        ET.SubElement(channel, "title").text = fixture["title"]
        for entry in fixture["entries"]:
            node = ET.SubElement(channel, "item")
            for key, tag in [
                ("title", "title"),
                ("url", "link"),
                ("published_at", "pubDate"),
            ]:
                if entry[key]:
                    ET.SubElement(node, tag).text = entry[key]
        scan, _ = parse_feed(ET.tostring(root, encoding="unicode"), fixture["url"])
        assert {e.url for e in scan.entries} == {e["url"] for e in fixture["entries"]}
        assert len(scan.entries) == len(fixture["entries"])
        assert registry.lookup(fixture["url"]) is None
        status = registry.lookup(
            "https://" + fixture["parent_host"], now=observed[fixture["parent_host"]]
        )
        assert status and any(
            alt["url"] == fixture["url"] for alt in status["alternatives"]
        )


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"status": "robots-unavailable", "error": "HTTP 403"}, "robots_unavailable"),
        ({"status": "unavailable", "error": "HTTP 403"}, "access_blocked"),
        ({"status": "unavailable", "error": "HTTP 429"}, "temporarily_unavailable"),
        ({"status": "unavailable", "error": "ReadTimeout"}, "temporarily_unavailable"),
        (
            {
                "status": "unavailable",
                "error": "Cross-host redirect left the reviewed site",
            },
            "unreviewed_redirect",
        ),
        ({"status": "robots-disallowed"}, "robots_disallowed"),
    ],
)
def test_access_failure_classes_are_not_blanket_domain_bans(row, expected):
    assert classify(row) == expected


async def test_audit_does_not_fetch_without_opt_in_or_repeat_finished_rows(
    tmp_path, monkeypatch
):
    from ml.media_audit import collect

    directory = ROOT / "data/media200/test-no-network"
    manifest = tmp_path / "sources.json"
    manifest.write_text(
        json.dumps(
            [dict(id="media200-001", url="https://example.org", status="pending")]
        )
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network collection")

    monkeypatch.setattr(collect, "collect", forbidden)
    await run(manifest, directory, fetch=False)
    manifest.write_text(
        json.dumps(
            [
                dict(
                    id="media200-001",
                    url="https://example.org",
                    status="access_blocked",
                )
            ]
        )
    )
    await run(manifest, directory, fetch=True)
