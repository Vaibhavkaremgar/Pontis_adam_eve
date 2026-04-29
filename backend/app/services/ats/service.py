from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta

from app.core.config import ATS_RETRY_INTERVAL_MINUTES, DEFAULT_ATS_PROVIDER
from app.db.repositories import ATSExportRepository, CandidateProfileRepository, CompanyRepository
from app.services.ats.factory import get_ats_provider

logger = logging.getLogger(__name__)


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_str(value: Any) -> str:
    return str(value or "").strip()


def _candidate_export_id(candidate: Any) -> str:
    candidate_id = _normalize_str(_get_value(candidate, "candidate_id"))
    if candidate_id:
        return candidate_id
    return _normalize_str(_get_value(candidate, "id"))


def _candidate_email(candidate: Any) -> str:
    direct = _normalize_str(_get_value(candidate, "email"))
    if direct:
        return direct

    raw_data = _get_value(candidate, "raw_data", {}) or {}
    if isinstance(raw_data, dict):
        for key in ("work_email", "email", "personal_email"):
            value = _normalize_str(raw_data.get(key))
            if value:
                return value
        for key in ("personal_emails", "emails", "work_emails"):
            values = raw_data.get(key)
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, str):
                        value = _normalize_str(item)
                        if value:
                            return value
                    elif isinstance(item, dict):
                        value = _normalize_str(item.get("address") or item.get("email"))
                        if value:
                            return value
    return ""


def _candidate_summary(candidate: Any) -> str:
    return _normalize_str(_get_value(candidate, "summary"))


def _candidate_name(candidate: Any) -> str:
    return _normalize_str(_get_value(candidate, "name"))


def _candidate_skills(candidate: Any) -> list[str]:
    skills = _get_value(candidate, "skills", []) or []
    if isinstance(skills, list):
        return [str(skill).strip() for skill in skills if str(skill).strip()]
    return []


def _serialize_export(row, *, existing: bool = False) -> dict[str, Any]:
    created_at = getattr(row, "exported_at", None)
    created_at_value = created_at.isoformat() if isinstance(created_at, datetime) else ""
    candidate_id = _normalize_str(getattr(row, "candidate_id", "")) or (
        str((getattr(row, "candidate_ids", None) or [""])[0]).strip()
    )
    return {
        "exportId": getattr(row, "id", ""),
        "candidateId": candidate_id,
        "jobId": getattr(row, "job_id", ""),
        "provider": getattr(row, "provider", DEFAULT_ATS_PROVIDER),
        "status": getattr(row, "status", "queued"),
        "externalReference": getattr(row, "external_reference", ""),
        "error": getattr(row, "error", ""),
        "createdAt": created_at_value,
        "existing": existing,
    }


def _resolve_provider(*, job, provider: str | None, db: Session | None) -> str:
    explicit = _normalize_str(provider)
    if explicit:
        return explicit
    if db is not None:
        company = getattr(job, "company", None) or CompanyRepository(db).get_by_id(getattr(job, "company_id", ""))
        if company:
            candidate_provider = _normalize_str(getattr(company, "ats_provider", ""))
            if candidate_provider:
                return candidate_provider
    return DEFAULT_ATS_PROVIDER


def _job_attachment_reference(job) -> str:
    ats_job_id = _normalize_str(getattr(job, "ats_job_id", ""))
    if ats_job_id:
        return ats_job_id
    logger.warning("ats_job_mapping_missing job_id=%s provider_job_id=missing_fallback", getattr(job, "id", ""))
    return _normalize_str(getattr(job, "id", ""))


