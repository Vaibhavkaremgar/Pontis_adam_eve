"""
feedback_memory_service.py  —  Sprint 5: Recruiter Feedback Memory + Candidate State Intelligence.

Responsibilities
----------------
* Look up prior recruiter feedback for a candidate at two scopes:
    - same_job   (strongest signal)
    - same_company (weaker signal — cross-job awareness)
* Tag each candidate with a deterministic candidate_state:
    new / seen_before / passed_before / approved_before / shortlisted_before / held_before
* Apply deterministic suppression / boost rules to a ranked pool.
* Expose state fields on candidate payloads.
* Never block sourcing or delivery — all operations are best-effort.

Design rules
------------
* Reuses the existing candidate_feedback table — no new DB table needed.
* The existing CandidateFeedbackEntity stores feedback = 'accept' | 'reject'.
  Sprint 5 reads 'accept' as approved / shortlisted_before and 'reject' as passed_before.
  For 'maybe' / 'not_now' actions the interview row status is used to detect hold / warm.
* All public functions catch exceptions and return safe defaults.
* The module does NOT touch voice intake, Slack intake, enrichment, outreach, or scheduling.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── candidate state constants ─────────────────────────────────────────────────
STATE_NEW = "new"
STATE_SEEN_BEFORE = "seen_before"
STATE_PASSED_BEFORE = "passed_before"
STATE_APPROVED_BEFORE = "approved_before"
STATE_SHORTLISTED_BEFORE = "shortlisted_before"
STATE_HELD_BEFORE = "held_before"

# ── ranking adjustment constants ──────────────────────────────────────────────
# same-job rules
BOOST_APPROVED_SAME_JOB: float = 0.15       # fit_score additive boost  (out of 5.0)
BOOST_SHORTLISTED_SAME_JOB: float = 0.10
SUPPRESS_PASSED_SAME_JOB: float = -3.0      # strong downrank (effectively removes from shortlist)

# same-company rules (milder)
BOOST_APPROVED_SAME_COMPANY: float = 0.05
DOWNRANK_PASSED_SAME_COMPANY: float = -0.30  # mild downrank only

# guard: never push score above 5.0 or below 0.0
_MAX_FIT_SCORE = 5.0
_MIN_FIT_SCORE = 0.0


# ── diagnostics dataclass ─────────────────────────────────────────────────────

@dataclass
class FeedbackMemoryDiagnostics:
    feedback_lookup_attempted: bool = False
    feedback_lookup_skipped: bool = False
    feedback_lookup_skip_reason: str = ""
    candidates_checked: int = 0
    candidates_new: int = 0
    candidates_seen_before: int = 0
    candidates_passed_before: int = 0
    candidates_approved_before: int = 0
    candidates_shortlisted_before: int = 0
    candidates_held_before: int = 0
    candidates_suppressed: int = 0
    candidates_boosted: int = 0
    feedback_lookup_latency_ms: float = 0.0
    error: str = ""


# ── internal helpers ──────────────────────────────────────────────────────────

def _t(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _candidate_id_from(candidate: Any) -> str:
    if isinstance(candidate, dict):
        return _t(candidate.get("id") or candidate.get("candidate_id") or "")
    return _t(getattr(candidate, "id", "") or getattr(candidate, "candidate_id", ""))


def _linkedin_url_from(candidate: Any) -> str:
    if isinstance(candidate, dict):
        return _t(
            candidate.get("linkedin_url")
            or candidate.get("linkedinUrl")
            or (candidate.get("profileData") or {}).get("linkedin_url")
            or ""
        ).lower().rstrip("/")
    profile_data = getattr(candidate, "profileData", None) or {}
    return _t(
        getattr(candidate, "linkedinUrl", "")
        or getattr(candidate, "linkedin_url", "")
        or (profile_data.get("linkedin_url") if isinstance(profile_data, dict) else "")
        or ""
    ).lower().rstrip("/")


def _fit_score_from(candidate: Any) -> float:
    if isinstance(candidate, dict):
        return float(candidate.get("fitScore") or candidate.get("fit_score") or 0.0)
    return float(getattr(candidate, "fitScore", 0.0) or 0.0)


def _set_fit_score(candidate: Any, value: float) -> None:
    clamped = round(max(_MIN_FIT_SCORE, min(_MAX_FIT_SCORE, value)), 2)
    if isinstance(candidate, dict):
        candidate["fitScore"] = clamped
        candidate["fit_score"] = clamped
    else:
        try:
            object.__setattr__(candidate, "fitScore", clamped)
        except Exception:
            pass


def _set_candidate_state_fields(candidate: Any, state: str, scope: str, feedback: dict[str, Any]) -> None:
    """Attach state metadata to a candidate (dict or CandidateResult)."""
    payload = {
        "candidate_state": state,
        "seen_before": state != STATE_NEW,
        "passed_before": state == STATE_PASSED_BEFORE,
        "approved_before": state == STATE_APPROVED_BEFORE,
        "shortlisted_before": state == STATE_SHORTLISTED_BEFORE,
        "held_before": state == STATE_HELD_BEFORE,
        "feedback_scope": scope,
        "prior_feedback": feedback,
    }
    if isinstance(candidate, dict):
        candidate.update(payload)
        # Also nest inside profileData if present
        if isinstance(candidate.get("profileData"), dict):
            candidate["profileData"].update(payload)
    else:
        # CandidateResult — set on profileData dict
        profile_data = getattr(candidate, "profileData", None)
        if isinstance(profile_data, dict):
            profile_data.update(payload)
        # Also try direct attribute for payload transparency
        for key, val in payload.items():
            try:
                object.__setattr__(candidate, key, val)
            except Exception:
                pass


# ── Phase 1 audit: load feedback index for a job + company ────────────────────

def _load_feedback_index(
    db: Session,
    *,
    job_id: str,
    company_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Returns:
        same_job_index   : {candidate_id -> feedback_type}
        company_index    : {candidate_id -> feedback_type}  (all jobs in company)

    feedback_type values: 'accept' | 'reject' | 'warm' | 'selected' | 'advanced'
    """
    from sqlalchemy import select
    from app.models.entities import CandidateFeedbackEntity, InterviewEntity

    same_job_index: dict[str, str] = {}
    company_index: dict[str, str] = {}

    # Load candidate_feedback rows for this job
    rows = db.scalars(
        select(CandidateFeedbackEntity).where(
            CandidateFeedbackEntity.job_id == job_id
        )
    ).all()
    for row in rows:
        cid = _t(str(row.candidate_id or ""))
        if not cid:
            continue
        fb = _t(str(row.feedback or "")).lower()
        same_job_index[cid] = fb

    # Load candidate_feedback rows for the whole company (cross-job)
    if company_id:
        company_rows = db.scalars(
            select(CandidateFeedbackEntity).where(
                CandidateFeedbackEntity.company_id == company_id
            )
        ).all()
        for row in company_rows:
            cid = _t(str(row.candidate_id or ""))
            if not cid:
                continue
            fb = _t(str(row.feedback or "")).lower()
            # Prefer same-job signal; company index supplements
            if cid not in company_index:
                company_index[cid] = fb

    # Load interview rows to detect 'selected', 'advanced', 'warm' states
    interview_rows = db.scalars(
        select(InterviewEntity).where(
            InterviewEntity.job_id == job_id
        )
    ).all()
    for row in interview_rows:
        cid = _t(str(row.candidate_id or ""))
        if not cid:
            continue
        status = _t(str(row.status or "")).lower()
        # Map interview status → feedback type
        if status in {"selected", "advanced", "shortlisted", "offer_sent", "hired"}:
            same_job_index[cid] = "accept"
        elif status in {"rejected", "archived", "disqualified"}:
            if cid not in same_job_index:
                same_job_index[cid] = "reject"
        elif status in {"warm", "not_now", "maybe"}:
            if cid not in same_job_index:
                same_job_index[cid] = "warm"

    return same_job_index, company_index


