import hashlib
import json

import pytest

from ml.dataset_provenance import freeze_protocol_document


def proposal(dataset, *, split="train"):
    return {
        "seed": 20260922,
        "splits": {"example.org": split},
        "dataset_sha256": {
            dataset.name: hashlib.sha256(dataset.read_bytes()).hexdigest()
        },
    }


def test_frozen_protocol_accepts_only_newline_equivalence_without_rewriting(tmp_path):
    dataset = tmp_path / "rows.jsonl"
    dataset.write_bytes(b'{"label":1}\r\n{"label":0}\r\n')
    path = tmp_path / "protocol.json"
    original = freeze_protocol_document(path, proposal(dataset), [dataset])
    frozen_bytes = path.read_bytes()
    provenance = (tmp_path / "input-provenance.json").read_bytes()
    dataset.write_bytes(dataset.read_bytes().replace(b"\r\n", b"\n"))
    assert freeze_protocol_document(path, proposal(dataset), [dataset]) == original
    assert path.read_bytes() == frozen_bytes
    assert (tmp_path / "input-provenance.json").read_bytes() == provenance
    dataset.write_bytes(dataset.read_bytes().replace(b"\n", b"\r\n"))
    assert freeze_protocol_document(path, proposal(dataset), [dataset]) == original


def test_changed_label_bytes_are_rejected(tmp_path):
    dataset = tmp_path / "rows.jsonl"
    dataset.write_bytes(b'{"label":1}\r\n')
    path = tmp_path / "protocol.json"
    freeze_protocol_document(path, proposal(dataset), [dataset])
    dataset.write_bytes(b'{"label":0}\n')
    with pytest.raises(ValueError, match="content differs"):
        freeze_protocol_document(path, proposal(dataset), [dataset])


def test_changed_split_is_rejected_even_with_identical_inputs(tmp_path):
    dataset = tmp_path / "rows.jsonl"
    dataset.write_bytes(b'{"label":1}\n')
    path = tmp_path / "protocol.json"
    freeze_protocol_document(path, proposal(dataset), [dataset])
    with pytest.raises(ValueError, match="protocol differs"):
        freeze_protocol_document(path, proposal(dataset, split="test"), [dataset])


def test_provenance_cannot_be_first_created_from_changed_bytes(tmp_path):
    dataset = tmp_path / "rows.jsonl"
    dataset.write_bytes(b'{"label":1}\r\n')
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(proposal(dataset)), encoding="utf8")
    dataset.write_bytes(b'{"label":1}\n')
    with pytest.raises(ValueError, match="Original dataset bytes"):
        freeze_protocol_document(path, proposal(dataset), [dataset])
    assert not (tmp_path / "input-provenance.json").exists()


def test_preexisting_protocol_gains_provenance_without_byte_changes(tmp_path):
    dataset = tmp_path / "rows.jsonl"
    dataset.write_bytes(b'{"label":1}\n')
    path = tmp_path / "protocol.json"
    path.write_bytes(
        json.dumps(proposal(dataset), separators=(",", ":")).encode() + b"\r\n"
    )
    original = path.read_bytes()
    freeze_protocol_document(path, proposal(dataset), [dataset])
    assert path.read_bytes() == original
    assert (tmp_path / "input-provenance.json").exists()


def test_lone_carriage_return_is_not_normalized(tmp_path):
    dataset = tmp_path / "rows.jsonl"
    dataset.write_bytes(b'{"label":1}\r\n')
    path = tmp_path / "protocol.json"
    freeze_protocol_document(path, proposal(dataset), [dataset])
    dataset.write_bytes(b'{"label":1}\r')
    with pytest.raises(ValueError, match="content differs"):
        freeze_protocol_document(path, proposal(dataset), [dataset])
