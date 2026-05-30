from __future__ import annotations

import hashlib
import logging
import re
from uuid import uuid4
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

logger = logging.getLogger(__name__)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def _normalize_display_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def normalize_linkedin_url(value: Any) -> str:
    text = _normalize_display_text(value)
    if not text:
        return ""
    if "linkedin.com" not in text.lower() and not text.lower().startswith("in.linkedin.com"):
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = parsed.netloc.lower().replace("www.", "")
    if host.startswith("in.linkedin.com"):
        host = "www.linkedin.com"
    path = re.sub(r"/+", "/", parsed.path or "").strip("/")
    path = re.sub(r"/(about|details|overlay|overlay/contact-info)$", "", path, flags=re.IGNORECASE).strip("/")
    lowered_path = f"/{path}".lower()
    if any(blocked in lowered_path for blocked in ("/jobs/", "/search/", "/company/", "/posts/", "/feed/")):
        return ""
    slug_match = re.search(r"/in/([^/?#]+)", f"/{path}", flags=re.IGNORECASE)
    if not slug_match:
        return ""
    slug = slug_match.group(1).strip().strip("/")
    if not slug:
        return ""
    normalized_path = f"/in/{slug}"
    return urlunparse(("https", "www.linkedin.com", normalized_path, "", "", ""))


def _is_linkedin_search_or_job_url(value: str) -> bool:
    lowered = value.lower()
    return any(blocked in lowered for blocked in ("linkedin.com/jobs", "/jobs/", "/search/", "/company/", "/posts/", "/feed/"))


def extract_linkedin_slug(value: Any) -> str:
    normalized = normalize_linkedin_url(value)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    match = re.search(r"/in/([^/?#]+)", parsed.path or "", flags=re.IGNORECASE)
    return (match.group(1) if match else "").strip().lower()


def _canonicalize_company(value: Any) -> str:
    return _normalize_display_text(value)


def _canonicalize_name(value: Any) -> str:
    return _normalize_display_text(value)


def _canonicalize_title(value: Any) -> str:
    return _normalize_display_text(value)


@dataclass(frozen=True)
class CanonicalCandidateIdentity:
    canonical_linkedin_url: str
    linkedin_slug: str
    normalized_name: str
    normalized_company: str
    normalized_title: str
    inferred_location: str
    identity_fingerprint: str
    enrichment_fingerprint: str
    source_provider: str
    source_query: str
    first_seen_at: str
    last_seen_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_candidate_id(*, candidate: dict[str, Any], source_provider: str = "", source_query: str = "") -> str:
    identity = build_candidate_identity(candidate=candidate, source_provider=source_provider, source_query=source_query)
    linkedin_url = identity.canonical_linkedin_url
    if linkedin_url and not _is_linkedin_search_or_job_url(linkedin_url):
        return hashlib.sha256(linkedin_url.encode("utf-8")).hexdigest()[:32]

    material = "|".join(
        [
            identity.identity_fingerprint,
            _normalize_text(source_provider),
            _normalize_text(source_query),
            _normalize_text(identity.normalized_name),
            _normalize_text(identity.normalized_company),
            _normalize_text(identity.normalized_title),
            _normalize_text(identity.inferred_location),
        ]
    ).strip("|")
    if material:
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return str(uuid4())


