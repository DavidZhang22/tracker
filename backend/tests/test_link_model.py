import json
import math
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup

from app.tracker import link_model
from app.tracker.link_model import FEATURES, LinkModel, candidates, load_model
from app.tracker.models import sequence_value
from app.tracker.parser import parse_page


def test_model_is_bounded_and_uses_only_numeric_parameters():
    model = load_model()
    assert model is not None
    assert link_model.MODEL_PATH.stat().st_size < 20_000
    assert len(FEATURES) == 47
    assert 0 <= model.score([0.0] * len(FEATURES)) <= 1
    with pytest.raises(ValueError):
        model.score([math.nan] * len(FEATURES))


def test_features_do_not_encode_hostnames_or_literal_titles():
    html = '<article><h2><a href="/blog/a-new-story">A new story</a></h2><time datetime="2025-03-01">March 1</time></article>'
    first = list(
        candidates(BeautifulSoup(html, "html.parser"), "https://one.example/")
    )[0][3]
    second = list(
        candidates(BeautifulSoup(html, "html.parser"), "https://two.example/")
    )[0][3]
    assert first == second
    assert all(0 <= x <= 1 for x in first)


def test_missing_or_malformed_model_falls_back_without_failing_scan(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRACKER_LINK_MODEL", "legacy")
    monkeypatch.setattr(link_model, "MODEL_PATH", tmp_path / "missing.json")
    load_model.cache_clear()
    try:
        assert load_model() is None
        scan = parse_page(
            '<article><h2><a href="/story">One story</a></h2></article>',
            "https://example.com/",
        )[0]
        assert len(scan.entries) == 1
        link_model.MODEL_PATH.write_text('{"version":999}')
        load_model.cache_clear()
        assert load_model() is None
    finally:
        load_model.cache_clear()


def test_explicit_selector_and_feed_do_not_consult_model(monkeypatch):
    def fail(*args):
        raise AssertionError("Model must not override explicit or structured sources")

    monkeypatch.setattr("app.tracker.parser.page_scores", fail)
    scan = parse_page(
        '<a href="/custom">My chosen link</a>', "https://example.com/", "a"
    )[0]
    assert len(scan.entries) == 1
    feed = "<rss><channel><title>Feed</title><item><title>Post</title><link>https://example.com/post</link></item></channel></rss>"
    assert len(parse_page(feed, "https://example.com/feed")[0].entries) == 1


def test_model_candidate_budget_and_unsafe_url_exclusion():
    soup = BeautifulSoup(
        '<a href="javascript:alert(1)">No</a>'
        + "".join(f'<a href="/{i}">Story {i}</a>' for i in range(10)),
        "html.parser",
    )
    rows = list(candidates(soup, "https://example.com/", limit=5))
    assert len(rows) == 4
    assert all(url.startswith("https://example.com/") for _, url, _, _ in rows)


def test_archive_month_is_not_a_chapter_number():
    assert sequence_value("March 2026", "https://example.com/blog/2026/mar") is None
    assert sequence_value("Chapter 2026") == 2026


def test_assistance_rescues_article_collection_and_rejects_calendar_indexes(
    monkeypatch,
):
    html = (
        '<h2><a href="/weblog/2025/jun/03/a">A fresh announcement</a></h2><h2><a href="/weblog/2025/jun/04/b">Another announcement</a></h2><ul>'
        + "".join(
            f'<li><a href="/weblog/{y}/mar">March {y}</a></li><li><a href="/weblog/{y}/jan">January {y}</a></li>'
            for y in range(2010, 2025)
        )
        + "</ul>"
    )
    monkeypatch.setenv("TRACKER_LINK_MODEL", "on")
    entries = parse_page(html, "https://example.com/weblog/")[0].entries
    assert {e.url for e in entries} == {
        "https://example.com/weblog/2025/jun/03/a",
        "https://example.com/weblog/2025/jun/04/b",
    }


def test_dataset_site_split_and_feature_schema_are_consistent():
    path = Path(__file__).resolve().parents[1] / "ml/dataset.jsonl"
    groups = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        assert (
            groups.setdefault(urlsplit(row["source"]).hostname, row["split"])
            == row["split"]
        )
        assert len(row["features"]) == len(FEATURES)
        if row["family"] == "synthetic":
            assert row["split"] == "train"


def test_invalid_artifact_cannot_load_large_network():
    payload = json.loads(link_model.MODEL_PATH.read_text())
    payload["layers"][0]["bias"] *= 5
    with pytest.raises(ValueError):
        LinkModel(payload)


def test_high_confidence_external_links_still_obey_url_guards(monkeypatch):
    monkeypatch.setenv("TRACKER_LINK_MODEL", "legacy")

    class Confident:
        lower, upper = 0.03, 0.85

    def scores(soup, source):
        return {id(a): 0.99 for a in soup.select("a")}, Confident()

    monkeypatch.setattr("app.tracker.parser.page_scores", scores)
    html = '<div><a href="https://publisher.example/story">A public article</a><a href="https://publisher.example/login">Sign in</a><a href="javascript:alert(1)">Invalid</a></div>'
    entries = parse_page(html, "https://index.example/")[0].entries
    assert [e.url for e in entries] == ["https://publisher.example/story"]


def test_model_mode_changes_cached_scan_identity(monkeypatch):
    monkeypatch.setenv("TRACKER_LINK_MODEL", "on")
    enabled = link_model.model_cache_tag()
    monkeypatch.setenv("TRACKER_LINK_MODEL", "off")
    assert enabled != link_model.model_cache_tag()
