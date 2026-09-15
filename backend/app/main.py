import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.tracker.accounts import Accounts, cookie_name
from app.tracker.accounts import router as account_router
from app.tracker.analysis_pool import PageAnalyzer
from app.tracker.api import router
from app.tracker.cache import FetchCache, cache_epochs
from app.tracker.discovery import Discoverer
from app.tracker.guards import ApiGuard, ScanGuard
from app.tracker.privacy import router as privacy_router
from app.tracker.store import LibraryErased, Store
from app.tracker.urls import SafeFetcher
from app.tracker.workers import run_blocking


def create_app(db_path=None, discoverer=None, auth_config=None):
    analyzer = (
        None
        if discoverer is not None
        else PageAnalyzer(int(os.environ.get("TRACKER_ANALYSIS_WORKERS", "2")))
    )

    @asynccontextmanager
    async def lifespan(app):
        async def maintain_privacy():
            while True:
                try:
                    cache = getattr(
                        getattr(app.state.discoverer, "fetcher", None), "cache", None
                    )
                    await run_blocking(app.state.accounts.maintenance, cache)
                except (sqlite3.Error, OSError):
                    logging.getLogger(__name__).error(
                        "Privacy maintenance failed; will retry"
                    )
                await asyncio.sleep(300)

        maintenance = None
        try:
            if analyzer is not None:
                await analyzer.start()
            if app.state.accounts:
                maintenance = asyncio.create_task(maintain_privacy())
            yield
        finally:
            if maintenance:
                maintenance.cancel()
                with suppress(asyncio.CancelledError):
                    await maintenance
            if analyzer is not None:
                await analyzer.aclose()

    app = FastAPI(
        title="Trackify",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.analyzer = analyzer
    app.state.store = Store(
        db_path
        or os.environ.get(
            "TRACKER_DB",
            str(Path(__file__).resolve().parents[1] / "data" / "tracker.sqlite3"),
        ),
        allow_erased=True,
    )
    app.state.discoverer = discoverer or Discoverer(
        SafeFetcher(
            FetchCache(Path(app.state.store.path).with_name("fetch-cache.sqlite3"))
        ),
        analyzer=analyzer,
    )
    app.state.scan_semaphore = asyncio.Semaphore(3)
    app.state.scan_guard = ScanGuard()
    app.state.refresh_locks = [asyncio.Lock() for _ in range(64)]
    config = (
        auth_config
        if auth_config is not None
        else {
            "required": os.environ.get("TRACKER_AUTH_REQUIRED", "0") == "1",
            "origin": os.environ.get("TRACKER_ORIGIN", ""),
            "signup_code": os.environ.get("TRACKER_SIGNUP_CODE", ""),
            "public_signup": os.environ.get("TRACKER_PUBLIC_SIGNUP", "0") == "1",
        }
    )
    origin = config.get("origin", "").rstrip("/")
    if config.get("required"):
        parsed = urlsplit(origin)
        if (
            not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.scheme not in {"http", "https"}
            or (
                parsed.scheme == "http"
                and parsed.hostname not in {"localhost", "127.0.0.1", "testserver"}
            )
        ):
            raise ValueError(
                "Set TRACKER_ORIGIN to the exact HTTPS address before enabling accounts."
            )
        if config.get("signup_code") and len(config["signup_code"]) < 20:
            raise ValueError(
                "Use an invite code with at least 20 characters, or disable registration."
            )
    app.state.accounts = (
        Accounts(
            app.state.store.path,
            config.get("signup_code", ""),
            public_signup=config.get("public_signup", False),
        )
        if config.get("required")
        else None
    )
    app.state.secure_cookies = origin.startswith("https://")
    hosts = os.environ.get(
        "TRACKER_HOSTS", "localhost,127.0.0.1,[::1],testserver"
    ).split(",")
    if origin:
        hosts.append(urlsplit(origin).hostname)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    def storage_failure(exc):
        logging.getLogger(__name__).error(
            "Database unavailable (%s)", type(exc).__name__
        )
        return JSONResponse(
            {
                "code": "DATABASE_UNAVAILABLE",
                "detail": "Storage is temporarily unavailable. Changes may not have been saved. Check your data before retrying.",
            },
            status_code=503,
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        )

    @app.exception_handler(sqlite3.OperationalError)
    @app.exception_handler(sqlite3.DatabaseError)
    async def database_error(request, exc):
        return storage_failure(exc)

    @app.middleware("http")
    async def protect_origin(request: Request, call_next):
        incoming_origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            incoming_origin or app.state.accounts
        ):
            allowed = (
                {origin}
                if app.state.accounts
                else {
                    str(request.base_url).rstrip("/"),
                    "http://localhost:3000",
                    "http://127.0.0.1:3000",
                }
            )
            if incoming_origin not in allowed:
                return JSONResponse(
                    {"detail": "This origin is not allowed."}, status_code=403
                )
        if (
            app.state.accounts
            and request.url.path.startswith("/api/")
            and request.url.path
            not in {
                "/api/privacy",
                "/api/health",
                "/api/ready",
                "/api/auth/status",
                "/api/auth/login",
                "/api/auth/register",
                "/api/auth/logout",
            }
        ):
            user = app.state.accounts.user(request.cookies.get(cookie_name(request)))
            if not user:
                return JSONResponse(
                    {"detail": "Sign in to continue."},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            request.state.user = user
            # Account recovery/deletion must remain possible if its library is down.
            request.state.store = (
                app.state.store
                if request.url.path.startswith("/api/auth/")
                else app.state.accounts.store(user)
            )
        else:
            request.state.store = app.state.store
        cache = getattr(getattr(app.state.discoverer, "fetcher", None), "cache", None)
        epoch = cache_epochs.set({id(cache): getattr(cache, "generation", 0)})
        try:
            response = await call_next(request)
        finally:
            cache_epochs.reset(epoch)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.middleware("http")
    async def storage_guard(request: Request, call_next):
        try:
            return await call_next(request)
        except sqlite3.DatabaseError as exc:
            return storage_failure(exc)
        except LibraryErased:
            return JSONResponse(
                {"detail": "This account's library has been deleted."},
                status_code=410,
                headers={"Cache-Control": "no-store"},
            )

    @app.exception_handler(LibraryErased)
    async def erased_library(request, exc):
        return JSONResponse(
            {"detail": "This account's library has been deleted."}, status_code=410
        )

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": exc.args[0]}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def invalid_fields(request, exc):
        # FastAPI's default errors include submitted values, including passwords.
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]
            },
            status_code=422,
        )

    app.include_router(router)
    app.include_router(account_router)
    app.include_router(privacy_router)
    build = Path(__file__).resolve().parents[2] / "frontend" / "build"
    if build.exists():
        app.mount("/static", StaticFiles(directory=build / "static"), name="static")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "API route not found.")
            target = (build / path).resolve()
            if target.is_relative_to(build.resolve()) and target.is_file():
                return FileResponse(
                    target,
                    headers={"Cache-Control": "no-store"}
                    if target.name == "index.html"
                    else None,
                )
            return FileResponse(
                build / "index.html", headers={"Cache-Control": "no-store"}
            )

    # Added last so admission runs before authentication and body parsing.
    app.add_middleware(ApiGuard)
    return app


app = create_app()
