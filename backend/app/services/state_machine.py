from __future__ import annotations

"""
Compatibility guard for candidate swipe and outreach transitions.

The canonical ATS lifecycle lives in `ats_lifecycle_service.py`; this module
normalizes legacy labels before validating transitions so older code paths keep
working without mutating the canonical state model in incompatible ways.
"""

import logging

from app.services.ats_lifecycle_service import normalize_ats_status
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

# Canonical states accepted by the runtime lifecycle service.
VALID_STATES: frozenset[str] = frozenset(
    {
        "sourced",
        "reviewed",
        "selected",
        "enriching",
        "enriched",
        "enrichment_failed",
        "outreach_pending",
        "outreach_sent",
        "replied_interested",
        "replied_not_interested",
        "interview_requested",
        "interview_scheduled",
        "interview_no_show",
        "interview_completed",
        "advanced",
        "second_round_requested",
        "second_round_scheduled",
        "final_round",
        "offer_stage",
        "offer_sent",
        "placed",
        "hired",
        "warm",
        "disqualified",
        "rejected",
        "search_closed",
        "archived",
    }
)

# Explicit allow-list - every other pair is forbidden
_ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("reviewed", "sourced"),
        ("reviewed", "selected"),
        ("reviewed", "disqualified"),
        ("reviewed", "warm"),
        ("reviewed", "archived"),
        ("sourced", "reviewed"),
        ("selected", "enriching"),
        ("selected", "disqualified"),
        ("selected", "warm"),
        ("selected", "archived"),
        ("enriching", "enriched"),
        ("enriching", "enrichment_failed"),
        ("enriching", "archived"),
        ("enriched", "outreach_pending"),
        ("enriched", "outreach_sent"),
        ("enriched", "rejected"),
        ("enriched", "archived"),
        ("outreach_pending", "outreach_sent"),
        ("outreach_pending", "rejected"),
        ("outreach_pending", "archived"),
        ("outreach_sent", "replied_interested"),
        ("outreach_sent", "replied_not_interested"),
        ("outreach_sent", "interview_requested"),
        ("outreach_sent", "archived"),
        ("replied_interested", "interview_requested"),
        ("replied_interested", "interview_scheduled"),
        ("replied_interested", "advanced"),
        ("replied_interested", "final_round"),
        ("replied_interested", "offer_sent"),
        ("replied_interested", "rejected"),
        ("interview_requested", "interview_scheduled"),
        ("interview_requested", "rejected"),
        ("interview_requested", "archived"),
        ("interview_scheduled", "interview_completed"),
        ("interview_scheduled", "advanced"),
        ("interview_scheduled", "second_round_requested"),
        ("interview_scheduled", "final_round"),
        ("interview_scheduled", "rejected"),
        ("interview_scheduled", "archived"),
        ("interview_completed", "advanced"),
        ("interview_completed", "second_round_requested"),
        ("interview_completed", "final_round"),
        ("interview_completed", "offer_sent"),
        ("interview_completed", "rejected"),
        ("interview_completed", "archived"),
        ("advanced", "final_round"),
        ("advanced", "second_round_requested"),
        ("advanced", "second_round_scheduled"),
        ("advanced", "offer_stage"),
        ("advanced", "placed"),
        ("advanced", "offer_sent"),
        ("advanced", "hired"),
        ("advanced", "rejected"),
        ("advanced", "search_closed"),
        ("advanced", "archived"),
        ("second_round_requested", "second_round_scheduled"),
        ("second_round_requested", "rejected"),
        ("second_round_requested", "search_closed"),
        ("second_round_requested", "archived"),
        ("second_round_scheduled", "offer_stage"),
        ("second_round_scheduled", "final_round"),
        ("second_round_scheduled", "placed"),
        ("second_round_scheduled", "rejected"),
        ("second_round_scheduled", "search_closed"),
        ("second_round_scheduled", "archived"),
        ("final_round", "offer_sent"),
        ("final_round", "hired"),
        ("final_round", "rejected"),
        ("final_round", "archived"),
        ("offer_stage", "placed"),
        ("offer_stage", "search_closed"),
        ("offer_stage", "offer_sent"),
        ("offer_stage", "hired"),
        ("offer_stage", "rejected"),
        ("offer_stage", "archived"),
        ("offer_sent", "hired"),
        ("offer_sent", "rejected"),
        ("offer_sent", "placed"),
        ("offer_sent", "search_closed"),
        ("offer_sent", "archived"),
        ("warm", "reviewed"),
        ("disqualified", "archived"),
        ("rejected", "selected"),
        ("placed", "search_closed"),
        ("placed", "archived"),
        ("search_closed", "archived"),
        ("hired", "archived"),
        ("rejected", "archived"),
    }
)

