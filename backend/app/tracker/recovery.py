"""Verified recovery addresses and narrowly scoped, single-use recovery grants."""

import logging
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .accounts import cookie_name, digest, hash_password, manager

router = APIRouter(prefix="/api/auth")
TOKEN_SECONDS = 1800
GRANT_SECONDS = 900
MESSAGE = (
    "If that email is verified on an account, a recovery link will arrive shortly."
)
MAIL_SLOTS = threading.BoundedSemaphore(2)
PUBLIC_PATHS = {
    "/api/auth/email/verify",
    "/api/auth/recovery/request",
    "/api/auth/recovery/exchange",
    "/api/auth/recovery/status",
    "/api/auth/recovery/password",
}


def email_address(value):
    value = value.strip()
    if (
        len(value) > 254
        or not re.fullmatch(
            r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
            r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
            value,
        )
        or len(value.partition("@")[0]) > 64
    ):
        raise ValueError("Enter a valid email address.")
    local, _, domain = value.partition("@")
    return local + "@" + domain.lower()


class Mailer:
    def __init__(self):
        self.host = os.environ.get("TRACKER_SMTP_HOST", "").strip()
        self.sender = os.environ.get("TRACKER_SMTP_FROM", "").strip()
        self.username = os.environ.get("TRACKER_SMTP_USERNAME", "")
        self.password = os.environ.get("TRACKER_SMTP_PASSWORD", "")
        self.tls = os.environ.get("TRACKER_SMTP_TLS", "starttls")
        self.port = int(os.environ.get("TRACKER_SMTP_PORT", "587"))
        self.available = bool(self.host and self.sender)
        if self.available:
            email_address(self.sender)
            if self.tls not in {"starttls", "ssl"} or not 1 <= self.port <= 65535:
                raise ValueError("SMTP requires STARTTLS or SSL and a valid port.")
            if bool(self.username) != bool(self.password):
                raise ValueError("Set both SMTP username and password.")

    def send(self, recipient, subject, body):
        if not self.available:
            return False
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = (
            self.sender,
            recipient,
            subject,
        )
        message.set_content(body)
        context = ssl.create_default_context()
        connection = (
            smtplib.SMTP_SSL(self.host, self.port, timeout=10, context=context)
            if self.tls == "ssl"
            else smtplib.SMTP(self.host, self.port, timeout=10)
        )
        with connection as smtp:
            if self.tls == "starttls":
                smtp.starttls(context=context)
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
        return True


