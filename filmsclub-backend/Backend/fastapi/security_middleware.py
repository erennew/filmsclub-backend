"""Security middleware: edge lock, admin guard, limits, headers, scrubbing."""

from __future__ import annotations

import hmac
import json
import time
from typing import Dict, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from Backend.config import Security
from Backend.fastapi.auth import COOKIE_NAME, client_ip, origin_allowed
from Backend.helper.security import sanitize_public_payload

_RATE_RULES: Tuple[Tuple[str, int, int], ...] = (
    ("/api/media-info/", 120, 60),
    ("/api/subtitle/", 120, 60),
    ("/dl/", 300, 60),
    ("/api/", 180, 60),
    ("/admin", 120, 60),
)
_rate_windows: Dict[Tuple[str, str], Tuple[float, int]] = {}

_HTML_PATHS = ("/", "/watch", "/admin")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
    "X-DNS-Prefetch-Control": "off",
}
_HTML_CSP = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; "
    "img-src 'self' data: https:; media-src 'self' blob:; "
    "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'"
)
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _is_html_path(path: str) -> bool:
    return path == "/" or path.startswith("/watch/") or path == "/watch" or path == "/admin"


def _rate_limited(request: Request) -> bool:
    path = request.url.path
    for prefix, limit, window in _RATE_RULES:
        if not path.startswith(prefix):
            continue
        key = (prefix, client_ip(request))
        now = time.time()
        start, count = _rate_windows.get(key, (now, 0))
        if now - start >= window:
            start, count = now, 0
        count += 1
        _rate_windows[key] = (start, count)
        if len(_rate_windows) > 20000:
            _rate_windows.clear()
        return count > limit
    return False


def install_security_middleware(app: FastAPI) -> None:
    """Install middleware; the last registered layer runs outermost."""

    @app.middleware("http")
    async def _public_payload_scrubber(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if request.method != "GET" or not path.startswith("/api/"):
            return response
        if "application/json" not in response.headers.get("content-type", "").lower():
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response(
                content=body,
                status_code=response.status_code,
                headers={
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() not in ("content-length", "content-type")
                },
                media_type=response.headers.get("content-type", "application/json"),
                background=response.background,
            )

        cleaned = sanitize_public_payload(payload)
        new_body = json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in ("content-length", "content-type")
        }
        headers["Content-Type"] = "application/json; charset=utf-8"
        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=headers,
            background=response.background,
        )

    @app.middleware("http")
    async def _rate_limiter(request: Request, call_next):
        if _rate_limited(request):
            return JSONResponse(
                {"detail": "Too many requests"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

    @app.middleware("http")
    async def _admin_guard(request: Request, call_next):
        path = request.url.path
        if not (path == "/admin" or path.startswith("/admin/")):
            return await call_next(request)

        if Security.ADMIN_ALLOWED_IPS:
            if client_ip(request) not in Security.ADMIN_ALLOWED_IPS:
                return Response(status_code=404)

        if request.method in _UNSAFE_METHODS and request.cookies.get(COOKIE_NAME):
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and not origin_allowed(request, origin):
                return JSONResponse(
                    {"detail": "Cross-site request rejected"}, status_code=403
                )
        return await call_next(request)

    @app.middleware("http")
    async def _body_limit(request: Request, call_next):
        raw_length = request.headers.get("content-length")
        if raw_length and raw_length.isdigit():
            limit = (
                Security.MAX_ADMIN_BODY_BYTES
                if request.url.path.startswith("/admin")
                else Security.MAX_JSON_BODY_BYTES
            )
            if int(raw_length) > limit:
                return JSONResponse({"detail": "Payload too large"}, status_code=413)
        return await call_next(request)

    @app.middleware("http")
    async def _edge_lock(request: Request, call_next):
        secret = Security.EDGE_SHARED_SECRET
        # Browsers cannot attach headers to a CORS preflight, so OPTIONS must
        # reach CORSMiddleware even when the edge lock is enabled.
        if secret and request.method != "OPTIONS" and request.url.path != "/health":
            provided = request.headers.get("x-edge-key", "")
            if not hmac.compare_digest(provided, secret):
                return Response(status_code=404)
        return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)

        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        if request.url.scheme == "https" or forwarded_proto == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        path = request.url.path
        if _is_html_path(path):
            response.headers.setdefault("Content-Security-Policy", _HTML_CSP)
        if path.startswith("/dl/") or path.startswith("/api/subtitle/"):
            response.headers["Cache-Control"] = "private, no-store"

        for header in ("server", "x-powered-by"):
            if header in response.headers:
                del response.headers[header]
        return response
