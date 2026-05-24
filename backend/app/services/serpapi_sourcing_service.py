from __future__ import annotations

import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.core.config import (
    HTTP_TIMEOUT_SECONDS,
    SERPAPI_API_KEY,
    SERPAPI_ENABLED,
    SERPAPI_ENGINE,
    SERPAPI_MAX_PAGES,
    SERPAPI_MIN_REQUEST_INTERVAL_SECONDS,
    SERPAPI_REQUEST_TIMEOUT_SECONDS,
    SERPAPI_RETRY_ATTEMPTS,
    SERPAPI_RESULTS_PER_PAGE,
    SERPAPI_URL,
)
from app.services.metrics_service import log_metric
from app.services.skill_normalizer import normalize_skills

logger = logging.getLogger(__name__)

_request_lock = threading.Lock()
_last_request_epoch = 0.0
_serpapi_disabled_until: datetime | None = None
_serpapi_disable_reason = ""

_TITLE_ROLE_STOPWORDS = {
    "linkedin",
    "profile",
    "at",
    "the",
    "and",
    "for",
    "of",
    "in",
}


@dataclass(frozen=True)
class SerpApiSearchResult:
    query: str
    page: int
    position: int
    title: str
    link: str
    snippet: str
    displayed_link: str
    source: str
    score: float


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_lower(value: Any) -> str:
    return _normalize_text(value).lower()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = _normalize_text(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return ordered


def _is_disabled() -> bool:
    global _serpapi_disabled_until, _serpapi_disable_reason

    if _serpapi_disabled_until is None:
        return False
    if datetime.now(timezone.utc) >= _serpapi_disabled_until:
        _serpapi_disabled_until = None
        _serpapi_disable_reason = ""
        logger.info("serpapi_reenabled_after_cooldown")
        return False
    return True


def _disable(reason: str, *, cooldown_seconds: int = 300) -> None:
    global _serpapi_disabled_until, _serpapi_disable_reason

    _serpapi_disable_reason = reason
    _serpapi_disabled_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, cooldown_seconds))
    logger.warning("serpapi_disabled reason=%s retry_at=%s", reason, _serpapi_disabled_until.isoformat())
    log_metric("fallback", source="serpapi", reason=reason)


def is_serpapi_disabled() -> bool:
    return _is_disabled()


def _mask_secret(secret: str) -> str:
    clean = secret.strip()
    if len(clean) <= 8:
        return "*" * len(clean)
    return f"{clean[:4]}...{clean[-4:]}"


def _is_technical_role(role: str, skills: list[str]) -> bool:
    haystack = " ".join([role, " ".join(skills)]).lower()
    return any(token in haystack for token in ("engineer", "developer", "architect", "platform", "infra", "backend", "full stack", "fullstack", "data", "machine learning", "ml", "devops", "security"))


def _candidate_name_from_title(title: str) -> str:
    cleaned = _normalize_text(title)
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s*-\s*LinkedIn\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\|\s*LinkedIn\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*at\s+LinkedIn\s*$", "", cleaned, flags=re.IGNORECASE)
    parts = [part for part in re.split(r"[-|]", cleaned) if part.strip()]
    first = _normalize_text(parts[0] if parts else cleaned)
    tokens = [token for token in re.split(r"\s+", first) if token]
    # Prefer a human-style name over role headlines when the result is a profile page.
    if len(tokens) <= 8 and not any(token.lower() in _TITLE_ROLE_STOPWORDS for token in tokens[:2]):
        return first
    return ""


def _extract_linkedin_url(link: str) -> str:
    url = _normalize_text(link)
    if "linkedin.com/" not in url.lower():
        return ""
    return url.rstrip("/")


def _extract_github_url(link: str) -> str:
    url = _normalize_text(link)
    if "github.com/" not in url.lower():
        return ""
    return url.rstrip("/")


