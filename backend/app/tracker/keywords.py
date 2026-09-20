"""Bounded, literal keyword matching; no user-supplied regular expressions."""

import re
import unicodedata
from functools import lru_cache

LANGUAGES = {
    "en": "English",
    "ja": "Japanese",
    "ja-ro": "Japanese Romanized",
    "ko": "Korean",
    "zh": "Chinese Simplified",
    "zh-hk": "Chinese Traditional",
    "es": "Spanish",
    "es-la": "Spanish Latin America",
    "pt": "Portuguese",
    "pt-br": "Portuguese Brazilian",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ru": "Russian",
    "uk": "Ukrainian",
    "pl": "Polish",
    "tr": "Turkish",
    "ar": "Arabic",
    "he": "Hebrew",
    "hi": "Hindi",
    "id": "Indonesian",
    "vi": "Vietnamese",
    "th": "Thai",
    "sr": "Serbian",
    "hu": "Hungarian",
    "fa": "Persian",
    "ta": "Tamil",
    "ne": "Nepali",
    "el": "Greek",
    "nl": "Dutch",
    "cs": "Czech",
    "ro": "Romanian",
    "sv": "Swedish",
    "fi": "Finnish",
    "da": "Danish",
    "no": "Norwegian",
    "bn": "Bengali",
    "ms": "Malay",
    "tl": "Tagalog",
    "bg": "Bulgarian",
}


def normalize(value):
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(re.findall(r"[^\W_]+", text, re.UNICODE))


def terms(value):
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("Keywords must contain at most 300 characters.")
    result = list(dict.fromkeys(normalize(t) for t in value.split(",") if normalize(t)))
    if len(result) > 10 or any(len(t) > 80 for t in result):
        raise ValueError(
            "Use up to 10 keywords or phrases, each at most 80 characters."
        )
    return result


def _language_aliases():
    aliases = {}
    for code, name in LANGUAGES.items():
        words = {normalize(code), normalize(name)}
        for family in ("spanish", "portuguese", "chinese"):
            if normalize(name).startswith(family + " "):
                words.add(family)
        for word in words:
            aliases.setdefault(word, []).append(code)
    return aliases


_LANGUAGE_CODES = _language_aliases()


@lru_cache(maxsize=256)
def language_codes(keyword):
    return list(_LANGUAGE_CODES.get(normalize(keyword), ()))


def language_text(value):
    if not isinstance(value, str):
        return ""
    return f"{LANGUAGES.get(value, value)} {value}" if value else ""


def matches(entry, keywords):
    text = " " + normalize(" ".join((entry.title, entry.summary, entry.context))) + " "
    return all(
        entry.language in codes
        if entry.language and (codes := language_codes(term))
        else " " + term + " " in text
        for term in terms(keywords)
    )
