from __future__ import annotations

import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.core.config import (
    DAILY_SERPAPI_BUDGET,
    HTTP_TIMEOUT_SECONDS,
    MAX_CALLS_PER_ROLE,
    MAX_TOTAL_PROFILES,
    SERPAPI_API_KEY,
    SERPAPI_ENABLED,
    SERPAPI_ENGINE,
    SERPAPI_MAX_PAGES_PER_LAYER,
    SERPAPI_MIN_REQUEST_INTERVAL_SECONDS,
    SERPAPI_REQUEST_TIMEOUT_SECONDS,
    SERPAPI_RETRY_ATTEMPTS,
    SERPAPI_RESULTS_PER_PAGE,
    SERPAPI_URL,
)
from app.services.llm_service import generate
from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)

_request_lock = threading.Lock()
_last_request_epoch = 0.0
_serpapi_disabled_until: datetime | None = None
_serpapi_disable_reason = ""
_quota_lock = threading.Lock()
_quota_day = ""
_quota_used_calls = 0
_quota_used_profiles = 0
_quota_budget = max(0, DAILY_SERPAPI_BUDGET)

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


def _sanitize_role_query(value: str) -> str:
    cleaned = _normalize_text(value)
    if not cleaned:
        return ""

    cleaned = re.sub(r"^\s*\d+\s*[-–—]?\s*\d*\+?\s*(?:years?|yrs?|yr)\b[:,-]?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:senior|jr|junior|mid|lead|principal|staff)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\(?\d+\s*[-–—]?\s*\d*\+?\s*(?:years?|yrs?|yr)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-/")
    return cleaned


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
    required_role = _sanitize_role_query(role) or _sanitize_role_query(seniority)
    title_phrase = required_role or role or seniority or ""
    skill_phrase = " ".join(_dedupe_preserve_order(skills[:4]))
    domain_phrase = " ".join(
        _dedupe_preserve_order(
            [part for part in [company_stage, industry, hiring_preferences, leadership_expectations] if part]
        )
    )
    layers: list[XRayQueryLayer] = []
    if title_phrase:
        layers.append(
            XRayQueryLayer(
                layer_type="exact_title",
                query=f'site:linkedin.com/in/ "{title_phrase}" {location}'.strip(),
            )
        )
    if role or skill_phrase:
        variation_terms = " ".join(
            _dedupe_preserve_order([part for part in [role, seniority, skill_phrase] if part])
        )
        if variation_terms:
            layers.append(
                XRayQueryLayer(
                    layer_type="title_variation",
                    query=f"site:linkedin.com/in/ {variation_terms} {location}".strip(),
                )
            )
    if domain_phrase:
        layers.append(
            XRayQueryLayer(
                layer_type="company_domain",
                query=f"site:linkedin.com/in/ {domain_phrase} {location}".strip(),
            )
        )
    if skill_phrase:
        layers.append(
            XRayQueryLayer(
                layer_type="skills_signal",
                query=f"site:linkedin.com/in/ {skill_phrase} {location}".strip(),
            )
        )
    layers.append(XRayQueryLayer(layer_type="github_placeholder", query="", enabled=False))
    return [layer for layer in layers if layer.query or not layer.enabled]


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
        pages = max(1, min(int(pages_value), max(1, min(2, SERPAPI_MAX_PAGES_PER_LAYER))))
    except (TypeError, ValueError):
        pages = 1
    if not query and not enabled:
        return XRayQueryLayer(layer_type=layer_type or f"layer_{fallback_index + 1}", query="", enabled=False, pages=pages)
    if not query:
        return None
    if not layer_type:
        layer_type = f"layer_{fallback_index + 1}"
    return XRayQueryLayer(layer_type=layer_type, query=query, enabled=enabled, pages=pages)


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

    prompt = (
        "Decompose this recruiter role into 3-5 LinkedIn X-Ray search layers.\n"
        "Return only JSON with a top-level 'layers' array.\n"
        "Each layer should have: layer_type, query, enabled, pages.\n"
        "Required layers: exact_title, title_variation, company_domain, skills_signal.\n"
        "Optional layer: github_placeholder, but keep it disabled by default.\n"
        "Do not use hardcoded role vocabularies. Infer semantic search intent from the role, skills, location,\n"
        "company stage, hiring preferences, industry, and leadership expectations.\n"
        "Keep LinkedIn profile search queries broad and high-signal, not deep-pagination oriented.\n\n"
        f"role: {role}\n"
        f"seniority: {seniority}\n"
        f"skills: {', '.join(skill_list)}\n"
        f"location: {location}\n"
        f"company_stage: {company_stage}\n"
        f"hiring_preferences: {hiring_preferences}\n"
        f"industry: {industry}\n"
        f"leadership_expectations: {leadership_expectations}\n"
        f"recruiter_preferences: {recruiter_preferences or {}}\n"
    )

    layers: list[XRayQueryLayer] = []
    try:
        response = generate(prompt, expect_json=True)
        payload = response if isinstance(response, dict) else {}
        raw_layers = payload.get("layers") if isinstance(payload.get("layers"), list) else payload.get("query_layers")
        if isinstance(raw_layers, list):
            for index, item in enumerate(raw_layers[:5]):
                if isinstance(item, dict):
                    normalized = _normalize_query_layer(item, fallback_index=index)
                    if normalized is not None:
                        layers.append(normalized)
    except Exception as exc:
        logger.info("serpapi_query_layer_llm_fallback error=%s", str(exc))

    if not layers:
        layers = _fallback_query_layers(
            role=role,
            seniority=seniority,
            skills=skill_list,
            location=location,
            company_stage=company_stage,
            hiring_preferences=hiring_preferences,
            industry=industry,
            leadership_expectations=leadership_expectations,
        )

    active_layers: list[XRayQueryLayer] = []
    seen_keys: set[str] = set()
    for layer in layers:
        if not layer.enabled and not layer.query:
            active_layers.append(layer)
            continue
        key = _normalize_lower(layer.query)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        active_layers.append(layer)

    if len([layer for layer in active_layers if layer.enabled]) < 3:
        fallback_layers = _fallback_query_layers(
            role=role,
            seniority=seniority,
            skills=skill_list,
            location=location,
            company_stage=company_stage,
            hiring_preferences=hiring_preferences,
            industry=industry,
            leadership_expectations=leadership_expectations,
        )
        for layer in fallback_layers:
            if not layer.enabled and not layer.query:
                continue
            key = _normalize_lower(layer.query)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            active_layers.append(layer)

    active_layers = _diversify_query_layers(layers=active_layers, recruiter_preferences=recruiter_preferences)
    return [layer for layer in active_layers[:5] if layer.query or not layer.enabled]


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
    return [
        layer.query
        for layer in build_linkedin_xray_query_layers(
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
        if layer.enabled and layer.query
    ]


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
        page_count = max(1, min(int(pages), max(1, min(2, SERPAPI_MAX_PAGES_PER_LAYER))))
        for page in range(page_count):
            start = page * max(1, SERPAPI_RESULTS_PER_PAGE)
            payload = self._request(query=query, start=start)
            organic_results = payload.get("organic_results", []) if isinstance(payload, dict) else []
            if not isinstance(organic_results, list) or not organic_results:
                continue
            for item in organic_results:
                if isinstance(item, dict):
                    results.append(item)
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
    search_pages = max(1, min(int(pages_per_query or 1), max(1, min(2, SERPAPI_MAX_PAGES_PER_LAYER))))
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

    active_layers = [layer for layer in query_layers if layer.enabled and layer.query]
    limited_layers = active_layers[: max(1, min(MAX_CALLS_PER_ROLE, 5))]
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
    log_metric(
        "serpapi_query_layers",
        role=job_role,
        layer_count=len(limited_layers),
        total_layers=len(query_layers),
        max_calls=MAX_CALLS_PER_ROLE,
        limit=limit,
    )

    if not limited_layers:
        logger.info("serpapi_discovery_completed role=%s count=0 reason=no_active_layers", job_role)
        return []

    layer_results: list[tuple[XRayQueryLayer, list[dict[str, Any]]]] = []
    with ThreadPoolExecutor(max_workers=len(limited_layers)) as executor:
        future_map = {}
        for layer in limited_layers:
            if not _reserve_serpapi_call(role=job_role, layer_type=layer.layer_type, query=layer.query):
                continue
            future = executor.submit(client.search, query=layer.query, pages=min(search_pages, layer.pages))
            future_map[future] = layer

        for future in as_completed(future_map):
            layer = future_map[future]
            try:
                raw = future.result()
            except Exception as exc:
                logger.warning(
                    "serpapi_layer_failed role=%s layer_type=%s error=%s",
                    job_role,
                    layer.layer_type,
                    str(exc),
                )
                log_metric(
                    "serpapi_layer_error",
                    role=job_role,
                    layer_type=layer.layer_type,
                    error_type=type(exc).__name__,
                )
                raw = []
            logger.info(
                "serpapi_layer_results role=%s layer_type=%s raw_count=%s pages=%s",
                job_role,
                layer.layer_type,
                len(raw),
                min(search_pages, layer.pages),
            )
            log_metric(
                "serpapi_layer_results",
                role=job_role,
                layer_type=layer.layer_type,
                raw_count=len(raw),
                pages=min(search_pages, layer.pages),
            )
            layer_results.append((layer, raw))

    normalized_results: list[dict[str, Any]] = []
    seen_identities: set[str] = set()

    for layer, raw_results in layer_results:
        layer_count = 0
        for position, result in enumerate(raw_results, start=1):
            if len(normalized_results) >= max(1, min(MAX_TOTAL_PROFILES, int(limit))):
                break
            normalized = _normalize_candidate_result(
                result=result,
                query=layer.query,
                page=((position - 1) // max(1, SERPAPI_RESULTS_PER_PAGE)) + 1,
                position=((position - 1) % max(1, SERPAPI_RESULTS_PER_PAGE)) + 1,
                intake=resolved_intake,
                source="serpapi",
            )
            if not normalized:
                continue
            identity = _normalize_lower(normalized.get("linkedin_url") or normalized.get("full_name") or normalized.get("name") or "")
            if not identity or identity in seen_identities:
                continue
            seen_identities.add(identity)
            normalized_results.append(normalized)
            layer_count += 1
            if len(normalized_results) >= max(1, min(MAX_TOTAL_PROFILES, int(limit))):
                break
        logger.info(
            "serpapi_layer_dedup role=%s layer_type=%s kept=%s dedup_total=%s",
            job_role,
            layer.layer_type,
            layer_count,
            len(normalized_results),
        )
        log_metric(
            "serpapi_layer_dedup",
            role=job_role,
            layer_type=layer.layer_type,
            kept=layer_count,
            dedup_total=len(normalized_results),
        )

    normalized_results = normalized_results[: max(1, min(MAX_TOTAL_PROFILES, int(limit)))]
    _register_profiles_found(count=len(normalized_results))
    quota_after = _quota_snapshot()
    logger.info(
        "serpapi_discovery_completed role=%s count=%s quota_calls=%s quota_profiles=%s budget_remaining=%s",
        job_role,
        len(normalized_results),
        quota_after.used_calls,
        quota_after.used_profiles,
        quota_after.budget,
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
    if not SERPAPI_ENABLED:
        return {"status": "disabled", "reason": "SERPAPI_ENABLED=false"}
    if not SERPAPI_API_KEY.strip():
        return {"status": "down", "reason": "SERPAPI_API_KEY missing"}
    if is_serpapi_disabled():
        return {"status": "down", "reason": "serpapi_disabled"}
    return {"status": "ok", "reason": "configured"}
