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

_REQUEST_BUCKETS: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
_BUCKET_LOCK = Lock()


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def rate_limit_middleware(request: Request, call_next):
    rule = _RATE_LIMIT_RULES.get((request.method.upper(), request.url.path))
    if not rule:
        return await call_next(request)

    limit, window_seconds = rule
    now = time()
    ip = _client_ip(request)
    key = (ip, request.method.upper(), request.url.path)

    with _BUCKET_LOCK:
        bucket = _REQUEST_BUCKETS[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            logger.warning(
                "rate_limit_exceeded ip=%s method=%s path=%s limit=%s window_seconds=%s",
                ip,
                request.method.upper(),
                request.url.path,
                limit,
                window_seconds,
            )
            return JSONResponse(status_code=429, content=error_response("Too many requests. Please retry shortly."))

        bucket.append(now)

    return await call_next(request)
