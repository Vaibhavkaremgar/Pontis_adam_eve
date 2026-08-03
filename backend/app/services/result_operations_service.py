from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.repositories import (
    CandidateLifecycleEventRepository,
    CandidateProfileRepository,
    CompanyRepository,
    InterviewRepository,
    OutreachEventRepository,
    JobRepository,
)
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.candidate_service import ensure_candidate_email
from app.services.email_service import send_email
from app.utils.exceptions import APIError
from app.utils.observability import emit_trace

logger = logging.getLogger(__name__)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _normalize_mode(value: Any) -> str:
    mode = _normalize_text(value).lower().replace("_", "-")
    return mode if mode in {"online", "in-person"} else "online"


def _normalize_round_type(value: Any) -> str:
    round_type = _normalize_text(value).lower().replace(" ", "_").replace("-", "_")
    return round_type if round_type in {"second_round", "final_round"} else "second_round"


def _build_transition_key(*, workflow_token: str, decision: str, payload: dict[str, Any]) -> str:
    digest_source = repr({
        "workflowToken": workflow_token,
        "decision": decision,
        "payload": payload,
    }).encode("utf-8")
    return hashlib.sha256(digest_source).hexdigest()


def _build_subject(*, role: str, company: str, round_type: str) -> str:
    round_label = "Second Round" if round_type == "second_round" else "Final Round"
    role_text = role or "the role"
    company_text = company or "the company"
    return f"{round_label} interview for {role_text} at {company_text}"


def _build_body(
    *,
    candidate_name: str,
    role: str,
    company: str,
    interviewer_name: str,
    interviewer_email: str,
    mode: str,
    meet_url: str,
    office_address: str,
    slots: list[str],
    notes: str,
    recruiter_email: str,
) -> tuple[str, str]:
    location_line = meet_url if mode == "online" else office_address
    slots_block = "\n".join(f"- {slot}" for slot in slots) if slots else "- To be confirmed"
    instructions = notes or "Please reply with your preferred slot or let us know if you need to reschedule."
    body = (
        f"Hi {candidate_name or 'there'},\n\n"
        f"We'd like to move you to the next round for {role or 'the role'} at {company or 'the company'}.\n\n"
        f"Interviewer: {interviewer_name or 'TBD'} <{interviewer_email or recruiter_email}>\n"
        f"Mode: {'Online' if mode == 'online' else 'In-Person'}\n"
        f"{'Meet link' if mode == 'online' else 'Office address'}: {location_line or 'To be shared'}\n\n"
        f"Available slots:\n{slots_block}\n\n"
        f"Notes / instructions:\n{instructions}\n\n"
        "Best,\nAdam"
    )
    html = (
        f"<p>Hi {candidate_name or 'there'},</p>"
        f"<p>We'd like to move you to the next round for <strong>{role or 'the role'}</strong> at <strong>{company or 'the company'}</strong>.</p>"
        f"<p><strong>Interviewer:</strong> {interviewer_name or 'TBD'} &lt;{interviewer_email or recruiter_email}&gt;<br>"
        f"<strong>Mode:</strong> {'Online' if mode == 'online' else 'In-Person'}<br>"
        f"<strong>{'Meet link' if mode == 'online' else 'Office address'}:</strong> {location_line or 'To be shared'}</p>"
        f"<p><strong>Available slots:</strong><br>{'<br>'.join(slots) if slots else 'To be confirmed'}</p>"
        f"<p><strong>Notes / instructions:</strong><br>{instructions}</p>"
        "<p>Best,<br>Adam</p>"
    )
    return body, html


def _normalize_email_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        email = _normalize_text(item).lower()
        if not email or "@" not in email:
            continue
        if email in seen:
            continue
        seen.add(email)
        normalized.append(email)
    return normalized


def _result_agency_matches(*, db: Session, job_id: str, candidate_id: str, agency_id: str) -> bool:
    normalized_agency_id = _normalize_text(agency_id)
    if not normalized_agency_id:
        return False
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if profile and _normalize_text(getattr(profile, "agency_id", "")) == normalized_agency_id:
        return True
    interview = InterviewRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    if interview and _normalize_text(getattr(interview, "agency_id", "")) == normalized_agency_id:
        return True
    job = JobRepository(db).get(job_id)
    return bool(job and _normalize_text(getattr(job, "company_id", "")) == normalized_agency_id)


