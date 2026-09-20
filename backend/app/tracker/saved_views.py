"""Private, bounded Library views; saving never fetches a source."""

import json
import unicodedata
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .media_metadata import MediaOverride
from .models import utcnow

MAX_VIEWS = 12


class SavedViewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    query: str = Field(default="", max_length=200)
    filter: Literal["all", "new", "unread", "favorites", "ignored", "trash"] = "all"
    kind: Literal["all"] | MediaOverride = "all"
    sort: Literal["recent", "unread", "title"] = "recent"
    search_mode: Literal["semantic", "local"] = "semantic"

    @field_validator("name", "query")
    @classmethod
    def clean_text(cls, value):
        if any(unicodedata.category(c) == "Cc" for c in value):
            raise ValueError("Use a single line of text.")
        return value.strip()

    @field_validator("name")
    @classmethod
    def named(cls, value):
        if not value:
            raise ValueError("Name this view.")
        return value

    @field_validator("kind")
    @classmethod
    def media_kind(cls, value):
        if not value:
            raise ValueError("Choose a media type or All types.")
        return value


def list_views(store):
    with store.connection() as db:
        return [
            json.loads(row["payload"]) | {"id": row["id"]}
            for row in db.execute(
                "SELECT id,payload FROM saved_views ORDER BY created_at,id"
            )
        ]


def save_view(store, values, view_id=None):
    values = SavedViewInput(**values).model_dump()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id,name FROM saved_views").fetchall()
        if view_id is not None and not any(row["id"] == view_id for row in rows):
            raise KeyError("Saved view not found.")
        if view_id is None and len(rows) >= MAX_VIEWS:
            raise ValueError(f"You can save up to {MAX_VIEWS} views. Remove one first.")
        if any(
            row["id"] != view_id and row["name"].casefold() == values["name"].casefold()
            for row in rows
        ):
            raise ValueError("A view with this name already exists.")
        view_id = view_id or uuid.uuid4().hex
        db.execute(
            "INSERT INTO saved_views(id,name,payload,created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,payload=excluded.payload",
            (view_id, values["name"], json.dumps(values), utcnow()),
        )
    return values | {"id": view_id}


def delete_view(store, view_id):
    with store.connection() as db:
        if not db.execute("DELETE FROM saved_views WHERE id=?", (view_id,)).rowcount:
            raise KeyError("Saved view not found.")
    return {"ok": True}
