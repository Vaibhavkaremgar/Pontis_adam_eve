"""
xray_candidate_persistence_service.py

Persists ranked X-Ray / SerpAPI candidates into Qdrant sourced_candidate_memory
after the sourcing pipeline has finished ranking.

Key design decisions:
- Hook point: called AFTER rerank_xray_candidates, BEFORE candidate delivery.
- Identity: profile-scoped point_id via sourced_candidate_point_id() — same
  LinkedIn profile never creates a duplicate vector even across multiple jobs.
- Failure isolation: per-candidate try/except — one bad candidate never aborts
  the rest of the batch. Qdrant unavailability never blocks delivery.
- Returns stats dict consumed by SourcingDiagnostics.
"""
from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from app.services.candidate_semantic_document import (
    build_candidate_semantic_document,
    sourced_candidate_point_id,
)
from app.services.qdrant_service import upsert_sourced_candidate

logger = logging.getLogger(__name__)


def persist_xray_candidates_to_qdrant(
    candidates: list[Any],
    *,
    job_id: str,
    company_id: str = "",
    request_source: str = "",
) -> dict[str, Any]:
    """
    Embed and upsert each ranked candidate into sourced_candidate_memory.

    Args:
        candidates:     list of CandidateResult (or dicts) already ranked/reranked.
        job_id:         the hiring job these candidates were sourced for.
        company_id:     optional company context stored in payload.
        request_source: "ui" | "slack" | "slack_calibration" | "api"

    Returns a stats dict for SourcingDiagnostics:
        {
            "qdrant_attempted": int,
            "qdrant_persisted": int,
            "qdrant_failed":    int,
            "qdrant_skipped":   bool,
            "qdrant_skip_reason": str,
            "qdrant_upsert_latency_ms": float,
        }
    """
    if not candidates:
        return {
            "qdrant_attempted": 0,
            "qdrant_persisted": 0,
            "qdrant_failed": 0,
            "qdrant_skipped": True,
            "qdrant_skip_reason": "no_candidates",
            "qdrant_upsert_latency_ms": 0.0,
        }

    # Import embed here to avoid circular imports at module load.
    try:
        from app.services.embedding_service import embed as _embed
    except Exception as exc:
        logger.warning("xray_qdrant_persist_skipped reason=embedding_import_failed error=%s", str(exc))
        return {
            "qdrant_attempted": 0,
            "qdrant_persisted": 0,
            "qdrant_failed": 0,
            "qdrant_skipped": True,
            "qdrant_skip_reason": "embedding_import_failed",
            "qdrant_upsert_latency_ms": 0.0,
        }

    attempted = 0
    persisted = 0
    failed = 0
    t_start = perf_counter()

    for candidate in candidates:
        attempted += 1
        try:
            doc = build_candidate_semantic_document(
                candidate,
                job_id=job_id,
                company_id=company_id,
                request_source=request_source,
            )
            semantic_text = doc["semantic_text"]
            payload = doc["payload"]
            point_id = sourced_candidate_point_id(candidate)

            try:
                vector = _embed(semantic_text)
            except Exception as emb_exc:
                logger.warning(
                    "xray_qdrant_embed_failed candidate_id=%s error=%s",
                    payload.get("candidate_id", ""),
                    str(emb_exc),
                )
                failed += 1
                continue

            ok = upsert_sourced_candidate(
                point_id=point_id,
                vector=vector,
                payload=payload,
            )
            if ok:
                persisted += 1
            else:
                failed += 1

        except Exception as exc:
            logger.warning(
                "xray_qdrant_persist_candidate_failed job_id=%s error=%s",
                job_id,
                str(exc),
            )
            failed += 1

    latency_ms = round((perf_counter() - t_start) * 1000.0, 1)
    logger.info(
        "xray_qdrant_persist_complete job_id=%s attempted=%s persisted=%s failed=%s latency_ms=%.1f",
        job_id,
        attempted,
        persisted,
        failed,
        latency_ms,
    )
    return {
        "qdrant_attempted": attempted,
        "qdrant_persisted": persisted,
        "qdrant_failed": failed,
        "qdrant_skipped": False,
        "qdrant_skip_reason": "",
        "qdrant_upsert_latency_ms": latency_ms,
    }
