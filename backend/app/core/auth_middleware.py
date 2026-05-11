from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
import secrets

from app.core.config import AUTH_COOKIE_NAME, CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from app.core.security import verify_access_token, verify_csrf_token
from app.utils.exceptions import APIError
from app.utils.responses import error_response


EXEMPT_PATHS = {
    "/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/health",
    "/slack/commands",
    "/slack/interactions",
    "/api/auth/login",
    "/api/auth/google",
    "/api/auth/google/callback",
    "/api/auth/request-otp",
    "/api/auth/verify-otp",
}

CSRF_EXEMPT_PATH_PREFIXES = (
    "/api/auth/request-otp",
    "/api/auth/verify-otp",
    "/api/auth/google",
    "/api/auth/csrf",
    "/api/outreach/webhook",
    "/api/webhooks/",
    "/api/slack/",
)


def _resolve_bearer_or_cookie_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    return (request.cookies.get(AUTH_COOKIE_NAME) or "").strip()


def _csrf_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS or any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PATH_PREFIXES)


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)
    if path.startswith("/api/outreach/webhook") or path.startswith("/api/webhooks/"):
        return await call_next(request)
    if path.startswith("/api/health") or path == "/health" or path.startswith("/health/"):
        return await call_next(request)
    if request.method == "GET" and path == "/api/interview/session":
        return await call_next(request)
    if request.method == "POST" and path == "/api/interview/book":
        return await call_next(request)
    if not path.startswith("/api") or path in EXEMPT_PATHS:
        return await call_next(request)

    token = _resolve_bearer_or_cookie_token(request)
    if not token:
        return JSONResponse(status_code=401, content=error_response("Missing session credentials"))
    try:
        claims = verify_access_token(token)
    except APIError as exc:
        return JSONResponse(status_code=exc.status_code, content=error_response(exc.message))

    request.state.user = {
        "id": claims["sub"],
        "email": claims.get("email"),
        "role": claims.get("role") or "recruiter",
    }

    if request.method not in {"GET", "HEAD"} and not _csrf_exempt(path):
        csrf_header = request.headers.get(CSRF_HEADER_NAME, "").strip()
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "").strip()
        if not csrf_header or not csrf_cookie or not secrets.compare_digest(csrf_header, csrf_cookie):
            return JSONResponse(status_code=403, content=error_response("Invalid CSRF token"))
        try:
            verify_csrf_token(csrf_header, user_id=claims["sub"])
        except APIError as exc:
            return JSONResponse(status_code=exc.status_code, content=error_response(exc.message))

    return await call_next(request)