class Recovery:
    def __init__(self, accounts, origin, mailer=None):
        self.accounts, self.origin = accounts, origin
        self.mailer = mailer if mailer is not None else Mailer()

    def throttle(self, address, identity, purpose="mail"):
        now = time.time()
        identity = identity.casefold() if purpose == "mail" else identity
        limits = [
            (f"{purpose}:ip:" + digest(address), 12 if purpose == "mail" else 30),
            (f"{purpose}:identity:" + digest(identity), 3 if purpose == "mail" else 10),
            (f"{purpose}:global", 60 if purpose == "mail" else 300),
        ]
        with self.accounts.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM recovery_limits WHERE created<?", (now - 3600,))
            for key, limit in limits:
                if (
                    db.execute(
                        "SELECT count(*) FROM recovery_limits WHERE key=?", (key,)
                    ).fetchone()[0]
                    >= limit
                ):
                    raise HTTPException(
                        429,
                        "Too many recovery attempts. Try again in an hour.",
                        headers={"Retry-After": "3600"},
                    )
            db.executemany(
                "INSERT INTO recovery_limits VALUES (?,?)",
                ((key, now) for key, _ in limits),
            )

    def details(self, uid):
        with self.accounts.connection() as db:
            address = db.execute(
                "SELECT email FROM recovery_emails WHERE user_id=?", (uid,)
            ).fetchone()
            pending = db.execute(
                "SELECT email FROM recovery_tokens WHERE user_id=? AND purpose='verify' AND expires>? ORDER BY created DESC LIMIT 1",
                (uid, time.time()),
            ).fetchone()
        return {
            "email": address[0] if address else None,
            "pending_email": pending[0] if pending else None,
            "available": self.mailer.available,
        }

    def issue(self, db, uid, purpose, email):
        token, now = secrets.token_urlsafe(32), time.time()
        db.execute("DELETE FROM recovery_tokens WHERE expires<=?", (now,))
        # Recovery requests cannot invalidate a legitimate link already in transit.
        if purpose == "verify":
            db.execute(
                "DELETE FROM recovery_tokens WHERE user_id=? AND purpose='verify'",
                (uid,),
            )
        db.execute(
            "INSERT INTO recovery_tokens VALUES (?,?,?,?,?,?)",
            (digest(token), uid, purpose, email, now + TOKEN_SECONDS, now),
        )
        return token

    def prepare_email(self, user, email):
        with self.accounts.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.fence(db, user)
            return self.issue(db, user["id"], "verify", email)

    @staticmethod
    def fence(db, user):
        row = db.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()
        if not row or row[0] != user["password_hash"]:
            raise HTTPException(401, "Your credentials changed. Sign in again.")

    def remove_email(self, user):
        with self.accounts.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.fence(db, user)
            for table in ("recovery_emails", "recovery_tokens", "recovery_sessions"):
                db.execute(f"DELETE FROM {table} WHERE user_id=?", (user["id"],))

    def deliver(self, email, subject, body):
        if not MAIL_SLOTS.acquire(blocking=False):
            logging.getLogger(__name__).warning("Recovery mail capacity unavailable")
            return False
        try:
            return self.mailer.send(email, subject, body)
        except (OSError, smtplib.SMTPException, ValueError):
            logging.getLogger(__name__).error("Recovery email delivery failed")
            return False
        finally:
            MAIL_SLOTS.release()

    def send_link(self, email, purpose, token):
        route = "verify-email" if purpose == "verify" else "recover"
        action = (
            "Confirm your recovery email"
            if purpose == "verify"
            else "Reset your password"
        )
        url = f"{self.origin}/account/{route}#token={token}"
        sent = self.deliver(
            email,
            f"Trackify: {action.lower()}",
            f"{action}:\n\n{url}\n\nThis link expires in 30 minutes and works once. If you did not request it, ignore this email. Your password has not changed.\n",
        )
        if not sent:
            with self.accounts.connection() as db:
                db.execute(
                    "DELETE FROM recovery_tokens WHERE token_hash=?", (digest(token),)
                )

    def request(self, email):
        # Lookup and delivery both happen after the generic HTTP response.
        with self.accounts.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT user_id,email FROM recovery_emails WHERE email=?", (email,)
            ).fetchone()
            token = self.issue(db, row[0], "recover", row[1]) if row else None
        if token:
            self.send_link(row[1], "recover", token)

    @staticmethod
    def take_token(db, token, purpose):
        row = db.execute(
            "SELECT * FROM recovery_tokens WHERE token_hash=? AND purpose=? AND expires>?",
            (digest(token), purpose, time.time()),
        ).fetchone()
        if not row:
            raise HTTPException(
                400, "This link has expired or already been used. Request a new one."
            )
        db.execute("DELETE FROM recovery_tokens WHERE token_hash=?", (digest(token),))
        return row

    def verify(self, token):
        with self.accounts.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.take_token(db, token, "verify")
            conflict = db.execute(
                "SELECT 1 FROM recovery_emails WHERE email=? AND user_id!=?",
                (row["email"], row["user_id"]),
            ).fetchone()
            if not conflict:
                db.execute(
                    "INSERT INTO recovery_emails VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET email=excluded.email,verified=excluded.verified",
                    (row["user_id"], row["email"], time.time()),
                )
                db.execute(
                    "DELETE FROM recovery_tokens WHERE user_id=?", (row["user_id"],)
                )
                db.execute(
                    "DELETE FROM recovery_sessions WHERE user_id=?", (row["user_id"],)
                )
        if conflict:
            raise HTTPException(
                400,
                "This email could not be added. Use another address or recover its existing account.",
            )

    def exchange(self, token):
        grant, now = secrets.token_urlsafe(32), time.time()
        with self.accounts.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.take_token(db, token, "recover")
            if not db.execute(
                "SELECT 1 FROM recovery_emails WHERE user_id=? AND email=?",
                (row["user_id"], row["email"]),
            ).fetchone():
                raise HTTPException(400, "This recovery link is no longer valid.")
            db.execute("DELETE FROM recovery_sessions WHERE expires<=?", (now,))
            db.execute(
                "INSERT INTO recovery_sessions VALUES (?,?,?,?)",
                (digest(grant), row["user_id"], now + GRANT_SECONDS, now),
            )
        return grant

    def session(self, token):
        if not token or len(token) > 128:
            return None
        with self.accounts.connection() as db:
            row = db.execute(
                "SELECT u.id,u.username FROM recovery_sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?",
                (digest(token), time.time()),
            ).fetchone()
        return dict(row) if row else None

    def password(self, token, password):
        if not self.session(token):
            raise HTTPException(
                401, "Your recovery session expired. Request another link."
            )
        encoded = hash_password(password)
        with self.accounts.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT user_id FROM recovery_sessions WHERE token_hash=? AND expires>?",
                (digest(token), time.time()),
            ).fetchone()
            if not row:
                raise HTTPException(
                    401, "Your recovery session expired. Request another link."
                )
            uid = row[0]
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, uid))
            for table in ("sessions", "recovery_tokens", "recovery_sessions"):
                db.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
            email = db.execute(
                "SELECT email FROM recovery_emails WHERE user_id=?", (uid,)
            ).fetchone()
        return email[0] if email else None


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class EmailChange(EmailRequest):
    current_password: str = Field(min_length=1, max_length=128)