def _extract_location(text: str, fallback: str = "") -> str:
    lowered = _normalize_lower(text)
    for token in ("remote", "hybrid", "on-site", "onsite"):
        if token in lowered:
            return token.replace("-", " ").title()
    location_match = re.search(r"(?:\bat\b|\bin\b)\s+([A-Z][A-Za-z0-9 ,.&-]{2,80})", text)
    if location_match:
        return _normalize_text(location_match.group(1))
    return _normalize_text(fallback)


def _extract_company(text: str) -> str:
    match = re.search(r"\bat\s+([A-Z][A-Za-z0-9 &.-]{2,80})", text)
    if match:
        return _normalize_text(match.group(1))
    return ""


def _extract_skills_from_text(text: str, known_skills: list[str]) -> list[str]:
    lowered = _normalize_lower(text)
    matches: list[str] = []
    for skill in known_skills:
        token = _normalize_lower(skill)
        if token and token in lowered:
            matches.append(_normalize_text(skill))
    return _dedupe_preserve_order(matches)


def build_linkedin_xray_queries(
    *,
    role: str,
    seniority: str,
    skills: list[str],
    location: str,
    company_stage: str,
    hiring_preferences: str,
    industry: str,
    leadership_expectations: str,
    recruiter_preferences: dict[str, Any] | None = None,
) -> list[str]:
    role = _normalize_text(role)
    seniority = _normalize_text(seniority)
    location = _normalize_text(location)
    company_stage = _normalize_text(company_stage)
    hiring_preferences = _normalize_text(hiring_preferences)
    industry = _normalize_text(industry)
    leadership_expectations = _normalize_text(leadership_expectations)
    recruiter_preferences = recruiter_preferences or {}
    normalized_skills = _dedupe_preserve_order([_normalize_text(skill) for skill in skills if _normalize_text(skill)])
    if not normalized_skills:
        normalized_skills = _dedupe_preserve_order([skill for skill in (normalize_skills(skills) or [])])

    preferred_skills = _dedupe_preserve_order(
        [
            _normalize_text(item.get("skill") or item.get("role") or item)
            for item in (
                recruiter_preferences.get("top_skills")
                or recruiter_preferences.get("skill_tokens")
                or []
            )
        ]
    )
    preferred_roles = _dedupe_preserve_order(
        [
            _normalize_text(item.get("role") or item.get("skill") or item)
            for item in (
                recruiter_preferences.get("top_roles")
                or recruiter_preferences.get("role_tokens")
                or []
            )
        ]
    )
    preferred_experience = _dedupe_preserve_order(
        [
            _normalize_text(item.get("experience_bucket") or item.get("bucket") or item)
            for item in (
                recruiter_preferences.get("top_experience")
                or recruiter_preferences.get("experience_tokens")
                or []
            )
        ]
    )
    preference_text = _normalize_text(recruiter_preferences.get("preference_text") or "")
    archetype = _normalize_text(recruiter_preferences.get("archetype") or "")

    role_terms = [role] if role else []
    if seniority and seniority.lower() not in role.lower():
        role_terms.insert(0, f"{seniority} {role}".strip())
    if seniority and "manager" in seniority.lower() and "manager" not in role.lower():
        role_terms.append(f"{seniority} {role}".strip())
    if preferred_roles:
        role_terms.extend(preferred_roles[:2])

    skill_terms = normalized_skills[:6]
    if preferred_skills:
        skill_terms = _dedupe_preserve_order([*skill_terms, *preferred_skills[:4]])[:6]
    stage_terms = [company_stage] if company_stage else []
    preference_terms = [term for term in [hiring_preferences, industry, leadership_expectations, preference_text, archetype] if term]
    location_terms = [location] if location else []
    if preferred_experience:
        stage_terms.extend(preferred_experience[:2])

    def _join_block(values: list[str]) -> str:
        cleaned = [f'"{value}"' if " " in value else value for value in values if value]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        return "(" + " OR ".join(cleaned) + ")"

    queries: list[str] = []
    base = ['site:linkedin.com/in/']
    if role_terms:
        base.append(_join_block(role_terms))
    if skill_terms:
        base.append(_join_block(skill_terms[:4]))
    if stage_terms:
        base.append(_join_block(stage_terms))
    if location_terms:
        base.append(_join_block(location_terms))
    if preference_terms:
        base.append(_join_block(preference_terms[:3]))
    queries.append(" ".join(part for part in base if part))

    if role_terms and skill_terms:
        queries.append(
            " ".join(
                part for part in [
                    'site:linkedin.com/in/',
                    _join_block(role_terms[:2]),
                    _join_block(skill_terms[2:6] or skill_terms[:3]),
                    _join_block(location_terms[:1]),
                ]
                if part
            )
        )

    if role_terms and leadership_expectations:
        queries.append(
            " ".join(
                part for part in [
                    'site:linkedin.com/in/',
                    _join_block(role_terms[:2]),
                    _join_block([leadership_expectations]),
                    _join_block(stage_terms),
                ]
                if part
            )
        )

    if industry and role_terms:
        queries.append(
            " ".join(
                part for part in [
                    'site:linkedin.com/in/',
                    _join_block(role_terms[:2]),
                    _join_block([industry]),
                    _join_block(location_terms),
                ]
                if part
            )
        )

    return _dedupe_preserve_order([query for query in queries if query.strip()])


