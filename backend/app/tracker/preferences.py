"""Account-level defaults stored alongside each isolated library."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .source_methods import SourceMethod


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_sort: Literal["auto", "date", "number", "source", "discovered", "title"] = (
        "auto"
    )
    link_direction: Literal["asc", "desc"] = "desc"
    library_sort: Literal["recent", "unread", "title"] = "recent"
    auto_read: bool = True
    refresh_mode: Literal["light", "deep"] = "light"
    source_method: SourceMethod = "auto"


class PreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_sort: (
        Literal["auto", "date", "number", "source", "discovered", "title"] | None
    ) = None
    link_direction: Literal["asc", "desc"] | None = None
    library_sort: Literal["recent", "unread", "title"] | None = None
    auto_read: bool | None = None
    refresh_mode: Literal["light", "deep"] | None = None
    apply_auto_read: bool = False
    source_method: SourceMethod | None = None
