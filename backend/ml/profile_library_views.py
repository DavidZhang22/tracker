"""Compare Library summaries on bounded synthetic data, without fetching sources."""

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "backend"))
from app.tracker.models import utcnow
from app.tracker.store import Store

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--baseline-ref", default="b5ab2b9")
parser.add_argument("--baseline-file", type=Path)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
source = (
    args.baseline_file.read_text(encoding="utf-8")
    if args.baseline_file
    else subprocess.check_output(
        ["git", "show", f"{args.baseline_ref}:backend/app/tracker/store.py"], cwd=root
    ).decode("utf-8")
)
ns = {"__name__": "app.tracker.baseline_store", "__package__": "app.tracker"}
exec(compile(source, "baseline_store.py", "exec"), ns)
Baseline = ns["Store"]


def measure(fn):
    fn()
    values = []
    for _ in range(7):
        start = perf_counter()
        fn()
        values.append((perf_counter() - start) * 1000)
    return round(statistics.median(values), 3)


output = []
for count, per_item, grouped in [
    (25, 100, False),
    (200, 500, False),
    (25, 100, True),
    (200, 500, True),
]:
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "library.db")
        now = utcnow()
        with store.connection() as db:
            db.executemany(
                "INSERT INTO items (id,url,title,kind,created_at,media_version) VALUES (?,?,?,?,?,999)",
                [
                    (f"i{i}", f"https://example.org/{i}", f"Item {i}", "novel", now)
                    for i in range(count)
                ],
            )
            db.executemany(
                "INSERT INTO links (id,item_id,identity,url,title,number,position,method,discovered_at,read) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"l{i}-{j}",
                        f"i{i}",
                        f"https://example.org/{i}/{j}",
                        f"https://example.org/{i}/{j}",
                        f"Chapter {j}",
                        j,
                        j,
                        "page",
                        now,
                        int(j < per_item // 2),
                    )
                    for i in range(count)
                    for j in range(per_item)
                ],
            )
        if grouped:
            with store.connection() as db:
                db.executemany(
                    "UPDATE links SET merged_into=?,merge_order=1 WHERE id=?",
                    [
                        (f"l{i}-{j}", f"l{i}-{j + 1}")
                        for i in range(count)
                        for j in range(0, per_item, 2)
                    ],
                )
        old = Baseline.__new__(Baseline)
        old.__dict__.update(store.__dict__)
        before = old.items()
        after = store.items()
        comparable = [
            {k: v for k, v in item.items() if k != "next_unread"} for item in after
        ]
        for item in comparable:
            if item["latest_link"]:
                item["latest_link"] = {
                    k: v for k, v in item["latest_link"].items() if k != "link_count"
                }
        assert before == comparable
        assert all(item["next_unread"]["number"] == per_item // 2 for item in after)
        output.append(
            {
                "grouped": grouped,
                "items": count,
                "links": count * per_item,
                "baseline_ms": measure(old.items),
                "current_ms": measure(store.items),
            }
        )
report = json.dumps(output, indent=2) + "\n"
if args.output:
    args.output.write_text(report, encoding="utf-8")
print(report)