def record_result_decision(
    *,
    db: Session,
    workflow_token: str,
    recruiter_id: str,
    agency_id: str,
    decision: str,
) -> dict[str, Any]:
    from app.services.results_service import resolve_result_context

    context = resolve_result_context(db=db, workflow_token=workflow_token)
    job_id = context["jobId"]
    candidate_id = context["candidateId"]
    normalized_decision = _normalize_text(decision).lower()
    if normalized_decision not in {"pass", "hold", "reject"}:
        raise APIError("Unsupported decision", status_code=400)

    job = JobRepository(db).get(job_id)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not job or not profile:
        raise APIError("Result not found", status_code=404)
    if not _result_agency_matches(db=db, job_id=job_id, candidate_id=candidate_id, agency_id=agency_id):
        raise APIError("Forbidden", status_code=403)

    current_status = str(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", "") or "results_ready").strip().lower()
    now = datetime.now(timezone.utc)
    profile.ats_metadata = {
        **dict(getattr(profile, "ats_metadata", {}) or {}),
        "recruiterDecision": normalized_decision,
        "recruiterDecisionAt": now.isoformat(),
        "workflowToken": workflow_token,
        "recruiterId": recruiter_id,
    }
    db.flush()

    event_repo = CandidateLifecycleEventRepository(db)
    try:
        event_repo.create(
            job_id=job_id,
            company_id=str(job.company_id),
            candidate_id=candidate_id,
            from_status=current_status,
            to_status=current_status,
            source="adam",
            actor_id=recruiter_id or None,
            transition_key=_build_transition_key(
                workflow_token=workflow_token,
                decision=normalized_decision,
                payload={"jobId": job_id, "candidateId": candidate_id},
            ),
            event_metadata={
                "workflowToken": workflow_token,
                "decision": normalized_decision,
                "recruiterId": recruiter_id,
                "candidateId": candidate_id,
                "jobId": job_id,
            },
        )
    except IntegrityError:
        db.rollback()
        return {
            "workflowToken": workflow_token,
            "jobId": job_id,
            "candidateId": candidate_id,
            "decision": normalized_decision,
            "status": current_status,
            "recordedAt": now.isoformat(),
            "duplicate": True,
        }

    if normalized_decision == "reject":
        transition_candidate_ats_state(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            to_status="rejected",
            source="adam",
            actor_id=recruiter_id,
            reason="results_rejected",
            metadata={
                "workflowToken": workflow_token,
                "decision": normalized_decision,
                "candidateId": candidate_id,
                "jobId": job_id,
            },
        )

    db.commit()
    emit_trace(
        logger,
        "result_decision_recorded",
        workflow_token=workflow_token,
        recruiter_id=recruiter_id,
        candidate_id=candidate_id,
        decision=normalized_decision,
    )

    return {
        "workflowToken": workflow_token,
        "jobId": job_id,
        "candidateId": candidate_id,
        "decision": normalized_decision,
        "status": "rejected" if normalized_decision == "reject" else current_status,
        "recordedAt": now.isoformat(),
    }


def advance_result_candidate(
    *,
    db: Session,
    workflow_token: str,
    recruiter_id: str,
    agency_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from app.services.results_service import resolve_result_context

    context = resolve_result_context(db=db, workflow_token=workflow_token)
    job_id = context["jobId"]
    candidate_id = context["candidateId"]
    job = JobRepository(db).get(job_id)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not job or not profile:
        raise APIError("Result not found", status_code=404)
    if not _result_agency_matches(db=db, job_id=job_id, candidate_id=candidate_id, agency_id=agency_id):
        raise APIError("Forbidden", status_code=403)
    ats_metadata = getattr(profile, "ats_metadata", {}) if isinstance(getattr(profile, "ats_metadata", {}), dict) else {}
    current_status = _normalize_text(getattr(profile, "ats_status", "") or getattr(profile, "candidate_status", "")).lower()
    if current_status in {"second_round_requested", "second_round_scheduled"} and _normalize_text(ats_metadata.get("providerMessageId")):
        emit_trace(
            logger,
            "second_round_invite_duplicate",
            workflow_token=workflow_token,
            recruiter_id=recruiter_id,
            candidate_id=candidate_id,
            round_type=_normalize_round_type(payload.get("roundType")),
            mode=_normalize_mode(payload.get("mode")),
        )
        return {
            "workflowToken": workflow_token,
            "jobId": job_id,
            "candidateId": candidate_id,
            "candidateEmail": ensure_candidate_email(profile),
            "subject": _build_subject(role=_normalize_text(getattr(job, "title", "")), company=_normalize_text(getattr(CompanyRepository(db).get_by_id(job.company_id), "name", "")), round_type=_normalize_round_type(payload.get("roundType"))),
            "messageId": _normalize_text(ats_metadata.get("providerMessageId")),
            "status": current_status,
            "decisionState": "advance",
            "roundType": _normalize_round_type(payload.get("roundType")),
            "mode": _normalize_mode(payload.get("mode")),
            "sentAt": _normalize_text(ats_metadata.get("recruiterDecisionAt")) or datetime.now(timezone.utc).isoformat(),
            "recipient": ensure_candidate_email(profile),
            "cc": [],
            "duplicate": True,
        }

    round_type = _normalize_round_type(payload.get("roundType"))
    mode = _normalize_mode(payload.get("mode"))
    interviewer = payload.get("interviewer") if isinstance(payload.get("interviewer"), dict) else {}
    interviewer_name = _normalize_text(interviewer.get("name"))
    interviewer_email = _normalize_text(interviewer.get("email")).lower()
    recruiter_email = _normalize_text(payload.get("recruiterEmail")).lower()
    meet_url = _normalize_text(payload.get("meetUrl"))
    office_address = _normalize_text(payload.get("officeAddress"))
    notes = _normalize_text(payload.get("notes"))
    timezone_value = _normalize_text(payload.get("timezone"))
    duration_value = _normalize_text(payload.get("duration"))
    slots = _normalize_list(payload.get("slots"))
    panel_interviewers = _normalize_email_list(_normalize_list(payload.get("panelInterviewers")))

    if not recruiter_email:
        raise APIError("recruiterEmail is required", status_code=400)
    if not interviewer_name:
        raise APIError("interviewer.name is required", status_code=400)
    if not interviewer_email:
        raise APIError("interviewer.email is required", status_code=400)
    if mode == "online" and not meet_url:
        raise APIError("meetUrl is required for online interviews", status_code=400)
    if mode == "in-person" and not office_address:
        raise APIError("officeAddress is required for in-person interviews", status_code=400)
    if not slots:
        raise APIError("slots is required", status_code=400)

    candidate_email = ensure_candidate_email(profile)
    if not candidate_email:
        raise APIError("Candidate email is required", status_code=400)

    company = CompanyRepository(db).get_by_id(job.company_id)
    role = _normalize_text(getattr(job, "title", ""))
    company_name = _normalize_text(getattr(company, "name", ""))
    candidate_name = _normalize_text(getattr(profile, "name", ""))
    subject = _build_subject(role=role, company=company_name, round_type=round_type)
    body, html = _build_body(
        candidate_name=candidate_name,
        role=role,
        company=company_name,
        interviewer_name=interviewer_name,
        interviewer_email=interviewer_email,
        mode=mode,
        meet_url=meet_url,
        office_address=office_address,
        slots=slots,
        notes=notes,
        recruiter_email=recruiter_email,
    )

    email_result = send_email(
        to_email=candidate_email,
        subject=subject,
        body=body,
        text=body,
        html=html,
        cc=_normalize_email_list([recruiter_email, interviewer_email, *panel_interviewers]),
        reply_to=recruiter_email,
        tags={
            "product": "adam",
            "flow": "second_round_invite",
            "round": round_type,
            "mode": mode,
        },
        headers={
            "X-Adam-Workflow-Token": workflow_token,
            "X-Adam-Candidate-Id": candidate_id,
            "X-Adam-Recruiter-Id": recruiter_id,
        },
    )

    provider_message_id = _normalize_text(
        email_result.get("id")
        or email_result.get("message_id")
        or email_result.get("messageId")
        or email_result.get("email_id")
    )

    outreach_repo = OutreachEventRepository(db)
    outreach_row = outreach_repo.upsert(
        job_id=job_id,
        candidate_id=candidate_id,
        provider="resend",
        to_email=candidate_email,
        subject=subject,
        body=body,
        status="sent",
        sent_at=datetime.now(timezone.utc),
        provider_message_id=provider_message_id or None,
    )
    outreach_row.reply_state = "second_round_requested"
    outreach_row.reply_intent = "second_round_requested"
    outreach_row.updated_at = datetime.now(timezone.utc)

    transition_candidate_ats_state(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        to_status="second_round_requested",
        source="adam",
        actor_id=recruiter_id,
        reason="second_round_invite_sent",
        metadata={
            "workflowToken": workflow_token,
            "roundType": round_type,
            "mode": mode,
            "meetUrl": meet_url,
            "officeAddress": office_address,
            "interviewer": {
                "name": interviewer_name,
                "email": interviewer_email,
            },
            "recruiterEmail": recruiter_email,
            "slots": slots,
            "notes": notes,
            "timezone": timezone_value,
            "duration": duration_value,
            "panelInterviewers": panel_interviewers,
            "providerMessageId": provider_message_id,
        },
    )
    db.commit()

    emit_trace(
        logger,
        "second_round_invite_sent",
        workflow_token=workflow_token,
        recruiter_id=recruiter_id,
        candidate_id=candidate_id,
        round_type=round_type,
        mode=mode,
    )

    return {
        "workflowToken": workflow_token,
        "jobId": job_id,
        "candidateId": candidate_id,
        "candidateEmail": candidate_email,
        "subject": subject,
        "messageId": provider_message_id,
        "status": "second_round_requested",
        "decisionState": "advance",
        "roundType": round_type,
        "mode": mode,
        "sentAt": datetime.now(timezone.utc).isoformat(),
        "recipient": candidate_email,
        "cc": _normalize_email_list([recruiter_email, interviewer_email, *panel_interviewers]),
    }
