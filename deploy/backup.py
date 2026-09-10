"""Create verified online SQLite backups; each database is a consistent snapshot.

Usage: python3 deploy/backup.py DATA_DIRECTORY BACKUP_DIRECTORY
Keeps seven completed snapshots. Run as the data owner (or root on the host).
"""

import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def backup(data_directory, backup_directory):
    os.umask(0o077)
    source = Path(data_directory).resolve(strict=True)
    destination = Path(backup_directory).resolve()
    if destination == source or destination.is_relative_to(source):
        raise ValueError("Backups must be outside the live data directory")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = datetime.now(timezone.utc).strftime("catchup-%Y%m%dT%H%M%S%fZ")
    staging = destination / (name + ".partial")
    staging.mkdir(mode=0o700)
    files = [source / "accounts.sqlite3", source / "tracker.sqlite3"]
    files += sorted((source / "users").glob("*/tracker.sqlite3"))
    started = time.monotonic()

    def progress(*_):
        if time.monotonic() - started > 240:
            raise TimeoutError("Backup exceeded four minutes")

    try:
        for path in files:
            if not path.resolve(strict=True).is_relative_to(source):
                raise ValueError("Database path leaves the data directory")
            relative = path.relative_to(source)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            live = sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3
            )
            saved = sqlite3.connect(target)
            try:
                live.backup(saved, pages=256, progress=progress, sleep=0.1)
                if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
            finally:
                saved.close()
                live.close()
            # The snapshot itself has been initialized even on older deployments.
            Path(str(target) + ".initialized").touch(mode=0o600)
        (staging / "manifest.json").write_text(
            json.dumps(
                {
                    "created": datetime.now(timezone.utc).isoformat(),
                    "databases": [str(p.relative_to(source)) for p in files],
                    "consistency": "online snapshot per database",
                },
                indent=2,
            )
        )
        final = destination / name
        staging.rename(final)
    except Exception:
        shutil.rmtree(staging)
        raise
    complete = sorted(
        p
        for p in destination.glob("catchup-*")
        if p.is_dir()
        and not p.is_symlink()
        and not p.name.endswith(".partial")
        and (p / "manifest.json").is_file()
    )
    for old in complete[:-7]:
        shutil.rmtree(old)
    print(f"Verified {len(files)} database snapshots: {final}")
    return final


if __name__ == "__main__":
    backup(*sys.argv[1:])
