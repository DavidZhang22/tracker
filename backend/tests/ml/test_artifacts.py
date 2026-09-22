import gzip
import hashlib

import pytest

from ml import artifacts


@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
def test_compression_preserves_logical_bytes_and_hashes(tmp_path, suffix):
    plain = tmp_path / ("report" + suffix)
    original = '{"title":"café", "value": 1}  \r\n'.encode()
    plain.write_bytes(original)
    original_hash = hashlib.sha256(original).hexdigest()

    compressed = artifacts.compress(plain)

    assert not plain.exists()
    assert compressed == plain.with_name(plain.name + ".gz")
    assert artifacts.stored_path(plain) == compressed
    assert artifacts.exists(plain)
    for path in (plain, compressed):
        assert artifacts.read_bytes(path) == original
        assert hashlib.sha256(artifacts.read_bytes(path)).hexdigest() == original_hash
        assert artifacts.read_text(path) == original.decode()
        assert artifacts.read_json(path) == {"title": "café", "value": 1}
    assert artifacts.compress(plain) == compressed
    assert artifacts.restore(compressed) == plain
    assert plain.read_bytes() == original
    assert not compressed.exists()
    assert artifacts.restore(plain) == plain


def test_compression_is_deterministic_without_timestamps_or_filenames(tmp_path):
    encoded = []
    for name in ("first.json", "second.json"):
        path = tmp_path / name
        path.write_bytes(b'{"a": 2}\n' * 10)
        encoded.append(artifacts.compress(path).read_bytes())
    assert encoded[0] == encoded[1]
    assert encoded[0][4:8] == b"\0" * 4
    assert encoded[0][9] == 255


@pytest.mark.parametrize(
    "action",
    [
        artifacts.stored_path,
        artifacts.exists,
        artifacts.read_bytes,
        artifacts.compress,
        artifacts.restore,
    ],
)
def test_conflicting_copies_are_rejected_without_mutation(tmp_path, action):
    plain = tmp_path / "report.json"
    compressed = tmp_path / "report.json.gz"
    plain.write_bytes(b'{"version": 1}')
    encoded = gzip.compress(b'{"version": 2}', mtime=0)
    compressed.write_bytes(encoded)
    with pytest.raises(ValueError, match="Conflicting artifact copies"):
        action(plain)
    assert plain.read_bytes() == b'{"version": 1}'
    assert compressed.read_bytes() == encoded


def test_matching_copies_are_accepted_and_compacted(tmp_path):
    plain = tmp_path / "report.json"
    original = b'{"a":1}\n'
    plain.write_bytes(original)
    compressed = tmp_path / "report.json.gz"
    compressed.write_bytes(gzip.compress(original, mtime=12))
    assert artifacts.stored_path(compressed) == plain
    assert artifacts.read_bytes(plain) == original
    assert artifacts.compress(plain) == compressed
    assert not plain.exists()
    assert compressed.read_bytes()[4:8] == b"\0" * 4


def test_corrupt_compressed_file_never_removes_plain_input(tmp_path):
    plain = tmp_path / "report.json"
    plain.write_bytes(b"{}")
    compressed = tmp_path / "report.json.gz"
    compressed.write_bytes(b"invalid gzip")
    with pytest.raises(gzip.BadGzipFile):
        artifacts.compress(plain)
    assert plain.read_bytes() == b"{}"
    assert compressed.read_bytes() == b"invalid gzip"


def test_failed_verification_keeps_original_and_removes_temporary_file(
    tmp_path, monkeypatch
):
    plain = tmp_path / "report.json"
    plain.write_bytes(b"{}")
    bad_payload = gzip.compress(b"different", mtime=0)
    monkeypatch.setattr(
        artifacts.gzip, "compress", lambda *_args, **_kwargs: bad_payload
    )
    with pytest.raises(ValueError, match="Artifact verification failed"):
        artifacts.compress(plain)
    assert plain.read_bytes() == b"{}"
    assert list(tmp_path.iterdir()) == [plain]


def test_missing_artifacts_and_plain_source_files(tmp_path):
    missing = tmp_path / "missing.json"
    assert not artifacts.exists(missing)
    with pytest.raises(FileNotFoundError):
        artifacts.read_json(missing)
    source = tmp_path / "extractor.py"
    source.write_bytes(b"# Preserve source hashes too.\r\n")
    assert artifacts.read_bytes(source) == source.read_bytes()
    assert artifacts.stored_path(source) == source
    with pytest.raises(ValueError, match="Only explicit"):
        artifacts.compress(source)


