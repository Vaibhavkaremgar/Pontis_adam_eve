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
    "llm_usage": 0,
    "embedding_usage": 0,
    "pdl_usage": 0,
    "outreach_usage": 0,
    "queue_retry": 0,
    "queue_deadletter": 0,
    "queue_replayed": 0,
    "ranking_regressions": 0,
    "ranking_drifts": 0,
    "retrieval_quality_events": 0,
    "retrieval_mismatches": 0,
    "embedding_drift_events": 0,
    "llm_failures": 0,
    "prompt_failures": 0,
    "queue_ai_latency_sum": 0.0,
    "queue_ai_latency_count": 0,
    "preference_learning_gain_sum": 0.0,
    "preference_learning_gain_count": 0,
    "rerank_precision_gain_sum": 0.0,
    "rerank_precision_gain_count": 0,
    "pair_signal_quality_sum": 0.0,
    "pair_signal_quality_count": 0,
    "recruiter_preference_confidence_sum": 0.0,
    "recruiter_preference_confidence_count": 0,
    "resumes_processed": 0,
    "resumes_failed": 0,
    "embeddings_generated": 0,
    "vectors_inserted": 0,
    "duplicate_candidates_detected": 0,
    "parsing_failures": 0,
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
        elif event == "llm_usage":
            _state["llm_usage"] += 1
        elif event == "embedding_usage":
            _state["embedding_usage"] += 1
        elif event == "pdl_usage":
            _state["pdl_usage"] += 1
        elif event == "outreach_usage":
            _state["outreach_usage"] += 1
        elif event == "queue_job_retry":
            _state["queue_retry"] += 1
        elif event == "queue_job_deadlettered":
            _state["queue_deadletter"] += 1
        elif event == "queue_dead_letter_replayed":
            _state["queue_replayed"] += 1
        elif event == "ranking_regression":
            _state["ranking_regressions"] += 1
        elif event == "ranking_drift":
            _state["ranking_drifts"] += 1
        elif event == "retrieval_quality":
            _state["retrieval_quality_events"] += 1
        elif event == "retrieval_mismatch":
            _state["retrieval_mismatches"] += 1
        elif event == "embedding_drift":
            _state["embedding_drift_events"] += 1
        elif event == "llm_failure":
            _state["llm_failures"] += 1
        elif event == "prompt_failure":
            _state["prompt_failures"] += 1
        elif event == "resumes_processed":
            _state["resumes_processed"] += 1
        elif event == "resumes_failed":
            _state["resumes_failed"] += 1
        elif event == "embeddings_generated":
            _state["embeddings_generated"] += 1
        elif event == "vectors_inserted":
            _state["vectors_inserted"] += 1
        elif event == "duplicate_candidates_detected":
            _state["duplicate_candidates_detected"] += 1
        elif event == "parsing_failures":
            _state["parsing_failures"] += 1

        if event in {"retrieval_similarity", "avg_similarity"}:
            _state["similarity_sum"] += _to_float(fields.get("value"), 0.0)
            _state["similarity_count"] += 1
        if event == "queue_ai_latency":
            _state["queue_ai_latency_sum"] += _to_float(fields.get("value"), 0.0)
            _state["queue_ai_latency_count"] += 1
        if event == "preference_learning_gain":
            _state["preference_learning_gain_sum"] += _to_float(fields.get("value"), 0.0)
            _state["preference_learning_gain_count"] += 1
        if event == "rerank_precision_gain":
            _state["rerank_precision_gain_sum"] += _to_float(fields.get("value"), 0.0)
            _state["rerank_precision_gain_count"] += 1
        if event == "pair_signal_quality":
            _state["pair_signal_quality_sum"] += _to_float(fields.get("value"), 0.0)
            _state["pair_signal_quality_count"] += 1
        if event == "recruiter_preference_confidence":
            _state["recruiter_preference_confidence_sum"] += _to_float(fields.get("value"), 0.0)
            _state["recruiter_preference_confidence_count"] += 1

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
        llm_usage = int(_state["llm_usage"])
        embedding_usage = int(_state["embedding_usage"])
        pdl_usage = int(_state["pdl_usage"])
        outreach_usage = int(_state["outreach_usage"])
        queue_retry = int(_state["queue_retry"])
        queue_deadletter = int(_state["queue_deadletter"])
        queue_replayed = int(_state["queue_replayed"])
        ranking_regressions = int(_state["ranking_regressions"])
        ranking_drifts = int(_state["ranking_drifts"])
        retrieval_quality_events = int(_state["retrieval_quality_events"])
        retrieval_mismatches = int(_state["retrieval_mismatches"])
        embedding_drift_events = int(_state["embedding_drift_events"])
        llm_failures = int(_state["llm_failures"])
        prompt_failures = int(_state["prompt_failures"])
        resumes_processed = int(_state["resumes_processed"])
        resumes_failed = int(_state["resumes_failed"])
        embeddings_generated = int(_state["embeddings_generated"])
        vectors_inserted = int(_state["vectors_inserted"])
        duplicate_candidates_detected = int(_state["duplicate_candidates_detected"])
        parsing_failures = int(_state["parsing_failures"])
        queue_ai_latency_sum = float(_state["queue_ai_latency_sum"])
        queue_ai_latency_count = int(_state["queue_ai_latency_count"])
        preference_learning_gain_sum = float(_state["preference_learning_gain_sum"])
        preference_learning_gain_count = int(_state["preference_learning_gain_count"])
        rerank_precision_gain_sum = float(_state["rerank_precision_gain_sum"])
        rerank_precision_gain_count = int(_state["rerank_precision_gain_count"])
        pair_signal_quality_sum = float(_state["pair_signal_quality_sum"])
        pair_signal_quality_count = int(_state["pair_signal_quality_count"])
        recruiter_preference_confidence_sum = float(_state["recruiter_preference_confidence_sum"])
        recruiter_preference_confidence_count = int(_state["recruiter_preference_confidence_count"])
        events = int(_state["events"])

    local_hit_rate = (local_hits / retrieval_requests) if retrieval_requests else 0.0
    pdl_fallback_rate = (pdl_fallbacks / retrieval_requests) if retrieval_requests else 0.0
    fallback_rate = (fallbacks / retrieval_requests) if retrieval_requests else 0.0
    error_rate = (errors / retrieval_requests) if retrieval_requests else 0.0
    reply_rate = (replies_received / emails_sent) if emails_sent else 0.0
    followup_rate = (followups_sent / emails_sent) if emails_sent else 0.0
    conversion_rate = (interviews_booked / replies_received) if replies_received else 0.0
    avg_similarity = (similarity_sum / similarity_count) if similarity_count else 0.0
    avg_queue_ai_latency = (queue_ai_latency_sum / queue_ai_latency_count) if queue_ai_latency_count else 0.0
    avg_preference_learning_gain = (
        preference_learning_gain_sum / preference_learning_gain_count
    ) if preference_learning_gain_count else 0.0
    avg_rerank_precision_gain = (
        rerank_precision_gain_sum / rerank_precision_gain_count
    ) if rerank_precision_gain_count else 0.0
    avg_pair_signal_quality = (
        pair_signal_quality_sum / pair_signal_quality_count
    ) if pair_signal_quality_count else 0.0
    avg_recruiter_preference_confidence = (
        recruiter_preference_confidence_sum / recruiter_preference_confidence_count
    ) if recruiter_preference_confidence_count else 0.0
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
        "usage": {
            "llm": llm_usage,
            "embedding": embedding_usage,
            "pdl": pdl_usage,
            "outreach": outreach_usage,
            "queue_retry": queue_retry,
            "queue_deadletter": queue_deadletter,
            "queue_replayed": queue_replayed,
        },
        "resume_ingestion": {
            "resumes_processed": resumes_processed,
            "resumes_failed": resumes_failed,
            "embeddings_generated": embeddings_generated,
            "vectors_inserted": vectors_inserted,
            "duplicate_candidates_detected": duplicate_candidates_detected,
            "parsing_failures": parsing_failures,
        },
        "ai_observability": {
            "ranking_regressions": ranking_regressions,
            "ranking_drifts": ranking_drifts,
            "retrieval_quality_events": retrieval_quality_events,
            "retrieval_mismatches": retrieval_mismatches,
            "embedding_drift_events": embedding_drift_events,
            "llm_failures": llm_failures,
            "prompt_failures": prompt_failures,
            "avg_queue_ai_latency": round(avg_queue_ai_latency, 4),
            "avg_preference_learning_gain": round(avg_preference_learning_gain, 4),
            "avg_rerank_precision_gain": round(avg_rerank_precision_gain, 4),
            "avg_pair_signal_quality": round(avg_pair_signal_quality, 4),
            "avg_recruiter_preference_confidence": round(avg_recruiter_preference_confidence, 4),
        },
        "evaluation": evaluation,
    }
