"""Download one bounded, pinned public archive. Never follow corpus hyperlinks."""

import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw" / "cleaneval"


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    archive = RAW / "web2text.zip"
    provenance = RAW / "provenance.json"
    if not archive.exists():
        revision = "0f9c7b787ff125ce5190784e741c5b453ddf0560"
        url = f"https://codeload.github.com/dalab/web2text/zip/{revision}"
        temporary = archive.with_suffix(".partial")
        try:
            with (
                urllib.request.urlopen(url, timeout=45) as response,
                temporary.open("wb") as out,
            ):
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 80 * 1024 * 1024:
                        raise ValueError(
                            "Corpus archive exceeded the 80 MiB download limit"
                        )
                    out.write(chunk)
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
        provenance.write_text(
            json.dumps(
                dict(
                    repository="https://github.com/dalab/web2text",
                    revision=revision,
                    archive_url=url,
                    sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                    license_note="Repository MIT license; original webpage rights remain with their publishers.",
                ),
                indent=2,
            )
        )
    with zipfile.ZipFile(archive) as source:
        files = [
            f
            for f in source.infolist()
            if "/cleaneval/orig/" in f.filename and not f.is_dir()
        ]
        print("Original HTML files:", len(files))
        print("Archive SHA256:", hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
