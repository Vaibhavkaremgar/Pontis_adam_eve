from __future__ import annotations

import difflib
import logging
import re
from typing import Any

from app.services.identity.candidate_identity_service import build_candidate_identity, candidate_identity_match_priority

logger = logging.getLogger(__name__)

_MIN_LINKEDIN_CONFIDENCE = 0.98
_MIN_STRONG_CONFIDENCE = 0.82
_MIN_ACCEPTABLE_CONFIDENCE = 0.72


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
    return text.rstrip("/")


def _tokens(value: Any) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", _normalize_lower(value)) if len(token) > 1}


def _similarity(left: Any, right: Any) -> float:
    left_text = _normalize_lower(left)
    right_text = _normalize_lower(right)
    if not left_text or not right_text:
        return 0.0
    token_overlap = 0.0
    left_tokens = _tokens(left_text)
    right_tokens = _tokens(right_text)
    if left_tokens or right_tokens:
        union = left_tokens.union(right_tokens)
        if union:
            token_overlap = len(left_tokens.intersection(right_tokens)) / len(union)
    ratio = difflib.SequenceMatcher(None, left_text, right_text).ratio()
    return round(max(ratio, token_overlap), 4)


def match_apollo_person(*, candidate: dict[str, Any], person: dict[str, Any]) -> dict[str, Any]:
    candidate_identity = build_candidate_identity(candidate=candidate, source_provider="apollo", source_query="")
    person_identity = build_candidate_identity(
        candidate={
            "linkedin_url": person.get("linkedin_url") or person.get("linkedinUrl") or person.get("profile_url") or person.get("profileUrl") or "",
            "full_name": person.get("name") or person.get("full_name") or "",
            "current_company": person.get("organization_name") or person.get("company") or person.get("current_company") or person.get("job_company_name") or "",
            "title": person.get("title") or person.get("headline") or person.get("job_title") or "",
            "location": person.get("present_raw_address") or person.get("city") or person.get("state") or person.get("country") or person.get("location") or "",
        },
        source_provider="apollo",
        source_query="",
    )
    candidate_linkedin = candidate_identity.canonical_linkedin_url
    person_linkedin = person_identity.canonical_linkedin_url
    candidate_name = candidate_identity.normalized_name
    person_name = person_identity.normalized_name
    candidate_company = candidate_identity.normalized_company
    person_company = person_identity.normalized_company
    candidate_title = candidate_identity.normalized_title
    person_title = person_identity.normalized_title
    candidate_location = candidate_identity.inferred_location
    person_location = person_identity.inferred_location

    signals = {
        "linkedinExactMatch": bool(candidate_linkedin and person_linkedin and candidate_linkedin == person_linkedin),
        "nameExactMatch": bool(candidate_name and person_name and _normalize_lower(candidate_name) == _normalize_lower(person_name)),
        "companyExactMatch": bool(candidate_company and person_company and _normalize_lower(candidate_company) == _normalize_lower(person_company)),
        "titleSimilarity": _similarity(candidate_title, person_title),
        "companySimilarity": _similarity(candidate_company, person_company),
        "locationSimilarity": _similarity(candidate_location, person_location),
        "nameSimilarity": _similarity(candidate_name, person_name),
    }

    confidence = 0.0
    match_type = "rejected"
    matched_fields: list[str] = []

    if signals["linkedinExactMatch"]:
        confidence = 1.0
        match_type = "linkedin_exact"
        matched_fields.append("linkedin_url")
    else:
        if signals["nameExactMatch"] and signals["companyExactMatch"]:
            confidence += 0.58
            match_type = "name_company_exact"
            matched_fields.extend(["name", "company"])
        elif signals["nameExactMatch"]:
            confidence += 0.46
            match_type = "name_exact"
            matched_fields.append("name")

        if signals["companyExactMatch"]:
            confidence += 0.20
            matched_fields.append("company")

        if signals["titleSimilarity"] >= 0.65:
            confidence += 0.12
            matched_fields.append("title")
        elif signals["titleSimilarity"] >= 0.45:
            confidence += 0.07

        if signals["companySimilarity"] >= 0.65:
            confidence += 0.10
        elif signals["companySimilarity"] >= 0.45:
            confidence += 0.05

        if signals["locationSimilarity"] >= 0.55:
            confidence += 0.05

        confidence += signals["nameSimilarity"] * 0.10

        if not matched_fields and signals["nameSimilarity"] >= 0.85 and signals["companySimilarity"] >= 0.70:
            match_type = "fuzzy_name_company"
            matched_fields.extend(["name", "company"])
            confidence += 0.10
        elif not matched_fields and signals["companySimilarity"] >= 0.80 and signals["titleSimilarity"] >= 0.60:
            match_type = "fuzzy_company_title"
            matched_fields.extend(["company", "title"])
            confidence += 0.08

    confidence = round(max(0.0, min(1.0, confidence)), 4)
    identity_priority = candidate_identity_match_priority(candidate, person)
    if confidence >= _MIN_LINKEDIN_CONFIDENCE:
        match_type = "linkedin_exact"
        matched_fields = matched_fields or ["linkedin_url"]
    elif confidence >= _MIN_STRONG_CONFIDENCE and match_type == "rejected":
        match_type = "strong_fuzzy"
    elif confidence >= _MIN_ACCEPTABLE_CONFIDENCE and match_type == "rejected":
        match_type = "acceptable_fuzzy"

    return {
        "confidence": max(confidence, float(identity_priority.get("confidence") or 0.0)),
        "matchType": identity_priority.get("matchType") or match_type,
        "matchedFields": sorted(set(matched_fields)),
        "signals": signals,
        "candidate": {
            "linkedinUrl": candidate_linkedin,
            "name": candidate_name,
            "company": candidate_company,
            "title": candidate_title,
            "location": candidate_location,
        },
        "person": {
            "linkedinUrl": person_linkedin,
            "name": person_name,
            "company": person_company,
            "title": person_title,
            "location": person_location,
            "personId": _normalize_text(person.get("id") or person.get("person_id") or person.get("apollo_id") or person.get("uuid") or ""),
        },
    }


def match_apollo_people(candidate: dict[str, Any], people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for person in people:
        if not isinstance(person, dict):
            continue
        trace = match_apollo_person(candidate=candidate, person=person)
        person_id = trace["person"]["personId"]
        dedupe_key = person_id or trace["person"]["linkedinUrl"] or _normalize_lower(trace["person"]["name"])
        if dedupe_key and dedupe_key in seen:
            continue
        if dedupe_key:
            seen.add(dedupe_key)
        ranked.append({**person, "_identity_match": trace})

    ranked.sort(
        key=lambda item: (
            -float(dict(item.get("_identity_match") or {}).get("confidence") or 0.0),
            -1.0 if bool(dict(item.get("_identity_match") or {}).get("signals", {}).get("linkedinExactMatch")) else 0.0,
            -1.0 if bool(dict(item.get("_identity_match") or {}).get("signals", {}).get("nameExactMatch")) else 0.0,
            -1.0 if bool(dict(item.get("_identity_match") or {}).get("signals", {}).get("companyExactMatch")) else 0.0,
            _normalize_lower(item.get("name") or item.get("full_name") or ""),
        )
    )
    return ranked


def build_apollo_match_trace(*, candidate: dict[str, Any], person: dict[str, Any] | None, confidence: float, status: str) -> dict[str, Any]:
    trace = match_apollo_person(candidate=candidate, person=person or {}) if person else {}
    trace.update(
        {
            "confidence": round(float(confidence or trace.get("confidence") or 0.0), 4),
            "status": status,
        }
    )
    return trace