def _feedback_to_state(feedback_type: str, scope: str) -> str:
    """Convert a raw feedback_type string to a candidate state constant."""
    fb = (feedback_type or "").lower().strip()
    if fb == "accept":
        return STATE_APPROVED_BEFORE if scope == "same_job" else STATE_APPROVED_BEFORE
    if fb == "reject":
        return STATE_PASSED_BEFORE
    if fb in {"warm", "not_now", "maybe"}:
        return STATE_HELD_BEFORE
    if fb in {"selected", "advanced", "shortlisted"}:
        return STATE_SHORTLISTED_BEFORE
    return STATE_SEEN_BEFORE


def _resolve_candidate_state(
    candidate_id: str,
    *,
    same_job_index: dict[str, str],
    company_index: dict[str, str],
) -> tuple[str, str, dict[str, Any]]:
    """
    Returns (state, scope, feedback_meta).
    scope: 'same_job' | 'same_company' | ''
    """
    if candidate_id in same_job_index:
        fb = same_job_index[candidate_id]
        state = _feedback_to_state(fb, "same_job")
        return state, "same_job", {"feedback": fb, "scope": "same_job"}

    if candidate_id in company_index:
        fb = company_index[candidate_id]
        state = _feedback_to_state(fb, "same_company")
        return state, "same_company", {"feedback": fb, "scope": "same_company"}

    return STATE_NEW, "", {}


