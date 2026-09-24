"""Validate reviewed facts and generate bounded, label-preserving DOM variants."""

import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

SPLITS = {"development", "validation", "heldout"}
TRANSFORMS = ("neutral_wrappers", "classless", "nested_details")
MAX_HTML_BYTES = 400_000
MAX_ANCHORS = 80


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def contains_fact(text, fact):
    return (
        re.search(
            r"(?<!\w)" + re.escape(normalized(fact)) + r"(?!\w)", normalized(text)
        )
        is not None
    )


def source_facts(roots):
    parts = []
    for root in roots:
        clone = BeautifulSoup(str(root), "html.parser")
        for node in clone.select("script, style, template, noscript"):
            node.decompose()
        parts.append(clone.get_text(" ", strip=True))
        for node in clone.find_all(True):
            parts.extend(
                str(node.get(key, ""))
                for key in ("title", "alt", "aria-label", "data-language")
            )
    return " ".join(parts)


def document_hash(html):
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(True):
        node.attrs = {
            k: v
            for k, v in sorted(node.attrs.items())
            if not k.startswith("data-eval-")
        }
    return hashlib.sha256(re.sub(r"\s+", " ", str(soup)).strip().encode()).hexdigest()


def select_one(soup, selector):
    matches = soup.select(selector)
    if len(matches) != 1:
        raise ValueError(f"Selector must match exactly one element: {selector!r}")
    return matches[0]


def validate_records(records):
    ids, groups, hosts, documents = set(), {}, {}, {}
    for record in records:
        key = record["id"]
        if key in ids:
            raise ValueError(f"Duplicate record ID: {key}")
        ids.add(key)
        split = record["split"]
        if split not in SPLITS:
            raise ValueError(f"Unknown split: {split}")
        family = record["family"]
        host = (urlsplit(record["source_url"]).hostname or "").lower()
        if not family or not host:
            raise ValueError(f"Missing source family or URL: {key}")
        for value, mapping, label in (
            (family, groups, "family"),
            (host, hosts, "host"),
        ):
            if value in mapping and mapping[value] != split:
                raise ValueError(f"Cross-split {label} leakage: {value}")
            mapping[value] = split
        html = record["html"]
        if len(html.encode()) > MAX_HTML_BYTES:
            raise ValueError(f"HTML exceeds bounded fixture size: {key}")
        digest = document_hash(html)
        if digest in documents and documents[digest] != split:
            raise ValueError(f"Cross-split duplicate HTML: {key}")
        documents[digest] = split
        anchors = record["anchors"]
        if not 1 <= len(anchors) <= MAX_ANCHORS:
            raise ValueError(f"Invalid anchor count: {key}")
        soup = BeautifulSoup(html, "html.parser")
        for spec in anchors:
            anchor = select_one(soup, spec["selector"])
            if anchor.name != "a" or anchor.get("href") != spec["href"]:
                raise ValueError(f"Anchor target changed: {key}")
            roots = [select_one(soup, spec["boundary_selector"])]
            if spec.get("neighbor_selector"):
                roots.append(select_one(soup, spec["neighbor_selector"]))
            if not any(
                anchor is root or any(parent is root for parent in anchor.parents)
                for root in roots
            ):
                raise ValueError(f"Anchor outside annotated boundary: {key}")
            text = source_facts(roots)
            if any(not contains_fact(text, value) for value in spec["required"]):
                raise ValueError(f"Required fact absent from annotated boundary: {key}")
            if any(contains_fact(text, value) for value in spec.get("forbidden", [])):
                raise ValueError(f"Forbidden fact present in annotated boundary: {key}")
            if any(not normalized(v) for v in spec["required"]):
                raise ValueError(f"Missing required facts: {key}")
            if set(map(normalized, spec["required"])) & set(
                map(normalized, spec.get("forbidden", []))
            ):
                raise ValueError(f"Conflicting fact labels: {key}")
    return records


def load_records(path):
    records = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf8").splitlines()
        if line.strip()
    ]
    return validate_records(records)


def _annotated(record):
    result = copy.deepcopy(record)
    soup = BeautifulSoup(record["html"], "html.parser")
    for index, spec in enumerate(result["anchors"]):
        for field in ("selector", "boundary_selector", "neighbor_selector"):
            if not spec.get(field):
                continue
            node = select_one(soup, spec[field])
            marker = "data-eval-" + field.replace("_selector", "").replace(
                "selector", "anchor"
            )
            # A shared boundary can own multiple targets; use a unique attribute per target.
            marker += "-" + str(index)
            node[marker] = "1"
            spec[field] = f'[{marker}="1"]'
    return result, soup


def augment(record, transform):
    if record["split"] != "development":
        raise ValueError("Only development families may be augmented")
    if transform not in TRANSFORMS:
        raise ValueError(f"Unknown transformation: {transform}")
    result, soup = _annotated(record)
    if transform == "classless":
        for node in soup.find_all(True):
            node.attrs.pop("class", None)
    elif transform == "neutral_wrappers":
        for spec in result["anchors"]:
            anchor = select_one(soup, spec["selector"])
            if anchor.name == "a" and anchor.parent.name not in {
                "table",
                "tbody",
                "tr",
                "ul",
                "ol",
            }:
                anchor.wrap(soup.new_tag("span"))
                anchor.wrap(soup.new_tag("span"))
    else:
        seen = set()
        for spec in result["anchors"]:
            boundary = select_one(soup, spec["boundary_selector"])
            if id(boundary) in seen:
                continue
            seen.add(id(boundary))
            for child in list(boundary.children):
                if not isinstance(child, Tag) or child.name in {
                    "a",
                    "table",
                    "tbody",
                    "tr",
                    "td",
                    "th",
                    "ul",
                    "ol",
                    "li",
                }:
                    continue
                if child.find("a") is None and boundary.name not in {
                    "table",
                    "tbody",
                    "tr",
                    "ul",
                    "ol",
                }:
                    child.wrap(soup.new_tag("div"))
    result["id"] += ":synthetic:" + transform
    result["synthetic"] = {"parent_id": record["id"], "transform": transform}
    result["html"] = str(soup)
    return result


def expanded(records, transforms=TRANSFORMS):
    validate_records(records)
    output, seen = [], set()
    for record in records:
        variants = [record]
        if record["split"] == "development":
            variants.extend(augment(record, name) for name in transforms)
        for variant in variants:
            fingerprint = (record["id"], document_hash(variant["html"]))
            if fingerprint not in seen:
                seen.add(fingerprint)
                output.append(variant)
    return validate_records(output)