class EmailRemove(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)


class LinkToken(BaseModel):
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")


class RecoveryPassword(BaseModel):
    new_password: str = Field(min_length=10, max_length=128)


def service(request):
    manager(request)
    return request.app.state.recovery


def address(request):
    return request.client.host if request.client else "unknown"


def verified_account(request, password):
    auth = manager(request)
    user = auth.user(request.cookies.get(cookie_name(request)))
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    auth.throttle(address(request), user["username"])
    verified = auth._credentials(user["username"], password)
    if verified["id"] != user["id"]:
        raise HTTPException(401, "Sign in to continue.")
    return verified


def recovery_cookie(request):
    return (
        "__Host-trackify_recovery"
        if request.app.state.secure_cookies
        else "trackify_recovery"
    )


@router.get("/email")
def email_details(request: Request):
    return service(request).details(request.state.user["id"])


@router.post("/email", status_code=202)
def add_email(body: EmailChange, request: Request, tasks: BackgroundTasks):
    recovery = service(request)
    user = verified_account(request, body.current_password)
    email = email_address(body.email)
    if not recovery.mailer.available:
        raise HTTPException(503, "Recovery email is not configured on this server.")
    recovery.throttle(address(request), email)
    token = recovery.prepare_email(user, email)
    tasks.add_task(recovery.send_link, email, "verify", token)
    return {"message": "Check your email to confirm your recovery address."}


@router.delete("/email")
def remove_email(body: EmailRemove, request: Request):
    service(request).remove_email(verified_account(request, body.current_password))
    return {"ok": True}


@router.post("/email/verify")
def verify_email(body: LinkToken, request: Request):
    recovery = service(request)
    recovery.throttle(address(request), body.token, "token")
    recovery.verify(body.token)
    return {"ok": True}


@router.post("/recovery/request", status_code=202)
def request_recovery(body: EmailRequest, request: Request, tasks: BackgroundTasks):
    recovery = service(request)
    email = email_address(body.email)
    recovery.throttle(address(request), email)
    if recovery.mailer.available:
        tasks.add_task(recovery.request, email)
    return {"message": MESSAGE}


@router.post("/recovery/exchange")
def exchange_recovery(body: LinkToken, request: Request, response: Response):
    recovery = service(request)
    recovery.throttle(address(request), body.token, "token")
    grant = recovery.exchange(body.token)
    response.set_cookie(
        recovery_cookie(request),
        grant,
        max_age=GRANT_SECONDS,
        httponly=True,
        secure=request.app.state.secure_cookies,
        samesite="strict",
        path="/",
    )
    return {"ok": True}


@router.get("/recovery/status")
def recovery_status(request: Request):
    user = service(request).session(request.cookies.get(recovery_cookie(request)))
    return {"active": bool(user), **({"username": user["username"]} if user else {})}


@router.post("/recovery/password")
def reset_password(
    body: RecoveryPassword, request: Request, response: Response, tasks: BackgroundTasks
):
    recovery = service(request)
    token = request.cookies.get(recovery_cookie(request))
    recovery.throttle(address(request), token or "missing", "password")
    email = recovery.password(token, body.new_password)
    for name in (recovery_cookie(request), cookie_name(request)):
        response.delete_cookie(
            name,
            path="/",
            secure=request.app.state.secure_cookies,
            httponly=True,
            samesite="strict" if name == recovery_cookie(request) else "lax",
        )
    if email:
        tasks.add_task(
            recovery.deliver,
            email,
            "Trackify: your password changed",
            "Your Trackify password was reset and all signed-in sessions were ended. If you did not make this change, use account recovery to secure your account.\n",
        )
    return {"ok": True}
