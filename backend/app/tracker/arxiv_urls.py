"""Translate arXiv listing URLs without changing their search terms."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from .errors import DiscoveryError

HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
API = "https://export.arxiv.org/api/query"
FIELDS = {
    "all": "all",
    "title": "ti",
    "author": "au",
    "abstract": "abs",
    "comments": "co",
    "journal_ref": "jr",
    "report_num": "rn",
}
PAPER = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+(?:\.[A-Z]{2})?/\d{7})(?:v[1-9]\d*)?"
)
API_KEYS = {"search_query", "id_list", "start", "max_results", "sortBy", "sortOrder"}


def is_api(url):
    p = urlsplit(url)
    return p.hostname in HOSTS and p.path.rstrip("/") == "/api/query"


def is_source(url):
    p = urlsplit(url)
    return p.hostname in HOSTS and not p.path.startswith(("/rss/", "/atom/"))


def paper_id(url):
    p = urlsplit(url)
    if p.hostname not in HOSTS:
        return None
    match = re.fullmatch(r"/(?:abs|pdf|html)/(.+)", p.path.rstrip("/"))
    value = match[1].removesuffix(".pdf") if match else ""
    return re.sub(r"v[1-9]\d*$", "", value) if PAPER.fullmatch(value) else None


def request_url(url):
    """Keep cache normalization separate from the API's wire representation."""
    if not is_api(url):
        return url
    params = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    keys = ["search_query", "id_list", "start", "max_results", "sortBy", "sortOrder"]
    ordered = [(key, params.pop(key)) for key in keys if key in params]
    ordered.extend(sorted(params.items()))
    return API + "?" + urlencode(ordered, quote_via=quote, safe=":,")


def integer(params, key, default, maximum):
    value = params.get(key, str(default))
    if not re.fullmatch(r"[0-9]{1,6}", value) or not 0 <= int(value) <= maximum:
        raise DiscoveryError(f"arXiv {key} must be between 0 and {maximum:,}.")
    return int(value)


def expression(text, field):
    if not text.strip() or len(text) > 1500:
        raise DiscoveryError("Enter an arXiv search with 1 to 1,500 characters.")
    tokens = re.findall(r'"[^"\n]+"|\(|\)|[^\s()"]+', text)
    if (
        text.count('"') != sum(t.startswith('"') for t in tokens) * 2
        or len(tokens) > 100
    ):
        raise DiscoveryError("Use balanced quotes and a shorter arXiv search.")
    output, depth, operand = [], 0, True
    for token in tokens:
        if token in {"AND", "OR", "ANDNOT", "NOT"}:
            if operand:
                raise DiscoveryError("Check the operators in the arXiv search.")
            output.append("ANDNOT" if token == "NOT" else token)
            operand = True
        elif token == ")":
            if operand or depth == 0:
                raise DiscoveryError("Check the parentheses in the arXiv search.")
            output.append(token)
            depth -= 1
        else:
            if not operand:
                output.append("AND")
            if token == "(":
                depth += 1
                if depth > 10:
                    raise DiscoveryError("The arXiv search is too deeply nested.")
                output.append(token)
                operand = True
            else:
                # A colon in website text is literal, not an injected API field.
                if ":" in token and not token.startswith('"'):
                    token = '"' + token + '"'
                output.append(field + ":" + token)
                operand = False
    if operand or depth:
        raise DiscoveryError("Check the arXiv search terms and parentheses.")
    return " ".join(output).replace("( ", "(").replace(" )", ")")


def category(value):
    groups = {"cs", "math", "stat", "econ", "eess", "q-bio", "q-fin"}
    if value in groups:
        return "cat:" + value + ".*"
    if re.fullmatch(
        r"(?:cs|math|stat|econ|eess|q-bio|q-fin|physics|astro-ph|cond-mat|nlin)\.[A-Za-z-]+",
        value,
    ):
        return "cat:" + value
    if value in {"astro-ph", "cond-mat", "nlin"}:
        return f"(cat:{value} OR cat:{value}.*)"
    if value in {
        "gr-qc",
        "hep-ex",
        "hep-lat",
        "hep-ph",
        "hep-th",
        "nucl-ex",
        "nucl-th",
        "quant-ph",
    }:
        return "cat:" + value
    if value == "physics":
        return (
            "("
            + " OR ".join(
                "cat:" + c
                for c in [
                    "physics.*",
                    "astro-ph.*",
                    "astro-ph",
                    "cond-mat.*",
                    "cond-mat",
                    "nlin.*",
                    "nlin",
                    "gr-qc",
                    "hep-ex",
                    "hep-lat",
                    "hep-ph",
                    "hep-th",
                    "nucl-ex",
                    "nucl-th",
                    "quant-ph",
                ]
            )
            + ")"
        )
    raise DiscoveryError(
        "This arXiv subject is not supported. Use an API URL with a cat: filter."
    )


