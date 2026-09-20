import json
from pathlib import Path

import numpy as np
import pytest

from app.tracker import media_classifier
from app.tracker.media_features import FEATURE_COUNT, vector
from app.tracker.media_metadata import (
    MAX_SAMPLE,
    annotate,
    baseline_classify,
    classify,
    evidence_text,
    sample_entries,
)

DATA = Path(__file__).resolve().parents[1] / "ml/datasets"


def profiles():
    result = []
    for name in (
        "media-profiles.json",
        "media-public-profiles.json",
        "media-final-profiles.json",
    ):
        result.extend(json.loads((DATA / name).read_text(encoding="utf8")))
    return {row["source_id"]: row for row in result}


@pytest.mark.parametrize(
    "source,expected",
    [
        ("pythonbytes", "podcast"),
        ("talkpython", "podcast"),
        ("w3c-standards", "research"),
        ("cascade-nationalgallery", "events"),
        ("media-questionablecontent", "comic"),
        ("media-darknetdiaries", "podcast"),
        ("media-neurips", "research"),
    ],
)
def test_learned_format_recovers_known_public_failures(source, expected):
    row = profiles()[source]
    assert baseline_classify(row) != expected
    assert classify(row) == expected


@pytest.mark.parametrize(
    "topic",
    [
        "manga",
        "music",
        "podcasts",
        "jobs",
        "research papers",
        "courses",
        "software releases",
    ],
)
def test_articles_about_a_medium_keep_the_article_format(topic):
    row = {
        "url": "https://independent.example/news",
        "title": f"{topic} industry blog",
        "source_summary": f"News articles about {topic}. Commentary and interviews.",
        "kind": "blog",
        "entries": [
            {
                "title": f"Annual {topic} industry report",
                "url": f"https://independent.example/articles/report-{index}",
            }
            for index in range(3)
        ],
    }
    assert classify(row) == "blog"


def test_media_annotation_reuses_inputs_and_does_not_mutate_progress():
    row = profiles()["media-darknetdiaries"]
    row["entries"][0].update(read=True, favorite=True, ignored=True)
    original = json.dumps(row, sort_keys=True)
    tagged = annotate(row, "novel")
    assert tagged["kind"] == "novel" and tagged["detected_kind"] == "podcast"
    assert tagged["entries"] == row["entries"]
    assert json.dumps(row, sort_keys=True) == original


@pytest.mark.parametrize("kind", ["youtube", "comic", "novel"])
def test_reliable_source_kind_does_not_load_a_second_model(monkeypatch, kind):
    def forbidden():
        raise AssertionError("Unnecessary classifier load")

    monkeypatch.setattr(media_classifier, "load", forbidden)
    row = {
        "url": "https://source.example",
        "title": "All releases",
        "kind": kind,
        "entries": [],
    }
    assert classify(row) == baseline_classify(row)


def test_missing_model_preserves_conservative_baseline(monkeypatch):
    def unavailable():
        raise OSError("Artifact unavailable")

    monkeypatch.setattr(media_classifier, "load", unavailable)
    row = profiles()["pythonbytes"]
    assert classify(row) == baseline_classify(row)


def test_model_uses_a_bounded_sample_and_small_readonly_arrays():
    row = profiles()["media-neurips"]
    row["entries"] = row["entries"] * 1000
    sampled = sample_entries(row["entries"])
    values = vector(row, evidence_text(row, sampled), sampled)
    assert len(sampled) == MAX_SAMPLE and len(values) == FEATURE_COUNT
    assert all(0 <= value <= 1 for value in values)
    _, weights, bias, _, _ = media_classifier.load()
    assert weights.nbytes + bias.nbytes < 5000
    assert not weights.flags.writeable and not bias.flags.writeable


@pytest.mark.parametrize("corruption", ["nan", "shape", "classes", "threshold"])
def test_invalid_numeric_artifact_is_rejected(tmp_path, monkeypatch, corruption):
    original = Path(media_classifier.__file__).with_name("media_classifier.json")
    model = json.loads(original.read_text())
    if corruption == "nan":
        model["weights"][0][0] = float("nan")
    elif corruption == "shape":
        model["weights"][0].pop()
    elif corruption == "classes":
        model["classes"][0] = "unknown"
    else:
        model["minimum_probability"] = 0
    file = tmp_path / "media_classifier.json"
    file.write_text(json.dumps(model))
    monkeypatch.setattr(media_classifier, "Path", lambda _: file)
    media_classifier.load.cache_clear()
    try:
        with pytest.raises(ValueError):
            media_classifier.load()
    finally:
        media_classifier.load.cache_clear()


def test_host_names_are_not_model_features():
    first = profiles()["pythonbytes"]
    second = json.loads(json.dumps(first).replace("pythonbytes.fm", "unknown.example"))
    assert np.array_equal(
        vector(first, evidence_text(first), sample_entries(first["entries"])),
        vector(second, evidence_text(second), sample_entries(second["entries"])),
    )
