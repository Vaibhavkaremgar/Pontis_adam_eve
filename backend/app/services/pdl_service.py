import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from app.core.config import ENABLE_MOCK_PDL, HTTP_TIMEOUT_SECONDS, PDL_API_KEY, PDL_MIN_REQUEST_INTERVAL_SECONDS, PDL_SEARCH_SIZE, PDL_URL
from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)
EXPECTED_PDL_URL = "https://api.peopledatalabs.com/v5/person/search"
_api_key_logged = False
_request_lock = threading.Lock()
_last_request_epoch = 0.0
_last_health_status = "unknown"
_last_health_error = ""
_pdl_disabled = False
_pdl_disabled_until: datetime | None = None
_pdl_disable_reason = ""
PDL_DISABLE_COOLDOWN_SECONDS = 300


def is_pdl_disabled() -> bool:
    global _pdl_disabled, _pdl_disabled_until, _pdl_disable_reason

    if not _pdl_disabled:
        return False
    if _pdl_disabled_until is None:
        return True
    if datetime.now(timezone.utc) >= _pdl_disabled_until:
        _pdl_disabled = False
        _pdl_disabled_until = None
        _pdl_disable_reason = ""
        logger.info("pdl_reenabled_after_cooldown")
        return False
    return True


def _disable_pdl(reason: str, *, cooldown_seconds: int = PDL_DISABLE_COOLDOWN_SECONDS) -> None:
    global _pdl_disabled, _pdl_disabled_until, _pdl_disable_reason, _last_health_status, _last_health_error

    _pdl_disabled = True
    _pdl_disabled_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, cooldown_seconds))
    _pdl_disable_reason = reason
    _last_health_status = "disabled"
    _last_health_error = reason
    log_metric("fallback", source="pdl", reason="disabled")
    logger.warning("pdl_disabled reason=%s retry_at=%s", reason, _pdl_disabled_until.isoformat())


def _mask_secret(secret: str) -> str:
    clean = secret.strip()
    if len(clean) <= 8:
        return "*" * len(clean)
    return f"{clean[:4]}...{clean[-4:]}"


def _get_api_key() -> str:
    global _api_key_logged

    api_key = (PDL_API_KEY or "").strip()
    if not api_key:
        logger.warning("PDL_API_KEY is missing; PDL integration disabled and local-only retrieval will be used")
        return ""

    if not _api_key_logged:
        logger.warning("PDL_API_KEY loaded: %s", _mask_secret(api_key))
        _api_key_logged = True
    return api_key


def _get_pdl_endpoint() -> str:
    endpoint = (PDL_URL or "").strip()
    if endpoint != EXPECTED_PDL_URL:
        logger.warning(
            "PDL_URL is not the expected endpoint. Using expected endpoint instead. configured=%s expected=%s",
            endpoint,
            EXPECTED_PDL_URL,
        )
    return EXPECTED_PDL_URL


def _build_pdl_payload(es_query: dict, size: int) -> dict:
    query_part = es_query.get("query") if isinstance(es_query, dict) else None
    if not isinstance(query_part, dict):
        query_part = {"match_all": {}}
    return {"query": query_part, "size": size}


def _mock_candidate_payload(filters: dict, size: int) -> dict:
    role = (filters.get("role") or "Software Engineer").strip()
    skills = [str(skill).strip() for skill in (filters.get("skills") or []) if str(skill).strip()]
    learned_tokens = [str(token).strip() for token in (filters.get("learned_query_tokens") or []) if str(token).strip()]
    preferred_roles = [str(token).strip() for token in (filters.get("preferred_roles") or []) if str(token).strip()]
    seed_terms = [term for term in [role, *skills[:4], *learned_tokens[:2], *preferred_roles[:2]] if term]

    mock_rows: list[dict] = []
    for index in range(max(1, min(size, 5))):
        skill_slice = skills[:3] or learned_tokens[:3] or ["communication", "execution"]
        mock_rows.append(
            {
                "id": f"mock-{index}-{role.lower().replace(' ', '-')}",
                "job_title": role if index == 0 else f"{role} {index + 1}",
                "skills": skill_slice,
                "summary": f"Mock candidate for {role} with signals around {', '.join(seed_terms[:4])}.",
                "experience": "3-5 years" if index % 2 == 0 else "5+ years",
                "full_name": f"Mock Candidate {index + 1}",
                "job_company_name": "Mock Company",
                "location": "Remote",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "score": max(0.1, 1.0 - (index * 0.1)),
            }
        )
    logger.info("pdl_mock_candidates_used role=%s count=%s", role, len(mock_rows))
    return {"data": mock_rows}


