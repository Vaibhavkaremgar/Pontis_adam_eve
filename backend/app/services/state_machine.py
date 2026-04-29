from __future__ import annotations

"""
Candidate state machine - single source of truth for all status transitions.

Allowed states:   new -> shortlisted -> contacted -> interview_scheduled -> exported
                  new -> rejected

Any other transition raises APIError(400).
"""

import logging

from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

# All valid states
VALID_STATES: frozenset[str] = frozenset(
    {"new", "shortlisted", "rejected", "contacted", "interview_scheduled", "exported"}
)

# Explicit allow-list - every other pair is forbidden
_ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("new", "shortlisted"),
        ("new", "rejected"),
        ("shortlisted", "contacted"),
        ("contacted", "interview_scheduled"),
        ("interview_scheduled", "exported"),
    }
)

# States from which NO further transition is ever allowed
_TERMINAL_STATES: frozenset[str] = frozenset({"rejected", "exported"})

# States that are locked against swipe (accept/reject) actions specifically
_SWIPE_LOCKED_STATES: frozenset[str] = frozenset(
    {"shortlisted", "contacted", "interview_scheduled", "exported"}
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
    effective_from = (from_status or "new").strip().lower()
    effective_to = to_status.strip().lower()

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
    return (status or "new").strip().lower() in _SWIPE_LOCKED_STATES


def swipe_to_status(action: str) -> str:
    """Map swipe action to the resulting interview status."""
    if action == "accept":
        return "shortlisted"
    if action == "reject":
        return "rejected"
    raise APIError(f"Unknown swipe action '{action}'", status_code=400)
