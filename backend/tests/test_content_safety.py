import json
from pathlib import Path

import pytest

from app.tracker.content_safety import excerpt_risk, public_metadata, public_scan
from app.tracker.models import Entry, Scan
from app.tracker.semantic_profile import describe, document
from app.tracker.semantic_search import SemanticSearch
from app.tracker.store import Store
from tests.test_semantic_search import FakeEncoder
from tests.test_store_api import add
from tests.test_store_api import client as client

FLAGGED = [
    "Explicit adult videos for subscribers.",
    "Download child sexual abuse material.",
    "Revenge porn collection.",
    "Uncensored gore videos.",
    "Real torture footage for members.",
    "Step-by-step bomb-making guide.",
    "Stolen credentials for sale.",
    "Phishing kits for sale.",
    "Non-consensual intimate images.",
    "ＰＯＲＮ videos.",
    "p\u200born videos.",
    "p0rn videos.",
]
BENIGN = [
    "An action novel about battles, magic, war and a dangerous dragon.",
    "A cybersecurity course covering phishing detection and account protection.",
    "Research on pornography and mental health outcomes.",
    "Reporting child sexual abuse material: prevention and survivor support.",
    "A medical course about sexual health and anatomy.",
    "A drug discovery research scientist job with clinical trial experience.",
    "An exhibition on the history of war and its effects on civilians.",
    "Software engineers help detect fraud and protect bank credentials.",
    "A romantic comedy about university students and scuba diving.",
    "A conference about suicide prevention and recovery support.",
]


@pytest.mark.parametrize("text", FLAGGED)
def test_flagged_optional_descriptions_are_omitted_before_inference(text):
    model = FakeEncoder()
    assert describe("Collection", text, model) == ("", "safety-filtered")
    assert model.calls == []


@pytest.mark.parametrize("text", BENIGN)
def test_benign_fiction_jobs_health_and_protective_context_are_not_flagged(text):
    assert excerpt_risk("", text) is None


def test_url_clue_and_entity_encoded_text_are_checked_without_fetching():
    assert excerpt_risk(url="https://example.org/explicit%2520adult%2520videos")
    assert excerpt_risk(text="p&#111;rn videos")


def test_preview_skips_model_loading_and_does_not_mutate_source():
    def unavailable():
        raise AssertionError("Model must not be loaded for a suppressed preview")

    raw = {
        "url": "https://example.org",
        "title": "Collection",
        "source_summary": FLAGGED[0],
        "entries": [
            {
                "url": "https://example.org/1",
                "title": "Entry",
                "summary": FLAGGED[2],
                "context": "Original uploaded column",
            }
        ],
    }
    original = json.dumps(raw)
    prepared = SemanticSearch(unavailable).preview(raw)
    visible = public_scan(prepared)
    assert visible["description"] == visible["source_summary"] == ""
    assert visible["description_suppressed"]
    assert visible["entries"][0]["summary"] == ""
    assert visible["entries"][0]["summary_suppressed"]
    assert visible["entries"][0]["context"] == "Original uploaded column"
    assert json.dumps(raw) == original


def test_legacy_description_is_hidden_and_manual_description_is_preserved(tmp_path):
    store = Store(tmp_path / "library.db")
    item = store.create(
        store.save_scan(
            Scan(
                "https://example.org",
                "Collection",
                entries=[
                    Entry(
                        "https://example.org/1",
                        "One",
                        summary=FLAGGED[0],
                        context="Original field",
                    )
                ],
            ).to_dict()
        )
    )
    with store.connection() as db:
        db.execute(
            "UPDATE items SET source_summary=?,description_auto=?,description_method='source-excerpt' WHERE id=?",
            (FLAGGED[0], FLAGGED[0], item["id"]),
        )
    current = store.item(item["id"])
    assert current["description_suppressed"] and current["description"] == ""
    assert current["description_auto"] == current["source_summary"] == ""
    service = SemanticSearch(lambda: None)
    service.enrich(store)
    stored = store.semantic_records()[0]
    assert (
        stored["description_auto"] == ""
        and stored["description_method"] == "safety-filtered"
    )
    assert FLAGGED[0] not in document(stored)
    store.update("items", item["id"], {"description_override": "My private note."})
    service.enrich(store)
    assert store.item(item["id"])["description"] == "My private note."
    assert "My private note." in document(store.semantic_records()[0])
    entry = store.links(item["id"])["links"][0]
    assert entry["summary"] == "" and entry["summary_suppressed"]
    assert (
        entry["context"] == "Original field" and entry["url"] == "https://example.org/1"
    )


