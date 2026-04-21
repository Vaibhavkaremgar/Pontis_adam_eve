from __future__ import annotations

import json
import logging
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


def _redis_key(namespace: str, key: str) -> str:
    return f"pontis:cache:{namespace}:{key}"


def _get_redis_client():
    global _redis_client, _redis_ready, _redis_failed
    if _redis_failed:
        return None
    if _redis_ready and _redis_client is not None:
        return _redis_client
    if not REDIS_URL or redis is None:
        return None

    with _cache_lock:
        if _redis_ready and _redis_client is not None:
            return _redis_client
        if _redis_failed:
            return None
        try:
            client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            client.ping()
            _redis_client = client
            _redis_ready = True
            logger.info("Redis cache backend enabled")
            return _redis_client
        except Exception as exc:
            _redis_failed = True
            logger.warning("Redis unavailable; using in-memory cache fallback", exc_info=exc)
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
            logger.warning("Redis cache read failed; falling back to in-memory cache", exc_info=exc)

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
            logger.warning("Redis cache write failed; falling back to in-memory cache", exc_info=exc)

    with _cache_lock:
        _cache_store[namespace][key] = payload
