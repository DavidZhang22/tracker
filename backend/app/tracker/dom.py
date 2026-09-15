"""Small DOM walks used by the feature extractors; no CSS or text matching."""

from itertools import islice

from bs4 import Tag

HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


def tags(node, names, *, attribute=None, limit=None):
    matches = (
        child
        for child in node.descendants
        if isinstance(child, Tag)
        and child.name in names
        and (attribute is None or child.get(attribute) is not None)
    )
    return islice(matches, limit) if limit is not None else matches


def first_tag(node, names, *, attribute=None):
    return next(tags(node, names, attribute=attribute), None)