def _respect_rate_limit() -> None:
    global _last_request_epoch

    interval = max(0.0, PDL_MIN_REQUEST_INTERVAL_SECONDS)
    if interval <= 0:
        return

    with _request_lock:
        now = time.monotonic()
        wait_seconds = (_last_request_epoch + interval) - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _last_request_epoch = time.monotonic()


def _run_person_search(es_query: dict, size: int) -> dict:
    global _last_health_status, _last_health_error

    if is_pdl_disabled():
        return {"data": []}

    api_key = _get_api_key()
    if not api_key:
        _disable_pdl("PDL_API_KEY missing")
        return {"data": []}

    endpoint = _get_pdl_endpoint()
    payload = _build_pdl_payload(es_query=es_query, size=size)

    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
    }

    logger.info("Calling PDL person search endpoint=%s payload=%s", endpoint, payload)
    _respect_rate_limit()
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        _disable_pdl(str(exc), cooldown_seconds=60)
        log_metric("error", source="pdl", kind="request_exception")
        logger.exception("PDL request exception: %s", str(exc))
        return {"data": []}

    logger.info("PDL response.status_code=%s", response.status_code)

    if response.status_code == 404:
        _last_health_status = "degraded"
        _last_health_error = "404"
        logger.info("PDL returned 404 (treated as no results)")
        return {"data": []}

    if response.status_code != 200:
        _disable_pdl(f"http_{response.status_code}")
        log_metric("error", source="pdl", kind=f"http_{response.status_code}")
        if response.status_code in {401, 403}:
            logger.error("PDL auth failed with status=%s", response.status_code)
        elif response.status_code == 402:
            logger.error("PDL billing/quota check failed with status=%s", response.status_code)
        elif response.status_code >= 500:
            logger.error("PDL server failure status=%s", response.status_code)
        else:
            logger.warning("PDL non-success status=%s", response.status_code)
        return {"data": []}

    try:
        parsed = response.json()
    except ValueError as exc:
        _disable_pdl(f"json_parse:{exc}")
        log_metric("error", source="pdl", kind="json_parse")
        logger.exception("PDL response JSON parse failed: %s", str(exc))
        return {"data": []}

    _last_health_status = "ok"
    _last_health_error = ""

    if not isinstance(parsed, dict):
        logger.error("PDL response is not a JSON object: %s", type(parsed).__name__)
        return {"data": []}

    return parsed


def fetch_candidates(query, size):
    es_query = {
        "query": {
            "bool": {
                "should": [
                    {"match": {"job_title": query}},
                    {"match": {"skills": query}},
                ],
            }
        }
    }

    return _run_person_search(es_query=es_query, size=size)


