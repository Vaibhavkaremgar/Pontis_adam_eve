from __future__ import annotations

import json
import hashlib
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.config import (
    DAILY_SERPAPI_BUDGET,
    HTTP_TIMEOUT_SECONDS,
    MAX_CALLS_PER_ROLE,
    MAX_TOTAL_PROFILES,
    LOCAL_DEV_MODE,
    MOCK_XRAY_MODE,
    SERPAPI_DEBUG,
    SERPAPI_DEBUG_LOG_DIR,
    SERPAPI_API_KEY,
    SERPAPI_ENABLED,
    SERPAPI_ENGINE,
    SERPAPI_MAX_PAGES_PER_LAYER,
    SERPAPI_QUERY_FINGERPRINT_COOLDOWN_SECONDS,
    SERPAPI_MIN_REQUEST_INTERVAL_SECONDS,
    SERPAPI_REQUEST_TIMEOUT_SECONDS,
    SERPAPI_RETRY_ATTEMPTS,
    SERPAPI_RESULTS_PER_PAGE,
    SERPAPI_URL,
)
from app.services.persistent_cache_service import get_json as cache_get_json, set_json as cache_set_json
from app.services.metrics_service import log_metric
from app.db.repositories import CandidateProfileRepository

logger = logging.getLogger(__name__)

_request_lock = threading.Lock()
_last_request_epoch = 0.0
_serpapi_disabled_until: datetime | None = None
_serpapi_disable_reason = ""
_request_count_lock = threading.Lock()
_request_total_hits = 0
_quota_lock = threading.Lock()
_quota_day = ""
_quota_used_calls = 0
_quota_used_profiles = 0
_quota_budget = max(0, DAILY_SERPAPI_BUDGET)
_query_fingerprint_lock = threading.Lock()
_query_fingerprint_last_seen: dict[str, float] = {}
_debug_write_lock = threading.Lock()
_XRAY_ROLE_CACHE_NAMESPACE = "serpapi_xray_role_results"
_XRAY_ROLE_CACHE_VERSION = "v1"

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

_ROLE_KEYWORDS = {
    "engineer",
    "developer",
    "architect",
    "platform",
    "backend",
    "frontend",
    "full stack",
    "fullstack",
    "infra",
    "infrastructure",
    "systems",
    "staff",
    "senior",
    "principal",
    "lead",
    "manager",
    "ml",
    "machine learning",
    "data",
    "product",
    "security",
    "devops",
    "cloud",
}

