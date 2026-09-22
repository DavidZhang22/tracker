"""Read frozen artifacts without changing their logical bytes or recorded hashes.

Readers accept a logical ``name.json``/``name.jsonl`` path when its stored file
is gzip compressed, or the explicit compressed path. Other files remain plain.
Compression and restoration operate only on individually supplied JSON paths.
"""

import argparse
import gzip
import json
import os
import tempfile
from pathlib import Path

JSON_SUFFIXES = {".json", ".jsonl"}


def _paths(path):
    path = Path(path)
    plain = path.with_suffix("") if path.suffix == ".gz" else path
    if plain.suffix not in JSON_SUFFIXES:
        return path, None
    return plain, plain.with_name(plain.name + ".gz")


def stored_path(path):
    """Resolve a logical artifact, rejecting different plain/compressed copies."""
    plain, compressed = _paths(path)
    if compressed is not None and compressed.exists():
        if plain.exists() and plain.read_bytes() != gzip.decompress(
            compressed.read_bytes()
        ):
            raise ValueError(f"Conflicting artifact copies: {plain} and {compressed}")
        return plain if plain.exists() else compressed
    if plain.exists():
        return plain
    raise FileNotFoundError(f"Artifact does not exist: {path}")


def exists(path):
    """Whether the logical artifact exists; conflicting copies remain an error."""
    try:
        stored_path(path)
    except FileNotFoundError:
        return False
    return True


def read_bytes(path):
    """Return original bytes, including their exact encoding and line endings."""
    stored = stored_path(path)
    data = stored.read_bytes()
    _, compressed = _paths(stored)
    return gzip.decompress(data) if stored == compressed else data


def read_text(path, encoding="utf8", errors="strict"):
    return read_bytes(path).decode(encoding, errors)


def read_json(path):
    return json.loads(read_text(path))


def _mutable_paths(path):
    plain, compressed = _paths(path)
    if compressed is None:
        raise ValueError("Only explicit .json or .jsonl artifact paths are supported")
    if plain.is_symlink() or compressed.is_symlink():
        raise ValueError("Artifact storage changes do not follow symbolic links")
    return plain, compressed


def _write_verified(path, data, logical, *, compressed):
    """Write beside the destination, verify the payload, then replace atomically."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        actual = temporary.read_bytes()
        if compressed:
            actual = gzip.decompress(actual)
        if actual != logical:
            raise ValueError(f"Artifact verification failed: {path}")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _gzip_bytes(data):
    encoded = gzip.compress(data, mtime=0)
    # Python 3.12 delegates mtime=0 to zlib, whose OS header varies by platform.
    # RFC 1952's unknown-OS value keeps identical output across operating systems.
    return encoded[:9] + b"\xff" + encoded[10:]


def write_bytes(path, data):
    """Replace an artifact while preserving its existing plain or gzip storage."""
    path = Path(path)
    plain, compressed = _paths(path)
    if plain.is_symlink() or (compressed is not None and compressed.is_symlink()):
        raise ValueError("Artifact storage changes do not follow symbolic links")
    if compressed is not None and plain.exists() and compressed.exists():
        stored_path(plain)  # Describe conflicting payloads before storage ambiguity.
        raise ValueError(f"Ambiguous artifact storage: {plain} and {compressed}")
    use_gzip = compressed is not None and (path == compressed or compressed.exists())
    if use_gzip and plain.exists():
        raise ValueError(
            f"Ambiguous artifact storage: compress the existing plain file first: {plain}"
        )
    target = compressed if use_gzip else plain
    if use_gzip and target.exists():
        read_bytes(target)  # Do not replace corrupt compressed evidence silently.
    encoded = _gzip_bytes(data) if use_gzip else data
    _write_verified(target, encoded, data, compressed=use_gzip)
    return len(data)


def write_text(path, text, encoding="utf8"):
    """Write exact encoded text without translating newline characters."""
    write_bytes(path, text.encode(encoding))
    return len(text)


def compress(path):
    """Store one JSON artifact deterministically, verifying before deleting it."""
    plain, compressed = _mutable_paths(path)
    stored_path(plain)  # Reject missing inputs and conflicting existing copies.
    if not plain.exists():
        read_bytes(compressed)  # Check the existing gzip stream before returning.
        return compressed
    original = plain.read_bytes()
    encoded = _gzip_bytes(original)
    _write_verified(compressed, encoded, original, compressed=True)
    if gzip.decompress(compressed.read_bytes()) != original:
        raise ValueError(f"Artifact verification failed: {compressed}")
    if plain.read_bytes() != original:
        raise ValueError(f"Artifact changed during compression: {plain}")
    plain.unlink()
    return compressed


def restore(path):
    """Restore one artifact's original bytes, verifying before removing gzip."""
    plain, compressed = _mutable_paths(path)
    stored_path(plain)
    if not compressed.exists():
        return plain
    encoded = compressed.read_bytes()
    original = gzip.decompress(encoded)
    _write_verified(plain, original, original, compressed=False)
    if plain.read_bytes() != original:
        raise ValueError(f"Artifact verification failed: {plain}")
    if compressed.read_bytes() != encoded:
        raise ValueError(f"Artifact changed during restoration: {compressed}")
    compressed.unlink()
    return plain


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restore", action="store_true", help="Restore the original plain files"
    )
    parser.add_argument(
        "paths", nargs="+", type=Path, help="Explicit JSON artifact paths"
    )
    args = parser.parse_args(argv)
    action = restore if args.restore else compress
    for path in args.paths:
        print(action(path))


if __name__ == "__main__":
    main()