class SerpApiClient:
    def __init__(self) -> None:
        self._session = requests.Session()

    def _respect_rate_limit(self) -> None:
        global _last_request_epoch

        interval = max(0.0, SERPAPI_MIN_REQUEST_INTERVAL_SECONDS)
        if interval <= 0:
            return
        with _request_lock:
            now = time.monotonic()
            wait_seconds = (_last_request_epoch + interval) - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            _last_request_epoch = time.monotonic()

    def _request(self, *, query: str, start: int = 0) -> dict[str, Any]:
        if not SERPAPI_ENABLED:
            return {}
        if _is_disabled():
            return {}

        api_key = SERPAPI_API_KEY.strip()
        if not api_key:
            _disable("SERPAPI_API_KEY missing", cooldown_seconds=300)
            return {}

        params = {
            "engine": SERPAPI_ENGINE or "google",
            "q": query,
            "api_key": api_key,
            "hl": "en",
            "gl": "us",
            "start": max(0, int(start)),
        }
        url = SERPAPI_URL or "https://serpapi.com/search.json"
        last_error: Exception | None = None
        for attempt in range(1, max(1, SERPAPI_RETRY_ATTEMPTS) + 1):
            self._respect_rate_limit()
            try:
                response = self._session.get(url, params=params, timeout=SERPAPI_REQUEST_TIMEOUT_SECONDS or HTTP_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "serpapi_request_failed query=%s start=%s attempt=%s error=%s",
                    query,
                    start,
                    attempt,
                    str(exc),
                )
                log_metric("error", source="serpapi", kind="request_exception")
                if attempt < SERPAPI_RETRY_ATTEMPTS:
                    time.sleep(min(2.0 * attempt, 6.0) + random.random() * 0.25)
                continue

            if response.status_code == 429:
                last_error = RuntimeError("http_429")
                logger.warning("serpapi_rate_limited query=%s start=%s attempt=%s", query, start, attempt)
                if attempt < SERPAPI_RETRY_ATTEMPTS:
                    time.sleep(min(3.0 * attempt, 10.0) + random.random() * 0.5)
                    continue
                _disable("http_429", cooldown_seconds=180)
                return {}

            if response.status_code in {401, 403}:
                _disable(f"http_{response.status_code}", cooldown_seconds=900)
                log_metric("error", source="serpapi", kind=f"http_{response.status_code}")
                logger.warning("serpapi_auth_failed status=%s", response.status_code)
                return {}

            if response.status_code >= 500:
                last_error = RuntimeError(f"http_{response.status_code}")
                logger.warning("serpapi_server_error query=%s start=%s status=%s attempt=%s", query, start, response.status_code, attempt)
                if attempt < SERPAPI_RETRY_ATTEMPTS:
                    time.sleep(min(1.5 * attempt, 5.0) + random.random() * 0.25)
                    continue
                _disable(f"http_{response.status_code}", cooldown_seconds=120)
                return {}

            if response.status_code != 200:
                _disable(f"http_{response.status_code}", cooldown_seconds=120)
                log_metric("error", source="serpapi", kind=f"http_{response.status_code}")
                logger.warning("serpapi_non_success status=%s query=%s start=%s", response.status_code, query, start)
                return {}

            try:
                payload = response.json()
            except ValueError as exc:
                last_error = exc
                logger.warning("serpapi_json_parse_failed query=%s start=%s error=%s", query, start, str(exc))
                log_metric("error", source="serpapi", kind="json_parse")
                if attempt < SERPAPI_RETRY_ATTEMPTS:
                    time.sleep(0.25 * attempt)
                    continue
                return {}

            if not isinstance(payload, dict):
                logger.warning("serpapi_invalid_payload_shape type=%s query=%s", type(payload).__name__, query)
                return {}

            search_metadata = payload.get("search_metadata") if isinstance(payload.get("search_metadata"), dict) else {}
            if search_metadata.get("status") and str(search_metadata.get("status")).lower() not in {"success", "cached"}:
                logger.warning(
                    "serpapi_non_success_status status=%s query=%s",
                    search_metadata.get("status"),
                    query,
                )

            return payload

        if last_error:
            logger.warning("serpapi_request_exhausted query=%s start=%s error=%s", query, start, str(last_error))
        return {}

    def search(self, *, query: str, pages: int = 1) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        query = _normalize_text(query)
        if not query:
            return results
        page_count = max(1, min(int(pages), SERPAPI_MAX_PAGES))
        for page in range(page_count):
            start = page * max(1, SERPAPI_RESULTS_PER_PAGE)
            payload = self._request(query=query, start=start)
            organic_results = payload.get("organic_results", []) if isinstance(payload, dict) else []
            if not isinstance(organic_results, list) or not organic_results:
                continue
            for item in organic_results:
                if isinstance(item, dict):
                    results.append(item)
            serpapi_pagination = payload.get("serpapi_pagination") if isinstance(payload, dict) else {}
            if page_count > 1 and not serpapi_pagination:
                continue
        return results

    def search_many(self, queries: list[str], *, pages: int = 1) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for query in _dedupe_preserve_order(queries):
            results.extend(self.search(query=query, pages=pages))
        return results


