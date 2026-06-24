"""
recall_service.py  —  Sprint 4: Semantic candidate recall from sourced_candidate_memory.

Design principles
-----------------
* Best-effort only.  Every public function swallows exceptions and returns safe
  defaults so that live X-Ray sourcing is never blocked by recall failures.
* Deterministic query text — no LLM, no external call.  The recall query is
  built from fields already present in the intake / job object.
* Bounded recall — hard cap at MAX_RECALL_CANDIDATES (default 20) before merge.
* Weak-result guard — hits below MIN_RECALL_SCORE (default 0.35) are dropped.
* Shape parity — recalled candidates are converted to the same dict shape used
  by live X-Ray candidates so the downstream merge/rerank works unchanged.
"""
from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import Any

logger = logging.getLogger(__name__)

# ── guardrails (tunable via env / caller) ────────────────────────────────────
MAX_RECALL_CANDIDATES: int = 20
MIN_RECALL_SCORE: float = 0.35   # cosine similarity floor; drops very weak hits


# ── helpers ───────────────────────────────────────────────────────────────────

def _t(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _list_str(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        return [_t(item) for item in value if _t(item)][:limit]
    if isinstance(value, str) and value.strip():
        return [s.strip() for s in re.split(r"[,;]+", value) if s.strip()][:limit]
    return []


# ── Phase 2: build recall query text ─────────────────────────────────────────

def build_recall_query_text(
    *,
    role: str,
    skills: list[str],
    location: str = "",
    seniority: str = "",
    archetype_signals: list[str] | None = None,
    job_summary: str = "",
) -> str:
    """
    Build a compact deterministic text used to embed and query Qdrant.

    Priority: role > skills > archetype_signals > location > seniority > summary.
    Capped at ~300 chars so the embedding stays focused on hiring intent.
    """
    parts: list[str] = []

    role_clean = _t(role)
    if role_clean:
        parts.append(f"Role: {role_clean}.")

    skill_clean = [_t(s) for s in skills if _t(s)][:6]
    if skill_clean:
        parts.append(f"Skills: {', '.join(skill_clean)}.")

    signals = [_t(s) for s in (archetype_signals or []) if _t(s)][:4]
    if signals:
        parts.append(f"Signals: {', '.join(signals)}.")

    loc = _t(location)
    if loc and loc.lower() not in {"remote", ""}:
        parts.append(f"Location: {loc}.")

    sen = _t(seniority)
    if sen:
        parts.append(f"Experience: {sen}.")

    if job_summary:
        parts.append(_t(job_summary)[:200])

    text = " ".join(parts).strip()
    return text or f"Candidate for {role_clean or 'software engineer'} role."


def build_recall_query_from_job(job: Any, intake: dict[str, Any] | None = None) -> str:
    """
    Convenience wrapper that extracts fields from a job ORM object + intake dict.
    Safe to call even if job or intake is None/malformed.
    """
    try:
        payload = intake if isinstance(intake, dict) else {}
        structured = getattr(job, "structured_data", None) or {}
        if not isinstance(structured, dict):
            structured = {}

        def _field(*keys: str) -> str:
            for k in keys:
                v = payload.get(k) or structured.get(k) or getattr(job, k, "")
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return ""

        def _skills() -> list[str]:
            for k in ("skills", "skills_required"):
                v = payload.get(k) or structured.get(k) or getattr(job, k, None)
                if isinstance(v, list):
                    return [_t(item) for item in v if _t(item)]
                if isinstance(v, str) and v.strip():
                    return [s.strip() for s in re.split(r"[,;]+", v) if s.strip()]
            return []

        # Extract archetype signal keywords for recall enrichment
        archetype_signals: list[str] = []
        calibration = structured.get("recruiterCalibration") or {}
        if isinstance(calibration, dict):
            for key in ("selected_archetypes", "selectedArchetypes", "archetype_pool"):
                pool = calibration.get(key)
                if isinstance(pool, list):
                    for arch in pool[:2]:
                        if not isinstance(arch, dict):
                            continue
                        for sk in ("signal_keywords", "signalKeywords", "keywords"):
                            kws = arch.get(sk)
                            if isinstance(kws, list):
                                archetype_signals.extend(_t(k) for k in kws[:3] if _t(k))
                    break

        return build_recall_query_text(
            role=_field("role", "title", "job_title"),
            skills=_skills(),
            location=_field("location"),
            seniority=_field("seniority", "experience_level", "experienceRequired"),
            archetype_signals=archetype_signals[:4],
            job_summary=_field("voice_summary", "voiceSummary") or _t(getattr(job, "description", ""))[:200],
        )
    except Exception as exc:
        logger.warning("build_recall_query_from_job_failed error=%s", str(exc))
        role_fallback = _t(getattr(job, "title", "") or "")
        return f"Candidate for {role_fallback or 'engineer'} role."


# ── Phase 3: query Qdrant sourced_candidate_memory ───────────────────────────

def query_sourced_candidate_memory(
    recall_text: str,
    *,
    top_k: int = MAX_RECALL_CANDIDATES,
    min_score: float = MIN_RECALL_SCORE,
) -> tuple[list[dict[str, Any]], str]:
    """
    Embed recall_text and search sourced_candidate_memory.

    Returns:
        (hits, skip_reason)
        hits        — list of raw Qdrant hit dicts with keys: score, payload
        skip_reason — non-empty string if recall was skipped/failed

    Failure modes:
        - Qdrant unavailable          → ([], "qdrant_unavailable")
        - embedding failure           → ([], "embedding_failed")
        - collection empty / no hits  → ([], "") with hits=[]
        - any other exception         → ([], "recall_exception")
    """
    if not recall_text or not recall_text.strip():
        return [], "empty_recall_query"

    # Import inside function to avoid circular imports at module load time.
    try:
        from app.services.embedding_service import embed as _embed
    except Exception as exc:
        logger.warning("recall_embedding_import_failed error=%s", str(exc))
        return [], "embedding_import_failed"

    try:
        from app.services.qdrant_service import _get_client, ensure_collection
        from app.core.config import SOURCED_CANDIDATE_COLLECTION_NAME as _COLL
    except ImportError:
        _COLL = "sourced_candidate_memory"
        try:
            from app.services.qdrant_service import _get_client, ensure_collection
        except Exception as exc:
            logger.warning("recall_qdrant_import_failed error=%s", str(exc))
            return [], "qdrant_import_failed"

    # Embed
    try:
        vector = list(_embed(recall_text))
    except Exception as exc:
        logger.warning("recall_embedding_failed error=%s", str(exc))
        return [], "embedding_failed"

    # Query
    try:
        client = _get_client()
        if not client:
            return [], "qdrant_unavailable"

        ensure_collection(_COLL)

        resolved_k = max(1, min(int(top_k), MAX_RECALL_CANDIDATES * 2))

        try:
            response = client.query_points(
                collection_name=_COLL,
                query=vector,
                limit=resolved_k,
                with_payload=True,
                with_vectors=False,
            )
            # normalize response shape
            raw_points = getattr(response, "points", None)
            if not isinstance(raw_points, list):
                raw_points = response if isinstance(response, list) else []
        except (AttributeError, TypeError):
            # Older client: fall back to search()
            raw_points = list(client.search(
                collection_name=_COLL,
                query_vector=vector,
                limit=resolved_k,
                with_payload=True,
                with_vectors=False,
            ))

        hits: list[dict[str, Any]] = []
        for point in raw_points:
            score = float(getattr(point, "score", 0.0) or 0.0)
            payload = getattr(point, "payload", None) or {}
            if score < min_score:
                continue
            hits.append({"score": score, "payload": payload})

        logger.info(
            "recall_query_complete collection=%s raw_hits=%s above_threshold=%s min_score=%.2f",
            _COLL,
            len(raw_points),
            len(hits),
            min_score,
        )
        return hits, ""

    except Exception as exc:
        logger.warning("recall_qdrant_search_failed error=%s", str(exc))
        return [], "recall_exception"


# ── Phase 4: normalize recalled candidates ────────────────────────────────────

def normalize_recalled_candidate(hit: dict[str, Any]) -> dict[str, Any] | None:
    """
    Convert a raw Qdrant hit (score + payload) into the live X-Ray candidate dict
    shape so the downstream merge/rerank works unchanged.

    Returns None if the hit is malformed or missing identity.
    """
    try:
        payload = hit.get("payload") or {}
        score = float(hit.get("score") or 0.0)

        linkedin_url = _t(payload.get("linkedin_url") or "")
        candidate_id = _t(payload.get("candidate_id") or "")
        name = _t(payload.get("candidate_name") or payload.get("name") or "Unknown")
        role = _t(payload.get("role") or payload.get("headline") or "")
        company = _t(payload.get("company") or "")
        location = _t(payload.get("location") or "")
        skills = _list_str(payload.get("all_skills") or payload.get("matched_skills") or [])
        fit_score = float(payload.get("fit_score") or 0.0)
        final_score = float(payload.get("final_score") or score)
        experience_label = _t(payload.get("experience_label") or "")
        summary = _t(payload.get("source_query") or "")  # best available text for recalled candidates
        source_query = _t(payload.get("source_query") or "")

        # Require at least one identity anchor
        if not linkedin_url and not candidate_id:
            logger.debug("recall_candidate_skipped reason=no_identity name=%s", name)
            return None

        canonical_id = candidate_id or linkedin_url

        return {
            # identity
            "id": canonical_id,
            "candidate_id": canonical_id,
            "full_name": name,
            "name": name,
            # profile
            "role": role or None,
            "job_title": role or None,
            "title": role or None,
            "headline": role or None,
            "company": company or None,
            "job_company_name": company or None,
            "current_company": company or None,
            "currentCompany": company or None,
            "location": location or None,
            "skills": skills,
            "experience": experience_label or None,
            "inferred_experience": experience_label or None,
            "inferredExperience": experience_label or None,
            # URLs
            "linkedin_url": linkedin_url or None,
            "linkedinUrl": linkedin_url or None,
            # snippet / summary
            "summary": summary or None,
            "snippet": summary or None,
            "snippet_quality": "partial",
            "snippetQuality": "partial",
            # scores
            "score": final_score,
            "fit_score": fit_score,
            "fitScore": fit_score,
            "recall_score": round(score, 4),
            # source provenance
            "source": "semantic_recall",
            "source_type": "semantic_recall",
            "sourceType": "semantic_recall",
            "source_provider": "qdrant_recall",
            "sourceProvider": "qdrant_recall",
            "source_query": source_query or None,
            "sourceQuery": source_query or None,
            "source_timestamp": payload.get("updated_at") or payload.get("created_at") or "",
            "sourceTimestamp": payload.get("updated_at") or payload.get("created_at") or "",
            # recall metadata
            "is_recalled": True,
            "recall_payload": payload,
            # raw_discovery passthrough
            "raw_discovery": {
                "linkedin_url": linkedin_url or None,
                "source_provider": "qdrant_recall",
                "source_type": "semantic_recall",
                "recall_score": round(score, 4),
            },
        }
    except Exception as exc:
        logger.warning("normalize_recalled_candidate_failed error=%s", str(exc))
        return None


def normalize_recalled_candidates(
    hits: list[dict[str, Any]],
    *,
    limit: int = MAX_RECALL_CANDIDATES,
) -> list[dict[str, Any]]:
    """Normalize a batch of Qdrant hits; skip malformed entries."""
    results: list[dict[str, Any]] = []
    for hit in hits[:limit]:
        normalized = normalize_recalled_candidate(hit)
        if normalized:
            results.append(normalized)
    return results


# ── Phase 5: merge live + recalled candidates ─────────────────────────────────

def _identity_key(candidate: dict[str, Any]) -> str:
    """Return the canonical deduplication key for a candidate dict."""
    linkedin = _t(
        candidate.get("linkedin_url")
        or candidate.get("linkedinUrl")
        or ""
    ).lower().rstrip("/")
    if linkedin and "linkedin.com/" in linkedin:
        return f"linkedin:{linkedin}"

    cid = _t(candidate.get("candidate_id") or candidate.get("id") or "").lower()
    if cid:
        return f"id:{cid}"

    name = _t(candidate.get("full_name") or candidate.get("name") or "").lower()
    company = _t(
        candidate.get("company")
        or candidate.get("current_company")
        or candidate.get("job_company_name")
        or ""
    ).lower()
    return f"profile:{name}|{company}"


def merge_live_and_recalled(
    live_candidates: list[dict[str, Any]],
    recalled_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """
    Merge live X-Ray and recalled candidates into one deduplicated pool.

    Rules:
    - Live candidates take precedence for all fields when same identity exists.
    - Recalled-only candidates are appended after live candidates.
    - Provenance is recorded in candidate["_sourcing_sources"]:
        "live_xray"      — appeared in live sourcing only
        "semantic_recall"— appeared in recall only
        "both"           — appeared in both (live fields win)

    Returns:
        (merged_pool, duplicates_collapsed_count)
    """
    seen: dict[str, int] = {}   # identity_key → index in merged
    merged: list[dict[str, Any]] = []
    collapsed = 0

    for candidate in live_candidates:
        key = _identity_key(candidate)
        candidate["_sourcing_sources"] = "live_xray"
        if key and key not in seen:
            seen[key] = len(merged)
            merged.append(candidate)
        # duplicate live candidates — skip silently

    for recalled in recalled_candidates:
        key = _identity_key(recalled)
        if key and key in seen:
            # Same candidate appeared in both pools — mark as "both", keep live fields
            existing = merged[seen[key]]
            existing["_sourcing_sources"] = "both"
            existing["recall_score"] = recalled.get("recall_score", 0.0)
            collapsed += 1
        else:
            recalled["_sourcing_sources"] = "semantic_recall"
            if key:
                seen[key] = len(merged)
            merged.append(recalled)

    logger.info(
        "merge_live_and_recalled live=%s recalled=%s merged=%s collapsed=%s",
        len(live_candidates),
        len(recalled_candidates),
        len(merged),
        collapsed,
    )
    return merged, collapsed


# ── High-level entry point ────────────────────────────────────────────────────

def run_semantic_recall(
    job: Any,
    intake: dict[str, Any] | None = None,
    *,
    top_k: int = MAX_RECALL_CANDIDATES,
    min_score: float = MIN_RECALL_SCORE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Full recall pipeline for one sourcing run.

    Returns:
        (recalled_candidates, diagnostics)

    diagnostics keys:
        recall_attempted, recall_skipped, recall_skip_reason,
        recalled_candidate_count, recalled_after_normalization, recall_latency_ms
    """
    diag: dict[str, Any] = {
        "recall_attempted": False,
        "recall_skipped": False,
        "recall_skip_reason": "",
        "recalled_candidate_count": 0,
        "recalled_after_normalization": 0,
        "recall_latency_ms": 0.0,
    }

    t0 = perf_counter()

    try:
        recall_text = build_recall_query_from_job(job, intake)
        diag["recall_attempted"] = True

        hits, skip_reason = query_sourced_candidate_memory(
            recall_text,
            top_k=top_k,
            min_score=min_score,
        )
        diag["recall_latency_ms"] = round((perf_counter() - t0) * 1000.0, 1)

        if skip_reason:
            diag["recall_skipped"] = True
            diag["recall_skip_reason"] = skip_reason
            return [], diag

        diag["recalled_candidate_count"] = len(hits)
        normalized = normalize_recalled_candidates(hits, limit=top_k)
        diag["recalled_after_normalization"] = len(normalized)

        return normalized, diag

    except Exception as exc:
        diag["recall_skipped"] = True
        diag["recall_skip_reason"] = f"unexpected_error: {str(exc)[:120]}"
        diag["recall_latency_ms"] = round((perf_counter() - t0) * 1000.0, 1)
        logger.warning("run_semantic_recall_failed error=%s", str(exc))
        return [], diag