_COMPANY_CLUSTER_LIBRARY: dict[str, list[str]] = {
    "payments": ["Stripe", "Razorpay", "PhonePe", "Adyen", "PayPal"],
    "developer_infrastructure": ["Datadog", "HashiCorp", "Cloudflare", "Vercel", "Grafana"],
    "ai_infrastructure": ["OpenAI", "Anthropic", "Scale AI", "Hugging Face", "Cohere"],
    "security": ["CrowdStrike", "Zscaler", "Wiz", "SentinelOne", "Snyk"],
    "data": ["Snowflake", "Databricks", "Confluent", "dbt Labs", "Fivetran"],
    "sales": ["Salesforce", "HubSpot", "Gong", "ZoomInfo", "Gainsight"],
    "enterprise": ["ServiceNow", "SAP", "Oracle", "Workday", "Atlassian"],
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


@dataclass(frozen=True)
class XRayQueryLayer:
    layer_type: str
    query: str
    enabled: bool = True
    pages: int = 1


@dataclass(frozen=True)
class SerpQuotaSnapshot:
    date: str
    used_calls: int
    used_profiles: int
    budget: int


def _serpapi_debug_enabled() -> bool:
    return bool(SERPAPI_DEBUG and LOCAL_DEV_MODE)


def _debug_log_dir() -> Path:
    return Path(SERPAPI_DEBUG_LOG_DIR or "backend/debug_logs/serpapi")


def _write_debug_artifact(filename: str, payload: Any) -> None:
    if not _serpapi_debug_enabled():
        return
    target_dir = _debug_log_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        with _debug_write_lock:
            target_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("serpapi_debug_write_failed file=%s error=%s", filename, str(exc))


def _log_structured(event: str, **fields: Any) -> None:
    ordered = " ".join(f"{key}={fields[key]}" for key in fields)
    logger.info("[%s] %s", event, ordered)


def _query_fingerprint(*, layer_type: str, query: str, page: int, num_requested: int, search_engine: str) -> str:
    material = "|".join(
        [
            _normalize_lower(layer_type),
            _normalize_lower(query),
            str(int(page)),
            str(int(num_requested)),
            _normalize_lower(search_engine),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_duplicate_query(*, fingerprint: str) -> bool:
    if not fingerprint:
        return False
    now = time.time()
    cooldown = max(1, int(SERPAPI_QUERY_FINGERPRINT_COOLDOWN_SECONDS))
    with _query_fingerprint_lock:
        last_seen = _query_fingerprint_last_seen.get(fingerprint)
        if last_seen is not None and (now - last_seen) < cooldown:
            return True
        _query_fingerprint_last_seen[fingerprint] = now
    return False


def _utc_day_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _reset_quota_if_needed() -> None:
    global _quota_day, _quota_used_calls, _quota_used_profiles, _quota_budget
    day = _utc_day_key()
    if _quota_day != day:
        _quota_day = day
        _quota_used_calls = 0
        _quota_used_profiles = 0
        _quota_budget = max(0, DAILY_SERPAPI_BUDGET)


def _quota_snapshot() -> SerpQuotaSnapshot:
    with _quota_lock:
        _reset_quota_if_needed()
        return SerpQuotaSnapshot(
            date=_quota_day,
            used_calls=_quota_used_calls,
            used_profiles=_quota_used_profiles,
            budget=_quota_budget,
        )


def _serpapi_request_total() -> int:
    with _request_count_lock:
        return _request_total_hits


def _record_serpapi_request_hit() -> None:
    global _request_total_hits
    with _request_count_lock:
        _request_total_hits += 1


def _reserve_serpapi_call(*, role: str, layer_type: str, query: str) -> bool:
    global _quota_used_calls, _quota_budget

    with _quota_lock:
        _reset_quota_if_needed()
        if _quota_budget <= 0:
            logger.warning(
                "serpapi_quota_exhausted reason=daily_budget_exhausted date=%s role=%s layer_type=%s",
                _quota_day,
                role,
                layer_type,
            )
            log_metric(
                "serpapi_quota_exhausted",
                date=_quota_day,
                role=role,
                layer_type=layer_type,
                used_calls=_quota_used_calls,
                used_profiles=_quota_used_profiles,
                budget=DAILY_SERPAPI_BUDGET,
            )
            return False
        _quota_used_calls += 1
        _quota_budget -= 1
        logger.info(
            "serpapi_quota_reserved date=%s role=%s layer_type=%s used_calls=%s remaining_budget=%s",
            _quota_day,
            role,
            layer_type,
            _quota_used_calls,
            _quota_budget,
        )
        log_metric(
            "serpapi_quota_usage",
            date=_quota_day,
            role=role,
            layer_type=layer_type,
            query=query,
            used_calls=_quota_used_calls,
            used_profiles=_quota_used_profiles,
            budget=DAILY_SERPAPI_BUDGET,
        )
        return True


def _register_profiles_found(*, count: int) -> None:
    global _quota_used_profiles
    if count <= 0:
        return
    with _quota_lock:
        _reset_quota_if_needed()
        _quota_used_profiles += count


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


def _tokenize_query_terms(value: str) -> list[str]:
    tokens = [token for token in re.split(r"[^a-z0-9+.#-]+", _normalize_lower(value)) if token]
    stopwords = {
        "site",
        "linkedin",
        "in",
        "com",
        "profile",
        "profiles",
        "jobs",
        "people",
        "and",
        "or",
        "the",
        "for",
        "with",
        "at",
    }
    return [token for token in tokens if token not in stopwords]


def _query_overlap_ratio(queries: list[str]) -> float:
    token_sets = [set(_tokenize_query_terms(query)) for query in queries if query]
    if len(token_sets) < 2:
        return 0.0
    overlap_total = 0
    pair_total = 0
    for index, left in enumerate(token_sets):
        for right in token_sets[index + 1 :]:
            pair_total += 1
            overlap_total += len(left.intersection(right))
    if pair_total <= 0:
        return 0.0
    return min(1.0, overlap_total / float(pair_total * 10 or 1))


def _infer_company_cluster(*, role: str, skills: list[str], recruiter_preferences: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    preference_text = _normalize_lower((recruiter_preferences or {}).get("preference_text") or "")
    selected_companies = _dedupe_preserve_order(
        [
            *[str(item).strip() for item in ((recruiter_preferences or {}).get("preferred_companies") or []) if str(item).strip()],
            *[str(item).strip() for item in ((recruiter_preferences or {}).get("preferredCompanies") or []) if str(item).strip()],
            *[str(item).strip() for item in ((recruiter_preferences or {}).get("selectedCompanies") or []) if str(item).strip()],
            *[str(item).strip() for item in ((recruiter_preferences or {}).get("topCompanies") or []) if str(item).strip()],
        ]
    )
    domain_tokens = _dedupe_preserve_order(
        [
            *[str(item).strip() for item in ((recruiter_preferences or {}).get("preferred_domains") or []) if str(item).strip()],
            *[str(item).strip() for item in ((recruiter_preferences or {}).get("preferredDomains") or []) if str(item).strip()],
            *[str(item).strip() for item in ((recruiter_preferences or {}).get("selectedDomains") or []) if str(item).strip()],
            *[str(item).strip() for item in ((recruiter_preferences or {}).get("topDomains") or []) if str(item).strip()],
        ]
    )
    text = " ".join([role, " ".join(skills), preference_text, " ".join(selected_companies), " ".join(domain_tokens)]).lower()
    if any(token in text for token in ("payment", "fintech", "settlement", "upi", "wallet", "fraud", "risk")):
        return "payments", selected_companies or _COMPANY_CLUSTER_LIBRARY["payments"]
    if any(token in text for token in ("ai", "ml", "machine learning", "retrieval", "ranking", "llm", "model", "inference")):
        return "ai_infrastructure", selected_companies or _COMPANY_CLUSTER_LIBRARY["ai_infrastructure"]
    if any(token in text for token in ("infra", "platform", "observability", "cloud", "sre", "devops", "distributed")):
        return "developer_infrastructure", selected_companies or _COMPANY_CLUSTER_LIBRARY["developer_infrastructure"]
    if any(token in text for token in ("security", "identity", "auth", "iam", "appsec", "threat")):
        return "security", selected_companies or _COMPANY_CLUSTER_LIBRARY["security"]
    if any(token in text for token in ("data", "analytics", "warehouse", "etl", "pipeline", "lakehouse")):
        return "data", selected_companies or _COMPANY_CLUSTER_LIBRARY["data"]
    if any(token in text for token in ("sales", "revenue", "bdr", "sdr", "revops", "gtm", "pipeline")):
        return "sales", selected_companies or _COMPANY_CLUSTER_LIBRARY["sales"]
    if any(token in text for token in ("enterprise", "saas", "erp", "crm", "workflow", "it")):
        return "enterprise", selected_companies or _COMPANY_CLUSTER_LIBRARY["enterprise"]
    return "general", selected_companies or []


def _diversify_query_layer_query(query: str, anchors: list[str]) -> str:
    cleaned = _normalize_text(query)
    if not cleaned or not anchors:
        return cleaned
    lower_query = cleaned.lower()
    for anchor in anchors:
        token = _normalize_text(anchor)
        if token and token.lower() in lower_query:
            return cleaned
    selected = anchors[:2]
    anchor_phrase = " OR ".join(f'"{anchor}"' for anchor in selected if anchor)
    if not anchor_phrase:
        return cleaned
    return f"{cleaned} ({anchor_phrase})"


def _query_diversity_report(*, layers: list[XRayQueryLayer], recruiter_preferences: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled_queries = [layer.query for layer in layers if layer.enabled and layer.query]
    overlap_ratio = _query_overlap_ratio(enabled_queries)
    duplicate_query_count = max(0, len(enabled_queries) - len(set(enabled_queries)))
    token_sets = [set(_tokenize_query_terms(query)) for query in enabled_queries]
    duplicate_tokens = sum(len(left.intersection(right)) for index, left in enumerate(token_sets) for right in token_sets[index + 1 :])
    cluster_name, anchors = _infer_company_cluster(
        role=" ".join(enabled_queries[:1]),
        skills=_dedupe_preserve_order(_tokenize_query_terms(" ".join(enabled_queries)))[:12],
        recruiter_preferences=recruiter_preferences,
    )
    company_concentration = sum(1 for query in enabled_queries if any(anchor.lower() in query.lower() for anchor in anchors[:3]))
    title_tokens = {token for token in _tokenize_query_terms(" ".join(enabled_queries)) if token in {"engineer", "developer", "architect", "manager", "director", "lead", "principal", "staff", "senior", "vp", "head"}}
    seniority_tokens = sum(1 for query in enabled_queries if any(token in query.lower() for token in ("senior", "principal", "staff", "lead", "director", "vp", "head", "manager")))
    return {
        "overlap_ratio": round(overlap_ratio, 4),
        "duplicate_query_count": duplicate_query_count,
        "duplicate_token_count": duplicate_tokens,
        "company_concentration": company_concentration,
        "title_token_count": len(title_tokens),
        "seniority_token_count": seniority_tokens,
        "cluster_name": cluster_name,
        "anchors": anchors,
    }


def _diversify_query_layers(*, layers: list[XRayQueryLayer], recruiter_preferences: dict[str, Any] | None = None) -> list[XRayQueryLayer]:
    report = _query_diversity_report(layers=layers, recruiter_preferences=recruiter_preferences)
    if report["overlap_ratio"] < 0.35 and report["company_concentration"] <= 1:
        return layers

    cluster_name, anchors = _infer_company_cluster(
        role=" ".join(layer.query for layer in layers[:1]),
        skills=_dedupe_preserve_order(_tokenize_query_terms(" ".join(layer.query for layer in layers))),
        recruiter_preferences=recruiter_preferences,
    )
    diversified: list[XRayQueryLayer] = []
    used_anchors: set[str] = set()
    for index, layer in enumerate(layers):
        updated = layer
        if index >= 2 and layer.enabled and layer.query:
            remaining_anchors = [anchor for anchor in anchors if anchor.lower() not in used_anchors]
            if remaining_anchors:
                updated_query = _diversify_query_layer_query(layer.query, remaining_anchors)
                if updated_query != layer.query:
                    updated = XRayQueryLayer(layer_type=layer.layer_type, query=updated_query, enabled=layer.enabled, pages=layer.pages)
                    used_anchors.update(anchor.lower() for anchor in remaining_anchors[:2])
        diversified.append(updated)

    logger.info(
        "serpapi_query_diversity_adjusted cluster=%s overlap_ratio=%.4f duplicate_queries=%s company_concentration=%s title_tokens=%s seniority_tokens=%s",
        cluster_name,
        report["overlap_ratio"],
        report["duplicate_query_count"],
        report["company_concentration"],
        report["title_token_count"],
        report["seniority_token_count"],
    )
    log_metric(
        "serpapi_query_diversity",
        cluster_name=cluster_name,
        overlap_ratio=report["overlap_ratio"],
        duplicate_queries=report["duplicate_query_count"],
        duplicate_tokens=report["duplicate_token_count"],
        company_concentration=report["company_concentration"],
        title_tokens=report["title_token_count"],
        seniority_tokens=report["seniority_token_count"],
    )
    return diversified


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

    if LOCAL_DEV_MODE:
        cooldown_seconds = min(max(1, cooldown_seconds), 30)
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


def _candidate_role_from_text(*values: str) -> str:
    for value in values:
        cleaned = _normalize_text(value)
        if not cleaned:
            continue
        parts = [part.strip() for part in re.split(r"\s*[|•–—-]\s*", cleaned) if part.strip()]
        for part in parts:
            lowered = part.lower()
            if "linkedin" in lowered or lowered.startswith("http"):
                continue
            if any(keyword in lowered for keyword in _ROLE_KEYWORDS):
                return part
        if len(parts) >= 2:
            for part in parts[1:]:
                lowered = part.lower()
                if "linkedin" in lowered or lowered.startswith("http"):
                    continue
                return part
    return ""


def _extract_linkedin_url(link: str) -> str:
    url = _normalize_text(link)
    lowered = url.lower()
    if "linkedin.com/" not in lowered:
        return ""
    if "/in/" not in lowered:
        return ""
    if "/jobs/" in lowered:
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


def _sanitize_role_query(value: str) -> str:
    cleaned = _normalize_text(value)
    if not cleaned:
        return ""

    cleaned = re.sub(r"^\s*\d+\s*[-–—]?\s*\d*\+?\s*(?:years?|yrs?|yr)\b[:,-]?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:senior|jr|junior|mid|lead|principal|staff)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\(?\d+\s*[-–—]?\s*\d*\+?\s*(?:years?|yrs?|yr)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-/")
    return cleaned


_GOOGLE_XRAY_NEGATIVE_FILTERS = [
    "-jobs",
    "-hiring",
]

_FRONTEND_MARKERS = ("frontend", "ui", "ux", "html", "css", "javascript", "typescript", "react", "next.js", "vue", "angular", "svelte")
_BACKEND_MARKERS = ("backend", "api", "fastapi", "django", "flask", "node", "express", "rest", "microservice", "microservices", "python", "go", "java")
_DATA_MARKERS = ("data", "analytics", "etl", "pipeline", "pipelines", "warehouse", "snowflake", "dbt", "databricks")
_FULLSTACK_MARKERS = ("full stack", "fullstack", "full-stack")


def _quote_query_term(value: str) -> str:
    cleaned = _normalize_text(value)
    if not cleaned:
        return ""
    return f'"{cleaned}"' if " " in cleaned else cleaned.lower()


def _or_group(values: list[str]) -> str:
    items = [_quote_query_term(value) for value in values if _normalize_text(value)]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return "(" + " OR ".join(items) + ")"


def _space_group(values: list[str]) -> str:
    items = [_normalize_text(value) for value in values if _normalize_text(value)]
    return " ".join(items).strip()


def _negative_filters_clause() -> str:
    return " ".join(_GOOGLE_XRAY_NEGATIVE_FILTERS)


def _experience_hints_for_query(seniority: str) -> list[str]:
    text = _normalize_lower(seniority)
    if not text:
        return []
    match = re.search(r"(\d+)\s*\+?\s*(?:years?|yrs?|yr)", text)
    if match:
        years = match.group(1)
        return [f"{years} years", f"{years}+ years"]
    if any(token in text for token in ("junior", "entry", "associate", "graduate", "trainee", "intern")):
        return ["junior", "associate", "2 years", "2+ years"]
    if any(token in text for token in ("mid", "intermediate", "regular")):
        return ["mid-level", "3 years", "3+ years"]
    if any(token in text for token in ("senior", "sr", "lead", "principal", "staff")):
        return ["senior", "5 years", "5+ years"]
    return []


def _role_family_for_query(role: str, skills: list[str]) -> str:
    text = " ".join([role, " ".join(skills)]).lower()
    has_frontend = any(marker in text for marker in _FRONTEND_MARKERS)
    has_backend = any(marker in text for marker in _BACKEND_MARKERS)
    has_data = any(marker in text for marker in _DATA_MARKERS)
    if any(marker in text for marker in _FULLSTACK_MARKERS):
        return "fullstack"
    if has_data:
        return "data"
    if has_frontend and has_backend:
        if any(token in text for token in ("python", "django", "fastapi", "flask", "api", "backend")):
            return "backend"
        return "fullstack"
    if has_backend or any(token in text for token in ("python", "django", "fastapi", "flask", "api")):
        return "backend"
    if has_frontend:
        return "frontend"
    return "generic"


def _role_variants_for_query(*, role: str, seniority: str, skills: list[str]) -> list[str]:
    role_text = _normalize_lower(role)
    skill_text = " ".join(skills).lower()
    family = _role_family_for_query(role=role, skills=skills)

    variants: list[str] = []
    if family == "frontend":
        variants = [
            "frontend developer",
            "ui developer",
            "frontend ui developer",
            "react frontend developer",
            "ui engineer",
        ]
    elif family == "fullstack":
        variants = [
            "full stack developer",
            "full stack engineer",
            "software engineer",
            "web developer",
            "frontend backend developer",
        ]
    elif family == "data":
        variants = [
            "data engineer",
            "analytics engineer",
            "data platform engineer",
            "software engineer",
            "python developer",
        ]
    elif family == "backend":
        if "python" in role_text or "python" in skill_text:
            variants = [
                "python developer",
                "python backend developer",
                "backend engineer",
                "software engineer",
                "django developer",
                "fastapi developer",
            ]
        elif any(token in role_text or token in skill_text for token in ("node", "javascript", "typescript")):
            variants = [
                "backend engineer",
                "node backend developer",
                "software engineer",
                "api developer",
                "express developer",
            ]
        else:
            variants = [
                "backend engineer",
                "software engineer",
                "api developer",
                "platform engineer",
                "developer",
            ]
    else:
        base_role = _sanitize_role_query(role) or _sanitize_role_query(seniority) or "software engineer"
        variants = [
            base_role,
            f"{base_role} developer",
            f"{base_role} engineer",
            "software engineer",
        ]

    cleaned_variants: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        normalized = _sanitize_role_query(variant) or _normalize_text(variant)
        key = _normalize_lower(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned_variants.append(normalized)
    return cleaned_variants[:6]


def _core_skills_for_query(skills: list[str], *, family: str) -> list[str]:
    core = _dedupe_preserve_order([_normalize_text(skill) for skill in skills if _normalize_text(skill)])
    lowered = {_normalize_lower(skill) for skill in core}

    def add(*items: str) -> None:
        for item in items:
            normalized = _normalize_text(item)
            key = _normalize_lower(normalized)
            if normalized and key not in lowered:
                core.append(normalized)
                lowered.add(key)

    if family in {"backend", "fullstack"}:
        if any(token in lowered for token in {"python", "django", "fastapi", "flask", "api"}):
            add("REST API", "backend", "microservices", "authentication")
        if any(token in lowered for token in {"mongodb", "postgres", "mysql", "sql"}):
            add("database", "api integration")
    if family in {"frontend", "fullstack"}:
        if any(token in lowered for token in {"html", "css", "javascript", "typescript", "react"}):
            add("responsive UI", "web app", "dashboards", "component development")
    if family == "data":
        add("data pipelines", "dashboards", "analytics", "ETL")

    return core[:8]


def _project_terms_for_query(*, family: str, skills: list[str]) -> list[str]:
    lowered = {_normalize_lower(skill) for skill in skills}
    project_terms: list[str] = []

    if family in {"backend", "fullstack"} or any(token in lowered for token in {"python", "django", "fastapi", "flask", "api", "node", "express"}):
        project_terms.extend(["rest api", "backend", "microservices", "authentication", "internal tools"])
    if family in {"frontend", "fullstack"} or any(token in lowered for token in {"html", "css", "javascript", "typescript", "react", "ui", "ux"}):
        project_terms.extend(["responsive ui", "dashboard", "admin panel", "web app", "components"])
    if family == "data" or any(token in lowered for token in {"data", "analytics", "etl", "pipeline"}):
        project_terms.extend(["data pipelines", "analytics", "dashboards", "etl"])

    if not project_terms:
        project_terms.extend(["project", "web app", "product engineering"])

    cleaned_terms: list[str] = []
    seen: set[str] = set()
    for term in project_terms:
        normalized = _normalize_text(term)
        key = _normalize_lower(normalized)
        if not normalized or key in seen:
            continue
        seen.add(key)
        cleaned_terms.append(normalized)
    return cleaned_terms[:8]


def _build_google_xray_query_strategy(
    *,
    role: str,
    seniority: str,
    skills: list[str],
    location: str,
    company_stage: str,
    hiring_preferences: str,
    industry: str,
    leadership_expectations: str,
) -> dict[str, Any]:
    normalized_role = _sanitize_role_query(role) or _sanitize_role_query(seniority) or _normalize_text(role) or "software engineer"
    normalized_location = _normalize_text(location)
    family = _role_family_for_query(role=normalized_role, skills=skills)
    role_variants = _role_variants_for_query(role=normalized_role, seniority=seniority, skills=skills)
    core_skills = _core_skills_for_query(skills, family=family)
    project_terms = _project_terms_for_query(family=family, skills=skills)
    negative_filters = list(_GOOGLE_XRAY_NEGATIVE_FILTERS)
    def q(parts: list[str]) -> str:
        return " ".join(_normalize_text(part) for part in parts if _normalize_text(part)).strip()

    def role_query(phrase: str) -> str:
        return q(["site:linkedin.com/in", f'"{_normalize_text(phrase)}"' if " " in _normalize_text(phrase) else _normalize_text(phrase).lower(), normalized_location, _negative_filters_clause()])

    def keyword_query(*phrases: str) -> str:
        return q(["site:linkedin.com/in", " ".join(_normalize_text(phrase) for phrase in phrases if _normalize_text(phrase)), normalized_location, _negative_filters_clause()])

    def phrase_query(*phrases: str) -> str:
        return q(["site:linkedin.com/in", " ".join(f'"{_normalize_text(phrase)}"' if " " in _normalize_text(phrase) else _normalize_text(phrase).lower() for phrase in phrases if _normalize_text(phrase)), normalized_location, _negative_filters_clause()])

    if family == "frontend":
        role_queries = [
            role_query("frontend developer"),
            role_query("ui developer"),
        ]
        stack_queries = [
            keyword_query("html", "css", "javascript", "react"),
            keyword_query("frontend", "ui", "react", "typescript"),
        ]
        project_queries = [
            keyword_query("responsive ui", "dashboard"),
            keyword_query("admin panel", "web app"),
        ]
        framework_queries = [
            keyword_query("react", "next.js"),
            keyword_query("vue", "angular"),
        ]
    elif family == "fullstack":
        role_queries = [
            role_query("full stack developer"),
            role_query("software engineer"),
        ]
        stack_queries = [
            keyword_query("react", "node", "python"),
            keyword_query("html", "css", "javascript", "react"),
        ]
        project_queries = [
            keyword_query("web app", "api integration"),
            keyword_query("dashboard", "product engineering"),
        ]
        framework_queries = [
            keyword_query("react", "django"),
            keyword_query("next.js", "node"),
        ]
    elif family == "data":
        role_queries = [
            role_query("data engineer"),
            role_query("analytics engineer"),
        ]
        stack_queries = [
            keyword_query("python", "sql", "dbt", "snowflake"),
            keyword_query("analytics", "pipeline", "sql"),
        ]
        project_queries = [
            keyword_query("data pipelines", "analytics"),
            keyword_query("etl", "dashboard"),
        ]
        framework_queries = [
            keyword_query("dbt", "snowflake"),
            keyword_query("airflow", "spark"),
        ]
    elif family == "backend":
        if "python" in _normalize_lower(" ".join([normalized_role, " ".join(skills)])):
            role_queries = [
                role_query("python developer"),
                role_query("backend engineer python"),
            ]
            stack_queries = [
                keyword_query("python", "django", "fastapi", "mongodb"),
                keyword_query("python", "fastapi", "rest api"),
            ]
            project_queries = [
                keyword_query("fastapi", "rest api", "python"),
                keyword_query("backend engineer", "python"),
            ]
            framework_queries = [
                keyword_query("django", "fastapi"),
                keyword_query("django", "rest api"),
            ]
        else:
            role_queries = [
                role_query("backend engineer"),
                role_query("software engineer"),
            ]
            stack_queries = [
                keyword_query("backend", "api", "python"),
                keyword_query("backend", "microservices", "rest api"),
            ]
            project_queries = [
                keyword_query("backend", "rest api"),
                keyword_query("microservices", "api"),
            ]
            framework_queries = [
                keyword_query("fastapi", "django"),
                keyword_query("node", "express"),
            ]
    else:
        role_queries = [
            role_query(normalized_role),
            role_query(f"{normalized_role} developer"),
        ]
        stack_queries = [
            keyword_query(*core_skills[:4]) if core_skills else keyword_query(*skills[:4]),
            keyword_query(*project_terms[:4]) if project_terms else keyword_query(normalized_role),
        ]
        project_queries = [
            keyword_query("web app", "backend"),
            keyword_query("software engineer"),
        ]
        framework_queries = [
            keyword_query("django", "fastapi"),
            keyword_query("react", "node"),
        ]

    def _dedupe_queries(values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = _normalize_text(value)
            key = _normalize_lower(normalized)
            if not normalized or key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return cleaned

    role_queries = _dedupe_queries(role_queries)
    stack_queries = _dedupe_queries(stack_queries)
    project_queries = _dedupe_queries(project_queries)
    framework_queries = _dedupe_queries(framework_queries)

    all_queries = _dedupe_queries([*role_queries, *stack_queries, *project_queries, *framework_queries])
    return {
        "role_queries": role_queries,
        "stack_queries": stack_queries,
        "project_queries": project_queries,
        "framework_queries": framework_queries,
        "location": normalized_location,
        "generated_query_count": len(all_queries),
        "queries": all_queries,
        "negative_filters": negative_filters,
        "family": family,
        "core_skills": core_skills,
    }


def _fallback_query_layers(
    *,
    role: str,
    seniority: str,
    skills: list[str],
    location: str,
    company_stage: str,
    hiring_preferences: str,
    industry: str,
    leadership_expectations: str,
) -> list[XRayQueryLayer]:
    strategy = _build_google_xray_query_strategy(
        role=role,
        seniority=seniority,
        skills=skills,
        location=location,
        company_stage=company_stage,
        hiring_preferences=hiring_preferences,
        industry=industry,
        leadership_expectations=leadership_expectations,
    )
    layers = [
        *[XRayQueryLayer(layer_type=f"role_query_{index}", query=query) for index, query in enumerate(strategy["role_queries"], start=1)],
        *[XRayQueryLayer(layer_type=f"stack_query_{index}", query=query) for index, query in enumerate(strategy["stack_queries"], start=1)],
        *[XRayQueryLayer(layer_type=f"project_query_{index}", query=query) for index, query in enumerate(strategy["project_queries"], start=1)],
        *[XRayQueryLayer(layer_type=f"framework_query_{index}", query=query) for index, query in enumerate(strategy["framework_queries"], start=1)],
    ]
    return [layer for layer in layers if layer.query]


def _normalize_query_layer(payload: dict[str, Any], *, fallback_index: int) -> XRayQueryLayer | None:
    layer_type = _normalize_text(payload.get("layer_type") or payload.get("type") or payload.get("name") or "")
    query = _normalize_text(payload.get("query") or payload.get("search_query") or payload.get("value") or "")
    enabled_value = payload.get("enabled", True)
    if isinstance(enabled_value, str):
        enabled = enabled_value.strip().lower() not in {"false", "0", "no", "off"}
    else:
        enabled = bool(enabled_value)
    pages_value = payload.get("pages", 1)
    try:
        pages = max(1, min(int(pages_value), max(1, min(3, SERPAPI_MAX_PAGES_PER_LAYER))))
    except (TypeError, ValueError):
        pages = 1
    if not query and not enabled:
        return XRayQueryLayer(layer_type=layer_type or f"layer_{fallback_index + 1}", query="", enabled=False, pages=pages)
    if not query:
        return None
    if not layer_type:
        layer_type = f"layer_{fallback_index + 1}"
    return XRayQueryLayer(layer_type=layer_type, query=query, enabled=enabled, pages=pages)


def _select_primary_query_layer(layers: list[XRayQueryLayer]) -> XRayQueryLayer | None:
    preferred_order = (
        "role_query_1",
        "stack_query_1",
        "project_query_1",
        "framework_query_1",
        "role_query_2",
        "stack_query_2",
        "project_query_2",
        "framework_query_2",
    )
    active_layers = [layer for layer in layers if layer.enabled and layer.query]
    if not active_layers:
        return None
    for layer_type in preferred_order:
        for layer in active_layers:
            if layer.layer_type == layer_type:
                return layer
    return active_layers[0]


def _select_primary_query_layers(layers: list[XRayQueryLayer], *, max_layers: int = 3) -> list[XRayQueryLayer]:
    preferred_order = (
        "role_query_1",
        "stack_query_1",
        "project_query_1",
        "framework_query_1",
        "role_query_2",
        "stack_query_2",
        "project_query_2",
        "framework_query_2",
    )
    active_layers = [layer for layer in layers if layer.enabled and layer.query]
    if not active_layers:
        return []

    selected: list[XRayQueryLayer] = []
    seen_queries: set[str] = set()
    for layer_type in preferred_order:
        for layer in active_layers:
            normalized_query = _normalize_lower(layer.query)
            if layer.layer_type != layer_type or not normalized_query or normalized_query in seen_queries:
                continue
            selected.append(layer)
            seen_queries.add(normalized_query)
            if len(selected) >= max(1, max_layers):
                return selected

    for layer in active_layers:
        normalized_query = _normalize_lower(layer.query)
        if not normalized_query or normalized_query in seen_queries:
            continue
        selected.append(layer)
        seen_queries.add(normalized_query)
        if len(selected) >= max(1, max_layers):
            break
    return selected


def _role_cache_key(*, role_search_id: str, role: str, location: str, skills: list[str], layers: list[XRayQueryLayer], limit: int) -> str:
    payload = {
        "role_search_id": _normalize_text(role_search_id),
        "role": _normalize_lower(role),
        "location": _normalize_lower(location),
        "skills": _dedupe_preserve_order([_normalize_lower(skill) for skill in skills if skill]),
        "queries": [_normalize_lower(layer.query) for layer in layers if layer.enabled and layer.query],
        "limit": int(limit),
        "pages_per_layer": 1,
        "engine": SERPAPI_ENGINE or "google",
        "page_size": SERPAPI_RESULTS_PER_PAGE,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_linkedin_xray_query_layers(
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
) -> list[XRayQueryLayer]:
    role = _normalize_text(role)
    seniority = _normalize_text(seniority)
    location = _normalize_text(location)
    company_stage = _normalize_text(company_stage)
    hiring_preferences = _normalize_text(hiring_preferences)
    industry = _normalize_text(industry)
    leadership_expectations = _normalize_text(leadership_expectations)
    skill_list = _dedupe_preserve_order(skills)

    strategy = _build_google_xray_query_strategy(
        role=role,
        seniority=seniority,
        skills=skill_list,
        location=location,
        company_stage=company_stage,
        hiring_preferences=hiring_preferences,
        industry=industry,
        leadership_expectations=leadership_expectations,
    )

    layers = [
        *[XRayQueryLayer(layer_type=f"role_query_{index}", query=query) for index, query in enumerate(strategy["role_queries"], start=1)],
        *[XRayQueryLayer(layer_type=f"stack_query_{index}", query=query) for index, query in enumerate(strategy["stack_queries"], start=1)],
        *[XRayQueryLayer(layer_type=f"project_query_{index}", query=query) for index, query in enumerate(strategy["project_queries"], start=1)],
        *[XRayQueryLayer(layer_type=f"framework_query_{index}", query=query) for index, query in enumerate(strategy["framework_queries"], start=1)],
    ]
    return [layer for layer in layers if layer.query]


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
    layers = build_linkedin_xray_query_layers(
        role=role,
        seniority=seniority,
        skills=skills,
        location=location,
        company_stage=company_stage,
        hiring_preferences=hiring_preferences,
        industry=industry,
        leadership_expectations=leadership_expectations,
        recruiter_preferences=recruiter_preferences,
    )
    return [layer.query for layer in layers if layer.query]


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

    def _request(self, *, query: str, start: int = 0, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not SERPAPI_ENABLED:
            return {}
        if _is_disabled():
            return {}

        api_key = SERPAPI_API_KEY.strip()
        if not api_key:
            _disable("SERPAPI_API_KEY missing", cooldown_seconds=300)
            return {}

        meta = dict(context or {})
        params = {
            "engine": SERPAPI_ENGINE or "google",
            "q": query,
            "api_key": api_key,
            "hl": "en",
            "gl": "us",
            "start": max(0, int(start)),
            "num": max(1, int(SERPAPI_RESULTS_PER_PAGE)),
        }
        url = SERPAPI_URL or "https://serpapi.com/search.json"
        last_error: Exception | None = None
        for attempt in range(1, max(1, SERPAPI_RETRY_ATTEMPTS) + 1):
            self._respect_rate_limit()
            _log_structured(
                "serpapi_call",
                role_search_id=meta.get("role_search_id", ""),
                recruiter_id=meta.get("recruiter_id", ""),
                company_id=meta.get("company_id", ""),
                job_id=meta.get("job_id", ""),
                workflow_token=meta.get("workflow_token", ""),
                layer_index=meta.get("layer_index", ""),
                layer_type=meta.get("layer_type", ""),
                query=query,
                page=meta.get("page", 1),
                num_requested=meta.get("num_requested", max(1, SERPAPI_RESULTS_PER_PAGE)),
                search_engine=SERPAPI_ENGINE or "google",
                attempt=attempt,
                start=start,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            try:
                _record_serpapi_request_hit()
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

    def search(self, *, query: str, pages: int = 1, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        query = _normalize_text(query)
        if not query:
            return results
        page_count = 1 if pages else 1
        for page in range(page_count):
            start = page * max(1, SERPAPI_RESULTS_PER_PAGE)
            payload = self._request(query=query, start=start, context={**(context or {}), "page": page + 1, "num_requested": SERPAPI_RESULTS_PER_PAGE})
            organic_results = payload.get("organic_results", []) if isinstance(payload, dict) else []
            if not isinstance(organic_results, list) or not organic_results:
                break
            for item in organic_results:
                if isinstance(item, dict):
                    results.append(item)
            if len(organic_results) < max(1, SERPAPI_RESULTS_PER_PAGE):
                break
        return results

    def search_many(self, queries: list[str], *, pages: int = 1, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for query in _dedupe_preserve_order(queries):
            results.extend(self.search(query=query, pages=pages, context=context))
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


def _snippet_quality(*, title: str, snippet: str, displayed_link: str, company: str, location: str) -> str:
    richness = 0
    text = " ".join([title, snippet, displayed_link, company, location]).strip()
    if len(_normalize_text(snippet)) >= 160:
        richness += 1
    if company:
        richness += 1
    if location:
        richness += 1
    if any(token in text.lower() for token in ("linkedin.com/in/", "at ", "engineering", "product", "sales", "platform", "infrastructure")):
        richness += 1
    if richness >= 3:
        return "rich"
    if richness >= 1:
        return "partial"
    return "thin"


def _normalize_candidate_result(*, result: dict[str, Any], query: str, page: int, position: int, intake: dict[str, str], source: str) -> dict[str, Any] | None:
    link = _normalize_text(result.get("link") or "")
    title = _normalize_text(result.get("title") or "")
    snippet = _normalize_text(result.get("snippet") or "")
    displayed_link = _normalize_text(result.get("displayed_link") or "")
    linkedin_url = _extract_linkedin_url(link)
    if not linkedin_url:
        logger.info(
            "xray_candidate_rejected reason=missing_linkedin_profile_url query=%s page=%s position=%s link=%s displayed_link=%s",
            query,
            page,
            position,
            link,
            displayed_link,
        )
        return None

    text = " ".join([title, snippet, displayed_link])
    name = _candidate_name_from_title(title) or _candidate_name_from_title(displayed_link) or _candidate_name_from_title(snippet)
    role = _candidate_role_from_text(title, snippet, displayed_link) or _normalize_text(title)
    company = _extract_company(snippet)
    location = _extract_location(snippet, fallback=intake.get("location", ""))
    skills = _extract_skills_from_text(text, [skill.strip() for skill in intake.get("skills", "").split(",") if skill.strip()])
    snippet_quality = _snippet_quality(title=title, snippet=snippet, displayed_link=displayed_link, company=company, location=location)

    normalized = {
        "id": linkedin_url or link,
        "full_name": name or title or "Unknown Candidate",
        "name": name or title or "Unknown Candidate",
        "job_title": role,
        "title": role,
        "role": role,
        "headline": role,
        "job_company_name": company,
        "company": company,
        "location": location,
        "skills": skills,
        "summary": snippet or role,
        "experience": _normalize_text(intake.get("seniority") or ""),
        "linkedin_url": linkedin_url,
        "source": source,
        "source_type": "linkedin_xray",
        "search_query": query,
        "search_page": page,
        "search_position": position,
        "snippet": snippet,
        "snippet_quality": snippet_quality,
        "snippetQuality": snippet_quality,
        "displayed_link": displayed_link,
        "score": _score_result(query=query, result=result, page=page, position=position, intake=intake),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "source_provider": "xray_apollo",
        "sourceProvider": "xray_apollo",
        "source": "linkedin_xray",
        "source_type": "linkedin_xray",
        "sourceType": "linkedin_xray",
        "source_query": query,
        "sourceQuery": query,
        "source_timestamp": datetime.now(timezone.utc).isoformat(),
        "sourceTimestamp": datetime.now(timezone.utc).isoformat(),
        "current_company": company,
        "currentCompany": company,
        "inferred_experience": _normalize_text(intake.get("seniority") or ""),
        "inferredExperience": _normalize_text(intake.get("seniority") or ""),
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
    logger.info(
        "xray_candidate_normalized linkedin_url=%s source_url=%s query=%s page=%s position=%s snippet_quality=%s",
        linkedin_url,
        _normalize_text(result.get("link") or ""),
        query,
        page,
        position,
        snippet_quality,
    )
    return normalized


def _fixture_path_for_role(*, role: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", _normalize_lower(role)).strip("_") or "default"
    base_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "xray"
    preferred = base_dir / f"{slug}.json"
    if preferred.exists():
        return preferred
    return base_dir / "default.json"


def _load_mock_xray_raw_results(*, role: str) -> list[dict[str, Any]]:
    fixture_path = _fixture_path_for_role(role=role)
    if not fixture_path.exists():
        return []
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("xray_mock_fixture_failed path=%s error=%s", fixture_path, str(exc))
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("organic_results"), list):
            return [item for item in payload["organic_results"] if isinstance(item, dict)]
        if isinstance(payload.get("candidates"), list):
            return [item for item in payload["candidates"] if isinstance(item, dict)]
    return []


def _xray_role_cache_key(
    *,
    job_id: str,
    company_id: str,
    intake: dict[str, str],
    limited_layers: list[XRayQueryLayer],
) -> str:
    payload = {
        "cache_version": _XRAY_ROLE_CACHE_VERSION,
        "job_id": _normalize_text(job_id),
        "company_id": _normalize_text(company_id),
        "role_title": _normalize_text(intake.get("role_title") or ""),
        "seniority": _normalize_text(intake.get("seniority") or ""),
        "location": _normalize_text(intake.get("location") or ""),
        "company_stage": _normalize_text(intake.get("company_stage") or ""),
        "hiring_preferences": _normalize_text(intake.get("hiring_preferences") or ""),
        "industry": _normalize_text(intake.get("industry") or ""),
        "leadership_expectations": _normalize_text(intake.get("leadership_expectations") or ""),
        "skills": [token.strip() for token in (intake.get("skills") or "").split(",") if token.strip()],
        "archetype_ids": _dedupe_preserve_order(archetype_ids),
        "recruiter_preferences": recruiter_preferences or {},
        "layers": [
            {
                "layer_type": layer.layer_type,
                "query": layer.query,
                "enabled": layer.enabled,
                "pages": 1,
            }
            for layer in limited_layers
        ],
        "search_engine": SERPAPI_ENGINE or "google",
        "results_per_page": int(SERPAPI_RESULTS_PER_PAGE),
    }
    material = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def discover_linkedin_xray_candidates(
    *,
    job: Any,
    intake: dict[str, Any] | None = None,
    limit: int = 10,
    pages_per_query: int = 1,
    recruiter_preferences: dict[str, Any] | None = None,
    db: Session | None = None,
    role_search_id: str = "",
    recruiter_id: str = "",
    company_id: str = "",
    workflow_token: str = "",
    archetype_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    mock_mode_active = bool(MOCK_XRAY_MODE and LOCAL_DEV_MODE)
    if not SERPAPI_ENABLED and not mock_mode_active:
        logger.info("serpapi_discovery_skipped reason=feature_disabled")
        return []
    client = SerpApiClient()
    if is_serpapi_disabled() and not mock_mode_active:
        logger.info("serpapi_discovery_skipped reason=service_disabled")
        return []
    if not SERPAPI_API_KEY.strip() and not mock_mode_active:
        logger.info("serpapi_discovery_skipped reason=missing_api_key")
        return []

    resolved_intake = _normalize_intake(job, intake)
    search_pages = 1
    resolved_job_id = _normalize_text(getattr(job, "id", ""))
    resolved_company_id = _normalize_text(company_id or getattr(job, "company_id", ""))
    resolved_recruiter_id = _normalize_text(recruiter_id)
    resolved_workflow_token = _normalize_text(workflow_token)
    resolved_role_search_id = _normalize_text(role_search_id) or resolved_job_id or f"{resolved_company_id or 'company'}:{_normalize_lower(resolved_intake['role_title'])}"
    resolved_archetype_ids = _dedupe_preserve_order([str(item).strip() for item in (archetype_ids or []) if str(item).strip()])

    query_generation_started = perf_counter()
    query_layers = build_linkedin_xray_query_layers(
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
    query_generation_ms = round((perf_counter() - query_generation_started) * 1000.0, 2)
    query_strategy = _build_google_xray_query_strategy(
        role=resolved_intake["role_title"],
        seniority=resolved_intake["seniority"],
        skills=[skill.strip() for skill in resolved_intake["skills"].split(",") if skill.strip()],
        location=resolved_intake["location"],
        company_stage=resolved_intake["company_stage"],
        hiring_preferences=resolved_intake["hiring_preferences"],
        industry=resolved_intake["industry"],
        leadership_expectations=resolved_intake["leadership_expectations"],
    )

    limited_layers = _select_primary_query_layers(query_layers, max_layers=3)
    job_role = resolved_intake["role_title"]
    diversity_report = _query_diversity_report(layers=limited_layers, recruiter_preferences=recruiter_preferences)
    quota_before = _quota_snapshot()
    logger.info(
        "serpapi_discovery_started role=%s location=%s layers=%s limit=%s pages=%s quota_date=%s quota_budget=%s",
        job_role,
        resolved_intake["location"],
        ",".join(layer.layer_type for layer in limited_layers),
        limit,
        search_pages,
        quota_before.date,
        quota_before.budget,
    )
    logger.info(
        "serpapi_query_layers role=%s layers=%s",
        job_role,
        [
            {"layer_type": layer.layer_type, "enabled": layer.enabled, "pages": layer.pages, "query": layer.query}
            for layer in limited_layers
        ],
    )
    logger.info(
        "serpapi_query_diversity role=%s overlap_ratio=%.4f duplicate_queries=%s company_concentration=%s cluster=%s",
        job_role,
        diversity_report["overlap_ratio"],
        diversity_report["duplicate_query_count"],
        diversity_report["company_concentration"],
        diversity_report["cluster_name"],
    )
    log_metric(
        "serpapi_query_diversity",
        role=job_role,
        overlap_ratio=diversity_report["overlap_ratio"],
        duplicate_queries=diversity_report["duplicate_query_count"],
        duplicate_tokens=diversity_report["duplicate_token_count"],
        company_concentration=diversity_report["company_concentration"],
        cluster_name=diversity_report["cluster_name"],
        title_tokens=diversity_report["title_token_count"],
        seniority_tokens=diversity_report["seniority_token_count"],
    )

    role_cache_key = _xray_role_cache_key(
        job_id=resolved_job_id,
        company_id=resolved_company_id,
        intake=resolved_intake,
        limited_layers=limited_layers,
    )
    cached_role_payload = cache_get_json(_XRAY_ROLE_CACHE_NAMESPACE, role_cache_key)
    if isinstance(cached_role_payload, dict):
        cached_results = cached_role_payload.get("results")
        if isinstance(cached_results, list):
            cached_dedupe = cached_role_payload.get("dedupe_report") if isinstance(cached_role_payload.get("dedupe_report"), dict) else {}
            logger.info(
                "serpapi_role_cache_hit role=%s role_search_id=%s count=%s cache_key=%s",
                job_role,
                resolved_role_search_id,
                len(cached_results),
                role_cache_key[:12],
            )
            if isinstance(cached_dedupe, dict):
                _log_structured(
                    "xray_dedup",
                    raw_candidates=cached_dedupe.get("raw_candidates", 0),
                    duplicate_candidates=cached_dedupe.get("duplicate_candidates", 0),
                    deduped_candidates=cached_dedupe.get("deduped_candidates", len(cached_results)),
                    duplicate_rate=cached_dedupe.get("duplicate_rate", 0.0),
                )
            _log_structured(
                "serpapi_call_count",
                role_search_id=resolved_role_search_id,
                calls_executed=0,
                quota_remaining=_quota_snapshot().budget,
                daily_budget=DAILY_SERPAPI_BUDGET,
                max_calls_per_role=MAX_CALLS_PER_ROLE,
            )
            _log_structured(
                "xray_timing",
                query_generation_ms=query_generation_ms,
                serpapi_latency_ms=0.0,
                dedupe_ms=0.0,
                prefilter_ms=0.0,
                rerank_ms=0.0,
                total_pipeline_ms=query_generation_ms,
            )
            _register_profiles_found(count=len(cached_results))
            return cached_results

    if not limited_layers:
        logger.info("serpapi_discovery_completed role=%s count=0 reason=no_active_layers", job_role)
        return []

    generated_queries_payload = {
        "job_id": resolved_job_id,
        "company_id": resolved_company_id,
        "recruiter_id": resolved_recruiter_id,
        "workflow_token": resolved_workflow_token,
        "role_search_id": resolved_role_search_id,
        "archetype_ids": resolved_archetype_ids,
        "query_strategy": query_strategy,
        "total_layer_count": len(query_layers),
        "active_layer_count": len(limited_layers),
        "generated_queries": [
            {
                "layer_index": index,
                "layer_type": layer.layer_type,
                "query": layer.query,
                "pages": 1,
                "enabled": layer.enabled,
            }
            for index, layer in enumerate(limited_layers, start=1)
        ],
    }
    _write_debug_artifact("generated_queries.json", generated_queries_payload)
    log_metric(
        "serpapi_query_layers",
        role=job_role,
        layer_count=len(limited_layers),
        total_layers=len(query_layers),
        max_calls=MAX_CALLS_PER_ROLE,
        limit=limit,
    )

    for index, layer in enumerate(limited_layers, start=1):
        _log_structured(
            "xray_query_layer",
            layer_index=index,
            layer_type=layer.layer_type,
            query=layer.query,
            page=1,
            num_requested=SERPAPI_RESULTS_PER_PAGE,
            search_engine=SERPAPI_ENGINE or "google",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    existing_memory_keys: set[str] = set()
    if db is not None:
        try:
            for row in CandidateProfileRepository(db).list_for_job(resolved_job_id):
                raw = row.raw_data if isinstance(row.raw_data, dict) else {}
                linkedin_url = _normalize_lower(raw.get("linkedin_url") or raw.get("linkedinUrl") or row.linkedin_url)
                candidate_id = _normalize_lower(row.candidate_id)
                name_company = _normalize_lower(f"{row.name}|{row.current_company or row.company}")
                for key in [linkedin_url, candidate_id, name_company]:
                    if key:
                        existing_memory_keys.add(key)
        except Exception as exc:
            logger.warning("xray_existing_candidate_scan_failed job_id=%s error=%s", resolved_job_id, str(exc))

    layer_results: list[tuple[XRayQueryLayer, list[dict[str, Any]], int]] = []
    serpapi_calls_before = _serpapi_request_total()
    serpapi_latency_started = perf_counter()
    duplicate_query_count = 0
    effective_workers = 1 if LOCAL_DEV_MODE else max(1, len(limited_layers))
    if mock_mode_active:
        mock_raw_results = _load_mock_xray_raw_results(role=job_role)
        for index, layer in enumerate(limited_layers, start=1):
            fingerprint = _query_fingerprint(
                layer_type=layer.layer_type,
                query=layer.query,
                page=1,
                num_requested=SERPAPI_RESULTS_PER_PAGE,
                search_engine=SERPAPI_ENGINE or "google",
            )
            if _is_duplicate_query(fingerprint=fingerprint):
                duplicate_query_count += 1
                logger.info("serpapi_duplicate_query_suppressed role=%s layer_type=%s fingerprint=%s", job_role, layer.layer_type, fingerprint[:12])
                continue
            layer_results.append((layer, mock_raw_results, 1))
        serpapi_calls_executed = 0
    else:
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            future_map: dict[Any, tuple[XRayQueryLayer, int]] = {}
            for index, layer in enumerate(limited_layers, start=1):
                pages_to_fetch = 1
                if not _reserve_serpapi_call(role=job_role, layer_type=layer.layer_type, query=layer.query):
                    continue
                fingerprint = _query_fingerprint(
                    layer_type=layer.layer_type,
                    query=layer.query,
                    page=1,
                    num_requested=SERPAPI_RESULTS_PER_PAGE,
                    search_engine=SERPAPI_ENGINE or "google",
                )
                if _is_duplicate_query(fingerprint=fingerprint):
                    duplicate_query_count += 1
                    logger.info("serpapi_duplicate_query_suppressed role=%s layer_type=%s fingerprint=%s", job_role, layer.layer_type, fingerprint[:12])
                    continue
                future = executor.submit(
                    client.search,
                    query=layer.query,
                    pages=pages_to_fetch,
                    context={
                        "role_search_id": resolved_role_search_id,
                        "recruiter_id": resolved_recruiter_id,
                        "company_id": resolved_company_id,
                        "job_id": resolved_job_id,
                        "workflow_token": resolved_workflow_token,
                        "layer_index": index,
                        "layer_type": layer.layer_type,
                        "num_requested": SERPAPI_RESULTS_PER_PAGE,
                    },
                )
                future_map[future] = (layer, pages_to_fetch)

            for future in as_completed(future_map):
                layer, pages_to_fetch = future_map[future]
                try:
                    raw = future.result()
                except Exception as exc:
                    logger.warning("serpapi_layer_failed role=%s layer_type=%s error=%s", job_role, layer.layer_type, str(exc))
                    log_metric("serpapi_layer_error", role=job_role, layer_type=layer.layer_type, error_type=type(exc).__name__)
                    raw = []
                layer_results.append((layer, raw, pages_to_fetch))

            serpapi_calls_executed = _serpapi_request_total() - serpapi_calls_before

    serpapi_latency_ms = round((perf_counter() - serpapi_latency_started) * 1000.0, 2)

    prefilter_started = perf_counter()
    prefiltered_results: list[dict[str, Any]] = []
    rejected_count = 0
    for layer, raw_results, pages_to_fetch in layer_results:
        logger.info(
            "serpapi_layer_results role=%s layer_type=%s raw_count=%s pages=%s",
            job_role,
            layer.layer_type,
            len(raw_results),
            pages_to_fetch,
        )
        log_metric("serpapi_layer_results", role=job_role, layer_type=layer.layer_type, raw_count=len(raw_results), pages=pages_to_fetch)
        page_index = 1
        position_index = 1
        for result in raw_results:
            normalized = _normalize_candidate_result(
                result=result,
                query=layer.query,
                page=page_index,
                position=position_index,
                intake=resolved_intake,
                source="serpapi",
            )
            position_index += 1
            if position_index > max(1, SERPAPI_RESULTS_PER_PAGE):
                page_index += 1
                position_index = 1
            if not normalized:
                rejected_count += 1
                continue
            prefiltered_results.append(normalized)

    prefilter_ms = round((perf_counter() - prefilter_started) * 1000.0, 2)

    dedupe_started = perf_counter()
    normalized_results: list[dict[str, Any]] = []
    seen_linkedin_urls: set[str] = set()
    seen_candidate_ids: set[str] = set()
    seen_name_company: set[str] = set()
    duplicate_linkedin_urls = 0
    duplicate_candidate_names = 0
    duplicate_companies = 0
    duplicate_candidate_ids = 0
    duplicate_memory_candidates = 0

    for candidate in prefiltered_results:
        linkedin_url = _normalize_lower(candidate.get("linkedin_url") or "")
        candidate_id = _normalize_lower(candidate.get("candidate_id") or candidate.get("id") or "")
        name_company = _normalize_lower(f"{candidate.get('full_name') or candidate.get('name') or ''}|{candidate.get('current_company') or candidate.get('company') or ''}")
        company = _normalize_lower(candidate.get("current_company") or candidate.get("company") or "")

        duplicate_reason = ""
        if linkedin_url and linkedin_url in seen_linkedin_urls:
            duplicate_linkedin_urls += 1
            duplicate_reason = "linkedin_url"
        elif candidate_id and candidate_id in seen_candidate_ids:
            duplicate_candidate_ids += 1
            duplicate_reason = "candidate_id"
        elif name_company and name_company in seen_name_company:
            duplicate_candidate_names += 1
            duplicate_reason = "name_company"
        elif linkedin_url and linkedin_url in existing_memory_keys:
            duplicate_memory_candidates += 1
            duplicate_reason = "recruiter_memory"
        elif candidate_id and candidate_id in existing_memory_keys:
            duplicate_memory_candidates += 1
            duplicate_reason = "recruiter_memory"
        elif name_company and name_company in existing_memory_keys:
            duplicate_memory_candidates += 1
            duplicate_reason = "recruiter_memory"
        if duplicate_reason:
            continue

        if linkedin_url:
            seen_linkedin_urls.add(linkedin_url)
        if candidate_id:
            seen_candidate_ids.add(candidate_id)
        if name_company:
            seen_name_company.add(name_company)
        if company:
            # Company-level repetition is allowed, but we still count it for observability.
            duplicate_companies += 1 if any(_normalize_lower(item.get("current_company") or item.get("company") or "") == company for item in normalized_results) else 0
        normalized_results.append(candidate)

    dedupe_ms = round((perf_counter() - dedupe_started) * 1000.0, 2)
    total_pipeline_ms = round(query_generation_ms + serpapi_latency_ms + prefilter_ms + dedupe_ms, 2)
    raw_candidates = len(prefiltered_results)
    duplicate_candidates = raw_candidates - len(normalized_results)
    duplicate_rate = round((duplicate_candidates / raw_candidates) if raw_candidates else 0.0, 4)

    dedupe_report = {
        "job_id": resolved_job_id,
        "company_id": resolved_company_id,
        "recruiter_id": resolved_recruiter_id,
        "workflow_token": resolved_workflow_token,
        "role_search_id": resolved_role_search_id,
        "raw_candidates": raw_candidates,
        "duplicate_candidates": duplicate_candidates,
        "deduped_candidates": len(normalized_results),
        "duplicate_rate": duplicate_rate,
        "duplicate_linkedin_urls": duplicate_linkedin_urls,
        "duplicate_companies": duplicate_companies,
        "duplicate_candidate_names": duplicate_candidate_names,
        "duplicate_canonical_ids": duplicate_candidate_ids,
        "duplicate_recruiter_memory_candidates": duplicate_memory_candidates,
        "calls_executed": serpapi_calls_executed,
        "pages_requested": sum(pages for _, _, pages in layer_results),
        "query_layers": [
            {"layer_type": layer.layer_type, "query": layer.query, "pages": pages_to_fetch}
            for layer, _, pages_to_fetch in layer_results
        ],
    }

    _write_debug_artifact("serpapi_raw_results.json", {
        "role_search_id": resolved_role_search_id,
        "job_id": resolved_job_id,
        "raw_results": [
            {
                "layer_type": layer.layer_type,
                "query": layer.query,
                "pages": pages_to_fetch,
                "results": raw_results,
            }
            for layer, raw_results, pages_to_fetch in layer_results
        ],
    })
    _write_debug_artifact("dedupe_report.json", dedupe_report)
    _write_debug_artifact("final_review_deck.json", normalized_results)
    cache_set_json(
        _XRAY_ROLE_CACHE_NAMESPACE,
        role_cache_key,
        {
            "role_search_id": resolved_role_search_id,
            "job_id": resolved_job_id,
            "company_id": resolved_company_id,
            "recruiter_id": resolved_recruiter_id,
            "workflow_token": resolved_workflow_token,
            "limit": int(limit),
            "query_generation_ms": query_generation_ms,
            "results": normalized_results,
            "dedupe_report": dedupe_report,
        },
    )

    _register_profiles_found(count=len(normalized_results))
    quota_after = _quota_snapshot()

    _log_structured(
        "serpapi_call_count",
        role_search_id=resolved_role_search_id,
        calls_executed=serpapi_calls_executed,
        quota_remaining=quota_after.budget,
        daily_budget=DAILY_SERPAPI_BUDGET,
        max_calls_per_role=MAX_CALLS_PER_ROLE,
    )
    _log_structured(
        "xray_dedup",
        raw_candidates=raw_candidates,
        duplicate_candidates=duplicate_candidates,
        deduped_candidates=len(normalized_results),
        duplicate_rate=duplicate_rate,
    )
    _log_structured(
        "xray_timing",
        query_generation_ms=query_generation_ms,
        serpapi_latency_ms=serpapi_latency_ms,
        dedupe_ms=dedupe_ms,
        prefilter_ms=prefilter_ms,
        rerank_ms=0.0,
        total_pipeline_ms=total_pipeline_ms,
    )
    logger.info(
        "serpapi_discovery_completed role=%s count=%s quota_calls=%s quota_profiles=%s budget_remaining=%s duplicate_rate=%.4f",
        job_role,
        len(normalized_results),
        quota_after.used_calls,
        quota_after.used_profiles,
        quota_after.budget,
        duplicate_rate,
    )
    log_metric(
        "serpapi_candidates_found",
        count=len(normalized_results),
        role=job_role,
        quota_used_calls=quota_after.used_calls,
        quota_used_profiles=quota_after.used_profiles,
        quota_budget=quota_after.budget,
    )
    return normalized_results


def serpapi_health_snapshot() -> dict[str, str]:
    if MOCK_XRAY_MODE and LOCAL_DEV_MODE:
        return {"status": "ok", "reason": "mock_xray_mode"}
    if not SERPAPI_ENABLED:
        return {"status": "disabled", "reason": "SERPAPI_ENABLED=false"}
    if not SERPAPI_API_KEY.strip():
        return {"status": "down", "reason": "SERPAPI_API_KEY missing"}
    if is_serpapi_disabled():
        return {"status": "down", "reason": "serpapi_disabled"}
    return {"status": "ok", "reason": "configured"}
