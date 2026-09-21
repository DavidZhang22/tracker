"""Parse website search forms into conditions, then compile supported arXiv filters."""

import calendar
import re
from datetime import UTC, date, datetime, time, timedelta

from dateutil.tz import gettz

from .arxiv_urls import FIELDS, PAPER, category, expression, integer
from .errors import DiscoveryError
from .search_query import (
    BooleanFilter,
    DateRange,
    FacetFilter,
    Omissions,
    SearchPlan,
    TextFilter,
    join,
    prune,
    row_expression,
)

GROUPS = {
    "computer_science": "cs",
    "economics": "econ",
    "eess": "eess",
    "mathematics": "math",
    "q_biology": "q-bio",
    "q_finance": "q-fin",
    "statistics": "stat",
}
FIELD_LABELS = {
    "doi": "DOI filter",
    "acm_class": "ACM classification",
    "msc_class": "MSC classification",
    "orcid": "ORCID filter",
    "author_id": "Author ID filter",
    "paper_id": "Paper ID filter",
    "cross_list_category": "Cross-list-only category",
    "primary_category": "Primary-only category",
}
SORTS = {
    "submitted_date": "lastUpdatedDate",
    "submitted_date_first": "submittedDate",
    "last_updated_date": "lastUpdatedDate",
}
ROW = re.compile(r"terms-(\d{1,3})-(term|field|operator)")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def current_time():
    return datetime.now(UTC)