# ── Phase 4: tag candidates with state ────────────────────────────────────────

def tag_candidates_with_feedback_state(
    candidates: list[Any],
    *,
    db: Session,
    job_id: str,
    company_id: str = "",
) -> tuple[list[Any], FeedbackMemoryDiagnostics]:
    """
    Tag each candidate with prior recruiter feedback state.
    Returns (tagged_candidates, diagnostics).
    Never raises — on error returns candidates unmodified.
    """
    diag = FeedbackMemoryDiagnostics()

    if not candidates:
        return candidates, diag

    if not job_id:
        diag.feedback_lookup_skipped = True
        diag.feedback_lookup_skip_reason = "no_job_id"
        return candidates, diag

    diag.feedback_lookup_attempted = True
    t0 = perf_counter()

    try:
        same_job_index, company_index = _load_feedback_index(
            db, job_id=job_id, company_id=company_id or ""
        )
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        diag.feedback_lookup_skipped = True
        diag.feedback_lookup_skip_reason = f"db_error: {str(exc)[:80]}"
        diag.feedback_lookup_latency_ms = round((perf_counter() - t0) * 1000.0, 1)
        diag.error = str(exc)[:200]
        logger.warning("feedback_memory_lookup_failed job_id=%s error=%s", job_id, str(exc))
        return candidates, diag

    diag.feedback_lookup_latency_ms = round((perf_counter() - t0) * 1000.0, 1)
    diag.candidates_checked = len(candidates)

    for candidate in candidates:
        try:
            cid = _candidate_id_from(candidate)
            if not cid:
                _set_candidate_state_fields(candidate, STATE_NEW, "", {})
                diag.candidates_new += 1
                continue

            state, scope, feedback_meta = _resolve_candidate_state(
                cid,
                same_job_index=same_job_index,
                company_index=company_index,
            )
            _set_candidate_state_fields(candidate, state, scope, feedback_meta)

            if state == STATE_NEW:
                diag.candidates_new += 1
            elif state == STATE_PASSED_BEFORE:
                diag.candidates_passed_before += 1
            elif state == STATE_APPROVED_BEFORE:
                diag.candidates_approved_before += 1
            elif state == STATE_SHORTLISTED_BEFORE:
                diag.candidates_shortlisted_before += 1
            elif state == STATE_HELD_BEFORE:
                diag.candidates_held_before += 1
            else:
                diag.candidates_seen_before += 1

        except Exception as exc:
            logger.debug("feedback_state_tag_failed candidate=%s error=%s", str(candidate)[:50], str(exc))
            try:
                _set_candidate_state_fields(candidate, STATE_NEW, "", {})
            except Exception:
                pass
            diag.candidates_new += 1

    logger.info(
        "feedback_memory_tag_complete job_id=%s checked=%s new=%s passed=%s approved=%s shortlisted=%s held=%s seen=%s latency_ms=%.1f",
        job_id,
        diag.candidates_checked,
        diag.candidates_new,
        diag.candidates_passed_before,
        diag.candidates_approved_before,
        diag.candidates_shortlisted_before,
        diag.candidates_held_before,
        diag.candidates_seen_before,
        diag.feedback_lookup_latency_ms,
    )
    return candidates, diag


