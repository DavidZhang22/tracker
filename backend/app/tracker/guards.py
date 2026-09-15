"""Bounded request admission for the single-process deployment."""

import asyncio
import math
import time
from contextlib import contextmanager
from threading import Lock

from fastapi import HTTPException
from starlette.responses import JSONResponse


class RateLimits:
    def __init__(self, clock=time.monotonic, max_keys=4096):
        self.clock, self.max_keys = clock, max_keys
        self.buckets = {}
        self.lock = Lock()

    def charge(self, rules, cost=1):
        """Token buckets; all rules are checked before any tokens are consumed."""
        with self.lock:
            now = self.clock()
            self.buckets = {k: v for k, v in self.buckets.items() if v[2] > now}
            updates = {}
            retry = 0
            for key, capacity, seconds in rules:
                tokens, last, _ = self.buckets.get(key, (capacity, now, now + seconds))
                tokens = min(capacity, tokens + (now - last) * capacity / seconds)
                retry = max(retry, (cost - tokens) * seconds / capacity)
                updates[key] = (tokens - cost, now, now + seconds)
            if len(self.buckets.keys() | updates.keys()) > self.max_keys:
                retry = max(retry, 60)
            if retry > 0:
                raise HTTPException(
                    429,
                    "Too many requests. Please wait before trying again.",
                    headers={"Retry-After": str(max(1, math.ceil(retry)))},
                )
            self.buckets.update(updates)


class ScanGuard:
    def __init__(self):
        self.active = set()
        self.rates = RateLimits()

    @contextmanager
    def operation(self, owner, cost=1):
        # No await before admission: a flood cannot create an unbounded task queue.
        if owner in self.active or len(self.active) >= 3:
            raise HTTPException(
                429,
                "A scan is already running. Please wait and try again.",
                headers={"Retry-After": "10"},
            )
        self.rates.charge(
            [(f"scan:{owner}", 200, 3600), ("scan:global", 600, 3600)], cost
        )
        self.active.add(owner)
        try:
            yield
        finally:
            self.active.discard(owner)


class ApiGuard:
    """Reject excess traffic and oversized/slow bodies before parsing or auth."""

    def __init__(self, app, rates=None, max_body=65_536, body_timeout=10):
        self.app = app
        self.rates = rates or RateLimits()
        self.max_body, self.body_timeout = max_body, body_timeout
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)

        async def reject(status, detail, headers=None):
            response = JSONResponse(
                {"detail": detail},
                status_code=status,
                headers={"Cache-Control": "no-store", **(headers or {})},
            )
            await response(scope, receive, send)

        ip = (scope.get("client") or ("unknown",))[0]
        try:
            self.rates.charge([("api:global", 1200, 60), (f"api:{ip}", 240, 60)])
        except HTTPException as exc:
            return await reject(exc.status_code, exc.detail, exc.headers)
        if self.active >= 64:
            return await reject(
                429,
                "The server is busy. Please try again shortly.",
                {"Retry-After": "10"},
            )
        body_limit = (
            262_144
            if self.max_body == 65_536
            and (
                scope["path"] == "/api/links/bulk"
                or (
                    scope["path"].startswith("/api/items/")
                    and scope["path"].endswith("/link-groups")
                )
            )
            else self.max_body
        )
        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    return await reject(400, "Invalid request size.")
                if length < 0 or length > body_limit:
                    return await reject(413, "This request is too large.")
        self.active += 1
        try:
            chunks, size = [], 0
            try:
                async with asyncio.timeout(self.body_timeout):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > body_limit:
                            return await reject(413, "This request is too large.")
                        if chunk:
                            chunks.append(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "The request took too long to arrive.")
            body = b"".join(chunks)
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, replay, send)
        finally:
            self.active -= 1
