from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

from app.core.config import HTTP_TIMEOUT_SECONDS, MERGE_ACCOUNT_TOKEN, MERGE_API_KEY, MERGE_BASE_URL
from app.db.repositories import ATSExportRepository, CandidateFeedbackRepository, CandidateProfileRepository, InterviewRepository, JobRepository
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def _resolve_candidate_ids(db: Session, *, job_id: str, candidate_ids: list[str]) -> list[str]:
    if candidate_ids:
        return list(dict.fromkeys(candidate_ids))

    feedback = CandidateFeedbackRepository(db).list_for_job(job_id)
    accepted_ids = [row.candidate_id for row in feedback if row.feedback == "accept"]
    if accepted_ids:
        return list(dict.fromkeys(accepted_ids))

    stored = CandidateProfileRepository(db).list_for_job(job_id)
    return [row.candidate_id for row in stored[:5]]


def _merge_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if MERGE_API_KEY:
        headers["Authorization"] = f"Bearer {MERGE_API_KEY}"
    if MERGE_ACCOUNT_TOKEN:
        headers["X-Account-Token"] = MERGE_ACCOUNT_TOKEN
    return headers


def _build_merge_candidates_payload(*, profiles) -> list[dict]:
    candidates: list[dict] = []
    for profile in profiles:
        candidates.append(
            {
                "first_name": (profile.name.split(" ")[0] if profile.name else "Candidate"),
                "last_name": (" ".join(profile.name.split(" ")[1:]) if profile.name and " " in profile.name else ""),
                "company": profile.company,
                "title": profile.role,
                "applications": [],
                "remote_id": profile.candidate_id,
                "custom_fields": {
                    "pontis_fit_score": profile.fit_score,
                    "pontis_decision": profile.decision,
                    "pontis_strategy": profile.strategy,
                },
            }
        )
    return candidates


def export_to_ats(*, db: Session, job_id: str, candidate_ids: list[str], provider: str = "merge") -> dict:
    jobs = JobRepository(db)
    if not jobs.get(job_id):
        raise APIError("Job not found", status_code=404)

    provider_name = provider.strip().lower()
    if provider_name != "merge":
        raise APIError("Only merge provider is supported", status_code=400)

    resolved_candidate_ids = _resolve_candidate_ids(db, job_id=job_id, candidate_ids=candidate_ids)
    if not resolved_candidate_ids:
        raise APIError("No candidates available to export", status_code=400)

    profile_repo = CandidateProfileRepository(db)
    profiles = [
        profile_repo.get(job_id=job_id, candidate_id=candidate_id)
        for candidate_id in resolved_candidate_ids
    ]
    profiles = [profile for profile in profiles if profile]
    if not profiles:
        raise APIError("Candidates not found for this job", status_code=404)

    payload = {"candidates": _build_merge_candidates_payload(profiles=profiles)}
    url = f"{MERGE_BASE_URL.rstrip('/')}/candidates"

    status = "queued"
    external_reference = f"local-{int(datetime.now(timezone.utc).timestamp())}"
    response_payload: dict = {
        "message": "Merge credentials missing; export queued locally",
    }

    if MERGE_API_KEY and MERGE_ACCOUNT_TOKEN:
        try:
            response = requests.post(url, headers=_merge_headers(), json=payload, timeout=HTTP_TIMEOUT_SECONDS)
            ok = 200 <= response.status_code < 300
            status = "exported" if ok else "failed"
            parsed = {}
            try:
                parsed = response.json() if response.text else {}
            except ValueError:
                parsed = {"raw": response.text[:300]}
            response_payload = {
                "status_code": response.status_code,
                "body": parsed,
            }
            if ok and isinstance(parsed, dict):
                external_reference = str(parsed.get("id") or external_reference)
        except requests.RequestException as exc:
            logger.warning("Merge export request failed", exc_info=exc)
            status = "failed"
            response_payload = {"message": str(exc)}

    ATSExportRepository(db).create(
        job_id=job_id,
        candidate_ids=[profile.candidate_id for profile in profiles],
        provider=provider_name,
        status=status,
        external_reference=external_reference,
        response_payload=response_payload,
    )
    if status == "exported":
        interviews = InterviewRepository(db)
        for profile in profiles:
            interviews.upsert_status(
                job_id=job_id,
                candidate_id=profile.candidate_id,
                status="exported",
                create_default="shortlisted",
            )
    db.commit()

    return {
        "provider": provider_name,
        "status": status,
        "exportedCount": len(profiles),
        "reference": external_reference,
    }