def test_cli_changes_only_explicit_artifacts(tmp_path):
    selected, untouched = tmp_path / "selected.json", tmp_path / "untouched.json"
    selected.write_bytes(b"{}")
    untouched.write_bytes(b"[]")
    artifacts.main([str(selected)])
    assert not selected.exists()
    assert untouched.read_bytes() == b"[]"
    artifacts.main(["--restore", str(selected) + ".gz"])
    assert selected.read_bytes() == b"{}"
    with pytest.raises(ValueError, match="Only explicit"):
        artifacts.main([str(tmp_path)])


@pytest.mark.parametrize("compressed", [False, True])
def test_text_decoding_honors_encoding_and_errors(tmp_path, compressed):
    path = tmp_path / "log.json"
    path.write_bytes(b"invalid utf8: \xff")
    if compressed:
        artifacts.compress(path)
    with pytest.raises(UnicodeDecodeError):
        artifacts.read_text(path)
    assert artifacts.read_text(path, errors="replace") == "invalid utf8: \ufffd"
    assert artifacts.read_text(path, encoding="latin1") == "invalid utf8: \u00ff"


@pytest.mark.parametrize(
    "compressed,explicit", [(False, False), (True, False), (True, True)]
)
def test_writes_preserve_storage_for_logical_and_explicit_paths(
    tmp_path, compressed, explicit
):
    plain = tmp_path / "report.json"
    plain.write_bytes(b'{"version": 1}\r\n')
    stored = artifacts.compress(plain) if compressed else plain
    target = stored if explicit else plain
    replacement = b'{"version": 2}  \r\n'

    assert artifacts.write_bytes(target, replacement) == len(replacement)
    assert artifacts.stored_path(plain) == stored
    assert artifacts.read_bytes(plain) == replacement
    assert len(list(tmp_path.iterdir())) == 1
    if compressed:
        assert stored.read_bytes()[4:8] == b"\0" * 4
        assert stored.read_bytes()[9] == 255

    text = '{"title": "café"}\n'
    assert artifacts.write_text(target, text, encoding="utf-8") == len(text)
    assert artifacts.read_bytes(plain) == text.encode()
    assert artifacts.stored_path(plain) == stored


@pytest.mark.parametrize("suffix", [".json", ".json.gz", ".jsonl.gz", ".md", ".py"])
def test_writes_create_only_the_requested_storage_format(tmp_path, suffix):
    path = tmp_path / ("new" + suffix)
    text = "café\r\n"
    assert artifacts.write_text(path, text, encoding="latin1") == len(text)
    assert artifacts.read_bytes(path) == text.encode("latin1")
    assert len(list(tmp_path.iterdir())) == 1
    if suffix.endswith(".gz"):
        assert gzip.decompress(path.read_bytes()) == text.encode("latin1")
    else:
        assert path.read_bytes() == text.encode("latin1")


@pytest.mark.parametrize("matching", [False, True])
def test_writes_reject_two_existing_storage_copies(tmp_path, matching):
    plain = tmp_path / "report.json"
    compressed = tmp_path / "report.json.gz"
    original = b'{"version": 1}\n'
    plain.write_bytes(original)
    encoded = gzip.compress(original if matching else b"{}", mtime=0)
    compressed.write_bytes(encoded)
    for target in (plain, compressed):
        with pytest.raises(ValueError, match="Ambiguous|Conflicting"):
            artifacts.write_text(target, "replacement")
    assert plain.read_bytes() == original
    assert compressed.read_bytes() == encoded


def test_explicit_gzip_write_does_not_leave_a_conflicting_plain_file(tmp_path):
    plain = tmp_path / "report.json"
    plain.write_bytes(b"{}")
    with pytest.raises(ValueError, match="Ambiguous artifact storage"):
        artifacts.write_bytes(tmp_path / "report.json.gz", b"[]")
    assert plain.read_bytes() == b"{}"
    assert list(tmp_path.iterdir()) == [plain]


def test_write_verification_failure_preserves_existing_compressed_artifact(
    tmp_path, monkeypatch
):
    compressed = tmp_path / "report.json.gz"
    original = gzip.compress(b"{}", mtime=0)
    compressed.write_bytes(original)
    broken = gzip.compress(b"wrong replacement", mtime=0)
    monkeypatch.setattr(artifacts.gzip, "compress", lambda *_args, **_kwargs: broken)
    with pytest.raises(ValueError, match="Artifact verification failed"):
        artifacts.write_bytes(compressed, b"[]")
    assert compressed.read_bytes() == original
    assert list(tmp_path.iterdir()) == [compressed]


def test_write_rejects_corrupt_existing_gzip(tmp_path):
    compressed = tmp_path / "report.json.gz"
    compressed.write_bytes(b"corrupt")
    with pytest.raises(gzip.BadGzipFile):
        artifacts.write_bytes(compressed, b"{}")
    assert compressed.read_bytes() == b"corrupt"
