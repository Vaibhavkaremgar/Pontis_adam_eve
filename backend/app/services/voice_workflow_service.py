from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import LINKEDIN_JOB_POST_MODE, PUBLIC_APP_URL
from app.db.repositories import CompanyRepository, JobRepository, NotificationWorkflowTokenRepository
from app.linkedin.job_posting import JobPostingSpec
from app.linkedin.job_posting.job_posting_types import JobPostingExecutionMode
from app.services.notification_service import generate_workflow_token, upsert_notification_workflow_token
from app.services.job_queue_service import enqueue_job
from app.services.voice_service import refine_job_with_voice

logger = logging.getLogger(__name__)

_EVE_WORKFLOW_NAME = "eve_workflow"
_EVE_WORKFLOW_TOKEN_TYPE = "eve_workflow"
_EVE_WORKFLOW_CANDIDATE_ID = "__job_workflow__"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def build_eve_workflow_link(token: str) -> str:
    base_url = (PUBLIC_APP_URL or "").rstrip("/")
    if not base_url:
        return f"/eve/{_normalize_text(token)}"
    return f"{base_url}/eve/{_normalize_text(token)}"


def _infer_workplace_type(job: Any) -> str:
    structured = getattr(job, "structured_data", None)
    remote_policy = ""
    if isinstance(structured, dict):
        remote_policy = _normalize_text(structured.get("remotePolicy") or structured.get("remote_policy"))
    remote_policy = remote_policy or _normalize_text(getattr(job, "remote_policy", ""))
    lowered = remote_policy.lower()
    if "remote" in lowered:
        return "Remote"
    if "hybrid" in lowered:
        return "Hybrid"
    if "onsite" in lowered or "on-site" in lowered:
        return "On-site"
    return "Hybrid"


def build_linkedin_job_posting_spec(*, db: Session, job_id: str) -> JobPostingSpec:
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    company = CompanyRepository(db).get_by_id(str(getattr(job, "company_id", "") or "").strip())
    company_name = _normalize_text(getattr(company, "name", "") if company else "")
    company_industry = _normalize_text(getattr(company, "industry", "") if company else "")
    structured = getattr(job, "structured_data", None) if isinstance(getattr(job, "structured_data", None), dict) else {}
    application_url = _normalize_text(structured.get("linkedinPosting", {}).get("applicationUrl") if isinstance(structured.get("linkedinPosting"), dict) else "")
    application_email = _normalize_text(structured.get("linkedinPosting", {}).get("applicationEmail") if isinstance(structured.get("linkedinPosting"), dict) else "")

    return JobPostingSpec(
        title=_normalize_text(getattr(job, "title", "")),
        company=company_name or _normalize_text(getattr(job, "company_name", "")),
        workplace_type=_infer_workplace_type(job),
        location=_normalize_text(getattr(job, "location", "")),
        job_type="Full-time",
        experience_level=_normalize_text(getattr(job, "experience_level", "") or getattr(job, "experience_required", "")),
        industry=company_industry,
        job_function="Engineering",
        description=_normalize_text(getattr(job, "description", "")),
        skills=[_normalize_text(skill) for skill in (getattr(job, "skills_required", None) or []) if _normalize_text(skill)],
        application_method="Through an external website" if application_url else "Through LinkedIn",
        application_email=application_email,
        application_url=application_url,
        execution_mode=JobPostingExecutionMode.normalize(LINKEDIN_JOB_POST_MODE),
    )


