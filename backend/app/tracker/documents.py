"""Bounded compressed documents; decode only when a consumer is ready."""

import base64
import codecs
import gzip
import hashlib
import re
import zlib
from dataclasses import dataclass

from .errors import DiscoveryError

MAX_RESPONSE = 8_000_000
MAX_TEXT = MAX_RESPONSE * 3
MAX_PACKED = MAX_TEXT + 16_384
CHUNK = 65_536
BLOCKED_TITLE = re.compile(
    r"<title>\s*(?:Just a moment|Attention Required|Access Denied)", re.I
)


def chunks(data, compression, limit=MAX_RESPONSE):
    if compression == "identity":
        if len(data) > limit:
            raise DiscoveryError("The page exceeds the 8 MB scan limit.")
        for start in range(0, len(data), CHUNK):
            yield data[start : start + CHUNK]
        return
    if compression not in {"gzip", "deflate"}:
        raise DiscoveryError("The source used unsupported response compression.")
    unpacker = zlib.decompressobj(31 if compression == "gzip" else zlib.MAX_WBITS)
    size = 0
    try:
        for start in range(0, len(data), CHUNK):
            pending = data[start : start + CHUNK]
            while pending:
                block = unpacker.decompress(pending, min(CHUNK, limit - size + 1))
                pending = unpacker.unconsumed_tail
                size += len(block)
                if size > limit:
                    raise DiscoveryError(
                        "The expanded response exceeds the 8 MB scan limit."
                    )
                if unpacker.unused_data:
                    raise DiscoveryError(
                        "The compressed response contains trailing data."
                    )
                if block:
                    yield block
        if not unpacker.eof:
            raise DiscoveryError("The compressed response is incomplete.")
    except zlib.error as exc:
        raise DiscoveryError("The compressed response is unreadable.") from exc


@dataclass(frozen=True, slots=True)
class Document:
    data: bytes
    compression: str
    encoding: str
    expanded_size: int
    text_size: int
    digest: str
    has_scripts: bool

    @classmethod
    def from_wire(cls, data, compression="identity", encoding="utf-8"):
        if len(data) > MAX_RESPONSE:
            raise DiscoveryError("The page exceeds the 8 MB scan limit.")
        return cls._from_data(data, compression, encoding, MAX_RESPONSE)

    @classmethod
    def _from_data(cls, data, compression, encoding, limit):
        try:
            decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        except (LookupError, TypeError) as exc:
            raise DiscoveryError(
                "The source used an unsupported text encoding."
            ) from exc
        digest, expanded, text_size = hashlib.sha256(), 0, 0
        carry, scripts = "", False

        def inspect(text):
            nonlocal carry, scripts, text_size
            if not isinstance(text, str):
                raise DiscoveryError("The source used an unsupported text encoding.")
            encoded = text.encode("utf-8")
            text_size += len(encoded)
            if text_size > MAX_TEXT:
                raise DiscoveryError(
                    "The decoded response exceeds the text size limit."
                )
            digest.update(encoded)
            view = carry + text
            lower = view.lower()
            scripts = scripts or "<script" in lower
            if BLOCKED_TITLE.search(view):
                raise DiscoveryError(
                    "The source requires a browser check. Retry later or supply a public feed URL."
                )
            carry = view[-7:]
            start = lower.rfind("<title>")
            if start >= 0:
                suffix = view[start + 7 :].lstrip()
                if any(
                    label.lower().startswith(suffix.lower())
                    for label in (
                        "Just a moment",
                        "Attention Required",
                        "Access Denied",
                    )
                ):
                    carry = "<title>" + suffix

        try:
            for block in chunks(data, compression, limit):
                expanded += len(block)
                inspect(decoder.decode(block))
            inspect(decoder.decode(b"", final=True))
        except (UnicodeError, LookupError, TypeError, AssertionError) as exc:
            raise DiscoveryError(
                "The source used an unsupported text encoding."
            ) from exc
        if compression == "identity":
            packed = gzip.compress(data, compresslevel=1, mtime=0)
            # Tiny/incompressible documents do not benefit from a gzip envelope.
            if len(packed) < len(data):
                data, compression = packed, "gzip"
        return cls(
            bytes(data),
            compression,
            encoding,
            expanded,
            text_size,
            digest.hexdigest(),
            scripts,
        )

    @classmethod
    def from_text(cls, text):
        data = text.encode("utf-8")
        if len(data) > MAX_TEXT:
            raise DiscoveryError("The decoded response exceeds the text size limit.")
        return cls._from_data(data, "identity", "utf-8", MAX_TEXT)

    def text(self):
        try:
            decoder = codecs.getincrementaldecoder(self.encoding)(errors="replace")
            parts = [
                decoder.decode(block)
                for block in chunks(
                    self.data,
                    self.compression,
                    min(MAX_TEXT, max(MAX_RESPONSE, self.expanded_size)),
                )
            ]
            parts.append(decoder.decode(b"", final=True))
            return "".join(parts)
        except (LookupError, UnicodeError, TypeError, AssertionError) as exc:
            raise DiscoveryError("The saved response could not be decoded.") from exc

    def to_cache(self):
        return {
            "data": base64.b64encode(self.data).decode("ascii"),
            "compression": self.compression,
            "encoding": self.encoding,
            "expanded_size": self.expanded_size,
            "text_size": self.text_size,
            "digest": self.digest,
            "has_scripts": self.has_scripts,
        }

    @classmethod
    def from_cache(cls, value):
        try:
            if (
                not isinstance(value["data"], str)
                or len(value["data"]) > (MAX_PACKED + 2) // 3 * 4
            ):
                raise ValueError()
            data = base64.b64decode(value["data"], validate=True)
            if (
                len(data) > MAX_PACKED
                or value["compression"] not in {"gzip", "deflate", "identity"}
                or not isinstance(value["encoding"], str)
                or len(value["encoding"]) > 80
                or type(value["expanded_size"]) is not int
                or not 0 <= value["expanded_size"] <= MAX_TEXT
                or type(value["text_size"]) is not int
                or not 0 <= value["text_size"] <= MAX_TEXT
                or not re.fullmatch(r"[0-9a-f]{64}", value["digest"])
                or type(value["has_scripts"]) is not bool
            ):
                raise ValueError()
            return cls(
                data,
                **{
                    k: value[k]
                    for k in (
                        "compression",
                        "encoding",
                        "expanded_size",
                        "text_size",
                        "digest",
                        "has_scripts",
                    )
                },
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise DiscoveryError(
                "The saved response is invalid. Saved links were kept."
            ) from exc


def unpack(value):
    return value.text() if isinstance(value, Document) else value
