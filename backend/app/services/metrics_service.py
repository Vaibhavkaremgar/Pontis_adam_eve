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
    "emails_sent": 0,
    "emails_failed": 0,
    "replies_received": 0,
    "interviews_booked": 0,
    "followups_sent": 0,
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
        elif event == "outreach_email_sent":
            _state["emails_sent"] += 1
        elif event == "outreach_email_failed":
            _state["emails_failed"] += 1
        elif event == "reply_received":
            _state["replies_received"] += 1
        elif event == "interview_booked":
            _state["interviews_booked"] += 1
        elif event == "followup_sent":
            _state["followups_sent"] += 1

        if event in {"retrieval_similarity", "avg_similarity"}:
            _state["similarity_sum"] += _to_float(fields.get("value"), 0.0)
            _state["similarity_count"] += 1

    payload = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("metric event=%s %s", event, payload)


def get_metrics_snapshot() -> dict[str, object]:
    from app.services.evaluation_service import get_evaluation_metrics_snapshot

    with _lock:
        retrieval_requests = int(_state["retrieval_requests"])
        local_hits = int(_state["local_hits"])
        pdl_fallbacks = int(_state["pdl_fallbacks"])
        fallbacks = int(_state["fallbacks"])
        errors = int(_state["errors"])
        emails_sent = int(_state["emails_sent"])
        emails_failed = int(_state["emails_failed"])
        replies_received = int(_state["replies_received"])
        interviews_booked = int(_state["interviews_booked"])
        followups_sent = int(_state["followups_sent"])
        similarity_count = int(_state["similarity_count"])
        similarity_sum = float(_state["similarity_sum"])
        events = int(_state["events"])

    local_hit_rate = (local_hits / retrieval_requests) if retrieval_requests else 0.0
    pdl_fallback_rate = (pdl_fallbacks / retrieval_requests) if retrieval_requests else 0.0
    fallback_rate = (fallbacks / retrieval_requests) if retrieval_requests else 0.0
    error_rate = (errors / retrieval_requests) if retrieval_requests else 0.0
    reply_rate = (replies_received / emails_sent) if emails_sent else 0.0
    followup_rate = (followups_sent / emails_sent) if emails_sent else 0.0
    conversion_rate = (interviews_booked / replies_received) if replies_received else 0.0
    avg_similarity = (similarity_sum / similarity_count) if similarity_count else 0.0
    evaluation = get_evaluation_metrics_snapshot()

    return {
        "events": events,
        "retrieval_requests": retrieval_requests,
        "local_hits": local_hits,
        "pdl_fallbacks": pdl_fallbacks,
        "fallbacks": fallbacks,
        "errors": errors,
        "emails_sent": emails_sent,
        "emails_failed": emails_failed,
        "replies_received": replies_received,
        "interviews_booked": interviews_booked,
        "followups_sent": followups_sent,
        "local_hit_rate": round(local_hit_rate, 4),
        "pdl_fallback_rate": round(pdl_fallback_rate, 4),
        "fallback_rate": round(fallback_rate, 4),
        "error_rate": round(error_rate, 4),
        "reply_rate": round(reply_rate, 4),
        "followup_rate": round(followup_rate, 4),
        "conversion_rate": round(conversion_rate, 4),
        "avg_similarity": round(avg_similarity, 4),
        "evaluation": evaluation,
    }
