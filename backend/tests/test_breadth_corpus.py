import hashlib
import json

import pytest
from bs4 import BeautifulSoup

from app.tracker.errors import DiscoveryError
from ml.collect_breadth import ListingFetcher, robots_policy, status_code
from ml.evaluate_breadth import aggregate, checked_scope, load_capture, score_sets


def test_robots_uses_the_actual_product_identity_and_crawl_interval():
    text = "User-agent: Trackify\nDisallow: /private\nCrawl-delay: 9\nUser-agent: *\nAllow: /\n"
    assert robots_policy(text, "https://example.org/private/list") == (False, 9.0)
    assert robots_policy(text, "https://example.org/public") == (True, 9.0)
    assert robots_policy(
        "User-agent: *\nRequest-rate: 1/12", "https://example.org/list"
    ) == (True, 12.0)


def test_missing_robots_status_preserves_wrapped_http_error():
    import httpx

    response = httpx.Response(
        404, request=httpx.Request("GET", "https://example.org/robots.txt")
    )
    original = httpx.HTTPStatusError(
        "missing", request=response.request, response=response
    )
    wrapped = DiscoveryError("missing robots")
    wrapped.__cause__ = original
    assert status_code(wrapped) == 404
    assert status_code(DiscoveryError("refused")) is None


@pytest.mark.asyncio
async def test_collector_does_not_follow_redirects_outside_the_reviewed_host():
    fetcher = ListingFetcher("https://www.example.org/list", None)
    with pytest.raises(DiscoveryError, match="Cross-host redirect"):
        await fetcher._request("https://other.example.org/list")
    assert fetcher.allowed_hosts == {"example.org", "www.example.org"}


def test_scopes_are_source_defined_deduplicated_and_not_model_predictions():
    soup = BeautifulSoup(
        '<nav><a href="/login">Login</a></nav><main><a href="/report/1">A</a><a href="/report/1">A cover</a><a href="/report/2?lang=fr">B</a></main>',
        "html.parser",
    )
    source = {
        "id": "test",
        "url": "https://old.example/",
        "final_url": "https://example.org/",
        "positive_selector": "main a",
        "positive_path": "^/report/",
        "expected_urls": [
            "https://example.org/report/1",
            "https://example.org/report/2?lang=fr",
        ],
    }
    expected, ignored = checked_scope(soup, source)
    assert expected == set(source["expected_urls"]) and not ignored
    source["expected_urls"] = ["https://example.org/report/1"]
    with pytest.raises(ValueError, match="Frozen scope differs"):
        checked_scope(soup, source)


def test_scope_annotations_reject_overlap_and_empty_inventories():
    soup = BeautifulSoup('<a href="/one">One</a>', "html.parser")
    source = {
        "id": "test",
        "url": "https://example.org/",
        "positive_selector": "a",
        "ignore_selector": "a",
        "expected_urls": ["https://example.org/one"],
    }
    with pytest.raises(ValueError, match="overlap"):
        checked_scope(soup, source)
    source["expected_urls"] = []
    with pytest.raises(ValueError, match="nonempty"):
        checked_scope(soup, source)


def test_capture_hashes_are_verified_before_evaluation(tmp_path, monkeypatch):
    from ml import evaluate_breadth

    monkeypatch.setattr(evaluate_breadth, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    raw = b"<main>public listing</main>"
    (tmp_path / "data/one.html").write_bytes(raw)
    source = {
        "id": "test",
        "file": "data/one.html",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert load_capture(source) == raw.decode()
    source["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        load_capture(source)
    source["file"] = "../outside.html"
    with pytest.raises(ValueError, match="outside"):
        load_capture(source)


def test_macro_scores_and_coverage_keep_failed_collections_visible():
    perfect = score_sets({"a", "b"}, {"a", "b"})
    missed = score_sets({"c"}, set())
    noisy = score_sets({"d"}, {"d", "noise"})
    summary = aggregate([perfect, missed, noisy])
    assert summary["pages"] == 3 and summary["correct"] == 3
    assert summary["expected"] == 4 and summary["unwanted"] == 1
    assert summary["clean_complete_pages"] == 1
    assert summary["zero_recall_pages"] == 1
    assert summary["source_macro_f1"] == pytest.approx((1 + 0 + 0.6667) / 3, abs=0.0001)


def test_summary_rejects_same_size_label_changes_and_family_mismatch():
    from ml.summarize_breadth import validate_scope

    source = {
        "id": "test",
        "status": "captured",
        "sha256": "capture",
        "url": "https://example.org/list",
        "site_family": "example.org",
        "expected_urls": ["https://example.org/one"],
    }
    page = {
        "capture_sha256": "capture",
        "site_family": "example.org",
        "source": "https://example.org/list",
        "scope_sha256": hashlib.sha256(
            json.dumps(source["expected_urls"]).encode()
        ).hexdigest(),
        "auxiliary_scope_sha256": hashlib.sha256(b"[]").hexdigest(),
    }
    validate_scope(source, page)
    changed = {**source, "expected_urls": ["https://example.org/two"]}
    with pytest.raises(ValueError, match="does not match"):
        validate_scope(changed, page)
    with pytest.raises(ValueError, match="does not match"):
        validate_scope({**source, "site_family": "different.org"}, page)


def test_summary_macro_does_not_let_a_large_catalog_hide_empty_sites():
    from ml.summarize_breadth import extraction_summary

    result = extraction_summary(
        [
            {"expected": 1000, "correct": 1000, "unwanted": 0, "f1": 1.0},
            {"expected": 1, "correct": 0, "unwanted": 0, "f1": 0.0},
        ]
    )
    assert result["recall"] == 0.999
    assert result["source_macro_f1"] == 0.5
    assert result["zero_recall_pages"] == 1


def test_frozen_auxiliary_scope_cannot_drift():
    source = {
        "id": "test",
        "url": "https://example.org/",
        "positive_selector": "main a",
        "ignore_selector": "aside a",
        "expected_urls": ["https://example.org/one"],
        "ignored_urls": ["https://example.org/older-reference"],
    }
    soup = BeautifulSoup(
        '<main><a href="/one">One</a></main><aside><a href="/new-reference">Ref</a></aside>',
        "html.parser",
    )
    with pytest.raises(ValueError, match="Frozen auxiliary"):
        checked_scope(soup, source)


def test_manifest_bytes_are_stable_across_platforms(tmp_path):
    from ml.collect_breadth import write_manifest

    path = tmp_path / "sources.json"
    write_manifest(path, [{"id": "breadth-example", "title": "Example"}])
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert json.loads(raw) == [{"id": "breadth-example", "title": "Example"}]