def test_model_availability_cannot_bypass_suppression_and_refresh_can_restore_safe_text(
    tmp_path,
):
    store = Store(tmp_path / "library.db")
    item = store.create(
        store.save_scan(
            Scan(
                "https://example.org", "Collection", source_summary=FLAGGED[0]
            ).to_dict()
        )
    )
    service = SemanticSearch(FakeEncoder)
    service.enrich(store)
    assert store.item(item["id"])["description"] == ""
    with store.connection() as db:
        db.execute(
            "UPDATE items SET source_summary=? WHERE id=?", (BENIGN[0], item["id"])
        )
    service.enrich(store)
    assert store.item(item["id"])["description"] == BENIGN[0]
    assert not store.item(item["id"])["description_suppressed"]


def test_suppressed_scan_item_and_group_members_are_not_exposed_in_api(client):
    client.fake.result = Scan(
        "https://example.org",
        "Collection",
        source_summary=FLAGGED[0],
        entries=[
            Entry("https://example.org/1", "One", summary=FLAGGED[2]),
            Entry("https://example.org/2", "Two", summary=FLAGGED[0]),
        ],
    )
    item = add(client)
    iid = item["id"]
    assert item["description_suppressed"] and item["description"] == ""
    links = client.get(f"/api/items/{iid}/links?direction=asc").json()["links"]
    client.post(
        f"/api/items/{iid}/link-groups",
        json={"ids": [links[1]["id"]], "action": "merge", "direction": "asc"},
    )
    entry = client.get(f"/api/items/{iid}/links").json()["links"][0]
    assert all(member["summary"] == "" for member in entry["members"])
    assert len(client.fake.calls) == 1


def test_public_metadata_does_not_rewrite_titles_urls_or_progress():
    row = {
        "title": FLAGGED[0],
        "url": "https://example.org",
        "read": True,
        "favorite": True,
        "ignored": True,
        "summary": "Collection description",
        "context": "Uploaded column",
    }
    result = public_metadata(row)
    assert result == row | {"summary": "", "summary_suppressed": True}


def test_screening_has_no_false_flags_on_previous_public_profile_corpus():
    root = Path(__file__).resolve().parents[1] / "ml/datasets"
    rows = []
    for name in (
        "media-profiles.json",
        "media-public-profiles.json",
        "media-final-profiles.json",
    ):
        rows.extend(json.loads((root / name).read_text(encoding="utf-8")))
    assert len(rows) >= 69
    assert not [
        row["source_id"]
        for row in rows
        if excerpt_risk(row.get("title"), row.get("source_summary"), row.get("url"))
    ]


def test_safe_legacy_vectors_upgrade_without_reencoding_while_flagged_ones_do_not(
    tmp_path,
):
    from app.tracker.semantic_model import MODEL_VERSION
    from app.tracker.semantic_profile import PROFILE_VERSION, fingerprint

    store = Store(tmp_path / "legacy.db")
    item = store.create(
        store.save_scan(
            Scan(
                "https://example.org", "Collection", source_summary=BENIGN[0]
            ).to_dict()
        )
    )
    model = FakeEncoder()
    service = SemanticSearch(lambda: model)
    service.enrich(store)
    row = store.semantic_records()[0]
    with store.connection() as db:
        db.execute(
            "UPDATE items SET semantic_key=? WHERE id=?",
            (
                "profile-v1:" + MODEL_VERSION + ":" + fingerprint(row, legacy=True),
                item["id"],
            ),
        )
    calls = len(model.calls)
    service.enrich(store)
    upgraded = store.semantic_records()[0]
    assert len(model.calls) == calls
    assert upgraded["semantic_vector"] == row["semantic_vector"]
    assert upgraded["semantic_key"].startswith(PROFILE_VERSION + ":")
    with store.connection() as db:
        db.execute(
            "UPDATE items SET source_summary=?,description_auto=? WHERE id=?",
            (FLAGGED[0], FLAGGED[0], item["id"]),
        )
    changed = store.semantic_records()[0]
    with store.connection() as db:
        db.execute(
            "UPDATE items SET semantic_key=? WHERE id=?",
            (
                "profile-v1:" + MODEL_VERSION + ":" + fingerprint(changed, legacy=True),
                item["id"],
            ),
        )
    service.enrich(store)
    assert store.semantic_records()[0]["description_auto"] == ""
    assert len(model.calls) > calls


def test_legacy_excerpt_checked_even_when_source_reaches_text_limit():
    row = {
        "title": "Archive",
        "source_summary": "a" * 8192,
        "description_auto": "Stolen credentials for sale",
        "description_override": None,
    }
    result = public_metadata(row)
    assert result["description_suppressed"] is True
    assert result["description_auto"] == ""
