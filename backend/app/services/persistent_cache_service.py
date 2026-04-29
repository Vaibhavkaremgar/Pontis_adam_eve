from __future__ import annotations

import json
import logging
import time
import threading
from collections import defaultdict

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)
_cache_lock = threading.Lock()
_cache_store: dict[str, dict[str, str]] = defaultdict(dict)

try:
    import redis
except Exception:  # pragma: no cover
    redis = None

_redis_client = None
_redis_ready = False
_redis_failed = False
_redis_retry_at = 0.0
_redis_last_error = ""
REDIS_RETRY_COOLDOWN_SECONDS = 60


def _redis_key(namespace: str, key: str) -> str:
    return f"pontis:cache:{namespace}:{key}"


def _get_redis_client():
    global _redis_client, _redis_ready, _redis_failed, _redis_retry_at, _redis_last_error

    if _redis_failed:
        if time.monotonic() < _redis_retry_at:
            return None
        _redis_ready = False
        _redis_client = None
    if _redis_ready and _redis_client is not None:
        return _redis_client
    if not REDIS_URL or redis is None:
        return None

    with _cache_lock:
        if _redis_ready and _redis_client is not None:
            return _redis_client
        if _redis_failed and time.monotonic() < _redis_retry_at:
            return None
        if _redis_failed:
            _redis_ready = False
            _redis_client = None
        try:
            client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            client.ping()
            _redis_client = client
            _redis_ready = True
            _redis_failed = False
            _redis_last_error = ""
            logger.info("Redis cache backend enabled")
            return _redis_client
        except Exception as exc:
            _redis_failed = True
            _redis_retry_at = time.monotonic() + REDIS_RETRY_COOLDOWN_SECONDS
            _redis_last_error = str(exc)
            logger.warning(
                "redis_unavailable fallback=in_memory retry_in_seconds=%s error=%s",
                REDIS_RETRY_COOLDOWN_SECONDS,
                exc,
            )
            return None


def get_json(namespace: str, key: str):
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(namespace, key))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            global _redis_failed, _redis_retry_at, _redis_last_error, _redis_ready, _redis_client
            _redis_failed = True
            _redis_ready = False
            _redis_client = None
            _redis_retry_at = time.monotonic() + REDIS_RETRY_COOLDOWN_SECONDS
            _redis_last_error = str(exc)
            logger.warning(
                "redis_read_failed fallback=in_memory retry_in_seconds=%s error=%s",
                REDIS_RETRY_COOLDOWN_SECONDS,
                exc,
            )

    with _cache_lock:
        value = _cache_store.get(namespace, {}).get(key)
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def set_json(namespace: str, key: str, value) -> None:
    payload = json.dumps(value)
    client = _get_redis_client()
    if client is not None:
        try:
            client.set(_redis_key(namespace, key), payload)
            return
        except Exception as exc:
            global _redis_failed, _redis_retry_at, _redis_last_error, _redis_ready, _redis_client
            _redis_failed = True
            _redis_ready = False
            _redis_client = None
            _redis_retry_at = time.monotonic() + REDIS_RETRY_COOLDOWN_SECONDS
            _redis_last_error = str(exc)
            logger.warning(
                "redis_write_failed fallback=in_memory retry_in_seconds=%s error=%s",
                REDIS_RETRY_COOLDOWN_SECONDS,
                exc,
            )

    with _cache_lock:
        _cache_store[namespace][key] = payload