def _normalize_intake(job: Any, intake: dict[str, Any] | None = None) -> dict[str, str]:
    payload = intake if isinstance(intake, dict) else {}
    structured = getattr(job, "structured_data", None)
    if not isinstance(structured, dict):
        structured = {}

    def _field(*keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            value = getattr(job, key, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _list(*keys: str) -> list[str]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return _dedupe_preserve_order([str(item) for item in value if str(item).strip()])
            value = structured.get(key)
            if isinstance(value, list):
                return _dedupe_preserve_order([str(item) for item in value if str(item).strip()])
            value = getattr(job, key, None)
            if isinstance(value, list):
                return _dedupe_preserve_order([str(item) for item in value if str(item).strip()])
        return []

    return {
        "role_title": _field("role", "title", "job_title"),
        "seniority": _field("seniority", "experience_level", "experienceRequired", "experience_required"),
        "location": _field("location"),
        "company_stage": _field("company_stage", "stage", "team_stage", "startup_stage"),
        "hiring_preferences": _field("hiring_preferences", "preferences", "culture_fit", "hiring_priorities"),
        "industry": _field("industry"),
        "leadership_expectations": _field("leadership_expectations", "leadership", "leadership_style"),
        "skills": ", ".join(_list("skills", "skills_required")),
    }


def _score_result(*, query: str, result: dict[str, Any], page: int, position: int, intake: dict[str, str]) -> float:
    title = _normalize_text(result.get("title") or "")
    snippet = _normalize_text(result.get("snippet") or "")
    link = _normalize_text(result.get("link") or "")
    text = " ".join([title, snippet, link]).lower()
    score = 0.0

    if "linkedin.com/in/" in link.lower():
        score += 0.45
    if intake["role_title"] and intake["role_title"].lower() in text:
        score += 0.25
    if intake["location"] and intake["location"].lower() in text:
        score += 0.10
    if intake["seniority"] and intake["seniority"].lower() in text:
        score += 0.10
    if intake["skills"]:
        skill_tokens = [skill.strip() for skill in intake["skills"].split(",") if skill.strip()]
        score += min(0.20, 0.03 * len(_extract_skills_from_text(text, skill_tokens)))
    score += max(0.0, 0.10 - (0.02 * max(0, position - 1)))
    score += max(0.0, 0.04 - (0.01 * page))
    if query.lower() in text:
        score += 0.05
    return max(0.0, min(1.0, score))


def _normalize_candidate_result(*, result: dict[str, Any], query: str, page: int, position: int, intake: dict[str, str], source: str) -> dict[str, Any] | None:
    link = _normalize_text(result.get("link") or "")
    title = _normalize_text(result.get("title") or "")
    snippet = _normalize_text(result.get("snippet") or "")
    displayed_link = _normalize_text(result.get("displayed_link") or "")
    linkedin_url = _extract_linkedin_url(link)
    github_url = _extract_github_url(link)
    if not linkedin_url and not github_url:
        return None

    text = " ".join([title, snippet, displayed_link])
    name = _candidate_name_from_title(title) or _candidate_name_from_title(displayed_link) or _candidate_name_from_title(snippet)
    company = _extract_company(snippet)
    location = _extract_location(snippet, fallback=intake.get("location", ""))
    skills = _extract_skills_from_text(text, [skill.strip() for skill in intake.get("skills", "").split(",") if skill.strip()])
    github_signals = []
    if github_url:
        github_signals.append({"url": github_url, "title": title, "snippet": snippet})

    normalized = {
        "id": linkedin_url or github_url or link,
        "full_name": name or title or "Unknown Candidate",
        "name": name or title or "Unknown Candidate",
        "job_title": _normalize_text(intake.get("role_title") or title),
        "title": _normalize_text(intake.get("role_title") or title),
        "job_company_name": company,
        "company": company,
        "location": location,
        "skills": skills,
        "summary": snippet or title,
        "experience": _normalize_text(intake.get("seniority") or ""),
        "linkedin_url": linkedin_url,
        "github_url": github_url,
        "github_signals": github_signals,
        "source": source,
        "source_type": "linkedin_xray",
        "search_query": query,
        "search_page": page,
        "search_position": position,
        "snippet": snippet,
        "displayed_link": displayed_link,
        "score": _score_result(query=query, result=result, page=page, position=position, intake=intake),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "raw_discovery": {
            "query": query,
            "page": page,
            "position": position,
            "title": title,
            "link": link,
            "snippet": snippet,
            "displayed_link": displayed_link,
            "source": source,
        },
    }
    return normalized


def _attach_github_signals(candidates: list[dict[str, Any]], *, intake: dict[str, str], client: SerpApiClient) -> None:
    if not candidates or not _is_technical_role(intake.get("role_title", ""), [skill.strip() for skill in intake.get("skills", "").split(",") if skill.strip()]):
        return

    unique_candidates: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for candidate in candidates:
        name = _normalize_lower(candidate.get("full_name") or candidate.get("name") or "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        unique_candidates.append(candidate)
        if len(unique_candidates) >= 5:
            break

    for candidate in unique_candidates:
        name = _normalize_text(candidate.get("full_name") or candidate.get("name") or "")
        if not name:
            continue
        skill_terms = [skill.strip() for skill in intake.get("skills", "").split(",") if skill.strip()]
        github_query_parts = [
            'site:github.com/',
            f'"{name}"',
        ]
        if skill_terms:
            github_query_parts.append(f'("{skill_terms[0]}" OR "{skill_terms[1]}")' if len(skill_terms) > 1 else f'"{skill_terms[0]}"')
        github_query = " ".join(part for part in github_query_parts if part)
        github_results = client.search(query=github_query, pages=1)
        for result in github_results:
            link = _normalize_text(result.get("link") or "")
            github_url = _extract_github_url(link)
            if not github_url:
                continue
            candidate["github_url"] = github_url
            signals = list(candidate.get("github_signals") or [])
            signals.append(
                {
                    "url": github_url,
                    "title": _normalize_text(result.get("title") or ""),
                    "snippet": _normalize_text(result.get("snippet") or ""),
                    "query": github_query,
                }
            )
            candidate["github_signals"] = signals
            break


def discover_linkedin_xray_candidates(
    *,
    job: Any,
    intake: dict[str, Any] | None = None,
    limit: int = 10,
    pages_per_query: int = 1,
    recruiter_preferences: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not SERPAPI_ENABLED:
        logger.info("serpapi_discovery_skipped reason=feature_disabled")
        return []
    client = SerpApiClient()
    if is_serpapi_disabled():
        logger.info("serpapi_discovery_skipped reason=service_disabled")
        return []
    if not SERPAPI_API_KEY.strip():
        logger.info("serpapi_discovery_skipped reason=missing_api_key")
        return []

    resolved_intake = _normalize_intake(job, intake)
    query_batches = build_linkedin_xray_queries(
        role=resolved_intake["role_title"],
        seniority=resolved_intake["seniority"],
        skills=[skill.strip() for skill in resolved_intake["skills"].split(",") if skill.strip()],
        location=resolved_intake["location"],
        company_stage=resolved_intake["company_stage"],
        hiring_preferences=resolved_intake["hiring_preferences"],
        industry=resolved_intake["industry"],
        leadership_expectations=resolved_intake["leadership_expectations"],
        recruiter_preferences=recruiter_preferences,
    )

    logger.info(
        "serpapi_discovery_started role=%s location=%s queries=%s limit=%s",
        resolved_intake["role_title"],
        resolved_intake["location"],
        len(query_batches),
        limit,
    )
    log_metric("serpapi_usage", enabled=True, query_batches=len(query_batches), limit=limit)

    raw_results = client.search_many(query_batches, pages=pages_per_query)
    normalized_results: list[dict[str, Any]] = []
    seen_identities: set[str] = set()

    for position, result in enumerate(raw_results, start=1):
        normalized = _normalize_candidate_result(
            result=result,
            query=query_batches[min(len(query_batches) - 1, (position - 1) % max(1, len(query_batches)))],
            page=((position - 1) // max(1, SERPAPI_RESULTS_PER_PAGE)) + 1,
            position=((position - 1) % max(1, SERPAPI_RESULTS_PER_PAGE)) + 1,
            intake=resolved_intake,
            source="serpapi",
        )
        if not normalized:
            continue
        identity = _normalize_lower(normalized.get("linkedin_url") or normalized.get("github_url") or normalized.get("full_name") or normalized.get("name") or "")
        if not identity or identity in seen_identities:
            continue
        seen_identities.add(identity)
        normalized_results.append(normalized)
        if len(normalized_results) >= max(1, limit):
            break

    _attach_github_signals(normalized_results, intake=resolved_intake, client=client)

    logger.info(
        "serpapi_discovery_completed role=%s count=%s",
        resolved_intake["role_title"],
        len(normalized_results),
    )
    log_metric("serpapi_candidates_found", count=len(normalized_results), role=resolved_intake["role_title"])
    return normalized_results
