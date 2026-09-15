"""Accounts with opaque, revocable sessions and isolated libraries."""

import argparse
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .erasure import library_path, wipe_library
from .guards import RateLimits
from .store import Store

COOKIE = "__Host-catchup_session"
LOCAL_COOKIE = "catchup_session"
SESSION_SECONDS = 7 * 86400
HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
HASH_LOCK = threading.BoundedSemaphore(4)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
router = APIRouter(prefix="/api/auth")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def username(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", value):
        raise ValueError(
            "Use 3–40 letters, numbers, dots, underscores, or hyphens for your username."
        )
    return value


def hash_password(value):
    if not 10 <= len(value) <= 128:
        raise ValueError("Use a password with 10–128 characters.")
    with HASH_LOCK:
        return HASHER.hash(value)


def matches(encoded, password):
    try:
        with HASH_LOCK:
            return HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


class Accounts:
    def __init__(self, library_path, signup_code="", public_signup=False):
        self.library_path = Path(library_path).resolve()
        self.root = self.library_path.parent
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "accounts.sqlite3"
        self.signup_code = signup_code
        self.public_signup = public_signup
        self.stores = {}
        self.lock = threading.Lock()
        self.privacy_rates = RateLimits()
        marker = Path(str(self.path) + ".initialized")
        if marker.exists() and not Path(self.path).is_file():
            raise sqlite3.OperationalError("Previously initialized database is missing")
        with self.connection(create=True) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL, created REAL NOT NULL,
                  legacy_library INTEGER NOT NULL DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS one_legacy_owner ON users(legacy_library) WHERE legacy_library=1;
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  expires REAL NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS attempts (key TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_key_created ON attempts(key,created);
                CREATE TABLE IF NOT EXISTS registrations (address_hash TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS registrations_created ON registrations(created);
                CREATE TABLE IF NOT EXISTS erasures (
                  user_id TEXT PRIMARY KEY, legacy_library INTEGER NOT NULL,
                  requested REAL NOT NULL, completed REAL, cache_cleared INTEGER NOT NULL DEFAULT 0);
            """)
        marker.touch(exist_ok=True)

    @contextmanager
    def connection(self, create=False):
        target = Path(self.path).resolve().as_uri() + (
            "?mode=rwc" if create else "?mode=rw"
        )
        db = sqlite3.connect(target, uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def throttle(self, address, name):
        now = time.time()
        limits = [
            ("ip:" + digest(address), 30),
            ("user:" + digest(name.strip().lower()), 10),
        ]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE created<?", (now - 900,))
            for key, limit in limits:
                if (
                    db.execute(
                        "SELECT count(*) FROM attempts WHERE key=?", (key,)
                    ).fetchone()[0]
                    >= limit
                ):
                    raise HTTPException(
                        429,
                        "Too many account attempts. Try again in 15 minutes.",
                        headers={"Retry-After": "900"},
                    )
            db.executemany(
                "INSERT INTO attempts VALUES (?,?)", ((key, now) for key, _ in limits)
            )

    def check_registration_limit(self, db, address, now):
        recent = db.execute(
            "SELECT address_hash,created FROM registrations WHERE created>? ORDER BY created",
            (now - 3600,),
        ).fetchall()
        for rows, limit in (
            ([row for row in recent if row["address_hash"] == digest(address)], 3),
            (recent, 20),
        ):
            if len(rows) >= limit:
                retry = max(1, int(rows[-limit]["created"] + 3600 - now) + 1)
                raise HTTPException(
                    429,
                    "Account creation is temporarily limited. Please try again later.",
                    headers={"Retry-After": str(retry)},
                )

    def create(self, name, password, claim_existing=False, signup_address=None):
        if claim_existing and Path(str(self.library_path) + ".erased").exists():
            raise ValueError(
                "The former owner's library was erased; create a new account without claiming it."
            )
        name = username(name)
        if signup_address is not None:
            with self.connection() as db:
                self.check_registration_limit(db, signup_address, time.time())
        encoded = hash_password(password)
        uid = uuid.uuid4().hex
        try:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                now = time.time()
                if signup_address is not None:
                    # Check again under the write lock so concurrent signups share the quota.
                    self.check_registration_limit(db, signup_address, now)
                    db.execute(
                        "DELETE FROM registrations WHERE created<=?", (now - 3600,)
                    )
                    db.execute(
                        "INSERT INTO registrations VALUES (?,?)",
                        (digest(signup_address), now),
                    )
                if db.execute("SELECT count(*) FROM users").fetchone()[0] >= 200:
                    raise ValueError(
                        "This server has reached its account limit. Contact the operator."
                    )
                db.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?)",
                    (uid, name, encoded, now, claim_existing),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "That username or existing library is already assigned."
            ) from exc
        return {"id": uid, "username": name, "legacy_library": claim_existing}

    def login(self, name, password):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username=?", (name.strip().lower(),)
            ).fetchone()
        valid = matches(row["password_hash"] if row else DUMMY_HASH, password)
        if not row or not valid:
            raise HTTPException(401, "Username or password is incorrect.")
        if HASHER.check_needs_rehash(row["password_hash"]):
            with self.connection() as db:
                db.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (hash_password(password), row["id"]),
                )
        return {k: row[k] for k in ("id", "username", "legacy_library")}

    def new_session(self, uid):
        token, now = secrets.token_urlsafe(32), time.time()
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            db.execute(
                "DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created DESC LIMIT 4)",
                (uid, uid),
            )
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?)",
                (digest(token), uid, now + SESSION_SECONDS, now),
            )
        return token

    def user(self, token):
        if not token or len(token) > 128:
            return None
        with self.connection() as db:
            row = db.execute(
                "SELECT u.id,u.username,u.legacy_library FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?",
                (digest(token), time.time()),
            ).fetchone()
        return dict(row) if row else None

    def revoke(self, token):
        with self.connection() as db:
            db.execute(
                "DELETE FROM sessions WHERE token_hash=?", (digest(token or ""),)
            )

    def password(self, uid, new_password):
        encoded = hash_password(new_password)
        with self.connection() as db:
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, uid))
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))

    def store(self, user):
        # IDs are generated on the server, never supplied as a library path.
        with self.lock:
            with self.connection() as db:
                if not db.execute(
                    "SELECT 1 FROM users WHERE id=?", (user["id"],)
                ).fetchone():
                    raise HTTPException(401, "Sign in to continue.")
            if user["id"] not in self.stores:
                path = (
                    self.library_path
                    if user["legacy_library"]
                    else self.root / "users" / user["id"] / "tracker.sqlite3"
                )
                self.stores[user["id"]] = Store(path)
                if len(self.stores) > 100:
                    del self.stores[next(iter(self.stores))]
            return self.stores[user["id"]]

    def delete(self, user):
        # Commit revocation before disk cleanup. A crash leaves a durable retry job.
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO erasures(user_id,legacy_library,requested) VALUES (?,?,?)",
                (user["id"], user["legacy_library"], time.time()),
            )
            db.execute("DELETE FROM users WHERE id=?", (user["id"],))
            db.execute(
                "DELETE FROM attempts WHERE key=?",
                ("user:" + digest(user["username"]),),
            )
            self.stores.pop(user["id"], None)
        return self.finish_erasure(user["id"])

    def finish_erasure(self, uid):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM erasures WHERE user_id=?", (uid,)
            ).fetchone()
        if not row or row["completed"]:
            return True
        try:
            wipe_library(
                library_path(self.root, self.library_path, uid, row["legacy_library"])
            )
            with self.connection() as db:
                db.execute(
                    "UPDATE erasures SET completed=? WHERE user_id=?",
                    (time.time(), uid),
                )
            return True
        except (sqlite3.Error, OSError):
            logging.getLogger(__name__).error(
                "Account erasure cleanup pending; retry scheduled"
            )
            return False

    def maintenance(self, cache=None):
        with self.connection() as db:
            pending = [
                r[0]
                for r in db.execute(
                    "SELECT user_id FROM erasures WHERE completed IS NULL"
                )
            ]
            db.execute("DELETE FROM attempts WHERE created<?", (time.time() - 900,))
            db.execute(
                "DELETE FROM registrations WHERE created<=?", (time.time() - 3600,)
            )
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            cache_pending = db.execute(
                "SELECT 1 FROM erasures WHERE cache_cleared=0 LIMIT 1"
            ).fetchone()
        for uid in pending:
            self.finish_erasure(uid)
        if cache_pending and cache:
            cache.clear()
            with self.connection() as db:
                db.execute("UPDATE erasures SET cache_cleared=1")
        if cache:
            cache.prune()


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class Signup(Credentials):
    password: str = Field(min_length=10, max_length=128)
    invite_code: str = Field(default="", max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class ConfirmAccount(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)


class DeleteAccount(ConfirmAccount):
    confirmation: str = Field(pattern=r"^DELETE$", max_length=6)


def reauthenticate(request, password):
    auth = manager(request)
    user = auth.user(request.cookies.get(cookie_name(request)))
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    auth.throttle(
        request.client.host if request.client else "unknown", user["username"]
    )
    auth.login(user["username"], password)
    return auth, user


@router.post("/export")
def export_account(body: ConfirmAccount, request: Request):
    auth, user = reauthenticate(request, body.current_password)
    auth.privacy_rates.charge(
        [(f"export:{user['id']}", 3, 3600), ("export:global", 12, 3600)]
    )
    store = auth.store(user)
    with auth.connection() as db:
        created = db.execute(
            "SELECT created FROM users WHERE id=?", (user["id"],)
        ).fetchone()[0]

    def export():
        started = time.monotonic()
        yield '{"format_version":1,"account":' + json.dumps(
            {"username": user["username"], "created": created}
        )
        with store.connection() as db:
            db.execute("BEGIN")
            for table in (
                "items",
                "links",
                "preferences",
                "suggestions",
                "suggestion_sources",
                "scans",
            ):
                yield ',"' + table + '":['
                first = True
                for row in db.execute(f"SELECT * FROM {table}"):
                    store.check_active()
                    if time.monotonic() - started > 120:
                        raise TimeoutError("Export timed out")
                    yield ("" if first else ",") + json.dumps(dict(row))
                    first = False
                yield "]"
        yield "}"

    return StreamingResponse(
        export(),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="trackify-export.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/delete")
def delete_account(body: DeleteAccount, request: Request, response: Response):
    auth, user = reauthenticate(request, body.current_password)
    complete = auth.delete(user)
    cache = (
        getattr(request.app.state.discoverer.fetcher, "cache", None)
        if hasattr(request.app.state.discoverer, "fetcher")
        else None
    )
    if cache:
        try:
            cache.clear()
            with auth.connection() as db:
                db.execute(
                    "UPDATE erasures SET cache_cleared=1 WHERE user_id=?", (user["id"],)
                )
        except (sqlite3.Error, OSError):
            complete = False
            logging.getLogger(__name__).error(
                "Account erased; shared cache cleanup requires retry"
            )
    response.delete_cookie(
        cookie_name(request),
        path="/",
        secure=request.app.state.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    response.status_code = 200 if complete else 202
    return {"ok": True, "cleanup_pending": not complete}


@router.post("/logout-all")
def logout_all(body: ConfirmAccount, request: Request, response: Response):
    auth, user = reauthenticate(request, body.current_password)
    with auth.connection() as db:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    response.delete_cookie(
        cookie_name(request),
        path="/",
        secure=request.app.state.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


def manager(request):
    if not request.app.state.accounts:
        raise HTTPException(404, "Accounts are not enabled on this server.")
    return request.app.state.accounts


def cookie_name(request):
    return COOKIE if request.app.state.secure_cookies else LOCAL_COOKIE


def set_session(request, response, uid):
    token = manager(request).new_session(uid)
    response.set_cookie(
        cookie_name(request),
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=request.app.state.secure_cookies,
        samesite="lax",
        path="/",
    )


def public_user(user):
    return {"username": user["username"]} if user else None


@router.get("/status")
def status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    auth = request.app.state.accounts
    return {
        "required": bool(auth),
        "registration": bool(auth and (auth.public_signup or auth.signup_code)),
        "invite_required": bool(auth and auth.signup_code and not auth.public_signup),
        "user": public_user(auth.user(request.cookies.get(cookie_name(request))))
        if auth
        else None,
    }


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    auth = manager(request)
    auth.throttle(request.client.host if request.client else "unknown", body.username)
    user = auth.login(body.username, body.password)
    set_session(request, response, user["id"])
    return {"user": public_user(user)}


@router.post("/register", status_code=201)
def register(body: Signup, request: Request, response: Response):
    auth = manager(request)
    address = request.client.host if request.client else "unknown"
    auth.throttle(address, body.username)
    if not auth.public_signup and (
        not auth.signup_code
        or not hmac.compare_digest(digest(body.invite_code), digest(auth.signup_code))
    ):
        raise HTTPException(403, "A valid invite code is required.")
    user = auth.create(
        body.username,
        body.password,
        signup_address=address if auth.public_signup else None,
    )
    set_session(request, response, user["id"])
    return {"user": public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response):
    manager(request).revoke(request.cookies.get(cookie_name(request)))
    response.delete_cookie(
        cookie_name(request),
        path="/",
        secure=request.app.state.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.post("/password")
def password(body: PasswordChange, request: Request, response: Response):
    auth = manager(request)
    user = auth.user(request.cookies.get(cookie_name(request)))
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    auth.throttle(
        request.client.host if request.client else "unknown", user["username"]
    )
    auth.login(user["username"], body.current_password)
    auth.password(user["id"], body.new_password)
    set_session(request, response, user["id"])
    return {"ok": True}


def main():
    parser = argparse.ArgumentParser(
        description="Manage accounts locally; passwords are prompted, never passed as arguments."
    )
    parser.add_argument(
        "action", choices=["create", "reset-password", "delete", "rename"]
    )
    parser.add_argument("username")
    parser.add_argument("--new-username")
    parser.add_argument(
        "--claim-existing",
        action="store_true",
        help="Assign the existing personal library to this account.",
    )
    args = parser.parse_args()
    auth = Accounts(
        os.environ.get(
            "TRACKER_DB",
            str(Path(__file__).resolve().parents[2] / "data" / "tracker.sqlite3"),
        )
    )
    if args.action in {"delete", "rename"}:
        with auth.connection() as db:
            row = db.execute(
                "SELECT id,username,legacy_library FROM users WHERE username=?",
                (username(args.username),),
            ).fetchone()
        if not row:
            parser.error("Account not found.")
        if args.action == "delete":
            if (
                input(
                    "Permanently erase account and library. Type the username to confirm: "
                )
                != row["username"]
            ):
                parser.error("Deletion cancelled.")
            complete = auth.delete(dict(row))
            from .cache import FetchCache

            auth.maintenance(FetchCache(auth.root / "fetch-cache.sqlite3"))
            print(
                "Account revoked and erased."
                if complete
                else "Account revoked; cleanup pending. Check maintenance logs."
            )
        else:
            if not args.new_username:
                parser.error("--new-username is required")
            with auth.connection() as db:
                db.execute(
                    "UPDATE users SET username=? WHERE id=?",
                    (username(args.new_username), row["id"]),
                )
                db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            print("Username updated; sessions revoked.")
        return
    password = getpass.getpass("Password (10–128 characters): ")
    if password != getpass.getpass("Repeat password: "):
        parser.error("Passwords did not match.")
    if args.action == "create":
        auth.create(args.username, password, args.claim_existing)
    else:
        with auth.connection() as db:
            row = db.execute(
                "SELECT id FROM users WHERE username=?", (username(args.username),)
            ).fetchone()
        if not row:
            parser.error("Account not found.")
        auth.password(row["id"], password)
    print("Account updated. Passwords and session tokens were not printed.")


if __name__ == "__main__":
    main()
