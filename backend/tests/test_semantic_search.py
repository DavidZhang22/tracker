import json
import sqlite3
from array import array
from contextlib import ExitStack
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.tracker.semantic_model import DIMENSIONS, MODEL_VERSION, EncoderUnavailable
from app.tracker.semantic_profile import (
    PROFILE_VERSION,
    describe,
    description_sentences,
    fingerprint,
)
from app.tracker.semantic_search import (
    SearchAdmission,
    SemanticSearch,
    corrected_query,
    lexical_score,
    rank,
)
from app.tracker.store import LibraryErased, Store
from tests.test_media_metadata import manga
from tests.test_store_api import add
from tests.test_store_api import client as semantic_client

client = semantic_client


class FakeEncoder:
    name = "minilm-l6"
    tokenizer = Mock(token_to_id=lambda word: 1)

    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append(texts)
        return [[1.0] + [0.0] * (DIMENSIONS - 1) for text in texts]


def test_source_description_removes_boilerplate_and_deduplicates():
    factual = "A comedy about college life and scuba diving in the ocean."
    source = (
        factual + " Subscribe to get the latest updates from our website. " + factual
    )
    assert description_sentences(source) == [factual]
    assert not description_sentences(
        "Grand Blue is a Manga in English and you can read translated chapters here."
    )
    assert describe("Grand Blue", source, FakeEncoder()) == (
        factual,
        "semantic-extractive",
    )
    assert describe("Unknown source", "") == ("", "unavailable")
    text, method = describe("Grand Blue", source)
    assert text == factual and method == "source-excerpt"


def test_summary_is_bounded_and_contains_only_source_sentences():
    source = " ".join(
        f"Researchers are studying the effects of experiment {i} in a laboratory."
        for i in range(100)
    )
    sentences = description_sentences(source)
    text, _ = describe("Research experiments", source, FakeEncoder())
    assert len(text) <= 700 and len(sentences) <= 20
    assert text in source


def test_typo_search_and_generic_titles_do_not_match_partial_stop_words():
    rows = [
        {"id": "manga", "title": "Grand Blue Dreaming", "kind": "comic"},
        {"id": "events", "title": "Exhibitions and events", "kind": "events"},
    ]
    assert rank("grnad blue", rows)[0]["id"] == "manga"
    assert rank("comedy and college life", rows) == []
    assert lexical_score("astronomy and planets", rows[1]) < 1


@pytest.mark.parametrize(
    "field,value,query,suggestion",
    [
        ("title", "Share", "Share", "shark"),
        ("source_name", "Hacker", "Hacker", "hacked"),
        ("url", "https://hacker.example/", "Hacker", "hacked"),
    ],
)
def test_spelling_suggestions_preserve_exact_matches(
    monkeypatch, field, value, query, suggestion
):
    rows = [
        {"id": "original", "title": "Space Digest", field: value},
        {"id": "suggestion", "title": suggestion},
    ]
    assert corrected_query(query, rows, None) == suggestion
    store = Mock()
    store.semantic_records.return_value = rows
    service = SemanticSearch(lambda: None)
    monkeypatch.setattr(service, "enrich", lambda store: None)
    result = service.search(store, query)
    assert [row["id"] for row in result["scores"]] == ["original", "suggestion"]
    assert result["scores"][0]["score"] > result["scores"][1]["score"]


def test_dense_matches_without_shared_words_and_ignores_bad_vector():
    blob = array("f", [1] + [0] * (DIMENSIONS - 1)).tobytes()
    rows = [
        {"id": "a", "title": "Grand Blue", "semantic_vector": blob},
        {"id": "b", "title": "Elsewhere", "semantic_vector": b"invalid"},
    ]
    assert [
        r["id"]
        for r in rank(
            "underwater university adventures", rows, [1] + [0] * (DIMENSIONS - 1)
        )
    ] == ["a"]
    rows[0]["semantic_vector"] = array("f", [float("nan")] * DIMENSIONS).tobytes()
    assert rank("unrelated", rows, [1] * DIMENSIONS) == []


def test_persisted_index_skips_repeated_inference_and_survives_restart(client):
    client.fake.result = manga()
    item = add(client)
    model = FakeEncoder()
    service = SemanticSearch(lambda: model)
    store = client.app.state.store
    service.enrich(store)
    count = len(model.calls)
    reopened = Store(store.path)
    service.enrich(reopened)
    assert len(model.calls) == count
    row = reopened.semantic_records()[0]
    assert len(row["semantic_vector"]) == DIMENSIONS * 4
    assert "semantic_vector" not in reopened.item(item["id"])
    store.update("items", item["id"], {"title": "Changed title"})
    service.enrich(store)
    assert len(model.calls) > count


