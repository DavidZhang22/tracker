"""Install pinned, verified encoder data. Runtime inference never downloads files."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fetch(name, destination):
    manifest = json.loads((ROOT / "app/tracker/semantic_assets.json").read_text())[name]
    destination.mkdir(parents=True, exist_ok=True)
    for remote, filename, expected in [
        (manifest["model"], "model.onnx", manifest["sha256"]),
        ("tokenizer.json", "tokenizer.json", manifest["tokenizer_git_blob"]),
    ]:

        def checksum(data, filename=filename):
            return (
                hashlib.sha256(data).hexdigest()
                if filename.endswith("onnx")
                else hashlib.sha1(
                    b"blob " + str(len(data)).encode() + b"\0" + data
                ).hexdigest()
            )

        target = destination / filename
        if target.is_file() and checksum(target.read_bytes()) == expected:
            continue
        url = f"https://huggingface.co/{manifest['repository']}/resolve/{manifest['revision']}/{remote}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(40_000_001)
        if len(data) > 40_000_000 or checksum(data) != expected:
            raise ValueError("Downloaded model data failed verification")
        temporary = target.with_suffix(".partial")
        temporary.write_bytes(data)
        temporary.replace(target)
    print(f"Verified {name}: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", choices=["minilm-l6", "minilm-l3", "bge-small"], default="minilm-l6"
    )
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    fetch(args.model, args.destination or ROOT / "models" / args.model)
