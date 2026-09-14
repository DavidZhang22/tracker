import asyncio
import hashlib
import ipaddress
import re
import socket
import time
import zlib
from contextvars import ContextVar
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .cache import FetchCache
from .workers import run_blocking


@dataclass
class RequestBudget:
    limit: int = 40
    requests: int = 0
    hits: int = 0
    received: int = 0
    byte_limit: int = 32_000_000

    def take_bytes(self, amount):
        if self.received + amount > self.byte_limit:
            raise DiscoveryError(
                "Scan reached its total response-size budget. Use a narrower source or feed."
            )
        self.received += amount

    def take(self):
        if self.requests >= self.limit:
            raise DiscoveryError(f"Scan reached its {self.limit}-request budget.")
        self.requests += 1


request_budget = ContextVar("request_budget", default=None)


class DiscoveryError(ValueError):
    pass


def content_key(url):
    # Apply the same normalization at discovery and storage boundaries, including
    # cached scans made by older parsers.
    url = canonical_url(url)
    p = urlsplit(url)
    if p.hostname in {"asurascans.com", "www.asurascans.com"}:
        match = re.fullmatch(
            r"/comics/([a-z0-9]+(?:-[a-z0-9]+)*)-[0-9a-f]{8}/chapter/(\d+(?:\.\d+)?)",
            p.path,
        )
        if match:
            # Asura rotates the series URL suffix without changing the chapter.
            # Retain the series, full chapter number and meaningful query values.
            return f"asura:{match[1]}:chapter:{match[2]}" + (
                "?" + p.query if p.query else ""
            )
    if p.hostname in {"www.royalroad.com", "royalroad.com"}:
        match = re.search(r"/chapter/(\d+)(?:/|$)", p.path)
        if match:
            return "royalroad:" + match[1]
    if p.hostname == "www.youtube.com" and p.path == "/watch":
        return "youtube:" + dict(parse_qsl(p.query)).get("v", "")
    return url


def canonical_url(value, base="", preserve_slash=False):
    if not isinstance(value, str) or not value.strip():
        raise DiscoveryError("Enter a public http or https URL.")
    raw = value.strip()
    if len(raw) > 4096 or re.search(r"[\x00-\x20\x7f\\]", raw):
        raise DiscoveryError("URL is too long or contains unsafe characters.")
    if raw.startswith("#"):
        raise DiscoveryError("Fragment-only links are not content.")
    p = urlsplit(urljoin(base, raw))
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise DiscoveryError("Enter a public http or https URL without credentials.")
    host = p.hostname.lower().encode("idna").decode()
    try:
        port = p.port
    except ValueError as exc:
        raise DiscoveryError("Invalid URL port.") from exc
    if port not in (None, 80, 443):
        raise DiscoveryError("Only standard web ports are supported.")
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"fbclid", "gclid", "ref_src"}
    ]
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        video = dict(query).get("v") if p.path == "/watch" else None
        if host == "youtu.be":
            video = p.path.strip("/")
        elif p.path.startswith(("/shorts/", "/embed/", "/live/")):
            video = p.path.split("/")[2]
        if video and re.fullmatch(r"[\w-]{11}", video):
            return "https://www.youtube.com/watch?v=" + video
        host = "www.youtube.com"
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit(
        (
            p.scheme.lower(),
            netloc,
            (p.path if preserve_slash else p.path.rstrip("/")) or "/",
            urlencode(sorted(query)),
            "",
        )
    )


async def public_addresses(host):
    if host.lower() == "localhost" or host.lower().endswith(
        (".localhost", ".local", ".internal")
    ):
        raise DiscoveryError("Local and private network addresses cannot be scanned.")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, 443, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise DiscoveryError("The source address could not be resolved.") from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses or any(not permitted_address(ip) for ip in addresses):
        raise DiscoveryError("Local and private network addresses cannot be scanned.")
    return addresses