def export_candidate_to_ats(
    candidate,
    job,
    provider: str | None = None,
    db: Session | None = None,
    *,
    allow_retry: bool = False,
) -> dict[str, Any]:
    provider_name = _resolve_provider(job=job, provider=provider, db=db)
    candidate_id = _candidate_export_id(candidate)
    if not candidate_id:
        raise ValueError("candidate id is required")

    candidate_data = {
        "name": _candidate_name(candidate),
        "email": _candidate_email(candidate),
        "skills": _candidate_skills(candidate),
        "summary": _candidate_summary(candidate),
        "source": "Pontis",
    }

    provider_impl = get_ats_provider(provider_name)

    repo: ATSExportRepository | None = ATSExportRepository(db) if db is not None else None
    existing = None
    if repo is not None:
        existing = repo.get(job_id=job.id, candidate_id=candidate_id, provider=provider_name)
        if existing:
            existing_status = (getattr(existing, "status", "") or "").strip().lower()
            if existing_status == "sent":
                logger.info(
                    "ats_export_duplicate_skipped job_id=%s candidate_id=%s provider=%s status=%s",
                    job.id,
                    candidate_id,
                    provider_name,
                    existing.status,
                )
                return _serialize_export(existing, existing=True)
            if existing_status == "sending" and not allow_retry:
                logger.info(
                    "ats_export_inflight_skipped job_id=%s candidate_id=%s provider=%s status=%s",
                    job.id,
                    candidate_id,
                    provider_name,
                    existing.status,
                )
                return _serialize_export(existing, existing=True)
            if existing_status == "failed" and not allow_retry:
                logger.info(
                    "ats_export_failed_skipped_without_retry job_id=%s candidate_id=%s provider=%s status=%s",
                    job.id,
                    candidate_id,
                    provider_name,
                    existing.status,
                )
                return _serialize_export(existing, existing=True)
            if existing_status in {"sending", "failed"} and allow_retry:
                try:
                    existing.status = "sending"
                    existing.error = ""
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    logger.warning(
                        "ats_export_claim_failed job_id=%s candidate_id=%s provider=%s error=%s",
                        job.id,
                        candidate_id,
                        provider_name,
                        str(exc),
                        exc_info=exc,
                    )
                    return _serialize_export(existing, existing=True)

        try:
            existing, created = repo.create_pending(
                job_id=job.id,
                candidate_id=candidate_id,
                candidate_ids=[candidate_id],
                provider=provider_name,
            )
            try:
                db.commit()
            except Exception as db_exc:
                db.rollback()
                logger.error(
                    "ats_export_claim_commit_failed job_id=%s candidate_id=%s provider=%s error=%s",
                    job.id,
                    candidate_id,
                    provider_name,
                    str(db_exc),
                    exc_info=db_exc,
                )
                return _serialize_export(existing, existing=True)
        except IntegrityError:
            db.rollback()
            existing = repo.get(job_id=job.id, candidate_id=candidate_id, provider=provider_name)
            if existing:
                logger.info(
                    "ats_export_duplicate_skipped_after_conflict job_id=%s candidate_id=%s provider=%s status=%s",
                    job.id,
                    candidate_id,
                    provider_name,
                    existing.status,
                )
                return _serialize_export(existing, existing=True)
            raise
        if not created:
            logger.info(
                "ats_export_duplicate_skipped_after_pending_conflict job_id=%s candidate_id=%s provider=%s status=%s",
                job.id,
                candidate_id,
                provider_name,
                existing.status,
            )
            return _serialize_export(existing, existing=True)

    export_row = existing
    external_candidate_id = ""
    try:
        external_candidate_id = provider_impl.create_candidate(candidate_data)
        provider_impl.attach_candidate_to_job(external_candidate_id, _job_attachment_reference(job))
        logger.info(
            "ats_export_provider_success job_id=%s candidate_id=%s provider=%s external_candidate_id=%s",
            job.id,
            candidate_id,
            provider_name,
            external_candidate_id,
        )
        if repo is not None and export_row is not None:
            try:
                repo.mark_sent(
                    export_row,
                    external_reference=external_candidate_id,
                    response_payload={
                        "candidateId": external_candidate_id,
                        "provider": provider_name,
                    },
                )
                db.commit()
                return _serialize_export(export_row)
            except Exception as db_exc:
                db.rollback()
                logger.error(
                    "ats_export_finalize_failed job_id=%s candidate_id=%s provider=%s external_candidate_id=%s error=%s",
                    job.id,
                    candidate_id,
                    provider_name,
                    external_candidate_id,
                    str(db_exc),
                    exc_info=db_exc,
                )
                return {
                    "exportId": getattr(export_row, "id", ""),
                    "candidateId": candidate_id,
                    "jobId": job.id,
                    "provider": provider_name,
                    "status": "sending",
                    "externalReference": external_candidate_id,
                    "error": "",
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "existing": False,
                }

        return {
            "exportId": "",
            "candidateId": candidate_id,
            "jobId": job.id,
            "provider": provider_name,
            "status": "sent",
            "externalReference": external_candidate_id,
            "error": "",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "existing": False,
        }
    except Exception as exc:
        error = str(exc).strip() or exc.__class__.__name__
        logger.error(
            "ats_export_provider_failed job_id=%s candidate_id=%s provider=%s error=%s",
            job.id,
            candidate_id,
            provider_name,
            error,
            exc_info=exc,
        )
        if repo is not None and export_row is not None:
            try:
                repo.mark_failed(
                    export_row,
                    error=error,
                    response_payload={
                        "error": error,
                        "provider": provider_name,
                    },
                    external_reference=external_candidate_id,
                )
                db.commit()
                return _serialize_export(export_row)
            except Exception as db_exc:
                db.rollback()
                logger.error(
                    "ats_export_failed_to_finalize job_id=%s candidate_id=%s provider=%s error=%s finalize_error=%s",
                    job.id,
                    candidate_id,
                    provider_name,
                    error,
                    str(db_exc),
                    exc_info=db_exc,
                )
                return {
                    "exportId": getattr(export_row, "id", ""),
                    "candidateId": candidate_id,
                    "jobId": job.id,
                    "provider": provider_name,
                    "status": "failed",
                    "externalReference": external_candidate_id,
                    "error": error,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "existing": False,
                }

        return {
            "exportId": "",
            "candidateId": candidate_id,
            "jobId": job.id,
            "provider": provider_name,
            "status": "failed",
            "externalReference": external_candidate_id,
            "error": error,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "existing": False,
        }


