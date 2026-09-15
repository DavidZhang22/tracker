import json
import math
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup

from app.tracker import context_model
from app.tracker.context_model import (
    NUMERIC_FEATURES,
    ContextModel,
    classify_context,
    load_context_model,
)
from app.tracker.link_context import context_candidates, tokens, words
from app.tracker.link_model import model_cache_tag
from app.tracker.parser import parse_page


def test_normalization_and_query_privacy():
    assert words("postTitle%20ＣＨＡＰＴＥＲ42") == ["post", "title", "chapter", "42"]
    encoded = tokens(
        "Chapter 42",
        "https://private-host.example/story/123?token=secret-value&p=987",
        "entryContent",
        "Heading",
    )
    assert "u:num" in encoded and "c:content" in encoded and "q:token" in encoded
    assert not any(
        value in " ".join(encoded)
        for value in ("private-host", "secret-value", "987", "123", "42")
    )


def test_context_scopes_primary_link_and_tracks_duplicate_dates():
    html = '<article><header><h4><a href="/a">A story</a></h4></header><p>See <a href="/reference">a reference</a></p><a href="/a" title="2026-09-01">Full Story</a></article>'
    rows = list(
        context_candidates(BeautifulSoup(html, "html.parser"), "https://example.com/")
    )
    values = [dict(zip(NUMERIC_FEATURES, r["features"], strict=True)) for r in rows]
    assert values[0]["same_url_has_date"] == 1
    assert values[1]["record_other_primary"] == 1
    assert rows[2]["label"] == "A story"
    assert all(math.isfinite(v) and 0 <= v <= 1 for r in rows for v in r["features"])


def test_nested_article_header_and_inline_reference():
    html = (
        '<main><article><header><h2><a href="/blog/2026/09/02/a-story">A new story</a></h2></header><time datetime="2026-09-02">September 2</time><p>'
        + ("Background information is discussed here. " * 25)
        + '<a href="https://reference.example/topic">reference</a></p></article></main>'
    )
    entries = parse_page(html, "https://publisher.example/")[0].entries
    assert [e.url for e in entries] == [
        "https://publisher.example/blog/2026/09/02/a-story"
    ]
    assert entries[0].published_at.startswith("2026-09-02")


def test_features_are_bounded_even_with_deep_layout_and_malicious_links():
    html = (
        "<div>" * 60
        + '<a href="javascript:alert(1)">Invalid</a>'
        + "".join(f'<a href="/post/{i}">Post {i}</a>' for i in range(12))
        + "</div>" * 60
    )
    rows = list(
        context_candidates(
            BeautifulSoup(html, "html.parser"), "https://example.com/", limit=5
        )
    )
    assert len(rows) == 4
    assert all(len(r["tokens"]) <= 220 for r in rows)


@pytest.mark.parametrize("corruption", ["cycle", "oversize", "nan", "features"])
def test_corrupt_export_cannot_execute_or_loop(corruption):
    payload = json.loads(context_model.MODEL_PATH.read_text())
    if corruption == "cycle":
        payload["trees"][0][0][2] = 0
    elif corruption == "oversize":
        payload["trees"] *= 3
    elif corruption == "nan":
        payload["trees"][0][0][1] = float("nan")
    else:
        payload["features"] = []
    with pytest.raises(ValueError):
        ContextModel(payload)


def test_context_model_missing_or_invalid_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(context_model, "MODEL_PATH", tmp_path / "model.json")
    load_context_model.cache_clear()
    try:
        for content in (None, '{"version":999}'):
            if content:
                context_model.MODEL_PATH.write_text(content)
            load_context_model.cache_clear()
            assert load_context_model() is None
            assert parse_page(
                '<h2><a href="/post">A valid post</a></h2>', "https://example.com/"
            )[0].entries
    finally:
        load_context_model.cache_clear()


def test_explicit_selectors_and_structured_sources_bypass_context(monkeypatch):
    def fail(*args):
        raise AssertionError("Classifier must not override explicit data")

    monkeypatch.setattr("app.tracker.parser.classify_context", fail)
    assert parse_page(
        '<a href="/post">My chosen post</a>', "https://example.com/", "a"
    )[0].entries
    assert parse_page(
        "<rss><channel><item><title>Post</title><link>https://example.com/post</link></item></channel></rss>",
        "https://example.com/feed",
    )[0].entries


def test_mode_identity_and_primary_rejects_low_scored_links(monkeypatch):
    tags = []
    html = '<nav><a href="/privacy">Privacy policy</a></nav>'
    for mode in ("on", "primary", "legacy", "off"):
        monkeypatch.setenv("TRACKER_LINK_MODEL", mode)
        tags.append(model_cache_tag())
    assert len(set(tags)) == 4
    monkeypatch.setenv("TRACKER_LINK_MODEL", "primary")
    scores, _, rejected, model = classify_context(
        BeautifulSoup(html, "html.parser"), "https://example.com/"
    )
    assert model and len(scores) == len(rejected) == 1
    assert not parse_page(html, "https://example.com/")[0].entries


def test_frozen_dataset_has_no_host_leak_and_corpus_is_training_only():
    root = Path(__file__).resolve().parents[1]
    groups = {}
    origins = set()
    for line in (root / "ml/datasets/v3-dataset.jsonl").read_text().splitlines():
        row = json.loads(line)
        host = urlsplit(row["source"]).hostname
        assert groups.setdefault(host, row["split"]) == row["split"]
        assert len(row["features"]) == len(NUMERIC_FEATURES)
        assert len(row["tokens"]) <= 220
        if row["origin"] == "cleaneval":
            assert row["split"] == "train"
        if row["origin"] == "cleaneval":
            assert row["label"] == 0
        origins.add(row["origin"])
    assert origins == {"index", "cleaneval", "authored"}