def permitted_address(value):
    ip = ipaddress.ip_address(value)
    # Azure's WireServer uses a globally classified address but is VM-local.
    if not ip.is_global or ip.is_multicast or str(ip) == "168.63.129.16":
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if (
            ip.ipv4_mapped
            or ip.sixtofour
            or ip.teredo
            or ip in ipaddress.ip_network("64:ff9b::/96")
        ):
            return False
    return True


class SafeFetcher:
    """Pin validated DNS results to the connection, including every redirect."""

    def __init__(self, cache=None, interval=2.0, ttl=600):
        self.cache = cache or FetchCache()
        self.interval, self.ttl = interval, ttl
        self.locks = [asyncio.Lock() for _ in range(64)]
        self.network_locks = [asyncio.Lock() for _ in range(64)]
        self.last_requests = [0.0] * 64

    async def get(self, url, *, secret_query=None):
        original = canonical_url(url, preserve_slash=True)
        if secret_query and urlsplit(original).scheme != "https":
            raise DiscoveryError("API credentials require HTTPS.")
        host = urlsplit(original).hostname
        slot = hash(host) % 64
        async with self.locks[slot]:
            return await self._get(original, slot, secret_query)

    async def _get(self, original, slot, secret_query=None):
        cache_key = (
            original
            if not secret_query
            else "credentialed:"
            + hashlib.sha256((original + urlencode(secret_query)).encode()).hexdigest()
        )
        cached = await run_blocking(self.cache.get, cache_key)
        budget = request_budget.get()
        if cached and cached.get("expires", 0) > time.time():
            if budget:
                budget.hits += 1
            if cached.get("error"):
                raise DiscoveryError(cached["error"])
            if budget:
                budget.take_bytes(len(cached["body"].encode()))
            return cached["final"], cached["body"]
        url = original
        for _ in range(6):
            url = canonical_url(url, preserve_slash=True)
            p = urlsplit(url)
            addresses = await public_addresses(p.hostname)
            ip = addresses[0]
            netloc = f"[{ip}]" if ":" in ip else ip
            target = urlunsplit((p.scheme, netloc, p.path, p.query, ""))
            if secret_query:
                target += ("&" if p.query else "?") + urlencode(secret_query)
            backoff = await run_blocking(self.cache.get, "backoff:" + p.hostname)
            if backoff and backoff["expires"] > time.time():
                raise DiscoveryError(
                    "This source requested a pause. Retry after "
                    + time.strftime("%H:%M UTC", time.gmtime(backoff["expires"]))
                    + "."
                )
            slot = hash(p.hostname) % 64
            async with self.network_locks[slot]:
                await asyncio.sleep(
                    max(
                        0, self.interval - (time.monotonic() - self.last_requests[slot])
                    )
                )
                if budget:
                    budget.take()
                self.last_requests[slot] = time.monotonic()
                headers = {
                    "Host": p.hostname,
                    "User-Agent": "MediaTracker/1.0 (personal link index)",
                    "Accept": "text/html,application/xml,application/json,*/*;q=0.5",
                    "Accept-Encoding": "gzip, deflate",
                }
                if cached and not cached.get("error") and url == cached.get("final"):
                    if cached.get("etag"):
                        headers["If-None-Match"] = cached["etag"]
                    if cached.get("modified"):
                        headers["If-Modified-Since"] = cached["modified"]
                try:
                    async with httpx.AsyncClient(
                        timeout=20, trust_env=False, follow_redirects=False
                    ) as client:
                        async with client.stream(
                            "GET",
                            target,
                            headers=headers,
                            extensions={"sni_hostname": p.hostname.encode()},
                        ) as response:
                            if (
                                response.status_code == 304
                                and cached
                                and not cached.get("error")
                            ):
                                if budget:
                                    budget.take_bytes(len(cached["body"].encode()))
                                cached.update(
                                    checked=time.time(), expires=time.time() + self.ttl
                                )
                                await run_blocking(self.cache.put, cache_key, cached)
                                return cached["final"], cached["body"]
                            if response.is_redirect:
                                if secret_query:
                                    raise DiscoveryError(
                                        "The API redirected. Credentials were not forwarded; check the configured API host."
                                    )
                                url = canonical_url(
                                    response.headers.get("location", ""),
                                    url,
                                    preserve_slash=True,
                                )
                                continue
                            if response.status_code in (401, 403, 429, 503):
                                message = f"The source refused access (HTTP {response.status_code}). Retry later or supply a public feed URL."
                                delay = self.ttl
                                retry = response.headers.get("Retry-After", "")
                                try:
                                    delay = max(
                                        delay,
                                        float(retry)
                                        if retry.isdigit()
                                        else parsedate_to_datetime(retry).timestamp()
                                        - time.time(),
                                    )
                                except (ValueError, TypeError, OverflowError):
                                    pass
                                await run_blocking(
                                    self.cache.put,
                                    "backoff:" + p.hostname,
                                    {"expires": time.time() + min(delay, 86400)},
                                )
                                await run_blocking(
                                    self.cache.put,
                                    cache_key,
                                    {
                                        "error": message,
                                        "expires": time.time() + min(delay, 86400),
                                    },
                                )
                                raise DiscoveryError(message)
                            response.raise_for_status()
                            encoding = (
                                response.headers.get("Content-Encoding", "identity")
                                .lower()
                                .strip()
                            )
                            if encoding not in {"identity", "gzip", "deflate", ""}:
                                raise DiscoveryError(
                                    "The source used unsupported response compression."
                                )
                            # Limit compressed bytes before decompression. httpx's
                            # decoded iterator can allocate a zip bomb in one chunk.
                            consumed = response.is_stream_consumed

                            async def raw_chunks(
                                raw_response=response, pre_read=consumed
                            ):
                                if pre_read:  # Pre-read mock/custom transports.
                                    yield raw_response.content
                                else:
                                    async for raw in raw_response.aiter_raw():
                                        yield raw

                            data = bytearray()
                            async for chunk in raw_chunks():
                                if budget:
                                    budget.take_bytes(len(chunk))
                                data.extend(chunk)
                                if len(data) > 8_000_000:
                                    raise DiscoveryError(
                                        "The page exceeds the 8 MB scan limit."
                                    )
                            compressed = data[:2] == b"\x1f\x8b" or (
                                not consumed and encoding in {"gzip", "deflate"}
                            )
                            if compressed:
                                try:
                                    bits = (
                                        16 + zlib.MAX_WBITS
                                        if data[:2] == b"\x1f\x8b" or encoding == "gzip"
                                        else zlib.MAX_WBITS
                                    )
                                    unpacker = zlib.decompressobj(bits)
                                    data = unpacker.decompress(bytes(data), 8_000_001)
                                    if len(data) > 8_000_000 or not unpacker.eof:
                                        raise DiscoveryError(
                                            "The expanded sitemap exceeds the 8 MB scan limit or is incomplete."
                                        )
                                    if budget:
                                        budget.take_bytes(len(data))
                                except zlib.error as exc:
                                    raise DiscoveryError(
                                        "The compressed sitemap is unreadable."
                                    ) from exc
                            text = data.decode(
                                response.encoding or "utf-8", errors="replace"
                            )
                            if re.search(
                                r"<title>\s*(?:Just a moment|Attention Required|Access Denied)",
                                text,
                                re.IGNORECASE,
                            ):
                                raise DiscoveryError(
                                    "The source requires a browser check. Retry later or supply a public feed URL."
                                )
                            if "no-store" not in response.headers.get(
                                "Cache-Control", ""
                            ):
                                await run_blocking(
                                    self.cache.put,
                                    cache_key,
                                    {
                                        "final": url,
                                        "body": text,
                                        "etag": response.headers.get("etag"),
                                        "modified": response.headers.get(
                                            "last-modified"
                                        ),
                                        "expires": time.time() + self.ttl,
                                    },
                                )
                            return url, text
                except httpx.HTTPError as exc:
                    raise DiscoveryError(
                        f"Could not load the source ({type(exc).__name__}). Retry later."
                    ) from exc
        raise DiscoveryError("The source redirected too many times.")
