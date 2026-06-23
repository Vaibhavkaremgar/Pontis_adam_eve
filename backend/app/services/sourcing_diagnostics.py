"""
sourcing_diagnostics.py

Lightweight sourcing-run diagnostics.

Collects and logs structured diagnostic data for each sourcing run so quality
is debuggable without modifying the sourcing engine itself.

No external dependencies — only stdlib + existing metrics_service.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)


@dataclass
class SourcingDiagnostics:
    job_id: str
    request_source: str                       # ui / slack / slack_calibration / api / selection
    role: str = ""
    mode: str = "volume"

    # query layer data
    query_layers: list[dict[str, Any]] = field(default_factory=list)

    # raw counts from SerpAPI
    raw_serpapi_count: int = 0

    # after _normalize_candidate_result (url filtering)
    normalized_count: int = 0

    # after 3-level dedup
    deduped_count: int = 0

    # after xray build + rerank
    ranked_count: int = 0

    # after swiped / reviewability filter
    delivered_count: int = 0

    # delivery channel
    delivered_to: str = ""           # "ui", "slack", "both", "none"

    # failure signals
    quota_exhausted: bool = False
    serpapi_disabled: bool = False
    no_results_reason: str = ""      # "" | "quota_exhausted" | "provider_disabled" | "all_filtered" | "zero_found"

    # timing (ms)
    query_generation_ms: float = 0.0
    serpapi_latency_ms: float = 0.0
    total_pipeline_ms: float = 0.0
    rerank_ms: float = 0.0

    # Qdrant persistence stats (Sprint 2)
    qdrant_attempted: int = 0
    qdrant_persisted: int = 0
    qdrant_failed: int = 0
    qdrant_skipped: bool = False
    qdrant_skip_reason: str = ""      # "" | "qdrant_unavailable" | "embedding_failed" | "no_candidates"
    qdrant_upsert_latency_ms: float = 0.0

    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def emit_sourcing_diagnostics(diag: SourcingDiagnostics) -> None:
    """
    Emit a structured sourcing diagnostic log line and log_metric entry.
    Call once at the end of each sourcing run.
    """
    no_results_triggered = diag.delivered_count == 0

    logger.info(
        "sourcing_run_diagnostics "
        "job_id=%s request_source=%s role=%s mode=%s "
        "query_layer_count=%s raw_serpapi_count=%s normalized_count=%s "
        "deduped_count=%s ranked_count=%s delivered_count=%s "
        "delivered_to=%s quota_exhausted=%s serpapi_disabled=%s "
        "no_results_reason=%s no_results_triggered=%s "
        "query_generation_ms=%.1f serpapi_latency_ms=%.1f total_pipeline_ms=%.1f "
        "qdrant_attempted=%s qdrant_persisted=%s qdrant_failed=%s "
        "qdrant_skipped=%s qdrant_skip_reason=%s qdrant_upsert_latency_ms=%.1f "
        "started_at=%s",
        diag.job_id,
        diag.request_source,
        diag.role,
        diag.mode,
        len(diag.query_layers),
        diag.raw_serpapi_count,
        diag.normalized_count,
        diag.deduped_count,
        diag.ranked_count,
        diag.delivered_count,
        diag.delivered_to,
        diag.quota_exhausted,
        diag.serpapi_disabled,
        diag.no_results_reason,
        no_results_triggered,
        diag.query_generation_ms,
        diag.serpapi_latency_ms,
        diag.total_pipeline_ms,
        diag.qdrant_attempted,
        diag.qdrant_persisted,
        diag.qdrant_failed,
        diag.qdrant_skipped,
        diag.qdrant_skip_reason,
        diag.qdrant_upsert_latency_ms,
        diag.started_at,
    )

    for index, layer in enumerate(diag.query_layers, start=1):
        logger.info(
            "sourcing_query_layer job_id=%s layer=%s/%s layer_type=%s query=%s",
            diag.job_id,
            index,
            len(diag.query_layers),
            layer.get("layer_type", ""),
            layer.get("query", ""),
        )

    log_metric(
        "sourcing_run_diagnostics",
        job_id=diag.job_id,
        request_source=diag.request_source,
        role=diag.role,
        mode=diag.mode,
        query_layer_count=len(diag.query_layers),
        raw_serpapi_count=diag.raw_serpapi_count,
        normalized_count=diag.normalized_count,
        deduped_count=diag.deduped_count,
        ranked_count=diag.ranked_count,
        delivered_count=diag.delivered_count,
        delivered_to=diag.delivered_to,
        quota_exhausted=diag.quota_exhausted,
        serpapi_disabled=diag.serpapi_disabled,
        no_results_reason=diag.no_results_reason,
        no_results_triggered=no_results_triggered,
        query_generation_ms=round(diag.query_generation_ms, 1),
        serpapi_latency_ms=round(diag.serpapi_latency_ms, 1),
        total_pipeline_ms=round(diag.total_pipeline_ms, 1),
        qdrant_attempted=diag.qdrant_attempted,
        qdrant_persisted=diag.qdrant_persisted,
        qdrant_failed=diag.qdrant_failed,
        qdrant_skipped=diag.qdrant_skipped,
        qdrant_skip_reason=diag.qdrant_skip_reason,
        qdrant_upsert_latency_ms=round(diag.qdrant_upsert_latency_ms, 1),
    )


def resolve_no_results_reason(
    *,
    quota_exhausted: bool,
    serpapi_disabled: bool,
    raw_count: int,
    deduped_count: int,
    ranked_count: int,
    delivered_count: int,
) -> str:
    """
    Classify the reason candidates were not delivered so the correct
    recruiter-facing message can be shown.
    """
    if serpapi_disabled:
        return "provider_disabled"
    if quota_exhausted:
        return "quota_exhausted"
    if raw_count == 0:
        return "zero_found"
    if deduped_count == 0 or ranked_count == 0:
        return "all_filtered"
    if delivered_count == 0:
        return "all_filtered"
    return ""
