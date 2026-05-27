from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from sqlalchemy.orm import Session

from app.core.config import APIFY_TOKEN, HTTP_TIMEOUT_SECONDS
from app.db.repositories import CandidateProfileRepository, JobRepository
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

APIFY_ACTOR_ID = "dev_fusion~linkedin-profile-scraper"
APIFY_ENDPOINT = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
_MAX_RETRIES = 3
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_url(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^www\.", "", text, flags=re.IGNORECASE)
    return text.rstrip("/")


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _clean_string_list(value: Any) -> list[str]:
    items: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                cleaned = _normalize_text(item)
                if cleaned:
                    items.append(cleaned)
            elif isinstance(item, dict):
                candidate = _normalize_text(
                    item.get("name")
                    or item.get("skill")
                    or item.get("title")
                    or item.get("label")
                    or item.get("value")
                )
                if candidate:
                    items.append(candidate)
    elif isinstance(value, str):
        for part in re.split(r"[,/|;]", value):
            cleaned = _normalize_text(part)
            if cleaned:
                items.append(cleaned)
    return list(dict.fromkeys(items))


def _normalize_experiences(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in _as_list(value):
        if isinstance(item, str):
            cleaned = _normalize_text(item)
            if cleaned:
                normalized.append({"summary": cleaned})
            continue
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": _normalize_text(item.get("title") or item.get("role") or item.get("position")),
                "company": _normalize_text(item.get("companyName") or item.get("company") or item.get("organizationName") or item.get("organization")),
                "location": _normalize_text(item.get("location") or item.get("locationName") or item.get("city") or item.get("country")),
                "start_date": _normalize_text(item.get("startDate") or item.get("startsAt") or item.get("from")),
                "end_date": _normalize_text(item.get("endDate") or item.get("endsAt") or item.get("to")),
                "duration": _normalize_text(item.get("duration") or item.get("timePeriod")),
                "description": _normalize_text(item.get("description") or item.get("summary") or item.get("descriptionText")),
            }
        )
    return normalized


def _normalize_educations(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in _as_list(value):
        if isinstance(item, str):
            cleaned = _normalize_text(item)
            if cleaned:
                normalized.append({"summary": cleaned})
            continue
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "school": _normalize_text(item.get("school") or item.get("schoolName") or item.get("institution") or item.get("institutionName")),
                "degree": _normalize_text(item.get("degree") or item.get("degreeName") or item.get("fieldOfStudy") or item.get("field")),
                "start_date": _normalize_text(item.get("startDate") or item.get("startsAt")),
                "end_date": _normalize_text(item.get("endDate") or item.get("endsAt")),
            }
        )
    return normalized


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _extract_profile_url(profile: Any) -> str:
    raw_data = dict(getattr(profile, "raw_data", {}) or {})
    return _first_non_empty(
        getattr(profile, "linkedin_url", ""),
        raw_data.get("linkedin_url"),
        raw_data.get("linkedinUrl"),
        raw_data.get("source_url"),
        raw_data.get("sourceUrl"),
    )


def _extract_candidate_identity(profile: Any) -> dict[str, str]:
    raw_data = dict(getattr(profile, "raw_data", {}) or {})
    return {
        "full_name": _first_non_empty(getattr(profile, "name", ""), raw_data.get("full_name"), raw_data.get("name")),
        "headline": _first_non_empty(getattr(profile, "role", ""), raw_data.get("headline"), raw_data.get("title")),
        "linkedin_url": _extract_profile_url(profile),
        "current_company": _first_non_empty(getattr(profile, "current_company", ""), getattr(profile, "company", ""), raw_data.get("current_company"), raw_data.get("company")),
    }


