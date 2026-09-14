"""Verify duplicate repair on a disposable database, optionally copied from live data.

--database opens its source read-only and uses SQLite backup; all migration and
refresh writes happen only in the temporary copy. No source pages are fetched.
"""

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


def verify(path):
    from app.tracker.models import Entry, Scan, date_rank
    from app.tracker.store import Store, identity

    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        groups = defaultdict(list)
        for row in db.execute("SELECT * FROM links ORDER BY discovered_at,id"):
            groups[(row["item_id"], identity(row["url"]))].append(dict(row))
        items = [tuple(r) for r in db.execute("SELECT * FROM items ORDER BY id")]
        old_count = sum(len(rows) for rows in groups.values())
    store = Store(path)
    with store.connection() as db:
        assert items == [
            tuple(r) for r in db.execute("SELECT * FROM items ORDER BY id")
        ]
        current = {
            (r["item_id"], r["identity"]): dict(r)
            for r in db.execute("SELECT * FROM links")
        }
        assert db.execute("SELECT count(*) FROM links").fetchone()[0] == len(groups)
        assert set(groups) == set(current)
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for key, rows in groups.items():
        row = current[key]
        assert row["id"] == rows[0]["id"]
        assert row["discovered_at"] == rows[0]["discovered_at"]
        assert row["url"] == rows[-1]["url"]
        for flag in ("read", "favorite", "ignored", "deleted"):
            assert row[flag] == any(r[flag] for r in rows)
        assert date_rank(row) == max(date_rank(r) for r in rows)
        assert identity(row["url"]) == row["identity"]
    # Repeat refresh using saved metadata only; retain all states and link IDs.
    per_item = defaultdict(list)
    for row in current.values():
        per_item[row["item_id"]].append(row)
    for iid, rows in per_item.items():
        item = store.item(iid)
        if item["deleted"]:
            continue
        entries = [
            Entry(**{k: r[k] for k in Entry.__dataclass_fields__})
            for r in sorted(rows, key=lambda r: r["position"])
        ]
        scan = Scan(item["url"], item["title"], entries=entries).to_dict()
        assert store.merge(iid, scan) == store.merge(iid, scan) == 0
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM links").fetchone()[0] == len(groups)
        for row in db.execute("SELECT * FROM links"):
            previous = current[(row["item_id"], row["identity"])]
            for field in ("id", "read", "favorite", "ignored", "deleted", "is_new"):
                assert row[field] == previous[field]
        before = [tuple(r) for r in db.execute("SELECT * FROM links ORDER BY id")]
    with Store(path).connection() as db:
        assert before == [
            tuple(r) for r in db.execute("SELECT * FROM links ORDER BY id")
        ]
    print(
        json.dumps(
            dict(
                migration="passed",
                progress="passed",
                repeat_refresh="passed",
                integrity="ok",
                links_before=old_count,
                links_after=len(groups),
                duplicates_removed=old_count - len(groups),
                source_requests=0,
            )
        )
    )


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.tracker.models import Entry, Scan
    from app.tracker.store import Store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "library.sqlite3"
        if args.database:
            source = sqlite3.connect(
                args.database.resolve().as_uri() + "?mode=ro", uri=True
            )
            target = sqlite3.connect(path)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
        else:
            url = "https://asurascans.com/comics/dungeon-odyssey-53fc8424"
            with patch("app.tracker.store.identity", side_effect=lambda value: value):
                store = Store(path)
                item = store.create(
                    store.save_scan(
                        Scan(
                            url,
                            "Example",
                            entries=[Entry(url + "/chapter/166", "Old", number=166)],
                        ).to_dict()
                    ),
                    mark_read=True,
                )
                store.merge(
                    item["id"],
                    Scan(
                        url,
                        "Example",
                        entries=[
                            Entry(
                                url.replace("53fc8424", "6f7fe6eb") + "/chapter/166",
                                "New",
                                number=166,
                            )
                        ],
                    ).to_dict(),
                )
            with store.connection() as db:
                db.execute("PRAGMA user_version=7")
        verify(path)


if __name__ == "__main__":
    main()