def build_identity_fingerprint(
    *,
    canonical_linkedin_url: str,
    normalized_name: str,
    normalized_company: str,
    normalized_title: str,
    inferred_location: str,
) -> str:
    material = "|".join(
        [
            normalize_linkedin_url(canonical_linkedin_url),
            _normalize_text(normalized_name),
            _normalize_text(normalized_company),
            _normalize_text(normalized_title),
            _normalize_text(inferred_location),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_enrichment_fingerprint(*, identity_fingerprint: str, source_provider: str, source_query: str) -> str:
    material = "|".join([identity_fingerprint, _normalize_text(source_provider), _normalize_text(source_query)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_candidate_identity(
    *,
    candidate: dict[str, Any],
    source_provider: str = "",
    source_query: str = "",
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
) -> CanonicalCandidateIdentity:
    linkedin_url = normalize_linkedin_url(
        candidate.get("linkedin_url")
        or candidate.get("linkedinUrl")
        or candidate.get("source_linkedin_url")
        or candidate.get("sourceLinkedinUrl")
        or ""
    )
    slug = extract_linkedin_slug(linkedin_url)
    normalized_name = _canonicalize_name(candidate.get("full_name") or candidate.get("name") or candidate.get("candidate_name") or "")
    normalized_company = _canonicalize_company(
        candidate.get("current_company")
        or candidate.get("company")
        or candidate.get("job_company_name")
        or candidate.get("organization_name")
        or ""
    )
    normalized_title = _canonicalize_title(candidate.get("title") or candidate.get("job_title") or candidate.get("headline") or "")
    inferred_location = _canonicalize_title(candidate.get("location") or candidate.get("present_raw_address") or candidate.get("city") or "")
    identity_fingerprint = build_identity_fingerprint(
        canonical_linkedin_url=linkedin_url,
        normalized_name=normalized_name,
        normalized_company=normalized_company,
        normalized_title=normalized_title,
        inferred_location=inferred_location,
    )
    first_seen = (first_seen_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    last_seen = (last_seen_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    enrichment_fingerprint = build_enrichment_fingerprint(
        identity_fingerprint=identity_fingerprint,
        source_provider=source_provider,
        source_query=source_query,
    )
    return CanonicalCandidateIdentity(
        canonical_linkedin_url=linkedin_url,
        linkedin_slug=slug,
        normalized_name=normalized_name,
        normalized_company=normalized_company,
        normalized_title=normalized_title,
        inferred_location=inferred_location,
        identity_fingerprint=identity_fingerprint,
        enrichment_fingerprint=enrichment_fingerprint,
        source_provider=_normalize_display_text(source_provider),
        source_query=_normalize_display_text(source_query),
        first_seen_at=first_seen,
        last_seen_at=last_seen,
    )


def candidate_identity_match_priority(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_identity = build_candidate_identity(candidate=left)
    right_identity = build_candidate_identity(candidate=right)

    signals = {
        "linkedinExact": bool(left_identity.canonical_linkedin_url and left_identity.canonical_linkedin_url == right_identity.canonical_linkedin_url),
        "linkedinSlug": bool(left_identity.linkedin_slug and left_identity.linkedin_slug == right_identity.linkedin_slug),
        "nameExact": bool(left_identity.normalized_name and left_identity.normalized_name == right_identity.normalized_name),
        "companyExact": bool(left_identity.normalized_company and left_identity.normalized_company == right_identity.normalized_company),
        "titleSimilarity": _text_similarity(left_identity.normalized_title, right_identity.normalized_title),
        "locationSimilarity": _text_similarity(left_identity.inferred_location, right_identity.inferred_location),
    }
    confidence = 0.0
    match_type = "rejected"
    if signals["linkedinExact"]:
        confidence = 1.0
        match_type = "linkedin_exact"
    elif signals["linkedinSlug"]:
        confidence = 0.98
        match_type = "linkedin_slug"
    else:
        if signals["nameExact"] and signals["companyExact"]:
            confidence += 0.56
            match_type = "name_company_exact"
        elif signals["nameExact"]:
            confidence += 0.40
            match_type = "name_exact"
        if signals["companyExact"]:
            confidence += 0.18
        confidence += signals["titleSimilarity"] * 0.14
        confidence += signals["locationSimilarity"] * 0.06
    confidence = round(min(1.0, max(0.0, confidence)), 4)
    ambiguous = confidence < 0.72
    return {
        "identity": left_identity.to_dict(),
        "matchIdentity": right_identity.to_dict(),
        "confidence": confidence,
        "matchType": match_type if not ambiguous else "rejected",
        "ambiguous": ambiguous,
        "signals": signals,
    }


def _text_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[a-z0-9]+", _normalize_text(left)))
    right_tokens = set(re.findall(r"[a-z0-9]+", _normalize_text(right)))
    if not left_tokens and not right_tokens:
        return 0.0
    union = left_tokens.union(right_tokens)
    if not union:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / len(union)
