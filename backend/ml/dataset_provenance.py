"""Freeze dataset provenance while accepting only CRLF/LF checkout differences."""

import hashlib
import json


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf8")


def freeze_protocol_document(path, proposed, dataset_paths):
    """Replay frozen inputs across checkouts without accepting content changes."""
    proposed = json.loads(json.dumps(proposed))
    raw = {dataset.name: dataset.read_bytes() for dataset in dataset_paths}
    if len(raw) != len(dataset_paths):
        raise ValueError("Dataset names must be unique")
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
    if proposed.get("dataset_sha256") != hashes:
        raise ValueError("Proposed dataset hashes do not match input bytes")
    existing = path.exists()
    frozen = json.loads(path.read_text(encoding="utf8")) if existing else proposed
    if {k: v for k, v in frozen.items() if k != "dataset_sha256"} != {
        k: v for k, v in proposed.items() if k != "dataset_sha256"
    }:
        raise ValueError(
            "Frozen experiment protocol differs; choose another output directory"
        )
    if set(frozen["dataset_sha256"]) != set(raw):
        raise ValueError("Frozen dataset inventory differs")
    provenance_path = path.with_name("input-provenance.json")
    if provenance_path.exists():
        provenance = json.loads(provenance_path.read_text(encoding="utf8"))
        if (
            provenance.get("version") != 1
            or provenance.get("normalization") != "CRLF-to-LF only"
            or set(provenance.get("datasets", {})) != set(raw)
        ):
            raise ValueError("Invalid input provenance")
    else:
        # Newline equivalence can only be certified against the original bytes.
        if hashes != frozen["dataset_sha256"]:
            raise ValueError(
                "Original dataset bytes or their frozen provenance are required"
            )
        provenance = dict(
            version=1,
            normalization="CRLF-to-LF only",
            datasets={
                name: dict(
                    raw_sha256=hashes[name],
                    lf_sha256=hashlib.sha256(value.replace(b"\r\n", b"\n")).hexdigest(),
                )
                for name, value in raw.items()
            },
        )
    for name, value in raw.items():
        reference = provenance["datasets"][name]
        if (
            reference.get("raw_sha256") != frozen["dataset_sha256"][name]
            or reference.get("lf_sha256")
            != hashlib.sha256(value.replace(b"\r\n", b"\n")).hexdigest()
        ):
            raise ValueError("Frozen dataset content differs: " + name)
    if not existing:
        _write_json(path, frozen)
    if not provenance_path.exists():
        _write_json(provenance_path, provenance)
    return frozen