@dataclass(frozen=True)
class Query:
    params: dict
    title: str
    notes: tuple = ()

    def url(self, start=None):
        values = dict(self.params)
        if start is not None:
            values["start"] = start
        return request_url(API + "?" + urlencode(values))


def translate(url):
    p = urlsplit(url)
    if (
        p.hostname not in HOSTS
        or p.username
        or p.password
        or p.port not in (None, 80, 443)
        or p.scheme not in {"https", "http"}
    ):
        raise DiscoveryError("Use a public arXiv search, category, paper, or API URL.")
    pairs = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k not in {"fbclid", "gclid", "ref_src"}
    ]
    params = dict(pairs)
    if len(params) != len(pairs):
        raise DiscoveryError("Remove repeated parameters from the arXiv URL.")
    path = p.path.rstrip("/")
    if is_api(url):
        if params.keys() - API_KEYS:
            raise DiscoveryError("The arXiv API URL has unsupported parameters.")
        search, ids = params.get("search_query", ""), params.get("id_list", "")
        if (
            not search
            and not ids
            or len(search) > 1800
            or re.search(r"[\x00-\x1f\x7f]", search)
        ):
            raise DiscoveryError("Use an arXiv API search_query or id_list.")
        if ids and (
            len(ids.split(",")) > 200
            or any(not PAPER.fullmatch(i) for i in ids.split(","))
        ):
            raise DiscoveryError("The arXiv paper ID list is invalid or too long.")
        if params.get("sortBy", "relevance") not in {
            "relevance",
            "submittedDate",
            "lastUpdatedDate",
        } or params.get("sortOrder", "descending") not in {"ascending", "descending"}:
            raise DiscoveryError("Choose a supported arXiv sort order.")
        params["start"] = integer(params, "start", 0, 29999)
        params["max_results"] = min(
            200, max(1, integer(params, "max_results", 200, 30000))
        )
        return Query(params, "arXiv: " + (search or ids)[:180])
    if paper_id(url):
        if params.keys() - {"download"}:
            raise DiscoveryError("The arXiv paper URL has unsupported parameters.")
        value = path.split("/", 2)[2].removesuffix(".pdf")
        return Query(
            {"id_list": value, "start": 0, "max_results": 200}, "arXiv: " + value
        )
    match = re.fullmatch(r"/search(?:/([^/]+))?", path)
    if match and match[1] != "advanced":
        if params.keys() - {
            "query",
            "searchtype",
            "abstracts",
            "order",
            "size",
            "start",
        }:
            raise DiscoveryError(
                "This arXiv search has unsupported filters. Use an API search URL to preserve them."
            )
        field = FIELDS.get(params.get("searchtype", "all"))
        if not field:
            raise DiscoveryError(
                "This arXiv search field has no supported API equivalent. Use an API search URL."
            )
        text = params.get("query", "")
        search = expression(text, field)
        if match[1]:
            search = "(" + search + ") AND " + category(match[1])
        values = {
            "search_query": search,
            "start": integer(params, "start", 0, 29999),
            "max_results": min(200, max(1, integer(params, "size", 200, 30000))),
        }
        order = params.get("order", "")
        notes = ()
        if order and order != "relevance":
            key = order.lstrip("-+")
            sort = {
                "submitted_date": "submittedDate",
                "announced_date_first": "submittedDate",
                "announced_date_last": "lastUpdatedDate",
                "last_updated_date": "lastUpdatedDate",
            }.get(key)
            if not sort:
                raise DiscoveryError(
                    "This arXiv sort order is unsupported. Use an API search URL."
                )
            values.update(
                sortBy=sort,
                sortOrder="descending" if order.startswith("-") else "ascending",
            )
            if key.startswith("announced_"):
                notes = (
                    "The API sorts by submission or update date; website announcement dates may differ.",
                )
        return Query(
            values,
            "arXiv: " + text[:150] + (" (" + match[1] + ")" if match[1] else ""),
            notes,
        )
    match = re.fullmatch(r"/list/([^/]+)/(recent|new)", path)
    if match and params.keys() <= {"skip", "show"}:
        return Query(
            {
                "search_query": category(match[1]),
                "start": integer(params, "skip", 0, 29999),
                "max_results": 200,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
            "arXiv: " + match[1],
            (
                "Tracking this subject by submission date, including older papers; announcement-only sections are not reproduced.",
            ),
        )
    raise DiscoveryError(
        "Use an arXiv search, recent category, paper, or API URL. Advanced searches need an equivalent API query; filters are never silently removed."
    )
