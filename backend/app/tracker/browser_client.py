"""Bounded broker for the networkless renderer. All network IO stays in SafeFetcher."""

import asyncio
import json
import os
import re
from urllib.parse import parse_qsl, urlsplit

from .urls import DiscoveryError, canonical_url

MAX_MESSAGE = 16_000_000
ALLOWED = {"document", "script", "stylesheet", "xhr", "fetch"}
PRIVATE_QUERY = re.compile(
    r"key|token|auth|password|secret|signature|session|jwt", re.I
)
MUTATION = re.compile(
    r"(?:^|/)(?:logout|signout|delete|remove|unsubscribe|admin|login|account|checkout|purchase)(?:/|$)",
    re.I,
)


def enabled():
    return bool(os.environ.get("TRACKER_BROWSER_SOCKET"))


def allowed_request(message, source):
    if message.get("method") != "GET" or message.get("resource") not in ALLOWED:
        return False
    try:
        url = canonical_url(message.get("url"), preserve_slash=True)
        p = urlsplit(url)
        if MUTATION.search(p.path) or any(
            PRIVATE_QUERY.search(k) for k, _ in parse_qsl(p.query)
        ):
            return False
        if message["resource"] == "document" and url != canonical_url(
            source, preserve_slash=True
        ):
            return False
        return url
    except (ValueError, TypeError, UnicodeError):
        return False


async def render(fetcher, source, initial=None):
    path = os.environ.get("TRACKER_BROWSER_SOCKET")
    if not path:
        raise DiscoveryError(
            "JavaScript scanning is not enabled on this server. Choose a public API, feed, or sitemap."
        )
    captured = []
    reader = writer = None
    received = 0
    requests = 0
    try:
        async with asyncio.timeout(55):
            reader, writer = await asyncio.open_unix_connection(path, limit=MAX_MESSAGE)

            async def send(message):
                raw = json.dumps(message, ensure_ascii=False).encode() + b"\n"
                if len(raw) > MAX_MESSAGE:
                    raise DiscoveryError("Browser response exceeded its message limit.")
                writer.write(raw)
                await writer.drain()

            await send({"url": source})
            while True:
                raw = await reader.readline()
                received += len(raw)
                if not raw or len(raw) > MAX_MESSAGE or received > MAX_MESSAGE:
                    raise DiscoveryError(
                        "Browser response exceeded its size limit or was interrupted."
                    )
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise DiscoveryError("Browser returned an invalid message.")
                if message.get("type") == "result":
                    snapshots = message.get("snapshots", [])
                    if (
                        not isinstance(snapshots, list)
                        or len(snapshots) > 5
                        or any(
                            not isinstance(s, str) or len(s) > 1_000_000
                            for s in snapshots
                        )
                    ):
                        raise DiscoveryError("Browser returned invalid page snapshots.")
                    return message | {"captured": captured}
                if message.get("type") == "error":
                    raise DiscoveryError(
                        "Browser scan could not finish. Try again later or select a public API/feed."
                    )
                if message.get("type") != "fetch":
                    raise DiscoveryError("Browser returned an invalid request.")
                requests += 1
                if requests > 40:
                    raise DiscoveryError("Browser scan reached its 40-resource limit.")
                url = allowed_request(message, source)
                reply = {"id": message.get("id"), "error": True}
                if url:
                    try:
                        if initial is not None and url == canonical_url(
                            source, preserve_slash=True
                        ):
                            final, body = source, initial
                        else:
                            final, body = await fetcher.get(url)
                        # Redirects were validated by SafeFetcher; no cookies or request
                        # headers supplied by the page ever reach the source server.
                        if len(body.encode()) > 8_000_000:
                            raise DiscoveryError(
                                "Browser resource exceeded its size limit."
                            )
                        reply = {"id": message.get("id"), "body": body}
                        if (
                            message["resource"] in {"xhr", "fetch"}
                            and len(captured) < 8
                            and len(body) <= 1_000_000
                        ):
                            try:
                                data = json.loads(body)
                                if isinstance(data, (dict, list)):
                                    captured.append({"url": final, "data": data})
                            except (ValueError, RecursionError):
                                pass
                    except DiscoveryError:
                        pass
                await send(reply)
    except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as exc:
        if isinstance(exc, DiscoveryError):
            raise
        raise DiscoveryError(
            "JavaScript scanning is temporarily unavailable. Saved links were kept."
        ) from exc
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
