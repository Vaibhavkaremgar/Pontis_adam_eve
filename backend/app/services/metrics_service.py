from __future__ import annotations

import logging
from threading import Lock
from typing import Any

logger = logging.getLogger("pontis.metrics")
_lock = Lock()

_state = {
    "events": 0,
    "retrieval_requests": 0,
    "local_hits": 0,
    "pdl_fallbacks": 0,
    "fallbacks": 0,
    "errors": 0,
    "similarity_sum": 0.0,
    "similarity_count": 0,
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def log_metric(event: str, **fields) -> None:
    with _lock:
        _state["events"] += 1
        if event == "retrieval_request":
            _state["retrieval_requests"] += 1
        elif event == "local_hit":
            _state["local_hits"] += 1
        elif event == "pdl_fallback":
            _state["pdl_fallbacks"] += 1
        elif event == "fallback":
            _state["fallbacks"] += 1
        elif event == "error":
            _state["errors"] += 1

        if event in {"retrieval_similarity", "avg_similarity"}:
            _state["similarity_sum"] += _to_float(fields.get("value"), 0.0)
            _state["similarity_count"] += 1

    payload = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("metric event=%s %s", event, payload)


def get_metrics_snapshot() -> dict[str, float | int]:
    with _lock:
        retrieval_requests = int(_state["retrieval_requests"])
        local_hits = int(_state["local_hits"])
        pdl_fallbacks = int(_state["pdl_fallbacks"])
        fallbacks = int(_state["fallbacks"])
        errors = int(_state["errors"])
        similarity_count = int(_state["similarity_count"])
        similarity_sum = float(_state["similarity_sum"])
        events = int(_state["events"])

    local_hit_rate = (local_hits / retrieval_requests) if retrieval_requests else 0.0
    pdl_fallback_rate = (pdl_fallbacks / retrieval_requests) if retrieval_requests else 0.0
    fallback_rate = (fallbacks / retrieval_requests) if retrieval_requests else 0.0
    error_rate = (errors / retrieval_requests) if retrieval_requests else 0.0
    avg_similarity = (similarity_sum / similarity_count) if similarity_count else 0.0

    return {
        "events": events,
        "retrieval_requests": retrieval_requests,
        "local_hits": local_hits,
        "pdl_fallbacks": pdl_fallbacks,
        "fallbacks": fallbacks,
        "errors": errors,
        "local_hit_rate": round(local_hit_rate, 4),
        "pdl_fallback_rate": round(pdl_fallback_rate, 4),
        "fallback_rate": round(fallback_rate, 4),
        "error_rate": round(error_rate, 4),
        "avg_similarity": round(avg_similarity, 4),
    }