def ensure_eve_workflow_token(*, db: Session, job_id: str) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise ValueError("Job not found")

    structured = dict(getattr(job, "structured_data", {}) or {})
    existing = structured.get("eveWorkflow")
    existing_token = ""
    if isinstance(existing, dict):
        existing_token = _normalize_text(existing.get("workflowToken") or existing.get("token"))

    payload = {
        "jobId": job_id,
        "jobTitle": _normalize_text(getattr(job, "title", "")),
        "companyId": _normalize_text(getattr(job, "company_id", "")),
        "sourceApp": "ui",
    }
    token_row = upsert_notification_workflow_token(
        db=db,
        job_id=job_id,
        candidate_id=_EVE_WORKFLOW_CANDIDATE_ID,
        workflow_name=_EVE_WORKFLOW_NAME,
        token=existing_token or None,
        payload=payload,
        token_type=_EVE_WORKFLOW_TOKEN_TYPE,
        source_app="ui",
        force_token=False,
    )
    workflow_token = _normalize_text(token_row.get("workflowToken") or token_row.get("token"))
    workflow_link = build_eve_workflow_link(workflow_token)
    structured["eveWorkflow"] = {
        "workflowToken": workflow_token,
        "workflowLink": workflow_link,
        "workflowName": _EVE_WORKFLOW_NAME,
        "tokenType": _EVE_WORKFLOW_TOKEN_TYPE,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
    structured["eveWorkflowToken"] = workflow_token
    structured["eveWorkflowLink"] = workflow_link
    structured["workflowToken"] = workflow_token
    return {
        "workflowToken": workflow_token,
        "workflowLink": workflow_link,
        "structuredData": structured,
    }


def finalize_voice_intake(*, db: Session, job_id: str, voice_notes: list[str], transcript: str = "") -> dict[str, Any]:
    refined = refine_job_with_voice(db=db, job_id=job_id, voice_notes=voice_notes, transcript=transcript)
    job_repo = JobRepository(db)
    job = job_repo.get(job_id)
    if not job:
        raise ValueError("Job not found")

    token_result = ensure_eve_workflow_token(db=db, job_id=job_id)
    structured = dict(getattr(job, "structured_data", {}) or {})
    structured.update(token_result["structuredData"])
    structured.setdefault("linkedinPosting", {})
    linkedin_posting = dict(structured.get("linkedinPosting") or {})
    linkedin_posting.update(
        {
            "status": "queued",
            "queuedAt": datetime.now(timezone.utc).isoformat(),
            "workflowToken": token_result["workflowToken"],
            "workflowLink": token_result["workflowLink"],
        }
    )
    structured["linkedinPosting"] = linkedin_posting
    structured["jobWorkflow"] = {
        "status": "ready",
        "workflowToken": token_result["workflowToken"],
        "workflowLink": token_result["workflowLink"],
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    job_repo.update_structured_fields(job_id=job_id, structured_data=structured)
    job_repo.update_candidate_sourcing_state(job_id=job_id, job_status="ready")
    db.commit()
    db.refresh(job)

    try:
        enqueue_result = enqueue_job(
            "linkedin_job_posting",
            {
                "job_id": job_id,
                "workflow_token": token_result["workflowToken"],
            },
            idempotency_key=f"linkedin-job-posting:{job_id}",
        )
        if not bool(enqueue_result.get("queued", False)):
            linkedin_posting["status"] = "deferred"
            linkedin_posting["deferredReason"] = str(enqueue_result.get("reason") or enqueue_result.get("mode") or "redis_unavailable")
            linkedin_posting["updatedAt"] = datetime.now(timezone.utc).isoformat()
            structured["linkedinPosting"] = linkedin_posting
            job_repo.update_structured_fields(job_id=job_id, structured_data=structured)
            db.commit()
            return {
                **refined,
                "queued": False,
                "workflowToken": token_result["workflowToken"],
                "workflowLink": token_result["workflowLink"],
                "jobStatus": "ready",
                "linkedinPostingStatus": structured.get("linkedinPosting", {}).get("status", "deferred"),
            }
    except Exception as exc:
        logger.warning("linkedin_job_posting_queue_failed job_id=%s error=%s", job_id, str(exc), exc_info=exc)
        linkedin_posting["status"] = "queue_failed"
        linkedin_posting["lastError"] = str(exc)
        structured["linkedinPosting"] = linkedin_posting
        job_repo.update_structured_fields(job_id=job_id, structured_data=structured)
        db.commit()

    return {
        **refined,
        "queued": True,
        "workflowToken": token_result["workflowToken"],
        "workflowLink": token_result["workflowLink"],
        "jobStatus": "ready",
        "linkedinPostingStatus": structured.get("linkedinPosting", {}).get("status", "queued"),
    }


def resolve_eve_workflow_context(*, db: Session, workflow_token: str) -> dict[str, Any]:
    token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token, source_app="ui")
    if not token_row:
        raise ValueError("Workflow token not found")
    if (token_row.workflow_name or "").strip().lower() not in {_EVE_WORKFLOW_NAME, _EVE_WORKFLOW_TOKEN_TYPE}:
        raise ValueError("Workflow token not found")

    job = JobRepository(db).get(str(token_row.job_id or "").strip())
    if not job:
        raise ValueError("Job not found")

    company = CompanyRepository(db).get_by_id(str(getattr(job, "company_id", "") or "").strip())
    structured = dict(getattr(job, "structured_data", {}) or {})
    eve_workflow = structured.get("eveWorkflow")
    if not isinstance(eve_workflow, dict):
        eve_workflow = {}
    return {
        "workflowToken": _normalize_text(token_row.token),
        "workflowLink": _normalize_text(eve_workflow.get("workflowLink") or build_eve_workflow_link(token_row.token)),
        "jobId": _normalize_text(job.id),
        "companyId": _normalize_text(getattr(job, "company_id", "")),
        "job": {
            "title": _normalize_text(getattr(job, "title", "")),
            "description": _normalize_text(getattr(job, "description", "")),
            "location": _normalize_text(getattr(job, "location", "")),
            "compensation": _normalize_text(getattr(job, "compensation", "")),
            "workAuthorization": _normalize_text(getattr(job, "work_authorization", "")),
            "remotePolicy": _normalize_text(getattr(job, "remote_policy", "")),
            "experienceRequired": _normalize_text(getattr(job, "experience_required", "")),
            "vettingMode": _normalize_text(getattr(job, "vetting_mode", "")) or "volume",
            "autoExportToAts": bool(getattr(job, "auto_export_to_ats", False)),
        },
        "company": {
            "name": _normalize_text(getattr(company, "name", "") if company else ""),
            "website": _normalize_text(getattr(company, "website", "") if company else ""),
            "description": _normalize_text(getattr(company, "description", "") if company else ""),
            "industry": _normalize_text(getattr(company, "industry", "") if company else ""),
        },
        "workflow": {
            "status": _normalize_text((structured.get("jobWorkflow") or {}).get("status") if isinstance(structured.get("jobWorkflow"), dict) else ""),
            "linkedinPosting": structured.get("linkedinPosting") if isinstance(structured.get("linkedinPosting"), dict) else {},
        },
    }
