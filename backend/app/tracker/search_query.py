"""Provider-neutral search conditions and explicit capability-based omissions."""

import re
from dataclasses import dataclass
from datetime import datetime

from .errors import DiscoveryError


@dataclass(frozen=True)
class TextFilter:
    field: str
    value: str


@dataclass(frozen=True)
class FacetFilter:
    field: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class DateRange:
    field: str
    lower: datetime
    upper: datetime


@dataclass(frozen=True)
class BooleanFilter:
    operator: str
    children: tuple


@dataclass
class SearchPlan:
    condition: object | None = None
    sort: str = ""
    offset: int = 0
    page_size: int = 200


class Omissions:
    def __init__(self, provider):
        self.provider = provider
        self.notes = []

    def add(self, label, reason="this filter is not supported"):
        label = re.sub(r"[^\w .()-]", " ", label)[:100].strip() or "A filter"
        note = f"{label} omitted from the {self.provider} search: {reason}."
        if note not in self.notes:
            self.notes.append(note)


def join(operator, nodes):
    nodes = tuple(node for node in nodes if node is not None)
    if not nodes:
        return None
    return nodes[0] if len(nodes) == 1 else BooleanFilter(operator, nodes)


def row_expression(rows, precedence):
    """Group form rows before an adapter removes unsupported conditions."""
    if not rows:
        return None
    if len(rows) > 50:
        raise DiscoveryError("Use at most 50 search rows.")
    values, operators = [rows[0][1]], []

    def reduce():
        right, left = values.pop(), values.pop()
        values.append(BooleanFilter(operators.pop(), (left, right)))

    for operator, node in rows[1:]:
        if operator not in precedence:
            raise DiscoveryError("Choose AND, OR, or NOT between search rows.")
        while operators and precedence[operators[-1]] >= precedence[operator]:
            reduce()
        operators.append(operator)
        values.append(node)
    while operators:
        reduce()
    return values[0]


def prune(node, supported, omissions):
    """Remove unsupported leaves while preserving surviving Boolean grouping."""
    if not isinstance(node, BooleanFilter):
        return node if node is not None and supported(node) else None
    children = [prune(child, supported, omissions) for child in node.children]
    if node.operator == "ANDNOT":
        left, right = children
        if left is None:
            if right is not None:
                omissions.add(
                    "Exclusion",
                    "its preceding positive search condition is unavailable",
                )
            return None
        return BooleanFilter("ANDNOT", (left, right)) if right else left
    return join(node.operator, children)
