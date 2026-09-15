"""Extend regenerated context rows with public jobs and authored table layouts."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml")]
from build_v2_dataset import sample_record

from app.tracker.link_context import context_candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base", type=Path, default=ROOT / "ml/datasets/v3-base-dataset.jsonl"
    )
    args = parser.parse_args()
    rows = [
        json.loads(line) for line in args.base.read_text(encoding="utf8").splitlines()
    ]
    raw = ROOT / "data/jobs-readme.md"
    soup = BeautifulSoup(
        MarkdownIt("commonmark", {"html": True})
        .enable("table")
        .render(raw.read_text(encoding="utf8")),
        "html.parser",
    )
    source = dict(
        id="github-jobs",
        url="https://github.com/SimplifyJobs/New-Grad-Positions",
        split="train",
        family="application-table",
    )
    # Reviewed source contract: fourth column is Application; company profiles and ads are negatives.
    selected = {id(a) for a in soup.select("tbody tr > td:nth-of-type(4) a[href]")}
    candidates = [
        sample_record(
            r,
            source,
            id(r["anchor"]) in selected,
            reason="Reviewed Application column, including direct and alternate application links",
        )
        for r in context_candidates(soup, source["url"])
    ]
    added = [
        r
        for label in (0, 1)
        for r in sorted(
            (r for r in candidates if r["label"] == label), key=lambda r: r["id"]
        )[:350]
    ]
    rows += added
    for variant in range(60):
        split = "train" if variant < 40 else "validation"
        url = f"https://table-layout-{variant}.example/opportunities"
        order = ["Company", "Role", "Location", "Application", "Date"]
        if variant % 3:
            order = ["Application", "Location", "Company", "Date", "Role"]
        if variant % 5 == 0:
            order = ["Employer", "Position", "Location", "Apply", "Posted"]
        body, positive = [], set()
        for i in range(8):
            label = ("Apply", "Apply now", "View details", "Application")[variant % 4]
            button = f'<img alt="{label}" src="button.svg">' if variant % 2 else label
            target = f"https://recruiting-{i}.example/job/{variant}-{i}?jobId={100 + i}"
            positive.add(target)
            fields = dict(
                Company=f'<a href="https://company-{i}.example">Company {i}</a>',
                Role=f"Graduate engineer {i}",
                Location="Remote",
                Application=f'<a href="{target}">{button}</a>',
                Date="2026-09-01",
            )
            fields.update(
                Employer=fields["Company"],
                Position=fields["Role"],
                Apply=fields["Application"],
                Posted=fields["Date"],
            )
            body.append(
                "<tr>" + "".join(f"<td>{fields[name]}</td>" for name in order) + "</tr>"
            )
        markup = (
            '<nav><a href="/login">Apply for an account</a><a href="/about">About</a></nav><h1>Opportunities</h1><table><thead><tr>'
            + "".join(f"<th>{name}</th>" for name in order)
            + "</tr></thead><tbody>"
            + "".join(body)
            + '</tbody></table><footer><a href="https://ads.example/apply">Apply now</a></footer>'
        )
        source = dict(
            id="table-layouts-" + split, url=url, split=split, family="authored-table"
        )
        for record in context_candidates(BeautifulSoup(markup, "html.parser"), url):
            rows.append(
                sample_record(
                    record,
                    source,
                    record["url"] in positive,
                    origin="authored",
                    reason="Authored application-column contract with profile and promotion negatives",
                )
            )
    target = ROOT / "ml/datasets/v3-dataset.jsonl"
    target.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf8",
    )
    report = dict(
        rows=len(rows),
        splits=dict(Counter(r["split"] for r in rows)),
        new_real_rows=len(added),
        authored_rows=60 * 19,
        source_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
        data_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        limitations="Rule-reviewed public rows and authored templates, not independent human gold labels. GitHub jobs and prior test pages are development/regression data. No claim of universal extraction.",
    )
    (ROOT / "ml/datasets/v3-dataset-card.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
