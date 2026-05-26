from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from requests import Response
from sqlalchemy.orm import Session

from app.core.config import APOLLO_API_KEY, APOLLO_URL, HTTP_TIMEOUT_SECONDS
from app.db.repositories import (
    CandidateProfileRepository,
    CandidateSelectionSessionRepository,
    JobRepository,
    NotificationWorkflowTokenRepository,
)
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.notification_intelligence_service import route_recruiter_notification
from app.services.metrics_service import log_metric
from app.services.persistent_cache_service import get_json as cache_get_json, set_json as cache_set_json
from app.services.sourcing.candidate_matching_service import match_apollo_people as _rank_apollo_people_external
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MIN_IDENTITY_CONFIDENCE = 0.72
_AMBIGUOUS_MATCH_GAP = 0.08
_ENRICHMENT_CACHE_NAMESPACE = "apollo-enrichment"
_ENRICHMENT_CACHE_REUSE_SECONDS = 7 * 24 * 60 * 60


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_lower(value: Any) -> str:
    return _normalize_text(value).lower()


def _normalize_url(value: Any) -> str:
    text = _normalize_lower(value)
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    text = text.rstrip("/")
    return text


def _metadata_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        mapped = dict(value)
    except Exception:
        return {}
    return mapped if isinstance(mapped, dict) else {}


def _tokens(value: Any) -> set[str]:
    text = _normalize_lower(value)
    if not text:
        return set()
    return {token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 1}


def _first_name(full_name: str) -> str:
    parts = [part for part in _normalize_text(full_name).split(" ") if part]
    return parts[0] if parts else ""


def _last_name(full_name: str) -> str:
    parts = [part for part in _normalize_text(full_name).split(" ") if part]
    return parts[-1] if len(parts) > 1 else ""


