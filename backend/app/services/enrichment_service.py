from __future__ import annotations

import logging
import re
from typing import Any

import requests

from app.core.config import HTTP_TIMEOUT_SECONDS, PROXYCURL_API_KEY, PROXYCURL_URL

logger = logging.getLogger(__name__)


def _get_value(candidate: Any, key: str, default: Any = None) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


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


def _merge_unique(existing: list[str], incoming: list[str], *, limit: int = 30) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
        if len(merged) >= limit:
            break
    return merged


def _candidate_raw(candidate: Any) -> dict[str, Any]:
    raw = _get_value(candidate, "raw_data", {}) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _candidate_email(candidate: Any) -> str:
    direct = _normalize_text(_get_value(candidate, "email"))
    if direct:
        return direct

    raw_data = _candidate_raw(candidate)
    for key in ("work_email", "email", "personal_email"):
        value = _normalize_text(raw_data.get(key))
        if value:
            return value
    return ""


def _extract_linkedin_url(candidate: Any) -> str:
    raw_data = _candidate_raw(candidate)
    for key in ("linkedin", "linkedin_url", "linkedinUrl", "profile_url"):
        value = _normalize_text(raw_data.get(key))
        if "linkedin.com" in value.lower():
            return value
    return ""


def _extract_github_username(candidate: Any) -> str:
    raw_data = _candidate_raw(candidate)
    for key in ("github", "github_url", "githubUrl", "github_username", "githubUsername"):
        value = _normalize_text(raw_data.get(key))
        if not value:
            continue
        if "github.com" in value.lower():
            path = value.rstrip("/").split("github.com/")[-1]
            return path.split("/")[0].strip("@")
        return value.lstrip("@")
    return ""


