from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import (
    CandidateProfileRepository,
    InterviewEvaluationRepository,
    InterviewSessionRepository,
    JobRepository,
    NotificationWorkflowTokenRepository,
    RecruiterNoteRepository,
)
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.audit_service import record_audit_event
from app.services.interview_evaluation_service import list_interview_evaluations
from app.services.interview_session_service import (
    INTERVIEW_STAGE_SEQUENCE,
    create_interview_session,
    mark_interview_no_show,
    _session_payload,
    _session_stage_name,
    _workflow_token,
)
from app.services.lifecycle_service import record_job_lifecycle_event
from app.services.notification_intelligence_service import route_recruiter_notification
from app.services.operational_intelligence_service import get_interview_intelligence, get_interview_stage_progression
from app.utils.exceptions import APIError


_STAGE_TO_ATS: dict[str, str] = {
    "recruiter_screen": "interview_scheduled",
    "technical_round": "advanced",
    "hiring_manager_round": "final_round",
    "final_round": "final_round",
    "offer_stage": "offer_sent",
    "placed": "hired",
    "rejected": "rejected",
    "archived": "archived",
    "withdrawn": "archived",
    "no_show": "interview_no_show",
}

_TERMINAL_ACTIONS = {"rejected", "archived", "withdrawn", "no_show"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_stage(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _next_stage(current_stage: str) -> str:
    normalized = _normalize_stage(current_stage)
    if normalized not in INTERVIEW_STAGE_SEQUENCE:
        return INTERVIEW_STAGE_SEQUENCE[0]
    index = INTERVIEW_STAGE_SEQUENCE.index(normalized)
    return INTERVIEW_STAGE_SEQUENCE[min(index + 1, len(INTERVIEW_STAGE_SEQUENCE) - 1)]


def _stage_from_action(action: str, target_stage: str | None, current_stage: str) -> str:
    normalized_action = _normalize_stage(action)
    normalized_target = _normalize_stage(target_stage)
    if normalized_action == "advance":
        return normalized_target or _next_stage(current_stage)
    if normalized_action in {"move_to_next_round", "next_round"}:
        return _next_stage(current_stage)
    if normalized_action in {"mark_offer", "offer", "offer_stage"}:
        return "offer_stage"
    if normalized_action in {"mark_placed", "place", "placed"}:
        return "placed"
    if normalized_action in {"reject", "archive", "withdraw", "no_show"}:
        return normalized_action
    return normalized_target or normalized_action or current_stage or INTERVIEW_STAGE_SEQUENCE[0]


def _stage_to_ats(stage: str) -> str:
    return _STAGE_TO_ATS.get(_normalize_stage(stage), "advanced")


def _append_stage_history(row, *, action: str, from_stage: str, to_stage: str, notes: str = "") -> None:
    metadata = _metadata_map(getattr(row, "scheduling_metadata", {}))
    history = list(metadata.get("stageHistory") or metadata.get("stage_history") or [])
    history.append(
        {
            "action": _normalize_stage(action),
            "fromStage": _normalize_stage(from_stage),
            "toStage": _normalize_stage(to_stage),
            "notes": (notes or "").strip(),
            "createdAt": _utc_now_iso(),
        }
    )
    metadata["stageHistory"] = history
    row.scheduling_metadata = metadata


def get_interview_insights(*, db: Session, job_id: str, candidate_id: str) -> dict[str, Any]:
    session = InterviewSessionRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    current_stage = _session_stage_name(session) if session else "recruiter_screen"
    progression = get_interview_stage_progression(db=db, job_id=job_id, candidate_id=candidate_id)
    intelligence = get_interview_intelligence(db=db, job_id=job_id, candidate_id=candidate_id)
    evaluations = list_interview_evaluations(db=db, job_id=job_id, candidate_id=candidate_id)
    workflow_token = _workflow_token(session) if session else ""
    token_row = (
        NotificationWorkflowTokenRepository(db).get_by_token(workflow_token, source_app="adam")
        if workflow_token
        else NotificationWorkflowTokenRepository(db).get_active_by_candidate(job_id=job_id, candidate_id=candidate_id, source_app="adam", token_type="slot_booking")
    )
    workflow_payload = _metadata_map(token_row.payload if token_row else {})
    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "currentStage": current_stage,
        "workflowToken": workflow_token or getattr(token_row, "token", ""),
        "workflowPayload": workflow_payload,
        "progression": progression.get("progression", []),
        "evaluationCount": len(evaluations),
        "evaluations": evaluations,
        "intelligence": intelligence,
        "currentSession": _session_payload(row=session, booking_link=session.booking_url if session else "") if session else None,
        "stageHistory": list(_metadata_map(getattr(session, "scheduling_metadata", {})).get("stageHistory") or []),
    }


def advance_interview_stage(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    action: str,
    target_stage: str | None = None,
    notes: str = "",
    recommendation: str = "",
    interviewer_id: str | None = None,
    source_app: str = "adam",
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)

    session_repo = InterviewSessionRepository(db)
    current_session = session_repo.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    current_stage = _session_stage_name(current_session) if current_session else "recruiter_screen"
    normalized_action = _normalize_stage(action)
    normalized_target = _normalize_stage(target_stage)
    next_stage = _stage_from_action(normalized_action, normalized_target, current_stage)
    ats_target = _stage_to_ats(next_stage)
    workflow_token = _workflow_token(current_session) if current_session else ""

    if normalized_action in _TERMINAL_ACTIONS:
        if normalized_action == "no_show":
            if not current_session or not current_session.token:
                raise APIError("Interview session not found", status_code=404)
            result = mark_interview_no_show(db=db, token=current_session.token, reason=notes or "manual_no_show")
            return {
                "jobId": job_id,
                "candidateId": candidate_id,
                "action": normalized_action,
                "currentStage": current_stage,
                "nextStage": normalized_action,
                "atsStatus": _STAGE_TO_ATS.get(normalized_action, "interview_no_show"),
                "workflowToken": workflow_token or result.get("workflowToken", ""),
                "currentSession": _session_payload(row=current_session, booking_link=current_session.booking_url if current_session.booking_url else ""),
                "decision": result,
                "progression": get_interview_stage_progression(db=db, job_id=job_id, candidate_id=candidate_id),
                "intelligence": get_interview_intelligence(db=db, job_id=job_id, candidate_id=candidate_id),
                "evaluations": list_interview_evaluations(db=db, job_id=job_id, candidate_id=candidate_id),
            }

        result = {
            "token": current_session.token if current_session else "",
            "status": normalized_action,
            "jobId": job_id,
            "candidateId": candidate_id,
            "sourceType": str((_metadata_map(getattr(current_session, "scheduling_metadata", {})).get("sourceType") or source_app or "adam")),
            "workflowToken": workflow_token,
            "stageName": current_stage,
        }
        if current_session:
            current_session.status = normalized_action
            current_session.stage = normalized_action
            current_session.evaluation_status = "pending"
            _append_stage_history(current_session, action=normalized_action, from_stage=current_stage, to_stage=normalized_action, notes=notes)
        transition_candidate_ats_state(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            to_status=_STAGE_TO_ATS.get(normalized_action, "archived"),
            source="interview_stage",
            reason=notes or normalized_action,
            metadata={"action": normalized_action, "fromStage": current_stage, "toStage": normalized_action, "workflowToken": workflow_token},
        )
        db.commit()
        route_recruiter_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            notification_key=f"interview-stage:{job_id}:{candidate_id}:{normalized_action}",
            notification_type=f"interview_{normalized_action}",
            title="Interview decision updated",
            body=f"Candidate {candidate_id} moved to {normalized_action.replace('_', ' ')}.",
            metadata={"action": normalized_action, "fromStage": current_stage, "workflowToken": workflow_token},
        )
        record_audit_event(
            db=db,
            actor_id=None,
            action="interview_stage_terminal",
            entity_type="interview_session",
            entity_id=current_session.id if current_session else candidate_id,
            metadata={"jobId": job_id, "candidateId": candidate_id, "action": normalized_action, "fromStage": current_stage},
        )
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "action": normalized_action,
            "currentStage": current_stage,
            "nextStage": normalized_action,
            "atsStatus": _STAGE_TO_ATS.get(normalized_action, "archived"),
            "workflowToken": workflow_token,
            "currentSession": _session_payload(row=current_session, booking_link=current_session.booking_url if current_session else "") if current_session else None,
            "decision": result,
            "progression": get_interview_stage_progression(db=db, job_id=job_id, candidate_id=candidate_id),
            "intelligence": get_interview_intelligence(db=db, job_id=job_id, candidate_id=candidate_id),
            "evaluations": list_interview_evaluations(db=db, job_id=job_id, candidate_id=candidate_id),
        }

    existing_next_session = session_repo.get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    if existing_next_session and _session_stage_name(existing_next_session) == next_stage:
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "action": normalized_action or "advance",
            "currentStage": current_stage,
            "nextStage": next_stage,
            "atsStatus": _stage_to_ats(next_stage),
            "workflowToken": workflow_token or _workflow_token(existing_next_session),
            "duplicate": True,
            "currentSession": _session_payload(row=existing_next_session, booking_link=existing_next_session.booking_url if existing_next_session.booking_url else ""),
            "progression": get_interview_stage_progression(db=db, job_id=job_id, candidate_id=candidate_id),
            "intelligence": get_interview_intelligence(db=db, job_id=job_id, candidate_id=candidate_id),
            "evaluations": list_interview_evaluations(db=db, job_id=job_id, candidate_id=candidate_id),
        }

    if current_session:
        current_session.status = "booked" if (current_session.status or "").strip().lower() == "booked" else current_session.status
        current_session.stage = "completed"
        current_session.evaluation_status = "completed"
        _append_stage_history(current_session, action=normalized_action or "advance", from_stage=current_stage, to_stage=next_stage, notes=notes)

    next_session = create_interview_session(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        outreach_event_id=getattr(current_session, "outreach_event_id", None),
        source_app=source_app,
        resume_text=str(getattr(profile, "resume_text", "") or ""),
        workflow_token=workflow_token or None,
        stage_name=next_stage,
    )

    if notes.strip():
        RecruiterNoteRepository(db).create(
            job_id=job_id,
            candidate_id=candidate_id,
            recruiter_id=interviewer_id,
            note_type="interview_decision",
            body=notes,
            metadata={"action": normalized_action, "fromStage": current_stage, "toStage": next_stage, "recommendation": recommendation},
        )

    transition_candidate_ats_state(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        to_status=ats_target,
        source="interview_stage",
        reason=notes or normalized_action or "stage_advance",
        metadata={
            "action": normalized_action or "advance",
            "fromStage": current_stage,
            "toStage": next_stage,
            "workflowToken": workflow_token or next_session.get("workflowToken"),
            "stageName": next_stage,
            "recommendation": recommendation,
        },
    )

    route_recruiter_notification(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        notification_key=f"interview-stage:{job_id}:{candidate_id}:{next_stage}",
        notification_type="interview_stage_advanced",
        title="Interview stage advanced",
        body=f"Candidate {candidate_id} moved to {next_stage.replace('_', ' ')}.",
        metadata={"action": normalized_action, "fromStage": current_stage, "toStage": next_stage, "workflowToken": workflow_token or next_session.get("workflowToken")},
    )

    record_job_lifecycle_event(
        db=db,
        job_id=job_id,
        event_type="INTERVIEW_STAGE_ADVANCED",
        payload={
            "jobId": job_id,
            "candidateId": candidate_id,
            "action": normalized_action or "advance",
            "fromStage": current_stage,
            "toStage": next_stage,
            "workflowToken": workflow_token or next_session.get("workflowToken"),
            "sessionToken": next_session.get("token"),
            "evaluationNotes": notes,
        },
        source="interview",
    )
    record_audit_event(
        db=db,
        actor_id=None,
        action="interview_stage_advanced",
        entity_type="interview_session",
        entity_id=next_session.get("id") or candidate_id,
        metadata={"jobId": job_id, "candidateId": candidate_id, "fromStage": current_stage, "toStage": next_stage, "workflowToken": workflow_token or next_session.get("workflowToken")},
    )
    db.commit()

    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "action": normalized_action or "advance",
        "currentStage": current_stage,
        "nextStage": next_stage,
        "atsStatus": ats_target,
        "workflowToken": workflow_token or next_session.get("workflowToken"),
        "currentSession": _session_payload(row=current_session, booking_link=current_session.booking_url if current_session and current_session.booking_url else "") if current_session else None,
        "nextSession": next_session,
        "progression": get_interview_stage_progression(db=db, job_id=job_id, candidate_id=candidate_id),
        "intelligence": get_interview_intelligence(db=db, job_id=job_id, candidate_id=candidate_id),
        "evaluations": list_interview_evaluations(db=db, job_id=job_id, candidate_id=candidate_id),
    }
