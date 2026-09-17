"""Hybrid library retrieval. Only the requesting account's rows are candidates."""

import json
import math
import re
from array import array
from contextlib import contextmanager
from threading import Lock

from fastapi import HTTPException
from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

from .guards import RateLimits
from .media_metadata import CONCEPTS, STOP, clean
from .semantic_model import DIMENSIONS, MODEL_VERSION, EncoderUnavailable, encoder
from .semantic_profile import (
    PROFILE_VERSION,
    describe,
    document,
    dot,
    fingerprint,
    words,
)

SEARCH_STOP = STOP - {
    "manga",
    "comic",
    "comics",
    "novel",
    "novels",
    "blog",
    "blogs",
    "posts",
    "chapter",
    "chapters",
    "episode",
    "episodes",
    "reading",
}


class SearchAdmission:
    def __init__(self):
        self.rates = RateLimits()
        self.active = 0

    @contextmanager
    def operation(self, owner):
        if self.active >= 4:
            raise HTTPException(
                429,
                "Search is busy. Please retry shortly.",
                headers={"Retry-After": "1"},
            )
        self.rates.charge([(f"search:{owner}", 90, 60), ("search:global", 300, 60)])
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


def lexical_score(query, row):
    query = " ".join(words(query))
    title = " ".join(words(row.get("title", "")))
    direct = " ".join(
        words(
            f"{row.get('title', '')} {row.get('url', '')} {row.get('source_name', '')}"
        )
    )
    if query and query in direct:
        return 3.0
    terms = [word for word in words(query) if word not in SEARCH_STOP][:20]
    if not terms:
        return 0.0
    text = " ".join(words(document(row)))
    vocab = set(text.split())
    hits = sum(term in vocab for term in terms) / len(terms)
    title_words = title.split()
    fuzzy = min(
        (
            max(
                (
                    DamerauLevenshtein.normalized_similarity(term, word)
                    if min(len(term), len(word)) >= 4
                    else float(term == word)
                    for word in title_words
                ),
                default=0,
            )
            for term in terms
        ),
        default=0,
    )
    if hits == 1:
        return 1.2
    if fuzzy >= 0.78:
        return 1 + fuzzy
    return hits * 0.25


def corrected_query(query, rows, model):
    vocab = set()
    for row in rows:
        vocab.update(
            word
            for word in words(document(row))
            if 4 <= len(word) <= 32 and word not in STOP
        )
    vocab = sorted(vocab)[:10000]

    def correct(match):
        word = match.group().casefold()
        if (
            len(word) < 4
            or word in vocab
            or (model and model.tokenizer.token_to_id(word) is not None)
        ):
            return match.group()
        nearby = process.extract(
            word,
            vocab,
            scorer=DamerauLevenshtein.normalized_similarity,
            score_cutoff=0.78,
            limit=2,
        )
        if nearby and (len(nearby) == 1 or nearby[0][1] > nearby[1][1]):
            return nearby[0][0]
        return match.group()

    return re.sub(r"[^\W_]+", correct, query)


def rank(query, rows, vector=None, *, alternate_query=None, floor=0.30):
    ranked = []
    concepts = {
        tag for tag, pattern in CONCEPTS.items() if re.search(pattern, query, re.I)
    }
    best_dense = 0
    for row in rows:
        lexical = lexical_score(query, row)
        if alternate_query and alternate_query != query:
            # A spelling suggestion must not replace an exact title, URL or source
            # match. Keep suggestions below the original query's direct matches.
            lexical = max(lexical, min(2.0, lexical_score(alternate_query, row)))
        dense = 0
        blob = row.get("semantic_vector")
        if vector is not None and blob and len(blob) == DIMENSIONS * 4:
            stored = array("f")
            stored.frombytes(blob)
            dense = dot(vector, stored)
            if not math.isfinite(dense):
                dense = 0
        best_dense = max(best_dense, dense)
        tags = row.get("search_tags", [])
        tags = json.loads(tags) if isinstance(tags, str) else tags
        overlap = len(concepts & set(tags)) / max(1, len(concepts))
        score = lexical + max(0, dense) * 0.8 + overlap * 0.18
        ranked.append((row["id"], score, lexical, dense))
    threshold = max(floor, best_dense - 0.14)
    return [
        {"id": iid, "score": round(score, 5)}
        for iid, score, lexical, dense in sorted(
            ranked, key=lambda result: (-result[1], result[0])
        )
        if lexical >= 1 or vector is not None and dense >= threshold
    ]


class SemanticSearch:
    def __init__(self, get_encoder=encoder):
        self.get_encoder = get_encoder
        self.profile_lock = Lock()

    def preview(self, payload):
        model = self.get_encoder()
        try:
            description, method = describe(
                payload.get("title", ""), payload.get("source_summary", ""), model
            )
        except EncoderUnavailable:
            description, method = describe(
                payload.get("title", ""), payload.get("source_summary", "")
            )
        return payload | {"description": description, "description_method": method}

    def enrich(self, store, ids=None):
        # Work happens outside SQLite transactions. Conditional commits reject stale results.
        with self.profile_lock:
            model = self.get_encoder()
            version = MODEL_VERSION if model else "text-fallback"
            for row in store.semantic_records(ids=ids):
                if row["deleted"]:
                    continue
                signature = fingerprint(row)
                key = PROFILE_VERSION + ":" + version + ":" + signature
                if row.get("semantic_key") == key or (
                    model is None
                    and row.get("semantic_key", "").startswith(PROFILE_VERSION + ":")
                    and row.get("semantic_key", "").endswith(":" + signature)
                ):
                    continue
                try:
                    automatic, method = describe(
                        row["title"], row["source_summary"], model
                    )
                except EncoderUnavailable:
                    model = None
                    automatic, method = describe(row["title"], row["source_summary"])
                effective = (
                    row["description_override"]
                    if row["description_override"] is not None
                    else automatic
                )
                try:
                    vector = (
                        model.encode([document(row, effective)])[0] if model else None
                    )
                except EncoderUnavailable:
                    model, vector = None, None
                if model is None:
                    key = PROFILE_VERSION + ":text-fallback:" + signature
                packed = array("f", vector).tobytes() if vector is not None else None
                store.save_semantic(
                    row["id"], signature, key, automatic, method, packed
                )
            return model

    def search(self, store, query, trash=False):
        query = clean(query, 200)
        if not query.strip():
            return {"scores": [], "semantic": False}
        model = self.enrich(store) if not trash else self.get_encoder()
        rows = store.semantic_records(trash=trash)
        for row in rows:
            if row.get(
                "semantic_key"
            ) != PROFILE_VERSION + ":" + MODEL_VERSION + ":" + fingerprint(row):
                row["semantic_vector"] = None
        corrected = corrected_query(query, rows, model)
        try:
            vector = model.encode([corrected], query=True)[0] if model else None
        except EncoderUnavailable:
            model, vector = None, None
        scores = rank(
            query,
            rows,
            vector,
            alternate_query=corrected,
            floor=0.48 if model and model.name == "bge-small" else 0.30,
        )
        store.check_active()
        return {"scores": scores, "semantic": model is not None}