def test_text_fallback_updates_old_profiles_but_keeps_current_vectors(client):
    client.fake.result = manga()
    item = add(client)
    store = client.app.state.store
    SemanticSearch(FakeEncoder).enrich(store)
    current = store.semantic_records()[0]
    fallback = SemanticSearch(lambda: None)
    fallback.enrich(store)
    assert store.semantic_records()[0] == current
    with store.connection() as db:
        db.execute(
            "UPDATE items SET semantic_key=?,description_auto='Obsolete excerpt' WHERE id=?",
            ("profile-v0:" + MODEL_VERSION + ":" + fingerprint(current), item["id"]),
        )
    fallback.enrich(store)
    updated = store.semantic_records()[0]
    assert updated["semantic_key"] == PROFILE_VERSION + ":text-fallback:" + fingerprint(
        updated
    )
    assert updated["semantic_vector"] is None
    assert (
        updated["description_auto"]
        == describe(updated["title"], updated["source_summary"])[0]
    )


def test_manual_description_survives_refresh_and_null_resets(client):
    client.fake.result = manga()
    item = add(client)
    path = f"/api/items/{item['id']}"
    assert item["description"]
    edited = client.patch(
        path, json={"description_override": "My own description."}
    ).json()
    assert edited["description"] == "My own description."
    assert client.post(path + "/refresh").status_code == 200
    assert client.get(path).json()["description"] == "My own description."
    assert (
        client.patch(path, json={"favorite": True}).json()["description_override"]
        == "My own description."
    )
    assert (
        client.patch(path, json={"description_override": ""}).json()["description"]
        == ""
    )
    reset = client.patch(path, json={"description_override": None}).json()
    assert (
        reset["description"] == item["description"]
        and reset["description_override"] is None
    )
    assert (
        client.patch(path, json={"description_override": "x" * 1201}).status_code == 422
    )


def test_stale_index_write_cannot_undo_concurrent_edits_or_deletion(client):
    item = add(client)
    store = client.app.state.store
    signature = fingerprint(store.semantic_records()[0])
    store.update("items", item["id"], {"description_override": "Keep this"})
    assert not store.save_semantic(
        item["id"], signature, "stale", "Stale text", "test", None
    )
    assert store.item(item["id"])["description"] == "Keep this"
    signature = fingerprint(store.semantic_records()[0])
    store.bulk_selected("items", [item["id"]], "delete")
    assert not store.save_semantic(
        item["id"], signature, "stale", "Stale text", "test", None
    )


def test_search_is_private_and_never_scrapes_or_indexes_trash(client, tmp_path):
    item = add(client)
    calls = list(client.fake.calls)
    service = client.app.state.semantic
    other = Store(tmp_path / "other.sqlite3")
    assert service.search(other, "A blog")["scores"] == []
    response = client.post("/api/search", json={"query": "A blog"})
    assert (
        response.status_code == 200 and response.json()["scores"][0]["id"] == item["id"]
    )
    client.app.state.store.bulk_selected("items", [item["id"]], "delete")
    assert client.post("/api/search", json={"query": "A blog"}).json()["scores"] == []
    model = FakeEncoder()
    SemanticSearch(lambda: model).search(client.app.state.store, "A blog", True)
    assert len(model.calls) == 1  # Query only; Trash is never reindexed.
    assert client.fake.calls == calls


def test_inference_failure_degrades_to_text_without_losing_saved_data(client):
    client.fake.result = manga()
    item = add(client)
    model = Mock(name="minilm-l6")
    model.encode.side_effect = EncoderUnavailable("test failure")
    service = SemanticSearch(lambda: model)
    result = service.search(client.app.state.store, "Grand Blue")
    assert not result["semantic"] and result["scores"][0]["id"] == item["id"]
    assert client.app.state.store.item(item["id"])["total_count"] == 1
    assert service.preview(manga().to_dict())["description"]


def test_stale_vectors_are_not_used_after_a_racing_edit(client, monkeypatch):
    item = add(client)
    model = FakeEncoder()
    service = SemanticSearch(lambda: model)
    store = client.app.state.store
    service.enrich(store)
    store.update("items", item["id"], {"description_override": "A different subject"})
    monkeypatch.setattr(service, "enrich", lambda store: model)
    assert not service.search(store, "underwater adventures")["scores"]


def test_search_limits_and_database_failure_are_explicit(client):
    assert client.post("/api/search", json={"query": "x" * 201}).status_code == 422
    guard = SearchAdmission()
    with ExitStack() as stack:
        for i in range(4):
            stack.enter_context(guard.operation(str(i)))
        with pytest.raises(HTTPException) as busy:
            with guard.operation("overflow"):
                pass
        assert busy.value.status_code == 429
    assert guard.active == 0
    with pytest.raises(sqlite3.OperationalError):
        client.app.state.store.path += "missing"
        client.app.state.semantic.search(client.app.state.store, "Hello")


def test_erased_library_cannot_be_reindexed(client):
    add(client)
    store = client.app.state.store
    store.allow_erased = False
    store.erased_marker.touch()
    with pytest.raises(LibraryErased):
        client.app.state.semantic.enrich(store)


def test_semantic_binary_is_not_in_exportable_item(client):
    item = add(client)
    SemanticSearch(FakeEncoder).enrich(client.app.state.store)
    assert "description" in json.loads(
        json.dumps(client.app.state.store.item(item["id"]))
    )
    row = client.app.state.store.semantic_records()[0]
    assert row[
        "semantic_key"
    ] == PROFILE_VERSION + ":" + MODEL_VERSION + ":" + fingerprint(row)