def _normalize_apify_item(item: dict[str, Any]) -> dict[str, Any]:
    experiences = _normalize_experiences(item.get("experiences") or item.get("experience"))
    educations = _normalize_educations(item.get("educations") or item.get("education"))
    skills = _clean_string_list(item.get("skills"))
    current_company = _first_non_empty(
        item.get("companyName"),
        item.get("currentCompany"),
        (experiences[0].get("company") if experiences else ""),
    )
    headline = _first_non_empty(item.get("headline"), item.get("title"), item.get("occupation"))
    full_name = _first_non_empty(item.get("fullName"), item.get("full_name"), item.get("name"), item.get("firstName"), item.get("lastName"))
    if not full_name:
        first = _normalize_text(item.get("firstName"))
        last = _normalize_text(item.get("lastName"))
        full_name = " ".join(part for part in [first, last] if part).strip()
    return {
        "full_name": full_name,
        "headline": headline,
        "linkedin_url": _first_non_empty(item.get("linkedinUrl"), item.get("linkedin_url"), item.get("url")),
        "current_company": current_company,
        "skills": skills,
        "experience": experiences,
        "education": educations,
        "about": _first_non_empty(item.get("about"), item.get("summary"), item.get("bio")),
        "email": _first_non_empty(item.get("email"), item.get("workEmail"), item.get("work_email"), item.get("personalEmail"), item.get("personal_email")),
        "phone": _first_non_empty(item.get("phone"), item.get("phoneNumber"), item.get("mobilePhone"), item.get("mobile_phone")),
        "enrichment_provider": "apify",
    }


