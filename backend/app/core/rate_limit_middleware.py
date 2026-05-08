from __future__ import annotations

import logging
from collections import defaultdict, deque
from threading import Lock
from time import time

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import (
    RATE_LIMIT_AUTH_REQUEST_OTP_PER_MINUTE,
    RATE_LIMIT_AUTH_VERIFY_OTP_PER_MINUTE,
    RATE_LIMIT_CANDIDATES_PER_MINUTE,
)
from app.utils.responses import error_response

logger = logging.getLogger(__name__)

# (method, path) -> (max_requests, window_seconds)
_RATE_LIMIT_RULES: dict[tuple[str, str], tuple[int, int]] = {
    ("POST", "/api/auth/request-otp"): (RATE_LIMIT_AUTH_REQUEST_OTP_PER_MINUTE, 60),
    ("POST", "/api/auth/verify-otp"): (RATE_LIMIT_AUTH_VERIFY_OTP_PER_MINUTE, 60),
    ("GET", "/api/candidates"): (RATE_LIMIT_CANDIDATES_PER_MINUTE, 60),
    ("GET", "/api/candidates/shortlisted"): (RATE_LIMIT_CANDIDATES_PER_MINUTE, 60),
    ("POST", "/api/outreach/webhook/reply"): (120, 60),
}

# In-memory fallback (used when Redis is unavailable)
_REQUEST_BUCKETS: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
_BUCKET_LOCK = Lock()

# Number of trusted proxy hops (Railway sits behind 1 proxy)
_TRUSTED_PROXY_DEPTH = 1


def _client_ip(request: Request) -> str:
    """
    Extract real client IP safely.
    Only trust the last N entries in X-Forwarded-For where N = _TRUSTED_PROXY_DEPTH.
    This prevents IP spoofing via crafted headers.
    """
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= _TRUSTED_PROXY_DEPTH:
            # Take the entry that is _TRUSTED_PROXY_DEPTH hops from the right
            return parts[-_TRUSTED_PROXY_DEPTH]
        if parts:
            return parts[0]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _redis_rate_limit(ip: str, method: str, path: str, limit: int, window_seconds: int) -> bool:
    """Returns True if request is ALLOWED. Uses Redis sliding window."""
    try:
        from app.services.redis_service import rate_limit_check
        key = f"{ip}:{method}:{path}"
        return rate_limit_check(key, limit, window_seconds)
    except Exception:
        return True  # fail-open


def _memory_rate_limit(ip: str, method: str, path: str, limit: int, window_seconds: int) -> bool:
    """In-memory fallback rate limiter."""
    now = time()
    key = (ip, method, path)
    with _BUCKET_LOCK:
        bucket = _REQUEST_BUCKETS[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
    return True


async def rate_limit_middleware(request: Request, call_next):
    rule = _RATE_LIMIT_RULES.get((request.method.upper(), request.url.path))
    if not rule:
        return await call_next(request)

    limit, window_seconds = rule
    ip = _client_ip(request)
    method = request.method.upper()
    path = request.url.path

    # Try Redis first, fall back to in-memory
    try:
        from app.services.redis_service import get_redis
        if get_redis() is not None:
            allowed = _redis_rate_limit(ip, method, path, limit, window_seconds)
        else:
            allowed = _memory_rate_limit(ip, method, path, limit, window_seconds)
    except Exception:
        allowed = _memory_rate_limit(ip, method, path, limit, window_seconds)

    if not allowed:
        logger.warning(
            "rate_limit_exceeded ip=%s method=%s path=%s limit=%s window_seconds=%s",
            ip, method, path, limit, window_seconds,
        )
        return JSONResponse(status_code=429, content=error_response("Too many requests. Please retry shortly."))

    return await call_next(request)
