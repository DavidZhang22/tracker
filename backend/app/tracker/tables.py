"""Bounded semantic table context, independent of the publishing host."""

import re

TABLE_FEATURES = (
    "table_headers",
    "action_column",
    "identity_column",
    "role_column",
    "job_table",
    "image_label",
    "action_label",
    "table_column_position",
)
ACTION = re.compile(
    r"\b(?:apply|application|applications|link|links|details|register|registration)\b",
    re.I,
)
ROLE = re.compile(r"\b(?:role|position|job|title|opportunity)\b", re.I)
COMPANY = re.compile(r"\b(?:company|employer|organization|organisation)\b", re.I)


def anchor_label(anchor):
    return (
        anchor.get_text(" ", strip=True)
        or anchor.get("aria-label")
        or anchor.get("title")
        or " ".join(image.get("alt", "") for image in anchor.find_all("img", limit=3))
    ).strip()[:500]


def table_context(soup):
    """Map anchors to their own columns; never borrow another row's metadata."""
    result = {}
    remaining = 10000
    for table in soup.find_all("table", limit=100):
        headers = table.find("tr")
        headers = headers.find_all(["th", "td"], recursive=False) if headers else []
        names = [cell.get_text(" ", strip=True)[:100] for cell in headers]
        roles = [i for i, name in enumerate(names) if ROLE.search(name)]
        companies = [i for i, name in enumerate(names) if COMPANY.search(name)]
        actions = [i for i, name in enumerate(names) if ACTION.search(name)]
        is_job = bool(roles and companies and actions)
        company = ""
        for row in table.find_all("tr", limit=5001):
            remaining -= 1
            if remaining < 0 or len(result) >= 20000:
                return result
            if row.find_parent("table") is not table:
                continue
            cells = row.find_all("td", recursive=False)
            if len(cells) != len(names) or any(
                c.get("colspan") or c.get("rowspan") for c in cells
            ):
                continue
            texts = [c.get_text(" ", strip=True)[:500] for c in cells]
            if is_job:
                current = texts[companies[0]].strip()
                if current not in {"", "↳", "↪", "→", "〃", '"'}:
                    company = current
            for column, cell in enumerate(cells):
                for anchor in cell.find_all("a", href=True, limit=20):
                    label = anchor_label(anchor)
                    target = column in actions
                    title = (
                        " · ".join(filter(None, [company, texts[roles[0]]]))
                        if is_job and target
                        else label
                    )
                    if (
                        is_job
                        and target
                        and label
                        and not re.fullmatch(r"apply(?: now)?|application", label, re.I)
                    ):
                        title += " · " + label
                    metadata = [
                        f"{name}: {text}"
                        for name, text in zip(names, texts, strict=True)
                        if text and re.search(r"location|age|posted|date", name, re.I)
                    ]
                    result[id(anchor)] = dict(
                        job=is_job,
                        action=target,
                        title=title[:1000],
                        summary=" · ".join(metadata)[:1000],
                        features=[
                            float(v)
                            for v in (
                                bool(names),
                                target,
                                column in companies,
                                column in roles,
                                is_job,
                                bool(anchor.find("img")),
                                bool(ACTION.search(label)),
                                column / max(len(names) - 1, 1),
                            )
                        ],
                    )
    return result
