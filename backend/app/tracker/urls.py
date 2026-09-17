import asyncio
import hashlib
import ipaddress
import re
import socket
import time
from contextvars import ContextVar
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .cache import FetchCache
from .documents import MAX_RESPONSE, Document
from .errors import DiscoveryError
from .source_cache import REUSE_SECONDS, SharedWork
from .workers import run_blocking


@dataclass
class RequestBudget:
    limit: int = 40
    requests: int = 0
    hits: int = 0
    received: int = 0
    byte_limit: int = 32_000_000
    cacheable: bool = True

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


def response_key(key):
    return "response-v1:" + hashlib.sha256(key.encode()).hexdigest()


async def fetch_document(fetcher, url):
    # Respect injected fetchers and request auditing overrides.
    if (
        isinstance(fetcher, SafeFetcher)
        and type(fetcher).get is SafeFetcher.get
        and "get" not in vars(fetcher)
    ):
        return await fetcher.get_document(url)
    return await fetcher.get(url)


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
    try:
        p = urlsplit(urljoin(base, raw))
    except ValueError as exc:
        raise DiscoveryError("Enter a valid public http or https URL.") from exc
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise DiscoveryError("Enter a public http or https URL without credentials.")
    try:
        host = p.hostname.lower().encode("idna").decode()
    except UnicodeError as exc:
        raise DiscoveryError("The source hostname is invalid.") from exc
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

    def __init__(self, cache=None, interval=2.0, ttl=REUSE_SECONDS):
        self.cache = cache or FetchCache()
        self.interval, self.ttl = interval, max(REUSE_SECONDS, ttl)
        self.locks = [asyncio.Lock() for _ in range(64)]
        self.network_locks = [asyncio.Lock() for _ in range(64)]
        self.last_requests = [0.0] * 64
        self.fetch_slots = asyncio.Semaphore(4)

    async def get(self, url, *, secret_query=None):
        final, body = await self.get_document(url, secret_query=secret_query)
        return final, await run_blocking(body.text)

    async def get_document(self, url, *, secret_query=None):
        original = canonical_url(url, preserve_slash=True)
        if secret_query and urlsplit(original).scheme != "https":
            raise DiscoveryError("API credentials require HTTPS.")
        if not secret_query:
            original = await run_blocking(self.cache.coordinator.resolve, original)
        host = urlsplit(original).hostname
        slot = hash(host) % 64
        async with self.locks[slot]:
            return await self._get(original, slot, secret_query)

    def _cached_response(self, cache_key, legacy_key):
        cached = self.cache.get(cache_key)
        if cached is None:
            cached = self.cache.get(legacy_key)
        if not cached or cached.get("error"):
            return cached, None
        if "document" in cached:
            return cached, Document.from_cache(cached["document"])
        body = Document.from_text(cached["body"])
        cached = {key: value for key, value in cached.items() if key != "body"} | {
            "document": body.to_cache()
        }
        self.cache.put(cache_key, cached)
        return cached, body

    def _keys(self, url, secret_query):
        key = (
            url
            if not secret_query
            else "credentialed:"
            + hashlib.sha256((url + urlencode(secret_query)).encode()).hexdigest()
        )
        return response_key(key), key

    def _fresh(self, cached, body):
        if (
            not cached
            or min(cached.get("expires", 0), cached["checked"] + self.ttl)
            <= time.time()
        ):
            return None
        budget = request_budget.get()
        if budget:
            budget.hits += 1
        if cached.get("error"):
            raise DiscoveryError(cached["error"])
        if budget:
            budget.take_bytes(body.text_size)
        return cached["final"], body

    async def _get(self, original, slot, secret_query=None):
        url, seen = original, set()
        for _ in range(6):
            url = canonical_url(url, preserve_slash=True)
            if not secret_query:
                url = await run_blocking(self.cache.coordinator.resolve, url)
            if url in seen:
                raise DiscoveryError("The source has a redirect loop.")
            seen.add(url)
            cache_key, legacy_key = self._keys(url, secret_query)
            cached, body = await run_blocking(
                self._cached_response, cache_key, legacy_key
            )
            if hit := self._fresh(cached, body):
                return hit
            async with SharedWork(self.cache.coordinator, "fetch:" + cache_key) as work:
                # Another library/process may have populated the body while we waited.
                cached, body = await run_blocking(
                    self._cached_response, cache_key, legacy_key
                )
                if hit := self._fresh(cached, body):
                    return hit
                work.require_owner()
                attempted = [False]
                try:
                    async with asyncio.timeout(60):
                        target, body, redirect_ttl = await self._request(
                            url, cache_key, cached, body, secret_query, attempted
                        )
                    if redirect_ttl:
                        # Persist only verified HTTP redirects, never HTML canonical hints.
                        await public_addresses(urlsplit(target).hostname)
                        await run_blocking(
                            self.cache.coordinator.remember, url, target, redirect_ttl
                        )
                        url = target
                    else:
                        return target, body
                except TimeoutError as exc:
                    work.error = "The source request timed out. Retry later."
                    raise DiscoveryError(work.error) from exc
                except DiscoveryError as exc:
                    work.error = str(exc)
                    raise
                finally:
                    work.seconds = self.ttl if attempted[0] else 0
        raise DiscoveryError("The source redirected too many times.")

    async def _request(self, url, cache_key, cached, body, secret_query, attempted):
        budget = request_budget.get()
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
                max(0, self.interval - (time.monotonic() - self.last_requests[slot]))
            )
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
                async with (
                    self.fetch_slots,
                    httpx.AsyncClient(
                        timeout=20, trust_env=False, follow_redirects=False
                    ) as client,
                ):
                    if budget:
                        budget.take()
                    attempted[0] = True
                    self.last_requests[slot] = time.monotonic()
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
                                budget.take_bytes(body.text_size)
                            cached.update(
                                checked=time.time(), expires=time.time() + self.ttl
                            )
                            await run_blocking(self.cache.put, cache_key, cached)
                            return cached["final"], body, 0
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
                            return (
                                url,
                                None,
                                86400
                                if response.status_code in (301, 308)
                                else REUSE_SECONDS,
                            )
                        if response.status_code in (401, 403, 429, 503):
                            message = f"The source refused access (HTTP {response.status_code}). Retry later or supply a public feed URL."
                            if response.headers.get("cf-mitigated") == "challenge":
                                message = (
                                    f"The source requires Cloudflare browser verification (HTTP {response.status_code}). "
                                    "Trackify could not read its listing. Use an accessible feed or import a CSV; saved entries were kept."
                                )
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

                        async def raw_chunks(raw_response=response, pre_read=consumed):
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
                            if len(data) > MAX_RESPONSE:
                                raise DiscoveryError(
                                    "The page exceeds the 8 MB scan limit."
                                )
                        compression = (
                            "gzip"
                            if data[:2] == b"\x1f\x8b"
                            else encoding
                            if not consumed and encoding in {"gzip", "deflate"}
                            else "identity"
                        )
                        body = await run_blocking(
                            Document.from_wire,
                            data,
                            compression,
                            response.encoding or "utf-8",
                        )
                        del data
                        if budget and compression != "identity":
                            budget.take_bytes(body.expanded_size)
                        control = response.headers.get("Cache-Control", "").lower()
                        cacheable = not any(
                            v in control for v in ("no-store", "private")
                        )
                        if budget and not cacheable:
                            budget.cacheable = False
                        if cacheable:

                            def save_response(url=url, body=body, response=response):
                                self.cache.put(
                                    cache_key,
                                    {
                                        "final": url,
                                        "document": body.to_cache(),
                                        "etag": response.headers.get("etag"),
                                        "modified": response.headers.get(
                                            "last-modified"
                                        ),
                                        "expires": time.time() + self.ttl,
                                    },
                                )

                            await run_blocking(save_response)
                        return url, body, 0
            except httpx.HTTPError as exc:
                raise DiscoveryError(
                    f"Could not load the source ({type(exc).__name__}). Retry later."
                ) from exc