def fetch_from_linkedin(candidate: Any) -> dict[str, Any] | None:
    linkedin_url = _extract_linkedin_url(candidate)
    if not linkedin_url or not (PROXYCURL_API_KEY or "").strip():
        return None

    try:
        response = requests.get(
            PROXYCURL_URL,
            params={"linkedin_profile_url": linkedin_url},
            headers={"Authorization": f"Bearer {PROXYCURL_API_KEY}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.info("candidate_linkedin_enrichment_unavailable status=%s", response.status_code)
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        return {
            "source": "linkedin",
            "skills": _normalize_list(payload.get("skills") or payload.get("headline") or []),
            "experience": _normalize_text(payload.get("summary") or payload.get("headline") or ""),
            "summary": _normalize_text(payload.get("summary") or payload.get("headline") or ""),
        }
    except Exception as exc:
        logger.info("candidate_linkedin_enrichment_unavailable error=%s", str(exc))
        return None


def fetch_from_github(candidate: Any) -> dict[str, Any] | None:
    username = _extract_github_username(candidate)
    if not username:
        return None

    try:
        profile_resp = requests.get(f"https://api.github.com/users/{username}", timeout=HTTP_TIMEOUT_SECONDS)
        if profile_resp.status_code != 200:
            logger.info("candidate_github_enrichment_unavailable status=%s", profile_resp.status_code)
            return None
        profile = profile_resp.json()
        if not isinstance(profile, dict):
            return None

        repos_resp = requests.get(
            f"https://api.github.com/users/{username}/repos",
            params={"per_page": 10, "sort": "updated"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        repos = repos_resp.json() if repos_resp.status_code == 200 else []
        repo_items = repos if isinstance(repos, list) else []
        repo_names = [str(repo.get("name") or "").strip() for repo in repo_items if isinstance(repo, dict)]
        languages = [str(repo.get("language") or "").strip() for repo in repo_items if isinstance(repo, dict)]
        skills = _merge_unique(
            _normalize_list(languages),
            _normalize_list(
                [profile.get("bio") or "", profile.get("company") or "", *repo_names[:5]]
            ),
        )
        summary_parts = [
            _normalize_text(profile.get("bio") or ""),
            f"Public repos: {profile.get('public_repos', 0)}" if profile.get("public_repos") is not None else "",
            f"Followers: {profile.get('followers', 0)}" if profile.get("followers") is not None else "",
        ]
        summary = ". ".join(part for part in summary_parts if part)
        created_at = _normalize_text(profile.get("created_at") or "")
        experience = f"GitHub activity from {created_at[:4] or 'public'}"
        return {
            "source": "github",
            "skills": skills,
            "experience": experience,
            "summary": summary,
        }
    except Exception as exc:
        logger.info("candidate_github_enrichment_unavailable error=%s", str(exc))
        return None


def enrich_candidate(candidate: Any) -> bool:
    logger.info(
        "candidate_enrichment_started candidate_id=%s job_id=%s",
        _normalize_text(_get_value(candidate, "candidate_id") or _get_value(candidate, "id")),
        _normalize_text(_get_value(candidate, "job_id")),
    )

    raw_data = _candidate_raw(candidate)
    existing_skills = _normalize_list(_get_value(candidate, "skills", []) or raw_data.get("skills") or [])
    existing_summary = _normalize_text(_get_value(candidate, "summary") or raw_data.get("summary") or "")
    existing_experience = _normalize_text(
        raw_data.get("experience")
        or raw_data.get("experience_level")
        or raw_data.get("years_experience")
        or ""
    )

    linkedin_data = fetch_from_linkedin(candidate)
    github_data = fetch_from_github(candidate)
    sources = [data for data in [linkedin_data, github_data] if isinstance(data, dict)]

    if not sources:
        logger.info(
            "candidate_enrichment_skipped candidate_id=%s job_id=%s reason=no_sources",
            _normalize_text(_get_value(candidate, "candidate_id") or _get_value(candidate, "id")),
            _normalize_text(_get_value(candidate, "job_id")),
        )
        return False

    merged_skills = existing_skills
    merged_summary = existing_summary
    merged_experience = existing_experience

    for source in sources:
        merged_skills = _merge_unique(merged_skills, source.get("skills") or [])
        candidate_summary = _normalize_text(source.get("summary") or "")
        if candidate_summary and candidate_summary not in merged_summary:
            merged_summary = merged_summary or candidate_summary
        candidate_experience = _normalize_text(source.get("experience") or "")
        if candidate_experience and not merged_experience:
            merged_experience = candidate_experience

    if not merged_skills and not merged_summary and not merged_experience:
        logger.info(
            "candidate_enrichment_skipped candidate_id=%s job_id=%s reason=empty_merged_result",
            _normalize_text(_get_value(candidate, "candidate_id") or _get_value(candidate, "id")),
            _normalize_text(_get_value(candidate, "job_id")),
        )
        return False

    updated_raw = dict(raw_data)
    enrichment = dict(updated_raw.get("enrichment") or {})
    enrichment["sources"] = [source.get("source", "") for source in sources if source.get("source")]
    updated_raw["enrichment"] = enrichment
    if merged_experience:
        updated_raw["experience"] = merged_experience
        updated_raw["experience_level"] = merged_experience
    if merged_summary and not existing_summary:
        updated_raw["summary"] = merged_summary
    if merged_skills:
        updated_raw["skills"] = merged_skills

    if merged_skills:
        candidate.skills = merged_skills
    if merged_summary:
        candidate.summary = merged_summary
    if merged_experience:
        candidate.raw_data = updated_raw
    else:
        candidate.raw_data = updated_raw

    logger.info(
        "candidate_enrichment_success candidate_id=%s job_id=%s sources=%s skills=%s",
        _normalize_text(_get_value(candidate, "candidate_id") or _get_value(candidate, "id")),
        _normalize_text(_get_value(candidate, "job_id")),
        ",".join(enrichment["sources"]) or "none",
        len(merged_skills),
    )
    return True
