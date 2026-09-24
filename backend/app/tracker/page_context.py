"""Per-document metadata caches; never shared across scans or observation times."""

from collections import Counter

from bs4 import Tag

from .dom import first_tag


class PageContext:
    def __init__(self):
        self.dates = {}
        self.headings = {}
        self.boundaries = {}
        self.snippets = {}
        self.detail_texts = {}

    def node_dates(self, node):
        from .dates import node_dates

        key = id(node)
        if key not in self.dates:
            self.dates[key] = node_dates(node)
        # Callers attach provenance; never mutate a different record's evidence.
        return self.dates[key].copy()

    def heading_count(self, node):
        key = id(node)
        if key not in self.headings:
            urls = set()
            for link in node.descendants:
                if not isinstance(link, Tag) or link.name != "a":
                    continue
                href = link.get("href")
                if href is None or href.startswith("#"):
                    continue
                for parent in link.parents:
                    if parent.name in {"h1", "h2", "h3"} or "entry-title" in parent.get(
                        "class", []
                    ):
                        urls.add(href)
                        break
                if len(urls) > 1:
                    break
            self.headings[key] = len(urls)
        return self.headings[key]

    def sibling_boundary(self, node):
        parent = node.parent
        if parent is None:
            return False
        key = id(parent)
        if key not in self.boundaries:
            counts, linked = Counter(), set()
            for sibling in parent.children:
                if (
                    isinstance(sibling, Tag)
                    and first_tag(sibling, {"a"}, attribute="href") is not None
                ):
                    counts[sibling.name, tuple(sibling.get("class", []))] += 1
                    linked.add(id(sibling))
            self.boundaries[key] = counts, linked
        counts, linked = self.boundaries[key]
        return counts[node.name, tuple(node.get("class", []))] > (id(node) in linked)

    def snippet(self, node, limit=1500):
        from .record_context import snippets

        key = id(node), limit
        if key not in self.snippets:
            self.snippets[key] = snippets(node, limit)
        return self.snippets[key]

    def details(self, node, limit=1500):
        from .context_details import details_text

        key = id(node), limit
        if key not in self.detail_texts:
            self.detail_texts[key] = details_text(node, limit)
        return self.detail_texts[key]
