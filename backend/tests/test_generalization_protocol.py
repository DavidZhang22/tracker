import gzip
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


def test_compressed_protocol_and_provenance_remain_frozen(tmp_path):
    dataset = tmp_path / "rows.jsonl"
    dataset.write_bytes(b'{"label":1}\r\n')
    path = tmp_path / "protocol.json"
    original = freeze_protocol_document(path, proposal(dataset), [dataset])
    stored = {}
    for artifact in (path, tmp_path / "input-provenance.json"):
        compressed = artifact.with_name(artifact.name + ".gz")
        stored[compressed] = gzip.compress(artifact.read_bytes(), mtime=0)
        compressed.write_bytes(stored[compressed])
        artifact.unlink()
    dataset.write_bytes(b'{"label":1}\n')
    assert freeze_protocol_document(path, proposal(dataset), [dataset]) == original
    assert not path.exists()
    assert not (tmp_path / "input-provenance.json").exists()
    assert all(path.read_bytes() == raw for path, raw in stored.items())
    with pytest.raises(ValueError, match="protocol differs"):
        freeze_protocol_document(path, proposal(dataset, split="test"), [dataset])


def test_compressed_baseline_does_not_fall_back_to_current_model(tmp_path, monkeypatch):
    from pathlib import Path

    from ml import train_breadth_generalization as trainer

    model_path = (
        Path(trainer.__file__).resolve().parents[1]
        / "app/tracker/link-cascade-model.json"
    )
    raw = model_path.read_bytes()
    compressed = tmp_path / "baseline-cascade.json.gz"
    compressed.write_bytes(gzip.compress(raw, mtime=0))
    stored = compressed.read_bytes()
    # This root deliberately contains no current model to copy over the baseline.
    monkeypatch.setattr(trainer, "ROOT", tmp_path)
    model = trainer.frozen_baseline(tmp_path)
    assert model.model_id == json.loads(raw)["model_id"]
    assert not (tmp_path / "baseline-cascade.json").exists()
    assert compressed.read_bytes() == stored


def test_compression_preserves_evaluation_fingerprint_and_resume(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ml import evaluate_breadth_upgrade as evaluator

    runtime = tmp_path / "app/tracker"
    runtime.mkdir(parents=True)
    (runtime / "parser.py").write_bytes(b"# unchanged runtime\r\n")
    monkeypatch.setattr(evaluator, "ROOT", tmp_path)
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"
    baseline.write_bytes(b'{"model_id":"baseline"}\r\n')
    candidate.write_bytes(b'{"model_id":"candidate"}\n')
    args = SimpleNamespace(
        baseline=baseline, candidate=candidate, candidate_mode="cascade", repeats=1
    )
    inputs = evaluator.run_inputs(args, {}, [])
    fingerprint = evaluator.digest_json(inputs)
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps({"run_fingerprint": fingerprint, "pages": {"saved": {}}})
    )
    for artifact in (baseline, candidate, report):
        artifact.with_name(artifact.name + ".gz").write_bytes(
            gzip.compress(artifact.read_bytes(), mtime=0)
        )
        artifact.unlink()
    assert evaluator.run_inputs(args, {}, []) == inputs
    assert evaluator.resumed_report(report, fingerprint)["pages"] == {"saved": {}}
    with pytest.raises(ValueError, match="Resume inputs changed"):
        evaluator.resumed_report(report, "changed")


def test_resumed_checkpoint_preserves_compressed_storage(tmp_path):
    from ml import evaluate_breadth_upgrade as evaluator
    from ml.artifacts import compress, read_json

    path = tmp_path / "checkpoint.json"
    evaluator.atomic_json(path, {"run_fingerprint": "frozen", "pages": {}})
    compressed = compress(path)
    updated = {"run_fingerprint": "frozen", "pages": {"completed": {"correct": 3}}}
    evaluator.checkpoint(path, updated, final=True)
    assert not path.exists()
    assert read_json(compressed) == updated
    assert evaluator.resumed_report(path, "frozen") == updated


def test_training_report_regeneration_preserves_compressed_storage(tmp_path):
    from ml.artifacts import compress, read_json
    from ml.train_breadth_generalization import write

    path = tmp_path / "training-report.json"
    write(path, {"runs": 1})
    compressed = compress(path)
    write(path, {"runs": 2})
    assert not path.exists()
    assert read_json(compressed) == {"runs": 2}


def test_checkpoint_retries_transient_write_lock(tmp_path, monkeypatch):
    import errno

    from ml import evaluate_breadth_upgrade as evaluator

    writer = evaluator.write_bytes
    attempts = []
    delays = []

    def locked_once(path, payload):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError(errno.EACCES, "temporary lock")
        return writer(path, payload)

    monkeypatch.setattr(evaluator, "write_bytes", locked_once)
    monkeypatch.setattr(evaluator.time, "sleep", delays.append)
    path = tmp_path / "checkpoint.json"
    evaluator.atomic_json(path, {"complete": True})
    assert len(attempts) == 2 and delays == [0.05]
    assert json.loads(path.read_text()) == {"complete": True}
