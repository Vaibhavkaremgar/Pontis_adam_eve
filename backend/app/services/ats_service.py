from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.repositories import CandidateProfileRepository, InterviewRepository, JobRepository
from app.services.ats.service import export_candidate_to_ats, run_ats_retry_cycle as _run_ats_retry_cycle
from app.services.metrics_service import log_metric
from app.services.state_machine import assert_valid_transition
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _selected_candidate_ids(job_id: str, candidate_ids: list[str], db: Session) -> list[str]:
    if candidate_ids:
        return list(dict.fromkeys([str(cid).strip() for cid in candidate_ids if str(cid).strip()]))

    interview_rows = InterviewRepository(db).list_for_job(job_id)
    return [
        row.candidate_id
        for row in interview_rows
        if (row.status or "").strip().lower() in {"contacted", "interview_scheduled", "shortlisted"}
    ]


def export_to_ats(*, db: Session, job_id: str, candidate_ids: list[str], provider: str | None = None) -> dict:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    selected_candidate_ids = _selected_candidate_ids(job_id, candidate_ids, db)
    if not selected_candidate_ids:
        raise APIError("No candidates selected for export", status_code=400)

    exported = failed = skipped = 0
    last_reference = ""
    results: list[dict] = []
    resolved_provider = provider or "mock"

    for candidate_id in selected_candidate_ids:
        try:
            profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
            if not profile:
                skipped += 1
                results.append({"candidateId": candidate_id, "status": "failed", "error": "Candidate not found"})
                continue

            result = export_candidate_to_ats(profile, job, provider=None, db=db)
            results.append(result)
            resolved_provider = result.get("provider", resolved_provider) or resolved_provider
            if result.get("status") == "sent":
                exported += 1
                last_reference = result.get("externalReference", last_reference) or last_reference
            elif result.get("status") == "failed":
                failed += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            results.append({"candidateId": candidate_id, "status": "failed", "error": str(exc)})
            logger.warning("ats_export_batch_failed job_id=%s candidate_id=%s error=%s", job_id, candidate_id, str(exc), exc_info=exc)

    status = "sent" if exported and not failed else "failed"
    if exported:
        logger.info("ats_export_batch_success job_id=%s count=%s", job_id, exported)
        log_metric("ats_export_batch_success", job_id=job_id, count=exported)
    if failed:
        logger.warning("ats_export_batch_partial_failure job_id=%s failed=%s skipped=%s", job_id, failed, skipped)
        log_metric("ats_export_batch_partial_failure", job_id=job_id, failed=failed, skipped=skipped)

    return {
        "provider": resolved_provider,
        "status": status,
        "exportedCount": exported,
        "reference": last_reference,
        "results": results,
    }


def run_ats_retry_cycle(db: Session) -> dict:
    return _run_ats_retry_cycle(db)