# ── Phase 5: apply deterministic ranking rules ────────────────────────────────

def apply_feedback_ranking_rules(
    candidates: list[Any],
    *,
    diag: FeedbackMemoryDiagnostics | None = None,
) -> list[Any]:
    """
    Apply deterministic suppression / boost rules based on candidate_state.

    Rules:
      same_job + passed_before  → fitScore -= 3.0  (effectively removes from shortlist)
      same_job + approved/shortlisted → fitScore += 0.15 / 0.10
      same_company + passed_before → fitScore -= 0.30 (mild downrank)
      same_company + approved → fitScore += 0.05

    Mutates fitScore in-place on the candidate objects/dicts.
    Returns the same list (sorted by new fitScore desc).
    Never raises.
    """
    suppressed = 0
    boosted = 0

    for candidate in candidates:
        try:
            if isinstance(candidate, dict):
                state = candidate.get("candidate_state") or STATE_NEW
                scope = candidate.get("feedback_scope") or ""
            else:
                profile_data = getattr(candidate, "profileData", {}) or {}
                state = profile_data.get("candidate_state") or STATE_NEW
                scope = profile_data.get("feedback_scope") or ""

            current_score = _fit_score_from(candidate)
            adjustment = 0.0

            if scope == "same_job":
                if state == STATE_PASSED_BEFORE:
                    adjustment = SUPPRESS_PASSED_SAME_JOB
                    suppressed += 1
                elif state == STATE_APPROVED_BEFORE:
                    adjustment = BOOST_APPROVED_SAME_JOB
                    boosted += 1
                elif state == STATE_SHORTLISTED_BEFORE:
                    adjustment = BOOST_SHORTLISTED_SAME_JOB
                    boosted += 1
            elif scope == "same_company":
                if state == STATE_PASSED_BEFORE:
                    adjustment = DOWNRANK_PASSED_SAME_COMPANY
                    suppressed += 1
                elif state == STATE_APPROVED_BEFORE:
                    adjustment = BOOST_APPROVED_SAME_COMPANY
                    boosted += 1

            if adjustment != 0.0:
                _set_fit_score(candidate, current_score + adjustment)

        except Exception as exc:
            logger.debug("feedback_ranking_rule_failed error=%s", str(exc))

    if diag is not None:
        diag.candidates_suppressed = suppressed
        diag.candidates_boosted = boosted

    logger.info(
        "feedback_ranking_rules_applied suppressed=%s boosted=%s",
        suppressed, boosted,
    )
    return candidates


# ── High-level entry point for sourcing pipeline ──────────────────────────────

def apply_feedback_memory(
    candidates: list[Any],
    *,
    db: Session,
    job_id: str,
    company_id: str = "",
) -> tuple[list[Any], FeedbackMemoryDiagnostics]:
    """
    Full Sprint 5 feedback memory pipeline for one sourcing run.

    1. Tag each candidate with prior recruiter feedback state.
    2. Apply deterministic suppression / boost rules.
    3. Return (candidates, diagnostics).

    Never raises — safe to call in any sourcing context.
    """
    diag = FeedbackMemoryDiagnostics()
    if not candidates:
        return candidates, diag
    try:
        candidates, diag = tag_candidates_with_feedback_state(
            candidates, db=db, job_id=job_id, company_id=company_id
        )
        candidates = apply_feedback_ranking_rules(candidates, diag=diag)
    except Exception as exc:
        diag.feedback_lookup_skipped = True
        diag.feedback_lookup_skip_reason = f"pipeline_error: {str(exc)[:120]}"
        diag.error = str(exc)[:200]
        logger.warning("apply_feedback_memory_failed job_id=%s error=%s", job_id, str(exc))
    return candidates, diag
