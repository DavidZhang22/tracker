"""Conservative screening for optional source excerpts, not a legality verdict.

Only already-collected text is inspected. No link is fetched, blocked or labelled
safe; user titles, imported columns, and manual descriptions are left intact.
"""

import re
import unicodedata
from html import unescape
from urllib.parse import unquote

MAX_TEXT = 8192
SPACE = re.compile(r"\s+")
INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
RULES = tuple(
    (name, re.compile(pattern, re.I))
    for name, pattern in (
        (
            "sexual-exploitation",
            r"\b(?:child sexual abuse material|child pornography|underage (?:porn|sex videos)|non[- ]consensual (?:intimate|sexual)|revenge porn)\b",
        ),
        (
            "explicit-sexual",
            r"\b(?:p[o0]rn(?:ography|ographic)?|xxx videos?|explicit (?:adult|sexual) (?:content|videos?|images?|photos?)|hardcore sex)\b",
        ),
        (
            "graphic-violence",
            r"\b(?:uncensored gore|graphic (?:execution|torture) (?:footage|videos?)|real (?:beheading|torture) (?:footage|videos?))\b",
        ),
        (
            "harm-instructions",
            r"\b(?:how to (?:build|make) (?:a |an )?(?:pipe bomb|explosive device)|step[- ]by[- ]step (?:bomb[- ]making|suicide) (?:guide|instructions)|instructions (?:for|to) (?:commit suicide|carry out an attack))\b",
        ),
        (
            "illicit-services",
            r"\b(?:(?:stolen (?:passwords|credentials|credit cards)|phishing kits|ransomware kits|forged passports) for sale|(?:buy|sell) (?:stolen credentials|child sexual abuse material))\b",
        ),
    )
)
HINTS = (
    "porn",
    "p0rn",
    "xxx",
    "sex",
    "explicit",
    "consensual",
    "gore",
    "execution",
    "torture",
    "beheading",
    "bomb",
    "explosive",
    "suicide",
    "attack",
    "stolen",
    "phishing",
    "ransomware",
    "forged",
)
PROTECTIVE = re.compile(
    r"\b(?:prevention|preventing|reporting|combat(?:ing)?|survivor support|victim support|"
    r"(?:research|study|studies) (?:on|of|into)|recovery (?:support|resources)|addiction (?:help|recovery)|"
    r"how to report|how to prevent|detect(?:ing|ion of))\b",
    re.I,
)
PROMOTIONAL = re.compile(
    r"\b(?:watch|stream|download|buy|sell|for sale|subscribe to|members[- ]only|"
    r"step[- ]by[- ]step|instructions|how to (?:build|make))\b",
    re.I,
)


def normalized(value, limit=MAX_TEXT):
    text = unescape(str(value or "")[:limit])
    text = unicodedata.normalize("NFKC", text).casefold()
    return SPACE.sub(" ", INVISIBLE.sub("", text))[:limit]


def excerpt_risk(title="", text="", url=""):
    """Return a coarse reason, or None; an unflagged excerpt is not certified safe."""
    fields = [normalized(title, 1000), normalized(text)]
    if url:
        fields.append(normalized(unquote(unquote(str(url)[:2048])), 2048))
    for field in fields:
        if not any(hint in field for hint in HINTS):
            continue
        for name, pattern in RULES:
            match = pattern.search(field)
            if not match:
                continue
            # Do not confuse plainly protective/educational discussion with a
            # promoted inventory. These narrow exceptions are not an assurance.
            if PROTECTIVE.search(field) and not PROMOTIONAL.search(field):
                continue
            return name
    return None


def item_risk(row):
    return excerpt_risk(
        row.get("title"), row.get("source_summary"), row.get("url")
    ) or excerpt_risk(text=row.get("description_auto"))


def public_metadata(row):
    """Redact automatic excerpts at the API boundary, including legacy records."""
    result = dict(row)
    if "description_auto" in result or "description_method" in result:
        suppressed = bool(item_risk(result))
        result["description_suppressed"] = suppressed
        if suppressed:
            result["description_auto"] = ""
            result["source_summary"] = ""
            result["description_method"] = "safety-filtered"
            result["description"] = result.get("description_override") or ""
    if result.get("summary") and excerpt_risk(
        result.get("title"), result["summary"], result.get("url")
    ):
        result["summary"] = ""
        result["summary_suppressed"] = True
    return result


def public_scan(payload):
    result = public_metadata(payload)
    result["entries"] = [public_metadata(entry) for entry in payload.get("entries", [])]
    return result
