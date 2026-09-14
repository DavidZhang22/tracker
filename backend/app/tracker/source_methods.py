"""Explicit, persisted source choices; Automatic retains the existing scanner."""

from typing import Literal

SourceMethod = Literal[
    "auto",
    "sitemap",
    "wordpress_com",
    "wordpress",
    "devto",
    "github",
    "codeforces",
    "mastodon",
    "youtube",
    "ghost",
    "mangadex",
]
