import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ml.collect_non_extraction_audit import collect
from ml.evaluate_non_extraction import retrieval, source_grounded

DATA = Path(__file__).resolve().parents[1] / "ml/datasets"


def test_additional_profiles_remain_source_disjoint_and_explicitly_held_out():
    old = []
    for name in (
        "media-profiles.json",
        "media-public-profiles.json",
        "media-final-profiles.json",
    ):
        old.extend(json.loads((DATA / name).read_text(encoding="utf8")))
    hosts = {urlsplit(row["url"]).hostname.removeprefix("www.") for row in old}
    new = json.loads(
        (DATA / "non-extraction-audit-profiles.json").read_text(encoding="utf8")
    )
    assert new
    assert not hosts & {
        urlsplit(row["url"]).hostname.removeprefix("www.") for row in new
    }
    assert all(row["split"] == "additional_source_holdout" for row in new)
    assert len({row["source_id"] for row in new}) == len(new)
    assert all(len(row["sha256"]) == 64 and len(row["entries"]) <= 32 for row in new)


def test_unavailable_targets_do_not_inflate_retrieval_denominators():
    rows = [
        {
            "id": "present",
            "title": "Some book",
            "url": "https://example.org",
            "kind": "novel",
            "search_tags": [],
        }
    ]
    result = retrieval(
        rows,
        [("Unavailable target", ["absent"], "meaning"), ("gibberish", [], "negative")],
        None,
    )
    assert result["skipped_missing_sources"] == ["Unavailable target"]
    assert result["groups"]["negative"]["count"] == 1
    assert "meaning" not in result["groups"]


def test_description_grounding_requires_verbatim_eligible_sentences_in_source_order():
    first = "Researchers explain discoveries in physics and astronomy through accessible interviews."
    second = "Each conversation follows the history of an experiment and the questions it leaves open."
    assert source_grounded(first + " " + second, first + " " + second)
    assert source_grounded(second, first + " " + second)
    assert not source_grounded(second + " " + first, first + " " + second)
    assert not source_grounded(
        "The podcast guarantees excellent scientific advice for every listener.", first
    )


@pytest.mark.asyncio
async def test_collection_reuses_rejected_results_without_network(
    tmp_path, monkeypatch
):
    saved = {
        "source_id": "blocked",
        "url": "https://blocked.example/list",
        "label": "blog",
        "status": "unavailable",
        "error": "HTTP 403",
    }
    (tmp_path / "capture-report.json").write_text(json.dumps([saved]), encoding="utf8")
    calls = []

    class Fetcher:
        def __init__(self, **kwargs):
            pass

        async def get(self, url):
            calls.append(url)
            raise AssertionError("Blocked sources must not be retried")

    monkeypatch.setattr("ml.collect_non_extraction_audit.SafeFetcher", Fetcher)
    assert await collect({"sources": [saved]}, tmp_path, fetch=True) == []
    assert not calls