# States from which NO further transition is ever allowed
_TERMINAL_STATES: frozenset[str] = frozenset({"hired", "archived"})

# States that are locked against swipe (accept/reject) actions specifically
_SWIPE_LOCKED_STATES: frozenset[str] = frozenset(
    {
        "selected",
        "enriching",
        "enriched",
        "outreach_pending",
        "outreach_sent",
        "replied_interested",
        "replied_not_interested",
        "interview_requested",
        "interview_scheduled",
        "interview_completed",
        "advanced",
        "second_round_requested",
        "second_round_scheduled",
        "final_round",
        "offer_stage",
        "offer_sent",
        "placed",
        "hired",
        "warm",
        "disqualified",
        "rejected",
        "search_closed",
        "archived",
    }
)


def assert_valid_transition(
    *,
    candidate_id: str,
    job_id: str,
    from_status: str | None,
    to_status: str,
) -> None:
    """
    Raise APIError(400) if the transition from_status -> to_status is not allowed.
    None from_status is treated as 'new' (first-time write).
    """
    effective_from = normalize_ats_status(from_status or "new")
    effective_to = normalize_ats_status(to_status)

    if effective_to not in VALID_STATES:
        logger.warning(
            "state_transition_blocked_invalid_target candidate_id=%s job_id=%s from=%s to=%s",
            candidate_id,
            job_id,
            effective_from,
            effective_to,
        )
        raise APIError(
            f"Invalid target state '{effective_to}' for candidate {candidate_id}",
            status_code=400,
        )

    if effective_from == effective_to:
        # Idempotent - same state, nothing to do, not an error
        return

    if effective_from in _TERMINAL_STATES:
        logger.warning(
            "state_transition_blocked_terminal candidate_id=%s job_id=%s from=%s to=%s",
            candidate_id,
            job_id,
            effective_from,
            effective_to,
        )
        raise APIError(
            f"Candidate {candidate_id} is in terminal state '{effective_from}' "
            f"and cannot transition to '{effective_to}'",
            status_code=409,
        )

    if (effective_from, effective_to) not in _ALLOWED_TRANSITIONS:
        logger.warning(
            "state_transition_blocked candidate_id=%s job_id=%s from=%s to=%s",
            candidate_id,
            job_id,
            effective_from,
            effective_to,
        )
        raise APIError(
            f"Invalid state transition '{effective_from}' -> '{effective_to}' "
            f"for candidate {candidate_id} on job {job_id}",
            status_code=409,
        )

    logger.info(
        "state_transition candidate_id=%s job_id=%s %s -> %s",
        candidate_id,
        job_id,
        effective_from,
        effective_to,
    )


def is_swipe_locked(status: str | None) -> bool:
    """Return True if the candidate's current status prevents a swipe action."""
    return normalize_ats_status(status or "new") in _SWIPE_LOCKED_STATES


def swipe_to_status(action: str) -> str:
    """Map swipe action to the resulting interview status."""
    normalized_action = (action or "").strip().lower()
    if normalized_action in {"accept", "like", "select", "save"}:
        return "selected"
    if normalized_action in {"reject", "pass"}:
        return "disqualified"
    if normalized_action in {"maybe", "not_now"}:
        return "warm"
    raise APIError(f"Unknown swipe action '{action}'", status_code=400)