def fetch_candidates_with_filters(filters: dict, size: int | None = None) -> dict:
    size = size or PDL_SEARCH_SIZE

    if ENABLE_MOCK_PDL and not (PDL_API_KEY or "").strip():
        logger.info("PDL query replaced with mock candidates due to missing key")
        return _mock_candidate_payload(filters=filters, size=size)

    if is_pdl_disabled() and ENABLE_MOCK_PDL:
        logger.info("PDL query replaced with mock candidates")
        return _mock_candidate_payload(filters=filters, size=size)

    if is_pdl_disabled():
        logger.info("PDL query skipped: service disabled")
        return {"data": []}

    role = (filters.get("role") or "").strip()
    skills = [str(skill).strip().lower() for skill in (filters.get("skills") or []) if str(skill).strip()]
    learned_tokens = [str(token).strip().lower() for token in (filters.get("learned_query_tokens") or []) if str(token).strip()]
    preferred_roles = [str(token).strip().lower() for token in (filters.get("preferred_roles") or []) if str(token).strip()]
    if not role and not skills and not learned_tokens and not preferred_roles:
        logger.info("PDL query skipped: no role/skills provided")
        return {"data": []}

    weighted_should_clauses: list[dict] = []
    if role:
        weighted_should_clauses.extend(
            [
                {"match": {"job_title": role}},
                {"match": {"job_title": role}},
                {"match": {"job_title": role}},
            ]
        )
    for learned_role in preferred_roles[:3]:
        weighted_should_clauses.extend(
            [
                {"match": {"job_title": learned_role}},
                {"match": {"job_title": learned_role}},
            ]
        )
    for skill in skills[:5]:
        weighted_should_clauses.extend(
            [
                {"match": {"skills": skill}},
                {"match": {"skills": skill}},
            ]
        )
    for token in learned_tokens[:4]:
        weighted_should_clauses.extend(
            [
                {"match": {"skills": token}},
                {"match": {"job_title": token}},
            ]
        )

    primary_query = {
        "query": {
            "bool": {
                "should": weighted_should_clauses,
            }
        }
    }

    fallback_used = False
    logger.info("PDL primary weighted query=%s", primary_query)
    try:
        response = _run_person_search(es_query=primary_query, size=size)
        candidates = response.get("data", []) if isinstance(response, dict) else []
        logger.info("PDL candidate count=%s fallback_used=%s", len(candidates), fallback_used)
        if candidates:
            return response
    except Exception as exc:
        logger.exception("PDL primary search failed; returning no candidates error=%s", str(exc))
        log_metric("error", source="pdl", kind="primary_search_exception")
        return {"data": []}

    fallback_used = True
    if not role:
        logger.info("PDL fallback skipped: no role available")
        logger.info("PDL candidate count=%s fallback_used=%s", 0, fallback_used)
        return {"data": []}

    fallback_query = {
        "query": {
            "bool": {
                "should": [
                    {"match": {"job_title": {"query": role}}},
                ],
            }
        }
    }
    logger.info("PDL fallback relaxed title query=%s", fallback_query)
    try:
        fallback_response = _run_person_search(es_query=fallback_query, size=size)
        fallback_candidates = fallback_response.get("data", []) if isinstance(fallback_response, dict) else []
        logger.info("PDL candidate count=%s fallback_used=%s", len(fallback_candidates), fallback_used)
        return fallback_response
    except Exception as exc:
        logger.exception("PDL fallback search failed; returning no candidates error=%s", str(exc))
        log_metric("error", source="pdl", kind="fallback_search_exception")
        return {"data": []}


def run_startup_connectivity_check() -> None:
    global _last_health_status, _last_health_error

    if is_pdl_disabled():
        logger.info("Skipping PDL connectivity check because service is disabled")
        return
    api_key = _get_api_key()
    if not api_key:
        logger.warning("Skipping PDL connectivity check because PDL_API_KEY is missing")
        return
    endpoint = _get_pdl_endpoint()
    payload = {"query": {"match_all": {}}, "size": 1}
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
    }

    logger.info("Running PDL connectivity check endpoint=%s", endpoint)
    _respect_rate_limit()
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("PDL connectivity check failed: %s", str(exc))
        return

    logger.info("PDL connectivity check status=%s", response.status_code)
    if response.status_code != 200:
        _last_health_status = "degraded"
        _last_health_error = f"http_{response.status_code}"
        logger.warning("PDL connectivity check failed with status=%s", response.status_code)


def pdl_health_snapshot() -> dict:
    status = _last_health_status
    if status == "unknown":
        status = "configured" if (PDL_API_KEY or "").strip() else "unconfigured"
    retry_at = None
    if _pdl_disabled_until is not None and datetime.now(timezone.utc) < _pdl_disabled_until:
        retry_at = _pdl_disabled_until.isoformat()
    return {
        "status": status,
        "last_error": _last_health_error,
        "retry_at": retry_at,
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "rate_limit_interval_seconds": PDL_MIN_REQUEST_INTERVAL_SECONDS,
    }
