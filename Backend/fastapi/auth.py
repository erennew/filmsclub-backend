"""Admin authentication: bcrypt password checks and signed session cookies."""

import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict

import jwt
from fastapi import HTTPException, Request, Response
from passlib.context import CryptContext

from Backend.config import Telegram
from Backend.logger import LOGGER

COOKIE_NAME = "filmsclub_admin_session"
SESSION_TTL_SECONDS = int(os.getenv("ADMIN_SESSION_TTL", "86400"))
MAX_LOGIN_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_attempts: Dict[str, Deque[float]] = defaultdict(deque)

_configured_hash = os.getenv("ADMIN_WEB_PASSWORD_HASH", "").strip()


def _build_password_hash() -> str:
    if _configured_hash:
        if not _pwd_context.identify(_configured_hash):
            raise RuntimeError("ADMIN_WEB_PASSWORD_HASH is not a valid bcrypt hash")
        return _configured_hash

    password = (Telegram.ADMIN_WEB_PASSWORD or "").strip()
    if len(password) < 8 or password.lower() in {"admin123", "password", "admin", "changeme"}:
        raise RuntimeError(
            "ADMIN_WEB_PASSWORD must be set to a strong value (at least 8 characters)."
        )
    return _pwd_context.hash(password)


ADMIN_PASSWORD_HASH = _build_password_hash()
SESSION_SECRET = os.getenv("ADMIN_SESSION_SECRET", "").strip()

if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)
    LOGGER.warning(
        "ADMIN_SESSION_SECRET is not set; admin sessions will be invalidated on restart."
    )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune_attempts(ip: str, now: float) -> None:
    bucket = _attempts[ip]
    while bucket and now - bucket[0] > LOGIN_WINDOW_SECONDS:
        bucket.popleft()


def login_allowed(ip: str) -> bool:
    now = time.monotonic()
    _prune_attempts(ip, now)
    return len(_attempts[ip]) < MAX_LOGIN_ATTEMPTS


def record_failed_login(ip: str) -> None:
    now = time.monotonic()
    _prune_attempts(ip, now)
    _attempts[ip].append(now)
    if len(_attempts) > 10000:
        for key in list(_attempts)[:2000]:
            _attempts.pop(key, None)


def verify_credentials(username: str, password: str) -> bool:
    username_ok = hmac.compare_digest(
        (username or "").encode("utf-8"), (Telegram.ADMIN_WEB_USERNAME or "").encode("utf-8")
    )
    try:
        password_ok = _pwd_context.verify(password or "", ADMIN_PASSWORD_HASH)
    except (ValueError, TypeError):
        password_ok = False
    return username_ok and password_ok


def create_session(username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": "admin",
        "iat": now,
        "exp": now + timedelta(seconds=SESSION_TTL_SECONDS),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm="HS256")


def read_session(token: str) -> bool:
    try:
        payload = jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return payload.get("role") == "admin" and payload.get("sub") == Telegram.ADMIN_WEB_USERNAME


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    secure = forwarded_proto == "https" or request.url.scheme == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def verify_admin_auth(request: Request) -> bool:
    """Accept a valid session cookie, or legacy admin headers for CLI callers."""
    token = request.cookies.get(COOKIE_NAME)
    if token and read_session(token):
        return True

    username = request.headers.get("x-admin-username")
    password = request.headers.get("x-admin-password")
    if username and password and verify_credentials(username, password):
        return True

    raise HTTPException(status_code=401, detail="Admin authentication required")
