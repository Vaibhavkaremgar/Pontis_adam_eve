from __future__ import annotations

import logging
from threading import Lock
from typing import Iterable

from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)
_lock = Lock()

_state = {
    "fetch_events": 0,
    "fetch_candidates": 0,
    "swipe_events": 0,
    "accept_events": 0,
    "reject_events": 0,
    "shortlist_events": 0,
    "shortlisted_candidates": 0,
    "similarity_sum": 0.0,
    "final_score_sum": 0.0,
}


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _iter_candidates(candidates: Iterable[object]) -> list[object]:
    return [candidate for candidate in candidates if candidate is not None]


def record_candidate_fetch(*, job_id: str, candidates: Iterable[object]) -> None:
    items = _iter_candidates(candidates)
    similarity_values: list[float] = []
    final_score_values: list[float] = []

    for candidate in items:
        explanation = getattr(candidate, "explanation", None)
        similarity_values.append(_to_float(getattr(explanation, "semanticScore", 0.0), 0.0))
        final_score_values.append(_to_float(getattr(explanation, "finalScore", 0.0), 0.0))

    avg_similarity = sum(similarity_values) / len(similarity_values) if similarity_values else 0.0
    avg_final_score = sum(final_score_values) / len(final_score_values) if final_score_values else 0.0

    with _lock:
        _state["fetch_events"] += 1
        _state["fetch_candidates"] += len(items)
        _state["similarity_sum"] += sum(similarity_values)
        _state["final_score_sum"] += sum(final_score_values)

    logger.info(
        "evaluation_metrics_updated event=candidate_fetch job_id=%s total=%s avg_similarity=%.4f avg_final_score=%.4f",
        job_id,
        len(items),
        avg_similarity,
        avg_final_score,
    )
    log_metric(
        "evaluation_metrics_updated",
        job_id=job_id,
        metric_event="candidate_fetch",
        total=len(items),
        avg_similarity=round(avg_similarity, 4),
        avg_final_score=round(avg_final_score, 4),
    )


def record_swipe_action(*, job_id: str, action: str, shortlisted: bool = False) -> None:
    normalized_action = (action or "").strip().lower()
    with _lock:
        _state["swipe_events"] += 1
        if normalized_action == "accept":
            _state["accept_events"] += 1
        elif normalized_action == "reject":
            _state["reject_events"] += 1
        if shortlisted:
            _state["shortlist_events"] += 1
            _state["shortlisted_candidates"] += 1

    logger.info(
        "evaluation_metrics_updated event=swipe_action job_id=%s action=%s shortlisted=%s",
        job_id,
        normalized_action,
        shortlisted,
    )
    log_metric(
        "evaluation_metrics_updated",
        job_id=job_id,
        metric_event="swipe_action",
        action=normalized_action,
        shortlisted=shortlisted,
    )


def record_shortlist_event(*, job_id: str, shortlisted_count: int) -> None:
    count = max(0, int(shortlisted_count))
    with _lock:
        _state["shortlist_events"] += 1

    logger.info(
        "evaluation_metrics_updated event=shortlist_view job_id=%s shortlisted_count=%s",
        job_id,
        count,
    )
    log_metric(
        "evaluation_metrics_updated",
        job_id=job_id,
        metric_event="shortlist_view",
        shortlisted_count=count,
    )


def get_evaluation_metrics_snapshot() -> dict[str, float | int]:
    with _lock:
        fetch_events = int(_state["fetch_events"])
        fetch_candidates = int(_state["fetch_candidates"])
        swipe_events = int(_state["swipe_events"])
        accept_events = int(_state["accept_events"])
        reject_events = int(_state["reject_events"])
        shortlist_events = int(_state["shortlist_events"])
        shortlisted_candidates = int(_state["shortlisted_candidates"])
        similarity_sum = float(_state["similarity_sum"])
        final_score_sum = float(_state["final_score_sum"])

    avg_similarity = similarity_sum / fetch_candidates if fetch_candidates else 0.0
    avg_final_score = final_score_sum / fetch_candidates if fetch_candidates else 0.0
    acceptance_rate = accept_events / swipe_events if swipe_events else 0.0
    shortlist_rate = shortlisted_candidates / fetch_candidates if fetch_candidates else 0.0

    return {
        "fetch_events": fetch_events,
        "fetch_candidates": fetch_candidates,
        "swipe_events": swipe_events,
        "accept_events": accept_events,
        "reject_events": reject_events,
        "shortlist_events": shortlist_events,
        "shortlisted_candidates": shortlisted_candidates,
        "avg_similarity": round(avg_similarity, 4),
        "avg_final_score": round(avg_final_score, 4),
        "acceptance_rate": round(acceptance_rate, 4),
        "shortlist_rate": round(shortlist_rate, 4),
    }