def _candidate_source(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        return dict(candidate)
    payload: dict[str, Any] = {}
    for attr in (
        "raw_data",
        "name",
        "role",
        "company",
        "summary",
        "skills",
        "phone",
        "linkedin_url",
        "current_title",
        "current_company",
        "total_experience_years",
        "parsed_resume_json",
        "parsed_resume_text",
        "resume_text",
    ):
        value = getattr(candidate, attr, None)
        if value is not None:
            payload[attr] = value
    return payload


def _candidate_identity_payload(candidate: Any) -> dict[str, str]:
    source = _candidate_source(candidate)
    raw_data = _metadata_map(source.get("raw_data"))
    profile_json = _metadata_map(source.get("parsed_resume_json"))

    name = _normalize_text(source.get("name") or raw_data.get("full_name") or raw_data.get("name") or profile_json.get("full_name") or profile_json.get("name"))
    linkedin_url = _normalize_text(
        source.get("linkedin_url")
        or raw_data.get("linkedin_url")
        or raw_data.get("linkedinUrl")
        or profile_json.get("linkedin_url")
        or profile_json.get("linkedinUrl")
    )
    email = _normalize_text(
        raw_data.get("email")
        or raw_data.get("work_email")
        or raw_data.get("personal_email")
        or profile_json.get("email")
        or profile_json.get("work_email")
    )
    company = _normalize_text(
        source.get("company")
        or source.get("current_company")
        or raw_data.get("current_company")
        or raw_data.get("company")
        or raw_data.get("currentCompany")
        or profile_json.get("company")
        or profile_json.get("current_company")
    )
    title = _normalize_text(
        source.get("role")
        or source.get("current_title")
        or raw_data.get("current_title")
        or raw_data.get("title")
        or raw_data.get("currentTitle")
        or profile_json.get("title")
        or profile_json.get("current_title")
    )
    location = _normalize_text(
        raw_data.get("location")
        or raw_data.get("current_location")
        or profile_json.get("location")
        or source.get("location")
    )
    domain = ""
    for key in ("company_domain", "companyDomain", "current_company_domain", "currentCompanyDomain", "website", "company_website"):
        value = _normalize_text(raw_data.get(key) or profile_json.get(key) or source.get(key))
        if value:
            domain = _normalize_url(value)
            break

    return {
        "name": name,
        "first_name": _first_name(name),
        "last_name": _last_name(name),
        "linkedin_url": linkedin_url,
        "email": email,
        "company": company,
        "title": title,
        "location": location,
        "domain": domain,
    }


def _response_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("people", "contacts", "matches", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return [value]
        if any(key in payload for key in ("first_name", "last_name", "name", "linkedin_url", "email")):
            return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _candidate_value(node: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = node.get(key)
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _identity_fingerprint(candidate: dict[str, str]) -> str:
    payload = {
        "linkedin_url": _normalize_url(candidate.get("linkedin_url", "")),
        "name": _normalize_lower(candidate.get("name", "")),
        "company": _normalize_lower(candidate.get("company", "")),
        "title": _normalize_lower(candidate.get("title", "")),
        "location": _normalize_lower(candidate.get("location", "")),
        "email": _normalize_lower(candidate.get("email", "")),
        "domain": _normalize_lower(candidate.get("domain", "")),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _enrichment_status_for_match(*, confidence: float, email: str, phone: str, match_type: str) -> str:
    normalized_match_type = _normalize_lower(match_type)
    has_contact = bool(email or phone)
    strong_identity = normalized_match_type in {"linkedin_exact", "name_company_exact", "strong_fuzzy"} or confidence >= _MIN_IDENTITY_CONFIDENCE
    if normalized_match_type == "linkedin_exact" and email and phone and confidence >= _MIN_IDENTITY_CONFIDENCE:
        return "verified"
    if has_contact and strong_identity:
        return "high_confidence"
    if has_contact:
        return "partial"
    return "no_match_found"


def _identity_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens and not right_tokens:
        return 0.0
    union = left_tokens.union(right_tokens)
    if not union:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / len(union)


def _resolve_person_identity(candidate: dict[str, str], person: dict[str, Any]) -> dict[str, Any]:
    person_name = _normalize_text(_candidate_value(person, "name", "full_name", "fullName"))
    person_linkedin = _normalize_url(_candidate_value(person, "linkedin_url", "linkedinUrl", "linkedin", "profile_url"))
    person_company = _normalize_text(_candidate_value(person, "organization_name", "company", "current_company", "job_company_name"))
    person_title = _normalize_text(_candidate_value(person, "title", "headline", "job_title", "current_title"))
    person_location = _normalize_text(_candidate_value(person, "present_raw_address", "city", "state", "country", "location"))
    person_email = _normalize_lower(_candidate_value(person, "email", "work_email", "personal_email"))

    candidate_linkedin = _normalize_url(candidate.get("linkedin_url", ""))
    candidate_name = _normalize_text(candidate.get("name", ""))
    candidate_company = _normalize_text(candidate.get("company", ""))
    candidate_title = _normalize_text(candidate.get("title", ""))
    candidate_location = _normalize_text(candidate.get("location", ""))
    candidate_email = _normalize_lower(candidate.get("email", ""))

    signals: dict[str, Any] = {
        "linkedinExactMatch": bool(candidate_linkedin and person_linkedin and candidate_linkedin == person_linkedin),
        "nameExactMatch": bool(candidate_name and person_name and _normalize_lower(candidate_name) == _normalize_lower(person_name)),
        "companyExactMatch": bool(candidate_company and person_company and _normalize_lower(candidate_company) == _normalize_lower(person_company)),
        "emailExactMatch": bool(candidate_email and person_email and candidate_email == person_email),
        "titleSimilarity": round(_identity_similarity(candidate_title, person_title), 4) if candidate_title and person_title else 0.0,
        "locationSimilarity": round(_identity_similarity(candidate_location, person_location), 4) if candidate_location and person_location else 0.0,
    }

    score = 0.0
    match_type = "fallback"

    if signals["linkedinExactMatch"]:
        score = 1.0
        match_type = "linkedin_exact"
    else:
        if signals["nameExactMatch"]:
            score += 0.52
            match_type = "name_exact"
        if signals["companyExactMatch"]:
            score += 0.23
            if match_type == "fallback":
                match_type = "company_exact"
        if signals["emailExactMatch"]:
            score += 0.12
            if match_type == "fallback":
                match_type = "email_exact"
        score += signals["titleSimilarity"] * 0.15
        score += signals["locationSimilarity"] * 0.08

        if not signals["nameExactMatch"] and person_name and candidate_name:
            score += 0.08 * _identity_similarity(candidate_name, person_name)
        if not signals["companyExactMatch"] and person_company and candidate_company:
            score += 0.05 * _identity_similarity(candidate_company, person_company)

        if signals["nameExactMatch"] and signals["companyExactMatch"]:
            score += 0.12
        if signals["companyExactMatch"] and signals["titleSimilarity"] >= 0.5:
            score += 0.05
            if match_type == "fallback":
                match_type = "company_title"

    score = max(0.0, min(1.0, score))
    if score >= _MIN_IDENTITY_CONFIDENCE and match_type == "fallback":
        match_type = "multi_signal"

    return {
        "confidence": round(score, 4),
        "matchType": match_type,
        "signals": signals,
        "personName": person_name,
        "personCompany": person_company,
        "personTitle": person_title,
        "personLocation": person_location,
        "personLinkedin": person_linkedin,
        "personEmail": person_email,
        "personId": _normalize_text(_candidate_value(person, "id", "person_id", "apollo_id", "uuid")),
    }


def _rank_apollo_people(candidate: dict[str, str], people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _rank_apollo_people_external(candidate, people)


def _apollo_cache_key(*, job_id: str, candidate_id: str, identity_fingerprint: str) -> str:
    return f"{job_id}:{candidate_id}:{identity_fingerprint}"


def _load_cached_enrichment(*, job_id: str, candidate_id: str, identity_fingerprint: str) -> dict[str, Any] | None:
    cached = cache_get_json(_ENRICHMENT_CACHE_NAMESPACE, _apollo_cache_key(job_id=job_id, candidate_id=candidate_id, identity_fingerprint=identity_fingerprint))
    if not isinstance(cached, dict):
        return None
    if cached.get("identityFingerprint") != identity_fingerprint:
        return None
    cached_at_text = _normalize_text(cached.get("cachedAt") or "")
    if cached_at_text:
        try:
            cached_at = datetime.fromisoformat(cached_at_text.replace("Z", "+00:00"))
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - cached_at).total_seconds() > _ENRICHMENT_CACHE_REUSE_SECONDS:
                return None
        except ValueError:
            return None
    return cached


def _store_cached_enrichment(*, job_id: str, candidate_id: str, identity_fingerprint: str, payload: dict[str, Any]) -> None:
    cache_set_json(
        _ENRICHMENT_CACHE_NAMESPACE,
        _apollo_cache_key(job_id=job_id, candidate_id=candidate_id, identity_fingerprint=identity_fingerprint),
        {
            "jobId": job_id,
            "candidateId": candidate_id,
            "identityFingerprint": identity_fingerprint,
            "cachedAt": datetime.now(timezone.utc).isoformat(),
            **payload,
        },
    )


def _sync_workflow_token_enrichment(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    workflow_token: str,
    source_type: str,
    status: str,
    confidence: float,
    selection_session_id: str,
    automation_job_id: str,
    email: str = "",
    phone: str = "",
    apollo_person_id: str = "",
    reason: str = "",
) -> None:
    token_row = None
    normalized_source_type = source_type or "adam"
    if workflow_token:
        token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token, source_app=normalized_source_type)
    if not token_row:
        token_row = NotificationWorkflowTokenRepository(db).get_active_by_candidate(
            job_id=job_id,
            candidate_id=candidate_id,
            source_app=normalized_source_type,
            token_type="slot_booking",
        )
    if not token_row:
        return

    payload = _metadata_map(getattr(token_row, "payload", None))
    payload.update(
        {
            "source_type": normalized_source_type,
            "sourceType": normalized_source_type,
            "enrichmentStatus": status,
            "enrichmentConfidence": round(float(confidence or 0.0), 4),
            "enrichmentSource": "apollo",
            "enrichmentUpdatedAt": datetime.now(timezone.utc).isoformat(),
            "contactEmail": email,
            "contactPhone": phone,
            "candidateId": candidate_id,
            "jobId": job_id,
            "apolloPersonId": apollo_person_id,
            "selectionSessionId": selection_session_id,
            "automationJobId": automation_job_id,
            "enrichmentReason": reason,
        }
    )
    token_row.payload = payload
    token_row.updated_at = datetime.now(timezone.utc)
    db.flush()


def _route_enrichment_notification(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    workflow_token: str,
    source_type: str,
    selection_session_id: str,
    automation_job_id: str,
    status: str,
    confidence: float,
    match_reason: str,
    should_outreach: bool,
    reason: str = "",
) -> None:
    normalized_source_type = source_type or "adam"
    title = "Candidate enriched" if should_outreach else "Candidate enrichment updated"
    if status in {"failed", "no_match_found", "ambiguous_match"}:
        title = "Candidate enrichment needs review"
    body = (
        f"Apollo enriched {candidate_id} with contact details."
        if should_outreach
        else f"Apollo enrichment completed for {candidate_id} with status {status}."
    )
    if reason:
        body = f"{body} Reason: {reason}."
    route_recruiter_notification(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        notification_key=f"apollo-enrichment:{job_id}:{candidate_id}:{automation_job_id or workflow_token or 'manual'}:{status}",
        notification_type="candidate_enrichment",
        title=title,
        body=body,
        metadata={
            "workflowToken": workflow_token,
            "sourceType": normalized_source_type,
            "status": status,
            "confidence": round(confidence, 4),
            "matchType": match_reason,
            "automationJobId": automation_job_id,
            "selectionSessionId": selection_session_id,
            "shouldOutreach": should_outreach,
            "reason": reason,
        },
    )


def _apollo_request(*, candidate: dict[str, str]) -> dict[str, Any]:
    if not APOLLO_API_KEY:
        raise APIError("APOLLO_API_KEY is missing", status_code=503)

    params: dict[str, Any] = {}
    if candidate["linkedin_url"]:
        params["linkedin_url"] = candidate["linkedin_url"]
    elif candidate["email"]:
        params["email"] = candidate["email"]
    elif candidate["name"]:
        params["name"] = candidate["name"]
        if candidate["first_name"]:
            params["first_name"] = candidate["first_name"]
        if candidate["last_name"]:
            params["last_name"] = candidate["last_name"]

    if candidate["domain"]:
        params["domain"] = candidate["domain"]

    headers = {
        "X-Api-Key": APOLLO_API_KEY,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Accept": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response: Response = requests.post(APOLLO_URL, params=params, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
            if response.status_code in _RETRYABLE_STATUS_CODES:
                last_error = RuntimeError(f"apollo_retryable_http_{response.status_code}")
                time.sleep(min(3.0, 0.35 * (attempt + 1)))
                continue
            if response.status_code in {401, 403, 404, 422}:
                raise APIError(f"Apollo enrichment rejected with status {response.status_code}", status_code=502)
            response.raise_for_status()
            try:
                data = response.json()
            except Exception as exc:
                raise APIError(f"Apollo enrichment returned invalid JSON: {exc}", status_code=502) from exc
            if not isinstance(data, dict):
                return {"raw": data}
            return data
        except requests.Timeout as exc:
            last_error = exc
            time.sleep(min(3.0, 0.35 * (attempt + 1)))
        except requests.RequestException as exc:
            message = str(exc).lower()
            if any(token in message for token in ("429", "timeout", "temporarily", "connection", "server error")):
                last_error = exc
                time.sleep(min(3.0, 0.35 * (attempt + 1)))
                continue
            raise APIError(f"Apollo enrichment failed: {exc}", status_code=502) from exc

    if last_error:
        raise APIError(f"Apollo enrichment failed after retries: {last_error}", status_code=502) from last_error
    raise APIError("Apollo enrichment failed", status_code=502)


def _merge_enrichment_payload(
    profile,
    job,
    *,
    person: dict[str, Any],
    status: str,
    confidence: float,
    reason: str = "",
    cache_hit: bool = False,
    match_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    raw_data = _metadata_map(getattr(profile, "raw_data", None))
    enrichment = _metadata_map(raw_data.get("enrichment"))
    company_name = ""
    company_record = getattr(job, "company", None)
    if company_record is not None:
        company_name = _normalize_text(getattr(company_record, "name", "") or "")
    if not company_name:
        company_name = _normalize_text(getattr(job, "company_name", "") or "")
    enrichment.update(
        {
            "source": "apollo",
            "status": status,
            "confidence": round(float(confidence or 0.0), 4),
            "reason": reason,
            "resolvedAt": now.isoformat(),
            "cachedAt": now.isoformat() if cache_hit else enrichment.get("cachedAt") or "",
            "jobTitle": _normalize_text(getattr(job, "title", "") or ""),
            "companyName": company_name,
            "matchDetails": _metadata_map(match_details or enrichment.get("matchDetails")),
        }
    )
    if person:
        company_metadata = {
            "name": _normalize_text(_candidate_value(person, "organization_name", "company", "current_company")),
            "title": _normalize_text(_candidate_value(person, "title", "headline", "job_title", "current_title")),
            "location": _normalize_text(_candidate_value(person, "location", "present_raw_address", "city", "state", "country")),
        }
        enrichment["apollo"] = {
            key: person.get(key)
            for key in (
                "id",
                "first_name",
                "last_name",
                "name",
                "linkedin_url",
                "title",
                "headline",
                "organization_name",
                "email",
                "work_email",
                "personal_email",
                "phone",
                "mobile_phone",
                "location",
                "city",
                "state",
                "country",
                "email_status",
                "phone_status",
            )
            if person.get(key) is not None
        }
        enrichment["apolloPersonId"] = _normalize_text(_candidate_value(person, "id", "person_id", "apollo_id", "uuid"))
        enrichment["enrichedEmail"] = _normalize_text(_candidate_value(person, "email", "work_email", "personal_email"))
        enrichment["enrichedPhone"] = _normalize_text(_candidate_value(person, "phone", "mobile_phone", "direct_dial", "phone_number"))
        enrichment["companyMetadata"] = company_metadata
        enrichment["identityMatchConfidence"] = round(float(confidence or 0.0), 4)
        enrichment["enrichmentTimestamp"] = now.isoformat()
        enrichment["enrichmentStatus"] = status
    raw_data["enrichment"] = enrichment

    email = _normalize_text(_candidate_value(person, "email", "work_email", "personal_email"))
    phone = _normalize_text(_candidate_value(person, "phone", "mobile_phone", "direct_dial", "phone_number"))
    linkedin_url = _normalize_text(_candidate_value(person, "linkedin_url", "linkedinUrl", "linkedin"))
    current_company = _normalize_text(_candidate_value(person, "organization_name", "company", "current_company"))
    current_title = _normalize_text(_candidate_value(person, "title", "headline", "job_title", "current_title"))
    location = _normalize_text(_candidate_value(person, "location", "present_raw_address", "city", "state", "country"))
    total_experience_years = getattr(profile, "total_experience_years", 0.0) or 0.0
    experience_text = _normalize_text(_candidate_value(person, "experience", "experience_summary", "summary"))

    if email:
        raw_data["email"] = email
        raw_data["work_email"] = email
        raw_data["personal_email"] = raw_data.get("personal_email") or email
        raw_data["contact_email"] = email
        raw_data["contactEmail"] = email
    if phone:
        raw_data["phone"] = phone
        raw_data["contact_phone"] = phone
        raw_data["contactPhone"] = phone
    if linkedin_url:
        raw_data["linkedin_url"] = linkedin_url
        raw_data["linkedinUrl"] = linkedin_url
    if current_company:
        raw_data["current_company"] = current_company
        raw_data["company"] = current_company
    if current_title:
        raw_data["current_title"] = current_title
        raw_data["title"] = current_title
    if location:
        raw_data["location"] = location
    if experience_text:
        raw_data["experience_summary"] = experience_text

    profile.raw_data = raw_data
    profile.phone = phone or profile.phone
    profile.linkedin_url = linkedin_url or profile.linkedin_url
    profile.current_company = current_company or profile.current_company
    profile.current_title = current_title or profile.current_title
    if total_experience_years:
        try:
            profile.total_experience_years = float(total_experience_years)
        except (TypeError, ValueError):
            pass
    profile.ats_metadata = {
        **_metadata_map(getattr(profile, "ats_metadata", None)),
        "enrichmentStatus": status,
        "enrichmentConfidence": round(float(confidence or 0.0), 4),
        "enrichmentSource": "apollo",
        "enrichmentResolvedAt": now.isoformat(),
        "enrichmentReason": reason,
        "apolloPersonId": enrichment.get("apolloPersonId", ""),
        "apolloCompanyName": enrichment.get("companyMetadata", {}).get("name", ""),
        "apolloCompanyTitle": enrichment.get("companyMetadata", {}).get("title", ""),
        "apolloCompanyLocation": enrichment.get("companyMetadata", {}).get("location", ""),
    }
    profile.last_refreshed_at = now
    return raw_data


def enrich_candidate_with_apollo(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    source_type: str = "adam",
    workflow_token: str = "",
    selection_session_id: str = "",
    automation_job_id: str = "",
) -> dict[str, Any]:
    job = JobRepository(db).get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    profile_repo = CandidateProfileRepository(db)
    profile = profile_repo.get(job_id=job_id, candidate_id=candidate_id)
    if not profile:
        raise APIError("Candidate not found", status_code=404)

    raw_data = _metadata_map(getattr(profile, "raw_data", None))
    enrichment_state = _metadata_map(raw_data.get("enrichment"))
    existing_status = _normalize_lower(enrichment_state.get("status"))

    candidate = _candidate_identity_payload(profile)
    if not candidate["name"] and not candidate["linkedin_url"] and not candidate["email"]:
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status="failed",
            confidence=0.0,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            reason="insufficient_identity",
        )
        _route_enrichment_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            status="failed",
            confidence=0.0,
            match_reason="insufficient_identity",
            should_outreach=False,
            reason="insufficient_identity",
        )
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "failed",
            "reason": "insufficient_identity",
            "shouldOutreach": False,
            "workflowToken": workflow_token,
        }

    identity_fingerprint = _identity_fingerprint(candidate)
    stored_identity_fingerprint = _normalize_text(enrichment_state.get("identityFingerprint") or "")

    selection_repo = CandidateSelectionSessionRepository(db)
    selection_session = selection_repo.get_by_job(job_id)
    if not selection_session:
        profile.raw_data = {
            **raw_data,
            "enrichment": {
                **enrichment_state,
                "source": "apollo",
                "status": "failed",
                "reason": "selection_required",
                "resolvedAt": datetime.now(timezone.utc).isoformat(),
            },
        }
        profile.ats_metadata = {**_metadata_map(getattr(profile, "ats_metadata", None)), "enrichmentStatus": "failed", "enrichmentReason": "selection_required"}
        db.flush()
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status="failed",
            confidence=0.0,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            reason="selection_required",
        )
        _route_enrichment_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            status="failed",
            confidence=0.0,
            match_reason="selection_required",
            should_outreach=False,
            reason="selection_required",
        )
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "failed",
            "reason": "selection_required",
            "shouldOutreach": False,
            "workflowToken": workflow_token,
        }

    selected_ids = {str(candidate).strip() for candidate in (selection_session.selected_candidate_ids or []) if str(candidate).strip()}
    if candidate_id not in selected_ids:
        profile.raw_data = {
            **raw_data,
            "enrichment": {
                **enrichment_state,
                "source": "apollo",
                "status": "failed",
                "reason": "candidate_not_selected",
                "resolvedAt": datetime.now(timezone.utc).isoformat(),
            },
        }
        profile.ats_metadata = {**_metadata_map(getattr(profile, "ats_metadata", None)), "enrichmentStatus": "failed", "enrichmentReason": "candidate_not_selected"}
        db.flush()
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status="failed",
            confidence=0.0,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            reason="candidate_not_selected",
        )
        _route_enrichment_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            status="failed",
            confidence=0.0,
            match_reason="candidate_not_selected",
            should_outreach=False,
            reason="candidate_not_selected",
        )
        return {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "failed",
            "reason": "candidate_not_selected",
            "shouldOutreach": False,
            "workflowToken": workflow_token,
        }

    deterministic_failure_reasons = {"selection_required", "candidate_not_selected", "insufficient_identity", "missing_api_key"}
    if existing_status in {"resolving", "enriched", "partial", "verified", "high_confidence", "ambiguous_match", "no_match_found"} and (
        not stored_identity_fingerprint or stored_identity_fingerprint == identity_fingerprint
    ):
        normalized_existing_status = existing_status
        if normalized_existing_status == "enriched":
            normalized_existing_status = "verified" if _normalize_text(raw_data.get("phone")) and _normalize_text(raw_data.get("email") or raw_data.get("work_email") or raw_data.get("personal_email")) else "high_confidence"
        elif normalized_existing_status == "partial":
            normalized_existing_status = "high_confidence"
        cached_payload = {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": normalized_existing_status,
            "duplicate": True,
            "confidence": float(enrichment_state.get("confidence") or enrichment_state.get("identityMatchConfidence") or 0.0),
            "shouldOutreach": normalized_existing_status in {"verified", "high_confidence"} and bool(_normalize_text(raw_data.get("email") or raw_data.get("work_email") or raw_data.get("personal_email"))),
            "workflowToken": workflow_token,
            "enrichment": enrichment_state,
            "contactEmail": _normalize_text(enrichment_state.get("enrichedEmail") or raw_data.get("email") or raw_data.get("work_email") or raw_data.get("personal_email")),
            "contactPhone": _normalize_text(enrichment_state.get("enrichedPhone") or raw_data.get("phone")),
        }
        return cached_payload
    if existing_status == "failed" and stored_identity_fingerprint == identity_fingerprint and _normalize_lower(enrichment_state.get("reason")) in deterministic_failure_reasons:
        cached_payload = {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": existing_status,
            "duplicate": True,
            "confidence": float(enrichment_state.get("confidence") or enrichment_state.get("identityMatchConfidence") or 0.0),
            "shouldOutreach": False,
            "workflowToken": workflow_token,
            "enrichment": enrichment_state,
            "contactEmail": _normalize_text(enrichment_state.get("enrichedEmail") or raw_data.get("email") or raw_data.get("work_email") or raw_data.get("personal_email")),
            "contactPhone": _normalize_text(enrichment_state.get("enrichedPhone") or raw_data.get("phone")),
        }
        return cached_payload

    cached = _load_cached_enrichment(job_id=job_id, candidate_id=candidate_id, identity_fingerprint=identity_fingerprint)
    if cached:
        status = str(cached.get("status") or "").strip().lower() or "failed"
        payload = _metadata_map(cached.get("result"))
        payload.setdefault("jobId", job_id)
        payload.setdefault("candidateId", candidate_id)
        payload.setdefault("status", status)
        payload.setdefault("workflowToken", workflow_token)
        payload["duplicate"] = True
        payload["cacheHit"] = True
        if payload.get("enrichment") is None:
            payload["enrichment"] = _metadata_map(getattr(profile, "raw_data", None)).get("enrichment") or {}
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status=status,
            confidence=float(payload.get("confidence") or payload.get("identityMatchConfidence") or 0.0),
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            email=_normalize_text(payload.get("contactEmail") or ""),
            phone=_normalize_text(payload.get("contactPhone") or ""),
            apollo_person_id=_normalize_text(_metadata_map(payload.get("enrichment")).get("apolloPersonId") or ""),
            reason=str(_metadata_map(payload.get("enrichment")).get("reason") or ""),
        )
        return payload

    if not APOLLO_API_KEY:
        raw_data = _merge_enrichment_payload(
            profile,
            job,
            person={},
            status="failed",
            confidence=0.0,
            reason="missing_api_key",
            match_details={},
        )
        db.flush()
        result = {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "failed",
            "reason": "missing_api_key",
            "shouldOutreach": False,
            "workflowToken": workflow_token,
            "enrichment": raw_data.get("enrichment") or {},
        }
        _store_cached_enrichment(
            job_id=job_id,
            candidate_id=candidate_id,
            identity_fingerprint=identity_fingerprint,
            payload={"status": "failed", "result": result, "retryable": False},
        )
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status="failed",
            confidence=0.0,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            reason="missing_api_key",
        )
        _route_enrichment_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            status="failed",
            confidence=0.0,
            match_reason="missing_api_key",
            should_outreach=False,
            reason="missing_api_key",
        )
        return result

    now = datetime.now(timezone.utc)
    enrichment_state.update(
        {
            "source": "apollo",
            "status": "resolving",
            "startedAt": now.isoformat(),
            "automationJobId": automation_job_id,
            "sourceType": source_type or "adam",
            "selectionSessionId": selection_session_id or str(selection_session.id or ""),
            "identityFingerprint": identity_fingerprint,
        }
    )
    raw_data["enrichment"] = enrichment_state
    profile.raw_data = raw_data
    profile.ats_metadata = {
        **_metadata_map(getattr(profile, "ats_metadata", None)),
        "enrichmentStatus": "resolving",
        "enrichmentSource": "apollo",
        "enrichmentStartedAt": now.isoformat(),
        "automationJobId": automation_job_id,
        "sourceType": source_type or "adam",
        "selectionSessionId": selection_session_id or str(selection_session.id or ""),
        "identityFingerprint": identity_fingerprint,
    }
    db.flush()

    try:
        person_payload = _apollo_request(candidate=candidate)
    except Exception as exc:
        raw_data = _merge_enrichment_payload(
            profile,
            job,
            person={},
            status="failed",
            confidence=0.0,
            reason="apollo_request_failed",
            match_details={"error": type(exc).__name__, "message": str(exc)},
        )
        db.flush()
        result = {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "failed",
            "reason": "apollo_request_failed",
            "error": str(exc),
            "shouldOutreach": False,
            "workflowToken": workflow_token,
            "enrichment": raw_data.get("enrichment") or {},
        }
        _store_cached_enrichment(
            job_id=job_id,
            candidate_id=candidate_id,
            identity_fingerprint=identity_fingerprint,
            payload={"status": "failed", "result": result, "retryable": True},
        )
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status="failed",
            confidence=0.0,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            reason="apollo_request_failed",
        )
        _route_enrichment_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            status="failed",
            confidence=0.0,
            match_reason="apollo_request_failed",
            should_outreach=False,
            reason="apollo_request_failed",
        )
        logger.warning(
            "apollo_candidate_enrichment_failed job_id=%s candidate_id=%s error=%s",
            job_id,
            candidate_id,
            str(exc),
        )
        return result

    candidates = _response_candidates(person_payload)
    ranked_candidates = _rank_apollo_people(candidate, candidates)
    if not ranked_candidates:
        raw_data = _merge_enrichment_payload(
            profile,
            job,
            person={},
            status="no_match_found",
            confidence=0.0,
            reason="apollo_returned_no_match",
            match_details={"responseCount": 0},
        )
        db.flush()
        result = {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "no_match_found",
            "confidence": 0.0,
            "reason": "apollo_returned_no_match",
            "shouldOutreach": False,
            "workflowToken": workflow_token,
            "enrichment": raw_data.get("enrichment") or {},
        }
        _store_cached_enrichment(
            job_id=job_id,
            candidate_id=candidate_id,
            identity_fingerprint=identity_fingerprint,
            payload={"status": "no_match_found", "result": result, "retryable": False},
        )
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status="no_match_found",
            confidence=0.0,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            reason="apollo_returned_no_match",
        )
        _route_enrichment_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            status="no_match_found",
            confidence=0.0,
            match_reason="apollo_returned_no_match",
            should_outreach=False,
            reason="apollo_returned_no_match",
        )
        return result

    top_match = ranked_candidates[0]
    top_identity = dict(top_match.get("_identity_match") or {})
    second_confidence = float(dict(ranked_candidates[1].get("_identity_match") or {}).get("confidence") or 0.0) if len(ranked_candidates) > 1 else 0.0
    confidence = float(top_identity.get("confidence") or 0.0)
    match_reason = str(top_identity.get("matchType") or "apollo_match_resolved")

    if confidence < _MIN_IDENTITY_CONFIDENCE:
        raw_data = _merge_enrichment_payload(
            profile,
            job,
            person=top_match,
            status="no_match_found",
            confidence=confidence,
            reason="identity_below_threshold",
            match_details={
                "responseCount": len(ranked_candidates),
                "topConfidence": round(confidence, 4),
                "secondConfidence": round(second_confidence, 4),
                "matchType": match_reason,
                "signals": top_identity.get("signals") or {},
            },
        )
        db.flush()
        result = {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "no_match_found",
            "confidence": round(confidence, 4),
            "reason": "identity_below_threshold",
            "shouldOutreach": False,
            "workflowToken": workflow_token,
            "enrichment": raw_data.get("enrichment") or {},
        }
        _store_cached_enrichment(
            job_id=job_id,
            candidate_id=candidate_id,
            identity_fingerprint=identity_fingerprint,
            payload={"status": "no_match_found", "result": result, "retryable": False},
        )
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status="no_match_found",
            confidence=round(confidence, 4),
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            reason="identity_below_threshold",
        )
        _route_enrichment_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            status="no_match_found",
            confidence=round(confidence, 4),
            match_reason=match_reason,
            should_outreach=False,
            reason="identity_below_threshold",
        )
        return result

    if len(ranked_candidates) > 1 and (confidence - second_confidence) < _AMBIGUOUS_MATCH_GAP and second_confidence >= (_MIN_IDENTITY_CONFIDENCE * 0.9):
        raw_data = _merge_enrichment_payload(
            profile,
            job,
            person=top_match,
            status="ambiguous_match",
            confidence=confidence,
            reason="multiple_high_confidence_matches",
            match_details={
                "responseCount": len(ranked_candidates),
                "topConfidence": round(confidence, 4),
                "secondConfidence": round(second_confidence, 4),
                "matchType": match_reason,
                "signals": top_identity.get("signals") or {},
                "runnerUp": dict(ranked_candidates[1].get("_identity_match") or {}),
            },
        )
        db.flush()
        result = {
            "jobId": job_id,
            "candidateId": candidate_id,
            "status": "ambiguous_match",
            "confidence": round(confidence, 4),
            "reason": "multiple_high_confidence_matches",
            "shouldOutreach": False,
            "workflowToken": workflow_token,
            "enrichment": raw_data.get("enrichment") or {},
        }
        _store_cached_enrichment(
            job_id=job_id,
            candidate_id=candidate_id,
            identity_fingerprint=identity_fingerprint,
            payload={"status": "ambiguous_match", "result": result, "retryable": False},
        )
        _sync_workflow_token_enrichment(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            status="ambiguous_match",
            confidence=round(confidence, 4),
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            reason="multiple_high_confidence_matches",
        )
        _route_enrichment_notification(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            workflow_token=workflow_token,
            source_type=source_type,
            selection_session_id=selection_session_id or "",
            automation_job_id=automation_job_id,
            status="ambiguous_match",
            confidence=round(confidence, 4),
            match_reason=match_reason,
            should_outreach=False,
            reason="multiple_high_confidence_matches",
        )
        return result

    email = _normalize_text(_candidate_value(top_match, "email", "work_email", "personal_email"))
    phone = _normalize_text(_candidate_value(top_match, "phone", "mobile_phone", "direct_dial", "phone_number"))
    status = _enrichment_status_for_match(
        confidence=confidence,
        email=email,
        phone=phone,
        match_type=match_reason,
    )
    raw_data = _merge_enrichment_payload(
        profile,
        job,
        person=top_match,
        status=status,
        confidence=confidence,
        reason=match_reason,
        match_details={
            "responseCount": len(ranked_candidates),
            "topConfidence": round(confidence, 4),
            "secondConfidence": round(second_confidence, 4),
            "matchType": match_reason,
            "signals": top_identity.get("signals") or {},
        },
    )
    db.flush()

    if status in {"verified", "high_confidence"}:
        transition_candidate_ats_state(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            to_status="enriched",
            source="apollo_enrichment",
            reason="apollo_contact_resolved",
            metadata={
                "workflowToken": workflow_token,
                "sourceType": source_type or "adam",
                "selectionSessionId": selection_session_id or str(selection_session.id or ""),
                "enrichmentStatus": status,
                "enrichmentConfidence": round(confidence, 4),
                "identityFingerprint": identity_fingerprint,
                "automationJobId": automation_job_id,
            },
        )

    token_row = None
    if workflow_token:
        token_row = NotificationWorkflowTokenRepository(db).get_by_token(workflow_token, source_app=source_type or "adam")
    if not token_row:
        token_row = NotificationWorkflowTokenRepository(db).get_active_by_candidate(
            job_id=job_id,
            candidate_id=candidate_id,
            source_app=source_type or "adam",
            token_type="slot_booking",
        )
    if token_row:
        payload = _metadata_map(getattr(token_row, "payload", None))
        payload.update(
            {
                "source_type": source_type or payload.get("source_type") or "adam",
                "sourceType": source_type or payload.get("sourceType") or "adam",
                "enrichmentStatus": status,
                "enrichmentConfidence": round(confidence, 4),
                "enrichmentSource": "apollo",
                "enrichmentUpdatedAt": datetime.now(timezone.utc).isoformat(),
                "contactEmail": email,
                "contactPhone": phone,
                "candidateId": candidate_id,
                "jobId": job_id,
                "apolloPersonId": _metadata_map(raw_data.get("enrichment")).get("apolloPersonId", ""),
            }
        )
        token_row.payload = payload
        token_row.updated_at = datetime.now(timezone.utc)
        db.flush()

    should_outreach = bool(email) and status in {"verified", "high_confidence"}
    route_recruiter_notification(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        notification_key=f"apollo-enrichment:{job_id}:{candidate_id}:{automation_job_id or 'manual'}",
        notification_type="candidate_enrichment",
        title="Candidate enriched" if should_outreach else "Candidate enrichment updated",
        body=(
            f"Apollo enriched {candidate_id} with contact details."
            if should_outreach
            else f"Apollo enrichment completed for {candidate_id} with status {status}."
        ),
        metadata={
            "workflowToken": workflow_token,
            "sourceType": source_type or "adam",
            "selectionSessionId": selection_session_id or str(selection_session.id or ""),
            "status": status,
            "confidence": round(confidence, 4),
            "matchType": match_reason,
            "automationJobId": automation_job_id,
            "shouldOutreach": should_outreach,
        },
    )

    result = {
        "jobId": job_id,
        "candidateId": candidate_id,
        "status": status,
        "confidence": round(confidence, 4),
        "identityMatchConfidence": round(confidence, 4),
        "matchType": match_reason,
        "shouldOutreach": should_outreach,
        "workflowToken": workflow_token,
        "enrichment": _metadata_map(getattr(profile, "raw_data", None)).get("enrichment") or {},
        "contactEmail": email,
        "contactPhone": phone,
        "person": top_match,
    }
    _store_cached_enrichment(
        job_id=job_id,
        candidate_id=candidate_id,
        identity_fingerprint=identity_fingerprint,
        payload={"status": status, "result": result, "retryable": False},
    )

    logger.info(
        "apollo_candidate_enrichment_complete job_id=%s candidate_id=%s status=%s confidence=%.4f should_outreach=%s cache_hit=%s",
        job_id,
        candidate_id,
        status,
        confidence,
        should_outreach,
        False,
    )
    log_metric(
        "enrichment_conversion",
        job_id=job_id,
        candidate_id=candidate_id,
        status=status,
        should_outreach=should_outreach,
        cache_hit=False,
    )
    return result


def apollo_health_snapshot() -> dict[str, str]:
    if not APOLLO_API_KEY.strip():
        return {"status": "down", "reason": "APOLLO_API_KEY missing"}
    if not APOLLO_URL.strip():
        return {"status": "down", "reason": "APOLLO_URL missing"}
    return {"status": "ok", "reason": "configured"}