def _select_profile_item(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        for key in ("items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                for item in value:
                    if isinstance(item, dict):
                        return item
            if isinstance(value, dict):
                return value
        if any(key in payload for key in ("fullName", "headline", "linkedinUrl", "skills")):
            return payload
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
    return None


def _request_apify_profile(*, linkedin_url: str) -> dict[str, Any]:
    if not APIFY_TOKEN:
        raise APIError("APIFY_TOKEN is missing", status_code=503)

    if not linkedin_url or "/in/" not in linkedin_url.lower() or "/jobs/" in linkedin_url.lower():
        raise APIError("A valid LinkedIn profile URL is required", status_code=400)

    params = urlencode({"token": APIFY_TOKEN})
    endpoint = f"{APIFY_ENDPOINT}?{params}"
    payload = {"profileUrls": [linkedin_url]}
    last_error: Exception | None = None

    logger.info("apify_request_start linkedin_url=%s endpoint=%s", linkedin_url, APIFY_ENDPOINT)
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = requests.post(endpoint, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
            if response.status_code in _RETRYABLE_STATUS_CODES:
                last_error = RuntimeError(f"retryable_http_{response.status_code}")
                logger.warning(
                    "apify_request_retryable_status linkedin_url=%s attempt=%s status_code=%s",
                    linkedin_url,
                    attempt,
                    response.status_code,
                )
                time.sleep(min(1.5 * attempt, 4.0))
                continue
            response.raise_for_status()
            body = response.json()
            logger.info(
                "apify_request_success linkedin_url=%s status_code=%s body_type=%s",
                linkedin_url,
                response.status_code,
                type(body).__name__,
            )
            item = _select_profile_item(body)
            if not item:
                raise APIError("Apify returned no profile data", status_code=502)
            return item
        except requests.Timeout as exc:
            last_error = exc
            logger.warning("apify_request_timeout linkedin_url=%s attempt=%s timeout_seconds=%s", linkedin_url, attempt, HTTP_TIMEOUT_SECONDS)
            time.sleep(min(1.5 * attempt, 4.0))
        except requests.RequestException as exc:
            last_error = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                logger.warning(
                    "apify_request_retryable_exception linkedin_url=%s attempt=%s error=%s",
                    linkedin_url,
                    attempt,
                    str(exc),
                )
                time.sleep(min(1.5 * attempt, 4.0))
                continue
            logger.exception("apify_request_failed linkedin_url=%s error=%s", linkedin_url, str(exc))
            raise APIError(f"Apify enrichment failed: {exc}", status_code=502) from exc
        except ValueError as exc:
            logger.exception("apify_request_invalid_json linkedin_url=%s error=%s", linkedin_url, str(exc))
            raise APIError(f"Apify enrichment returned invalid JSON: {exc}", status_code=502) from exc

    raise APIError(f"Apify enrichment failed after retries: {last_error}", status_code=502)


def enrich_candidate_with_apify(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    source_type: str = "linkedin_xray",
    workflow_token: str = "",
    selection_session_id: str = "",
    automation_job_id: str = "",
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        return {"jobId": job_id, "candidateId": candidate_id, "status": "failed", "reason": "job_missing", "shouldOutreach": False}

    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        profile = CandidateProfileRepository(db).ensure_candidate_profile(job_id=job_id, candidate_id=candidate_id)

    raw_profile = dict(getattr(profile, "raw_data", {}) or {})
    linkedin_url = _extract_profile_url(profile)
    identity = _extract_candidate_identity(profile)
    logger.info(
        "apify_candidate_identity job_id=%s candidate_id=%s name=%s linkedin=%s company=%s title=%s selection_session_id=%s automation_job_id=%s",
        job_id,
        candidate_id,
        identity["full_name"],
        linkedin_url,
        identity["current_company"],
        identity["headline"],
        selection_session_id,
        automation_job_id,
    )

    if not linkedin_url:
        profile.candidate_status = "missing_email"
        profile.ats_metadata = {
            **dict(profile.ats_metadata or {}),
            "enrichmentStatus": "missing_email",
            "enrichmentSource": "apify",
            "enrichmentProvider": "apify",
            "enrichmentReason": "missing_linkedin_url",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        profile.raw_data = {
            **raw_profile,
            "candidate_status": "missing_email",
            "enrichment": {
                **identity,
                "skills": _clean_string_list(raw_profile.get("skills")),
                "experience": _normalize_experiences(raw_profile.get("experience") or raw_profile.get("experiences")),
                "education": _normalize_educations(raw_profile.get("education") or raw_profile.get("educations")),
                "about": _first_non_empty(raw_profile.get("about"), raw_profile.get("summary")),
                "email": _first_non_empty(raw_profile.get("email"), raw_profile.get("work_email"), raw_profile.get("personal_email")),
                "phone": _first_non_empty(raw_profile.get("phone"), raw_profile.get("phoneNumber")),
                "enrichment_provider": "apify",
                "status": "missing_email",
                "emailStatus": "missing",
                "shouldOutreach": False,
                "contactEmail": "",
                "contactPhone": "",
            },
        }
        db.flush()
        logger.info("apify_enrichment_missing_linkedin job_id=%s candidate_id=%s", job_id, candidate_id)
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "missing_email",
            "enrichmentStatus": "missing_email",
            "enrichmentSource": "apify",
            "enrichmentProvider": "apify",
            "shouldOutreach": False,
            "emailStatus": "missing",
            "contactEmail": "",
            "contactPhone": "",
            "reason": "missing_linkedin_url",
        }

    try:
        apify_item = _request_apify_profile(linkedin_url=linkedin_url)
    except Exception as exc:
        logger.warning("apify_candidate_enrichment_failed job_id=%s candidate_id=%s error=%s", job_id, candidate_id, str(exc))
        profile.candidate_status = "enrichment_failed"
        profile.ats_metadata = {
            **dict(profile.ats_metadata or {}),
            "enrichmentStatus": "failed",
            "enrichmentSource": "apify",
            "enrichmentProvider": "apify",
            "enrichmentReason": str(exc),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        profile.raw_data = {
            **raw_profile,
            "candidate_status": "enrichment_failed",
            "enrichment": {
                **identity,
                "skills": _clean_string_list(raw_profile.get("skills")),
                "experience": _normalize_experiences(raw_profile.get("experience") or raw_profile.get("experiences")),
                "education": _normalize_educations(raw_profile.get("education") or raw_profile.get("educations")),
                "about": _first_non_empty(raw_profile.get("about"), raw_profile.get("summary")),
                "email": _first_non_empty(raw_profile.get("email"), raw_profile.get("work_email"), raw_profile.get("personal_email")),
                "phone": _first_non_empty(raw_profile.get("phone"), raw_profile.get("phoneNumber")),
                "enrichment_provider": "apify",
                "status": "failed",
                "emailStatus": "missing",
                "shouldOutreach": False,
                "contactEmail": "",
                "contactPhone": "",
                "reason": str(exc),
            },
        }
        db.flush()
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "failed",
            "enrichmentStatus": "failed",
            "enrichmentSource": "apify",
            "enrichmentProvider": "apify",
            "shouldOutreach": False,
            "emailStatus": "missing",
            "contactEmail": "",
            "contactPhone": "",
            "reason": str(exc),
        }

    normalized = _normalize_apify_item(apify_item)
    email = _normalize_text(normalized.get("email"))
    phone = _normalize_text(normalized.get("phone"))
    profile_payload = {
        "full_name": normalized["full_name"] or identity["full_name"],
        "headline": normalized["headline"] or identity["headline"],
        "linkedin_url": normalized["linkedin_url"] or linkedin_url,
        "current_company": normalized["current_company"] or identity["current_company"],
        "skills": normalized["skills"],
        "experience": normalized["experience"],
        "education": normalized["education"],
        "about": normalized["about"],
        "email": email,
        "phone": phone,
        "enrichment_provider": "apify",
    }
    status = "high_confidence" if email else "missing_email"
    should_outreach = bool(email)
    email_status = "found" if email else "missing"

    profile.name = profile_payload["full_name"] or profile.name
    profile.role = profile_payload["headline"] or profile.role
    profile.company = profile_payload["current_company"] or profile.company
    profile.summary = profile_payload["about"] or profile.summary
    profile.skills = profile_payload["skills"] or profile.skills
    profile.linkedin_url = profile_payload["linkedin_url"] or profile.linkedin_url
    profile.current_company = profile_payload["current_company"] or profile.current_company
    profile.phone = phone or profile.phone
    profile.candidate_status = "enriched" if email else "missing_email"
    profile.ats_metadata = {
        **dict(profile.ats_metadata or {}),
        "enrichmentStatus": status,
        "enrichmentSource": "apify",
        "enrichmentProvider": "apify",
        "emailStatus": email_status,
        "contactEmail": email,
        "contactPhone": phone,
        "linkedinUrl": profile_payload["linkedin_url"],
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    profile.raw_data = {
        **raw_profile,
        **profile_payload,
        "candidate_status": profile.candidate_status,
        "enrichment": {
            **profile_payload,
            "status": status,
            "emailStatus": email_status,
            "shouldOutreach": should_outreach,
            "contactEmail": email,
            "contactPhone": phone,
            "enrichment_provider": "apify",
            "sourceType": source_type,
            "workflowToken": workflow_token,
            "selectionSessionId": selection_session_id,
            "automationJobId": automation_job_id,
        },
    }
    db.flush()

    logger.info(
        "apify_candidate_enrichment_complete job_id=%s candidate_id=%s status=%s email_status=%s should_outreach=%s",
        job_id,
        candidate_id,
        status,
        email_status,
        should_outreach,
    )
    logger.info(
        "apify_candidate_stored job_id=%s candidate_id=%s status=%s enrichment_provider=apify",
        job_id,
        candidate_id,
        profile.candidate_status,
    )

    return {
        "jobId": job_id,
        "candidateId": candidate_id,
        "status": status,
        "enrichmentStatus": status,
        "enrichmentSource": "apify",
        "enrichmentProvider": "apify",
        "shouldOutreach": should_outreach,
        "emailStatus": email_status,
        "contactEmail": email,
        "contactPhone": phone,
        "candidateStatus": profile.candidate_status,
        "profile": profile_payload,
        "person": profile_payload,
        "enrichment": profile_payload,
        "reason": "" if email else "missing_email",
    }


def enrich_selected_candidate(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    source_type: str = "linkedin_xray",
    workflow_token: str = "",
    selection_session_id: str = "",
    automation_job_id: str = "",
) -> dict[str, Any]:
    return enrich_candidate_with_apify(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        source_type=source_type,
        workflow_token=workflow_token,
        selection_session_id=selection_session_id,
        automation_job_id=automation_job_id,
    )


def apify_health_snapshot() -> dict[str, str]:
    if not APIFY_TOKEN:
        return {"status": "degraded", "reason": "APIFY_TOKEN missing"}
    return {"status": "ok", "reason": ""}
