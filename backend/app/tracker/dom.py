"""Small DOM walks used by the feature extractors; no CSS or text matching."""

from itertools import islice

from bs4 import Tag

HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


def tags(node, names, *, attribute=None, limit=None):
    matches = (
        child
        for child in node.descendants
        if isinstance(child, Tag)
        and (names is None or child.name in names)
        and (attribute is None or child.get(attribute) is not None)
    )
    return islice(matches, limit) if limit is not None else matches


def first_tag(node, names, *, attribute=None):
    return next(tags(node, names, attribute=attribute), None)


def first_parent(node, names=None, *, class_pattern=None):
    """Nearest matching ancestor without constructing a SoupStrainer per link."""
    for parent in node.parents:
        if names is not None and parent.name not in names:
            continue
        if class_pattern is not None:
            classes = parent.get("class", [])
            if not any(class_pattern.search(value) for value in classes) and not (
                len(classes) > 1 and class_pattern.search(" ".join(classes))
            ):
                continue
        return parent
    return None
