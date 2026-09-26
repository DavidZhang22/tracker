"""Validate authored entries without executing or fetching their contents."""

import hashlib
import json
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from .csv_import import safe_link
from .entry_identity import record_id
from .models import Entry, Scan, sequence_value, utcnow
from .parser import merge_entries


class ManualEntry(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(default="", max_length=2048)
    context: str = Field(default="", max_length=8192)
    published_at: str | None = Field(default=None, max_length=50)
    number: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)

    @field_validator("title")
    @classmethod
    def title_text(cls, value):
        if not value.strip():
            raise ValueError("Enter an entry title.")
        return value.strip()

    @field_validator("url")
    @classmethod
    def optional_url(cls, value):
        if not value.strip():
            return ""
        url = safe_link(value.strip())
        if not url:
            raise ValueError(
                "Use a complete public HTTP or HTTPS link, or leave it empty."
            )
        return url

    @field_validator("published_at")
    @classmethod
    def optional_date(cls, value):
        if not value:
            return None
        try:
            result = datetime.fromisoformat(value)
            return (
                (result if result.tzinfo else result.replace(tzinfo=UTC))
                .astimezone(UTC)
                .isoformat()
            )
        except ValueError as exc:
            raise ValueError("Enter a valid date.") from exc


class ManualPreview(BaseModel):
    entries: list[ManualEntry] = Field(min_length=1, max_length=100)
    item_id: str | None = Field(default=None, min_length=1, max_length=64)


def manual_scan(entries):
    records = []
    for position, value in enumerate(entries):
        data = value.model_dump()
        data["context"] = data["context"].strip()
        if data["number"] is None:
            data["number"] = sequence_value(data["title"])
        records.append(
            Entry(
                **data,
                position=position,
                method="Manual entry",
                date_source="User-entered date" if data["published_at"] else "",
                source_id=""
                if data["url"]
                else record_id("manual-entry", json.dumps(data, sort_keys=True)),
            )
        )
    merged = merge_entries(records)
    digest = hashlib.sha256(
        json.dumps([vars(e) for e in merged], sort_keys=True).encode()
    ).hexdigest()
    result = Scan(
        "document:" + digest,
        "Manual entries",
        entries=merged,
        methods=["Manual entry"],
        order_hint="source",
        checked_at=utcnow(),
        coverage="complete",
    ).to_dict()
    if len(records) != len(merged):
        result["warnings"].append(
            f"Combined {len(records) - len(merged)} duplicate entries."
        )
    return result | {
        "source_type": "document",
        "source_name": "Manual entries",
        "source_method": "auto",
        "manual_import": True,
        "document": {"format": "Manual", "candidates": len(records)},
    }
