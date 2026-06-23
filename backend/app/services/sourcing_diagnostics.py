"""
sourcing_diagnostics.py

Lightweight sourcing-run diagnostics.
Sprint 3: extended with per-query-family diagnostics + structured no-results codes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)


@dataclass
class QueryFamilyDiagnostic:
    """Sprint 3 — per-query-family diagnostic record."""
    query_family_name: str                  # e.g. "role_query_1"
    query_family_purpose: str               # human description of the family
    actual_query_text: str
    is_fallback: bool = False
    raw_serpapi_results_count: int = 0
    normalized_candidates_count: int = 0
    deduped_candidates_count: int = 0
    ranked_candidates_count: int = 0
    produced_delivered_candidates: bool = False
    suppressed_by_diversity_guard: bool = False
    suppress_reason: str = ""
    fallback_trigger: str = ""             # why fallback was generated


@dataclass
class SourcingDiagnostics:
    job_id: str
    request_source: str                       # ui / slack / slack_calibration / api / selection
    role: str = ""
    mode: str = "volume"

    # query layer data (Sprint 1/2 legacy — kept for backward compat)
    query_layers: list[dict[str, Any]] = field(default_factory=list)

    # Sprint 3: per-family diagnostics
    query_family_diagnostics: list[QueryFamilyDiagnostic] = field(default_factory=list)

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
    # Sprint 3: richer no-results codes
    no_results_reason: str = ""      # see resolve_no_results_reason() for all values

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
        "query_layer_count=%s query_family_count=%s raw_serpapi_count=%s normalized_count=%s "
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
        len(diag.query_family_diagnostics),
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

    # Legacy per-layer logging (Sprint 1/2 backward compat)
    for index, layer in enumerate(diag.query_layers, start=1):
        logger.info(
            "sourcing_query_layer job_id=%s layer=%s/%s layer_type=%s query=%s",
            diag.job_id,
            index,
            len(diag.query_layers),
            layer.get("layer_type", ""),
            layer.get("query", ""),
        )

    # Sprint 3: per-family diagnostic log lines
    for fdiag in diag.query_family_diagnostics:
        logger.info(
            "sourcing_query_family_diagnostic "
            "job_id=%s family=%s purpose=%s is_fallback=%s "
            "raw=%s normalized=%s deduped=%s ranked=%s delivered=%s "
            "suppressed=%s suppress_reason=%s fallback_trigger=%s query=%s",
            diag.job_id,
            fdiag.query_family_name,
            fdiag.query_family_purpose,
            fdiag.is_fallback,
            fdiag.raw_serpapi_results_count,
            fdiag.normalized_candidates_count,
            fdiag.deduped_candidates_count,
            fdiag.ranked_candidates_count,
            fdiag.produced_delivered_candidates,
            fdiag.suppressed_by_diversity_guard,
            fdiag.suppress_reason,
            fdiag.fallback_trigger,
            fdiag.actual_query_text,
        )
        log_metric(
            "sourcing_query_family_diagnostic",
            job_id=diag.job_id,
            family=fdiag.query_family_name,
            is_fallback=fdiag.is_fallback,
            raw=fdiag.raw_serpapi_results_count,
            normalized=fdiag.normalized_candidates_count,
            deduped=fdiag.deduped_candidates_count,
            ranked=fdiag.ranked_candidates_count,
            delivered=fdiag.produced_delivered_candidates,
            suppressed=fdiag.suppressed_by_diversity_guard,
        )

    log_metric(
        "sourcing_run_diagnostics",
        job_id=diag.job_id,
        request_source=diag.request_source,
        role=diag.role,
        mode=diag.mode,
        query_layer_count=len(diag.query_layers),
        query_family_count=len(diag.query_family_diagnostics),
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


def build_query_family_diagnostics(
    layer_results: list[tuple[Any, list[dict[str, Any]], int]],
    *,
    delivered_candidates: list[Any],
) -> list[QueryFamilyDiagnostic]:
    """
    Sprint 3 — build per-family diagnostics from layer_results produced
    by discover_linkedin_xray_candidates.

    layer_results is a list of (XRayQueryLayer, raw_results, pages_fetched).
    """
    delivered_queries: set[str] = set()
    for c in delivered_candidates:
        q = ""
        if isinstance(c, dict):
            q = str(c.get("search_query") or c.get("source_query") or c.get("sourceQuery") or "").strip()
        else:
            q = str(getattr(c, "sourceQuery", "") or "").strip()
        if q:
            delivered_queries.add(q.lower())

    result: list[QueryFamilyDiagnostic] = []
    for layer, raw_results, _pages in layer_results:
        signals = dict(layer.signals or {})
        suppressed = bool(signals.get("suppressed_by_diversity_guard"))
        result.append(QueryFamilyDiagnostic(
            query_family_name=layer.layer_type,
            query_family_purpose=str(signals.get("family_purpose") or signals.get("family") or ""),
            actual_query_text=layer.query,
            is_fallback=bool(signals.get("is_fallback")),
            raw_serpapi_results_count=len(raw_results),
            normalized_candidates_count=len(raw_results),  # pre-dedup approximation
            deduped_candidates_count=len(raw_results),
            ranked_candidates_count=len(raw_results),
            produced_delivered_candidates=bool(
                layer.query.lower() in delivered_queries
                or any(layer.query.lower() in q for q in delivered_queries)
            ),
            suppressed_by_diversity_guard=suppressed,
            suppress_reason=str(signals.get("suppress_reason") or ""),
            fallback_trigger=str(signals.get("fallback_trigger") or ""),
        ))
    return result


# Sprint 3 — no-results reason codes
# ""                     → candidates delivered successfully
# "provider_disabled"    → SerpAPI is disabled / auth failed
# "quota_exhausted"      → daily SerpAPI budget exhausted
# "query_too_narrow"     → all query families returned zero raw results
# "filters_reduced_recall" → results existed but constrained families killed them
# "all_ranked_filtered"  → SerpAPI returned profiles but none survived scoring
# "all_filtered"         → generic: dedup/reviewability removed everything
# "zero_found"           → SerpAPI returned nothing at all

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
    Sprint 3 — classify why candidates were not delivered.
    Expanded from Sprint 1 to distinguish narrow-query vs filtered-result cases.
    """
    if serpapi_disabled:
        return "provider_disabled"
    if quota_exhausted:
        return "quota_exhausted"
    if raw_count == 0:
        return "query_too_narrow"          # Sprint 3: was "zero_found"
    if deduped_count == 0:
        return "filters_reduced_recall"    # Sprint 3: new — had raw but dedup killed them
    if ranked_count == 0:
        return "all_ranked_filtered"       # Sprint 3: new — had deduped but ranking removed them
    if delivered_count == 0:
        return "all_filtered"              # reviewability / swipe filter
    return ""
