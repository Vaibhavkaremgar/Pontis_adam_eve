from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy.orm import Session

from app.core.config import TALENT_POOL_SHORTLIST_THRESHOLD
from app.db.repositories import (
    CandidateApplicationRepository,
    CandidateProfileRepository,
    InterviewRepository,
    JobRepository,
)
from app.services.email_service import send_email
from app.services.job_text_service import build_job_text
from app.services.resume_ingestion_service import extract_pdf_text
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.job_queue_service import enqueue_job
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

_WORD_SPLIT = re.compile(r"[^a-z0-9+#.]+", re.IGNORECASE)


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_list(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
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


def _application_fingerprint(*, job_id: str, email: str, resume_text: str, resume_file_name: str) -> str:
    material = "|".join([job_id.strip(), email.strip().lower(), resume_text.strip(), resume_file_name.strip().lower()])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _resume_fingerprint(*, resume_text: str, resume_file_name: str) -> str:
    material = "|".join([resume_text.strip(), resume_file_name.strip().lower()])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _extract_resume_text(*, resume_text: str, resume_file_path: str, resume_file_name: str) -> str:
    if resume_text.strip():
        return resume_text.strip()

    path_text = _normalize_text(resume_file_path)
    if path_text:
        path = Path(path_text)
        if path.exists() and path.is_file():
            try:
                extracted, _ = extract_pdf_text(path)
                return _normalize_text(extracted)
            except Exception as exc:
                logger.warning("application_resume_extract_failed file=%s error=%s", resume_file_name or path.name, str(exc), exc_info=exc)
                raise APIError("Resume extraction failed", status_code=500)
    return ""


def _tokenize(text: str) -> list[str]:
    return [token for token in _WORD_SPLIT.split(text.lower()) if token]


def _evaluate_resume(*, job, resume_text: str, applicant_name: str, applicant_role: str = "", applicant_company: str = "", applicant_location: str = "") -> dict[str, Any]:
    job_text = build_job_text(job)
    job_tokens = set(_tokenize(job_text))
    resume_tokens = set(_tokenize(resume_text))
    overlap = sorted(token for token in resume_tokens.intersection(job_tokens) if len(token) > 1)

    structured_job = job.structured_data if isinstance(getattr(job, "structured_data", {}), dict) else {}
    required_skills = _normalize_list(structured_job.get("skills") or job.skills_required or [])
    responsibilities = _normalize_list(structured_job.get("responsibilities") or job.responsibilities or [])
    job_experience = _normalize_text(structured_job.get("experience") or job.experience_required or job.experience_level or "")
    job_education = _normalize_text(structured_job.get("education") or structured_job.get("education_level") or "")

    matched_skills = [skill for skill in required_skills if skill.lower() in resume_text.lower()]
    missing_skills = [skill for skill in required_skills if skill.lower() not in resume_text.lower()]
    experience_match = "strong" if job_experience and any(token in resume_text.lower() for token in _tokenize(job_experience)) else "partial" if job_experience else "unknown"
    education_match = "strong" if job_education and job_education.lower() in resume_text.lower() else "partial" if job_education else "unknown"

    role_hint = applicant_role or applicant_company or applicant_name
    strength_lines: list[str] = []
    weakness_lines: list[str] = []
    if matched_skills:
        strength_lines.append(f"Matched skills: {', '.join(matched_skills[:8])}")
    if len(overlap) >= 4:
        strength_lines.append(f"Strong keyword overlap with the job description and resume: {', '.join(overlap[:8])}")
    if experience_match == "strong":
        strength_lines.append("Experience alignment looks strong against the job description.")
    if education_match == "strong":
        strength_lines.append("Education alignment appears strong.")
    if missing_skills:
        weakness_lines.append(f"Missing skills: {', '.join(missing_skills[:8])}")
    if experience_match == "partial":
        weakness_lines.append("Experience alignment is only partial based on available resume text.")
    if education_match == "partial" and job_education:
        weakness_lines.append("Education requirement is not explicitly confirmed in the resume.")

    keyword_signal = min(1.0, len(overlap) / max(1, len(required_skills) + len(responsibilities) // 2))
    skill_signal = min(1.0, len(matched_skills) / max(1, len(required_skills)))
    experience_signal = 1.0 if experience_match == "strong" else 0.55 if experience_match == "partial" else 0.4
    education_signal = 1.0 if education_match == "strong" else 0.55 if education_match == "partial" else 0.5
    resume_score = round(min(100.0, max(0.0, ((skill_signal * 42.0) + (keyword_signal * 28.0) + (experience_signal * 20.0) + (education_signal * 10.0)))), 2)
    recommendation = "shortlist" if resume_score >= float(TALENT_POOL_SHORTLIST_THRESHOLD) else "reject"

    summary = (
        f"{applicant_name or 'Candidate'} shows {experience_match} experience alignment and "
        f"{len(matched_skills)} matched skill(s) against the role."
    )
    if role_hint:
        summary = f"{summary} Profile context: {role_hint}."

    return {
        "resume_score": resume_score,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "experience_match": experience_match,
        "education_match": education_match,
        "strengths": strength_lines,
        "weaknesses": weakness_lines,
        "summary": summary,
        "recommendation": recommendation,
        "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
        "evaluation_metadata": {
            "model": "rule_based_resume_evaluator",
            "evaluation_version": "v1",
            "prompt_version": "v1",
        },
        "signals": {
            "keyword_signal": round(keyword_signal, 4),
            "skill_signal": round(skill_signal, 4),
            "experience_signal": round(experience_signal, 4),
            "education_signal": round(education_signal, 4),
        },
    }


def submit_candidate_application(
    *,
    db: Session,
    job_id: str,
    name: str,
    email: str,
    phone: str = "",
    resume_text: str = "",
    resume_file_name: str = "",
    resume_file_path: str = "",
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    normalized_name = _normalize_text(name)
    normalized_email = _normalize_text(email).lower()
    normalized_phone = _normalize_text(phone)
    normalized_resume_file_name = _normalize_text(resume_file_name)
    normalized_resume_text = _extract_resume_text(
        resume_text=_normalize_text(resume_text),
        resume_file_path=resume_file_path,
        resume_file_name=normalized_resume_file_name,
    )
    if not normalized_email:
        raise APIError("email is required", status_code=400)
    if not normalized_resume_text:
        raise APIError("resume text is required", status_code=400)

    resume_fp = _resume_fingerprint(resume_text=normalized_resume_text, resume_file_name=normalized_resume_file_name)
    application_fp = _application_fingerprint(
        job_id=job_id,
        email=normalized_email,
        resume_text=normalized_resume_text,
        resume_file_name=normalized_resume_file_name,
    )
    candidate_id = str(uuid5(NAMESPACE_URL, f"pontis-eve-application:{application_fp}"))
    application_repo = CandidateApplicationRepository(db)
    row = application_repo.upsert(
        job_id=job_id,
        company_id=str(job.company_id),
        candidate_id=candidate_id,
        name=normalized_name,
        email=normalized_email,
        phone=normalized_phone,
        resume_file_name=normalized_resume_file_name,
        resume_file_path=_normalize_text(resume_file_path),
        resume_text=normalized_resume_text,
        resume_fingerprint=resume_fp,
        application_fingerprint=application_fp,
        application_status="application_received",
        resume_processing_status="pending",
    )
    db.commit()

    queue_result: dict[str, Any]
    try:
        queue_result = enqueue_job(
            "candidate_application_processing",
            {
                "application_id": row.id,
                "job_id": job_id,
                "candidate_id": candidate_id,
            },
            idempotency_key=f"candidate_application_processing:{row.id}",
            job_id=row.id,
            max_attempts=5,
        )
    except Exception as exc:
        queue_result = {"queued": False, "queue_type": "candidate_application_processing", "error": str(exc)}
        logger.warning("candidate_application_queue_failed application_id=%s error=%s", row.id, str(exc), exc_info=exc)
    return {
        "applicationId": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "status": row.application_status,
        "resumeProcessingStatus": row.resume_processing_status,
        "queue": queue_result,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


def process_candidate_application(*, db: Session, application_id: str) -> dict[str, Any]:
    repo = CandidateApplicationRepository(db)
    row = repo.get(application_id)
    if not row:
        return {"status": "skipped", "reason": "application_missing"}

    if (row.resume_processing_status or "").lower() == "evaluated" and row.evaluation_json:
        return {"status": "skipped", "reason": "already_evaluated", "applicationId": row.id}

    job = JobRepository(db).get(row.job_id)
    if not job:
        repo.update_status(
            application_id=row.id,
            application_status="application_received",
            resume_processing_status="failed",
            last_error="job_missing",
        )
        db.commit()
        return {"status": "failed", "reason": "job_missing", "applicationId": row.id}

    repo.update_status(
        application_id=row.id,
        application_status="resume_processing",
        resume_processing_status="processing",
        last_error="",
    )
    db.commit()

    try:
        row = repo.get(row.id) or row
        evaluation = _evaluate_resume(
            job=job,
            resume_text=row.resume_text or "",
            applicant_name=row.name,
            applicant_role="",
            applicant_company="",
            applicant_location="",
        )
        shortlisted = float(evaluation["resume_score"]) >= float(TALENT_POOL_SHORTLIST_THRESHOLD)
        evaluation_json = dict(evaluation)
        evaluation_json.setdefault("resume_score", float(evaluation["resume_score"]))
        repo.update_status(
            application_id=row.id,
            application_status="shortlisted" if shortlisted else "resume_evaluated",
            resume_processing_status="evaluated",
            resume_score=float(evaluation["resume_score"]),
            evaluation_json=evaluation_json,
            evaluation_timestamp=datetime.now(timezone.utc),
            shortlist_email_status="pending" if shortlisted else "not_applicable",
            shortlisted_at=datetime.now(timezone.utc) if shortlisted else None,
            rejected_at=None if shortlisted else datetime.now(timezone.utc),
            last_error="",
        )
        db.commit()

        if shortlisted:
            repo.update_status(
                application_id=row.id,
                shortlist_email_status="sending",
                last_error="",
            )
            db.commit()
            email_sent_at = _send_shortlist_emails(job=job, application=row, evaluation=evaluation)
            if email_sent_at:
                repo.update_status(
                    application_id=row.id,
                    shortlist_email_sent_at=datetime.fromisoformat(email_sent_at),
                    shortlist_email_status="sent",
                    last_error="",
                )
                db.commit()
        else:
            _send_rejection_email(job=job, application=row, evaluation=evaluation)
        return {
            "status": "completed",
            "applicationId": row.id,
            "shortlisted": shortlisted,
            "resumeScore": float(evaluation["resume_score"]),
        }
    except Exception as exc:
        repo.update_status(
            application_id=row.id,
            application_status="application_received",
            resume_processing_status="failed",
            last_error=str(exc),
        )
        db.commit()
        logger.warning("candidate_application_processing_failed application_id=%s error=%s", row.id, str(exc), exc_info=exc)
        raise


def _send_shortlist_emails(*, job: Any, application: Any, evaluation: dict[str, Any]) -> None:
    existing_sent_at = str(getattr(application, "shortlist_email_sent_at", "") or "").strip()
    existing_status = str(getattr(application, "shortlist_email_status", "") or "").strip().lower()
    if existing_status == "sent" or existing_sent_at:
        return existing_sent_at or datetime.now(timezone.utc).isoformat()
    subject = f"Your application for {getattr(job, 'title', '')} is moving forward"
    body = (
        f"Hi {application.name or 'there'},\n\n"
        f"We reviewed your resume for {getattr(job, 'title', 'the role')} and would like to move forward.\n"
        f"Resume score: {evaluation['resume_score']}\n\n"
        f"Matched skills: {', '.join(evaluation.get('matched_skills') or []) or 'n/a'}\n"
        f"Summary: {evaluation.get('summary') or ''}\n"
    )
    send_email(to_email=application.email, subject=subject, body=body, text=body)
    slot_subject = f"Next steps for {getattr(job, 'title', '')}"
    slot_body = (
        f"Hi {application.name or 'there'},\n\n"
        "Thank you for your application. We would like to schedule the next step.\n"
        "Please reply with your availability.\n"
    )
    send_email(to_email=application.email, subject=slot_subject, body=slot_body, text=slot_body)
    return datetime.now(timezone.utc).isoformat()


def _send_rejection_email(*, job: Any, application: Any, evaluation: dict[str, Any]) -> None:
    subject = f"Update on your application for {getattr(job, 'title', '')}"
    body = (
        f"Hi {application.name or 'there'},\n\n"
        f"Thank you for applying for {getattr(job, 'title', 'the role')}. "
        "After reviewing your resume, we will not be moving forward at this time.\n\n"
        f"Summary: {evaluation.get('summary') or ''}\n"
    )
    send_email(to_email=application.email, subject=subject, body=body, text=body)


def mark_candidate_talent_pool_ready(*, db: Session, job_id: str, candidate_id: str) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)

    interview = InterviewRepository(db).get_by_job_and_candidate(job_id=job_id, candidate_id=candidate_id)
    if not interview:
        return {"status": "skipped", "reason": "interview_missing"}
    if (interview.status or "").strip().lower() not in {"completed", "advanced", "interview_completed"}:
        return {"status": "skipped", "reason": "interview_not_completed"}

    evaluation_json = dict(profile.ats_metadata or {})
    evaluation_json["talentPoolReadyAt"] = datetime.now(timezone.utc).isoformat()
    profile.ats_status = "talent_pool_ready"
    profile.candidate_status = "talent_pool_ready"
    profile.ats_status_source = "interview_completion"
    profile.ats_status_reason = "resume_evaluated_and_interview_completed"
    profile.ats_status_updated_at = datetime.now(timezone.utc)
    profile.talent_pool_ready_at = profile.talent_pool_ready_at or datetime.now(timezone.utc)
    profile.ats_metadata = evaluation_json
    db.flush()
    transition_candidate_ats_state(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        to_status="talent_pool_ready",
        source="interview_completion",
        reason="interview_completed",
        metadata={"status": "talent_pool_ready"},
    )
    db.commit()
    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "status": "talent_pool_ready",
    }