def export_candidate_by_id(*, db: Session, job, candidate_id: str, provider: str | None = None) -> dict[str, Any]:
    from app.db.repositories import CandidateProfileRepository

    candidate = CandidateProfileRepository(db).get(job_id=job.id, candidate_id=candidate_id)
    if not candidate:
        raise ValueError("Candidate not found for this job")
    return export_candidate_to_ats(candidate, job, provider=provider, db=db)


def run_ats_retry_cycle(db: Session) -> dict[str, Any]:
    repo = ATSExportRepository(db)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, ATS_RETRY_INTERVAL_MINUTES))
    retryable = [row for row in repo.list_retryable(status="failed", limit=100) if getattr(row, "exported_at", None) is None or getattr(row, "exported_at") <= cutoff]

    succeeded = failed = exhausted = 0
    for row in retryable:
        job = getattr(row, "job", None)
        if not job:
            continue
        candidate_id = _normalize_str(getattr(row, "candidate_id", "")) or ""
        if not candidate_id:
            continue

        try:
            candidate = CandidateProfileRepository(db).get(job_id=getattr(job, "id", ""), candidate_id=candidate_id)
            if not candidate:
                exhausted += 1
                continue
            result = export_candidate_to_ats(
                candidate=candidate,
                job=job,
                provider=getattr(row, "provider", None),
                db=db,
                allow_retry=True,
            )
            if result.get("status") == "sent":
                succeeded += 1
            elif result.get("status") == "failed":
                failed += 1
            else:
                exhausted += 1
        except Exception as exc:
            failed += 1
            logger.warning(
                "ats_retry_failed job_id=%s candidate_id=%s provider=%s error=%s",
                getattr(job, "id", ""),
                candidate_id,
                getattr(row, "provider", ""),
                str(exc),
                exc_info=exc,
            )

    logger.info("ats_retry_cycle_complete succeeded=%s failed=%s exhausted=%s", succeeded, failed, exhausted)
    return {"succeeded": succeeded, "failed": failed, "exhausted": exhausted, "total": len(retryable)}
