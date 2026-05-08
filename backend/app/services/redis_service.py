"""
Redis service — single connection pool shared across the process.

Provides:
- get_redis()          : returns a live Redis client or None if unavailable
- acquire_lock()       : distributed advisory lock (for scheduler deduplication)
- release_lock()       : release a held lock
- rate_limit_check()   : sliding-window rate limiter backed by Redis
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Generator

import redis as redis_lib
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)

_client: Redis | None = None
_client_failed = False


def get_redis() -> Redis | None:
    """Return a shared Redis client, or None if Redis is unavailable."""
    global _client, _client_failed

    if _client_failed:
        return None
    if _client is not None:
        return _client

    url = (REDIS_URL or "").strip()
    if not url:
        logger.warning("redis_unavailable reason=REDIS_URL_not_set")
        _client_failed = True
        return None

    try:
        client = redis_lib.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        client.ping()
        _client = client
        logger.info("redis_connected url=%s", url[:40])
        return _client
    except RedisError as exc:
        logger.warning("redis_connection_failed error=%s", str(exc))
        _client_failed = True
        return None


def reset_redis_client() -> None:
    """Force reconnect on next call — used after transient failures."""
    global _client, _client_failed
    _client = None
    _client_failed = False


def close_redis_client() -> None:
    global _client
    if _client is None:
        return
    try:
        _client.close()
    except Exception as exc:
        logger.warning("redis_close_failed error=%s", str(exc))
    finally:
        _client = None


# ── Distributed lock ──────────────────────────────────────────────────────────

_LOCK_PREFIX = "pontis:lock:"
_LOCK_TTL_SECONDS = 120  # max time a scheduler job may hold a lock


def acquire_lock(name: str, ttl: int = _LOCK_TTL_SECONDS) -> bool:
    """
    Try to acquire a named distributed lock.
    Returns True if the lock was acquired, False if already held.
    """
    r = get_redis()
    if r is None:
        # No Redis → allow execution (single-instance fallback)
        return True
    key = f"{_LOCK_PREFIX}{name}"
    try:
        acquired = r.set(key, "1", nx=True, ex=ttl)
        return bool(acquired)
    except RedisError as exc:
        logger.warning("redis_lock_acquire_failed name=%s error=%s", name, str(exc))
        return True  # fail-open: allow execution


def release_lock(name: str) -> None:
    """Release a named distributed lock."""
    r = get_redis()
    if r is None:
        return
    key = f"{_LOCK_PREFIX}{name}"
    try:
        r.delete(key)
    except RedisError as exc:
        logger.warning("redis_lock_release_failed name=%s error=%s", name, str(exc))


@contextmanager
def distributed_lock(name: str, ttl: int = _LOCK_TTL_SECONDS) -> Generator[bool, None, None]:
    """Context manager that acquires and releases a distributed lock."""
    acquired = acquire_lock(name, ttl)
    try:
        yield acquired
    finally:
        if acquired:
            release_lock(name)


# ── Sliding-window rate limiter ───────────────────────────────────────────────

_RATE_PREFIX = "pontis:rate:"


def rate_limit_check(key: str, limit: int, window_seconds: int) -> bool:
    """
    Sliding-window rate limiter.
    Returns True if the request is ALLOWED, False if rate-limited.

    Falls back to allowing all requests if Redis is unavailable.
    """
    r = get_redis()
    if r is None:
        return True  # fail-open

    redis_key = f"{_RATE_PREFIX}{key}"
    now = time.time()
    window_start = now - window_seconds

    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(redis_key, "-inf", window_start)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window_seconds + 1)
        results = pipe.execute()
        count = int(results[2])
        return count <= limit
    except RedisError as exc:
        logger.warning("redis_rate_limit_failed key=%s error=%s", key, str(exc))
        return True  # fail-open