def partial_date(value, upper=False):
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise DiscoveryError("Use YYYY, YYYY-MM, or YYYY-MM-DD for arXiv dates.")
    parts = [int(part) for part in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 12 if upper else 1
    try:
        day = (
            parts[2]
            if len(parts) > 2
            else calendar.monthrange(year, month)[1]
            if upper
            else 1
        )
        result = date(year, month, day)
        if year < 1991 or year > 9998:
            raise ValueError()
        return result
    except ValueError as exc:
        raise DiscoveryError("Enter a valid arXiv date from 1991 onward.") from exc


def date_filter(params, omissions):
    mode = params.get("date-filter_by", "all_dates")
    if mode == "all_dates":
        return None
    field = params.get("date-date_type", "submitted_date")
    if field != "submitted_date_first":
        label = {
            "submitted_date": "Latest-submission date filter",
            "announced_date_first": "Announcement-date filter",
        }.get(field, "Date filter")
        omissions.add(label, "the API can filter only original submission dates")
        return None
    zone = gettz("America/New_York")
    if zone is None:
        omissions.add("Submission-date filter", "the source timezone is unavailable")
        return None
    now = current_time().astimezone(zone)
    if mode == "specific_year":
        year = params.get("date-year", "")
        if not re.fullmatch(r"\d{4}", year):
            raise DiscoveryError("Enter a four-digit arXiv year.")
        lower = partial_date(year)
        upper = date(lower.year + 1, 1, 1)
    elif mode == "date_range":
        start, end = params.get("date-from_date", ""), params.get("date-to_date", "")
        if not start or not end:
            omissions.add(
                "Open-ended submission-date filter",
                "the API adapter needs both date boundaries",
            )
            return None
        lower, upper = partial_date(start), partial_date(end, upper=True)
    elif mode == "past_12":
        lower = date(now.year - 1, now.month, 1)
        return DateRange(
            "submitted_date_first",
            datetime.combine(lower, time(), zone).astimezone(UTC),
            now.astimezone(UTC),
        )
    else:
        omissions.add("Date filter", "this date-range option is unavailable")
        return None
    lower, upper = (
        datetime.combine(value, time(), zone).astimezone(UTC)
        for value in (lower, upper)
    )
    if lower >= upper:
        raise DiscoveryError("The arXiv end date must be later than the start date.")
    return DateRange("submitted_date_first", lower, upper)


def selected(params, key, omissions):
    value = params.get(key, "").lower()
    if value in {"y", "yes", "true", "1", "on"}:
        return True
    if value not in {"", "n", "no", "false", "0", "off"}:
        omissions.add(
            key.replace("classification-", "").replace("_", " ") + " filter",
            "this selection value is unavailable",
        )
    return False


def paper_value(value):
    return (
        value[1:-1]
        if value.startswith('"') and value.endswith('"') and value.count('"') == 2
        else value
    )


def parse_form(params, scope, advanced, omissions):
    allowed = {"query", "searchtype", "advanced", "abstracts", "order", "size", "start"}
    plan = SearchPlan(
        sort=params.get("order", ""),
        offset=integer(params, "start", 0, 29999),
        page_size=min(200, max(1, integer(params, "size", 200, 30000))),
    )
    if advanced:
        rows = {}
        for key, value in params.items():
            if match := ROW.fullmatch(key):
                allowed.add(key)
                row = rows.setdefault(int(match[1]), {})
                if match[2] in row:
                    raise DiscoveryError(
                        "Remove repeated fields from the same search row."
                    )
                row[match[2]] = value
        terms = []
        for index in sorted(rows):
            row = rows[index]
            value = row.get("term", "").strip()
            if not value:
                continue
            if len(value) > 1500 or CONTROL.search(value):
                raise DiscoveryError(
                    "Use shorter search terms without control characters."
                )
            operator = row.get("operator", "AND").upper()
            terms.append(
                (
                    "ANDNOT" if operator == "NOT" else operator,
                    TextFilter(row.get("field", "all"), value),
                )
            )
        plan.condition = row_expression(terms, {"OR": 1, "AND": 2, "ANDNOT": 3})
        subjects = []
        for key, category_id in GROUPS.items():
            allowed.add("classification-" + key)
            if selected(params, "classification-" + key, omissions):
                subjects.append(category_id)
        allowed.update(
            {
                "classification-physics",
                "classification-physics_archives",
                "classification-include_cross_list",
                "include_older_versions",
                "date-filter_by",
                "date-date_type",
                "date-year",
                "date-from_date",
                "date-to_date",
            }
        )
        if selected(params, "classification-physics", omissions):
            archive = params.get("classification-physics_archives", "all")
            try:
                if archive not in {
                    "all",
                    "physics",
                    "astro-ph",
                    "cond-mat",
                    "nlin",
                    "gr-qc",
                    "hep-ex",
                    "hep-lat",
                    "hep-ph",
                    "hep-th",
                    "nucl-ex",
                    "nucl-th",
                    "quant-ph",
                }:
                    raise DiscoveryError("Unsupported physics archive")
                category("physics" if archive == "all" else archive)
                subjects.append("physics" if archive == "all" else archive)
            except DiscoveryError:
                omissions.add("Physics archive filter", "this archive is unavailable")
        if (
            params.get("classification-include_cross_list", "include") != "include"
            and subjects
        ):
            omissions.add(
                "Primary-category-only restriction",
                "the API also includes cross-listed papers",
            )
        if selected(params, "include_older_versions", omissions):
            omissions.add(
                "Older-version search", "the API search returns current paper versions"
            )
        plan.condition = join(
            "AND",
            (
                plan.condition,
                FacetFilter("subject", tuple(subjects)) if subjects else None,
                date_filter(params, omissions),
            ),
        )
    else:
        value = params.get("query", "").strip()
        if not value or CONTROL.search(value):
            raise DiscoveryError("Enter an arXiv search without control characters.")
        plan.condition = TextFilter(params.get("searchtype", "all"), value)
        if scope:
            try:
                category(scope)
                plan.condition = join(
                    "AND", (plan.condition, FacetFilter("subject", (scope,)))
                )
            except DiscoveryError:
                omissions.add("Subject filter", "this subject is unavailable")
    for key in sorted(params.keys() - allowed):
        if params[key]:
            omissions.add(
                key.replace("-", " ").replace("_", " "),
                "this option has no supported API equivalent",
            )
    return plan


def render(node):
    if isinstance(node, TextFilter):
        if node.field == "paper_id":
            return "id:" + re.sub(r"v[1-9]\d*$", "", paper_value(node.value))
        return expression(node.value, FIELDS[node.field])
    if isinstance(node, FacetFilter):
        parts = [category(value) for value in node.values]
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
    if isinstance(node, DateRange):
        # Include the boundary minute in the API query, then apply exact bounds
        # using returned publication metadata. Relative upper bounds share a daily URL.
        upper = node.upper
        if upper.second or upper.microsecond:
            upper = (upper + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        return f"submittedDate:[{node.lower:%Y%m%d%H%M} TO {upper:%Y%m%d%H%M}]"
    values = [render(child) for child in node.children]
    return (" " + node.operator + " ").join("(" + value + ")" for value in values)


def compile_form(params, scope=None, advanced=False):
    omissions = Omissions("arXiv API")
    plan = parse_form(params, scope, advanced, omissions)

    def supported(node):
        if isinstance(node, TextFilter) and node.field == "paper_id":
            value = paper_value(node.value)
            if not PAPER.fullmatch(value):
                omissions.add(
                    "Paper ID filter", "use one complete paper ID for this field"
                )
                return False
            if re.search(r"v[1-9]\d*$", value):
                omissions.add(
                    "Paper version restriction",
                    "API searches return the latest version of that paper",
                )
            return True
        if isinstance(node, TextFilter) and node.field not in FIELDS:
            omissions.add(
                FIELD_LABELS.get(node.field, node.field.replace("_", " ") + " filter"),
                "this search field has no supported API equivalent",
            )
            return False
        return True

    condition = prune(plan.condition, supported, omissions)
    if condition is None:
        detail = " ".join(omissions.notes)
        raise DiscoveryError(
            "No supported search terms or filters remain. Add a title, author, subject, or original-submission date filter. "
            + detail
        )
    query = render(condition)
    if len(query) > 3500:
        raise DiscoveryError(
            "The translated API query is too long. Use fewer search terms."
        )
    values = {
        "search_query": query,
        "start": plan.offset,
        "max_results": plan.page_size,
    }
    order = plan.sort
    if order and order != "relevance":
        key = order[1:] if order[0] in "-+" else order
        if key in SORTS:
            values.update(
                sortBy=SORTS[key],
                sortOrder="descending" if order.startswith("-") else "ascending",
            )
        else:
            omissions.add(
                "Sort order", "the API uses relevance because this sort is unavailable"
            )

    def dates(node):
        if isinstance(node, DateRange):
            return (node.lower, node.upper)
        if isinstance(node, BooleanFilter):
            return next(
                (value for child in node.children if (value := dates(child))), None
            )
        return None

    # Retain the compact established representation for basic search cache reuse.
    if (
        not advanced
        and isinstance(condition, BooleanFilter)
        and len(condition.children) == 2
        and isinstance(condition.children[1], FacetFilter)
        and len(condition.children[1].values) == 1
    ):
        values["search_query"] = (
            "("
            + render(condition.children[0])
            + ") AND "
            + render(condition.children[1])
        )
    title = (
        "arXiv: "
        + (
            params.get("query", "")
            if not advanced
            else "; ".join(
                value
                for key, value in params.items()
                if ROW.fullmatch(key) and key.endswith("-term") and value
            )
        )[:150]
    )
    return (
        values,
        title + (" (" + scope + ")" if scope else ""),
        tuple(omissions.notes),
        dates(condition),
    )
