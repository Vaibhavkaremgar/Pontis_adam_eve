from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.services.metrics_service import log_metric

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def tokenize(text: str) -> list[str]:
    normalized = _normalize_text(text)
    return [token for token in _TOKEN_RE.findall(normalized) if token]


def candidate_document_text(*, candidate: dict[str, Any] | None = None, profile: Any | None = None) -> str:
    candidate = candidate or {}
    profile_raw = dict(getattr(profile, "raw_data", None) or {}) if profile is not None else {}
    merged = {**profile_raw, **candidate}
    parts: list[str] = []
    for key in (
        "name",
        "full_name",
        "role",
        "job_title",
        "headline",
        "company",
        "job_company_name",
        "location",
        "summary",
        "bio",
        "experience_summary",
    ):
        value = _normalize_text(merged.get(key))
        if value:
            parts.append(value)
    for key in ("years_experience", "yearsExperience"):
        value = merged.get(key)
        if isinstance(value, (int, float)):
            parts.append(f"{float(value):g} years experience")
        elif isinstance(value, str):
            normalized = _normalize_text(value)
            if normalized:
                parts.append(normalized)
    skills = merged.get("skills") or []
    if isinstance(skills, list):
        parts.extend(_normalize_text(str(item)) for item in skills if _normalize_text(str(item)))
    for key in ("companies", "projects", "education", "certifications", "domain_experience", "domainExperience"):
        values = merged.get(key) or []
        if isinstance(values, list):
            parts.extend(_normalize_text(str(item)) for item in values if _normalize_text(str(item)))
    resume_text = _normalize_text(merged.get("raw_resume_text") or merged.get("resume_text") or merged.get("parsed_resume_text") or "")
    if resume_text:
        parts.append(resume_text[:8000])
    return " ".join(parts)


def job_query_text(job, *, learned_tokens: list[str] | None = None, preferred_roles: list[str] | None = None) -> str:
    parts: list[str] = []
    structured = getattr(job, "structured_data", None)
    if not isinstance(structured, dict):
        structured = {}
    def _structured_value(*keys: str) -> str:
        for key in keys:
            value = structured.get(key)
            if isinstance(value, (int, float)):
                return f"{float(value):g}"
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    for key in (
        "title",
        "description",
        "location",
        "compensation",
        "work_authorization",
        "workAuthorization",
        "remote_policy",
        "remotePolicy",
        "experience_level",
        "experienceLevel",
        "experience_required",
        "experienceRequired",
    ):
        value = _normalize_text(getattr(job, key, "") or _structured_value(key, key.replace("_", ""), key.split("_")[0] + key.split("_")[-1]) or "")
        if value:
            parts.append(value)
    if _structured_value("companyName", "company"):
        parts.append(_structured_value("companyName", "company"))
    if _structured_value("industry"):
        parts.append(_structured_value("industry"))
    if _structured_value("salary_range", "salaryRange"):
        parts.append(_structured_value("salary_range", "salaryRange"))
    skills = structured.get("skills") or structured.get("skills_required") or getattr(job, "skills_required", None) or []
    responsibilities = structured.get("responsibilities") or getattr(job, "responsibilities", None) or []
    if isinstance(skills, list):
        parts.extend(_normalize_text(str(item)) for item in skills if _normalize_text(str(item)))
    if isinstance(responsibilities, list):
        parts.extend(_normalize_text(str(item)) for item in responsibilities if _normalize_text(str(item)))
    parts.extend(_normalize_text(token) for token in (learned_tokens or []) if _normalize_text(token))
    parts.extend(_normalize_text(role) for role in (preferred_roles or []) if _normalize_text(role))
    return " ".join(parts)


def bm25_like_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0

    doc_counts = Counter(doc_tokens)
    query_counts = Counter(query_tokens)
    doc_len = max(1, len(doc_tokens))
    avg_doc_len = max(1.0, float(len(doc_tokens)))
    k1 = 1.2
    b = 0.75
    score = 0.0

    for token, query_freq in query_counts.items():
        tf = float(doc_counts.get(token, 0))
        if tf <= 0:
            continue
        idf = math.log(1.0 + ((avg_doc_len - tf + 0.5) / (tf + 0.5)))
        numerator = tf * (k1 + 1.0)
        denominator = tf + k1 * (1.0 - b + b * (doc_len / avg_doc_len))
        score += idf * (numerator / max(0.001, denominator)) * min(3, query_freq)

    # Normalize to a stable 0-1-ish range for downstream blending.
    return max(0.0, min(1.0, score / 6.0))


def lexical_overlap_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    query_set = set(query_tokens)
    doc_set = set(doc_tokens)
    if not query_set or not doc_set:
        return 0.0
    return len(query_set.intersection(doc_set)) / max(1, len(query_set.union(doc_set)))


def structured_match_score(*, job, candidate: dict[str, Any] | None = None, profile: Any | None = None) -> float:
    candidate = candidate or {}
    profile_raw = dict(getattr(profile, "raw_data", None) or {}) if profile is not None else {}
    merged = {**profile_raw, **candidate}

    structured = getattr(job, "structured_data", None)
    if not isinstance(structured, dict):
        structured = {}
    job_skills = {
        tokenize(str(skill))[0]
        for skill in ((structured.get("skills") or structured.get("skills_required") or []) or getattr(job, "skills_required", None) or [])
        if tokenize(str(skill))
    }
    candidate_skills = {tokenize(str(skill))[0] for skill in (merged.get("skills") or []) if tokenize(str(skill))}

    def _text_value(*keys: str) -> str:
        for key in keys:
            value = merged.get(key)
            if isinstance(value, (int, float)):
                return f"{float(value):g}"
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _job_text(*keys: str) -> str:
        for key in keys:
            value = getattr(job, key, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _job_structured(*keys: str) -> str:
        for key in keys:
            for candidate_key in (key, key.replace("_", ""), key[:1].lower() + key[1:] if key and key[0].isupper() else key):
                value = structured.get(candidate_key)
                if isinstance(value, (int, float)):
                    return f"{float(value):g}"
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _alignment_score(left: str, right: str) -> float:
        left = _normalize_text(left)
        right = _normalize_text(right)
        if not left or not right:
            return 0.5
        if left == right:
            return 1.0
        left_tokens = set(tokenize(left))
        right_tokens = set(tokenize(right))
        if not left_tokens or not right_tokens:
            return 0.5
        return len(left_tokens.intersection(right_tokens)) / max(1, len(left_tokens.union(right_tokens)))

    skill_overlap = 0.0
    if job_skills and candidate_skills:
        skill_overlap = len(job_skills.intersection(candidate_skills)) / max(1, len(job_skills.union(candidate_skills)))

    experience_alignment = _alignment_score(
        _job_text("experience_level", "experience_required", "experienceRequired", "experience"),
        _text_value("experience", "experience_level", "experience_summary", "years_experience", "yearsExperience"),
    )
    location_alignment = _alignment_score(
        _job_text("location"),
        _text_value("location", "location_name", "location_region", "location_country"),
    )
    compensation_alignment = _alignment_score(
        _job_text("compensation", "salary_range") or _job_structured("compensation", "salary_range", "salaryRange"),
        _text_value("compensation", "salary_range", "salary", "target_salary", "desired_compensation"),
    )
    if not job_skills and not candidate_skills and experience_alignment == location_alignment == compensation_alignment == 0.5:
        return 0.5
    return max(
        0.0,
        min(
            1.0,
            (skill_overlap * 0.50)
            + (experience_alignment * 0.20)
            + (location_alignment * 0.20)
            + (compensation_alignment * 0.10),
        ),
    )


@dataclass(frozen=True)
class RetrievalAttribution:
    vector_score: float
    lexical_score: float
    structured_score: float
    recruiter_score: float
    hybrid_score: float
    source: str


def hybrid_retrieval_score(
    *,
    job,
    candidate: dict[str, Any] | None = None,
    profile: Any | None = None,
    vector_score: float = 0.0,
    recruiter_score: float = 0.0,
    learned_tokens: list[str] | None = None,
    preferred_roles: list[str] | None = None,
) -> RetrievalAttribution:
    query_tokens = tokenize(job_query_text(job, learned_tokens=learned_tokens, preferred_roles=preferred_roles))
    doc_text = candidate_document_text(candidate=candidate, profile=profile)
    doc_tokens = tokenize(doc_text)
    lexical = max(
        bm25_like_score(query_tokens, doc_tokens),
        lexical_overlap_score(query_tokens, doc_tokens),
    )
    structured = structured_match_score(job=job, candidate=candidate, profile=profile)

    # Weighted fusion favors vector recall while letting lexical and structured
    # signals rescue niche skill matches and role-specific candidates.
    hybrid = (
        max(0.0, min(1.0, vector_score)) * 0.40
        + lexical * 0.35
        + structured * 0.20
        + max(0.0, min(1.0, recruiter_score)) * 0.05
    )
    if lexical >= 0.60 and vector_score < 0.35:
        hybrid += min(0.12, lexical * 0.15)
    return RetrievalAttribution(
        vector_score=max(0.0, min(1.0, vector_score)),
        lexical_score=max(0.0, min(1.0, lexical)),
        structured_score=max(0.0, min(1.0, structured)),
        recruiter_score=max(0.0, min(1.0, recruiter_score)),
        hybrid_score=max(0.0, min(1.0, hybrid)),
        source="hybrid",
    )


def rerank_candidates(
    *,
    job,
    rows: list[dict[str, Any]],
    recruiter_score_lookup: dict[str, float] | None = None,
    learned_tokens: list[str] | None = None,
    preferred_roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    recruiter_score_lookup = recruiter_score_lookup or {}
    ranked: list[dict[str, Any]] = []
    lexical_scores: list[float] = []

    for row in rows:
        candidate_id = str(row.get("candidate_id") or "").strip()
        profile = row.get("profile")
        vector_score = float(row.get("semantic") or 0.0)
        recruiter_score = float(recruiter_score_lookup.get(candidate_id) or 0.0)
        attribution = hybrid_retrieval_score(
            job=job,
            candidate=row.get("payload") if isinstance(row.get("payload"), dict) else None,
            profile=profile,
            vector_score=vector_score,
            recruiter_score=recruiter_score,
            learned_tokens=learned_tokens,
            preferred_roles=preferred_roles,
        )
        lexical_scores.append(attribution.lexical_score)
        ranked.append(
            {
                **row,
                "retrieval": attribution,
                "hybrid_score": attribution.hybrid_score,
            }
        )

    ranked.sort(key=lambda item: float(item.get("hybrid_score") or 0.0), reverse=True)
    if lexical_scores:
        log_metric(
            "retrieval_quality",
            lexical_mean=round(sum(lexical_scores) / len(lexical_scores), 4),
            lexical_max=round(max(lexical_scores), 4),
            lexical_min=round(min(lexical_scores), 4),
            total=len(lexical_scores),
        )
    if ranked:
        top = ranked[0].get("retrieval")
        if top and float(getattr(top, "lexical_score", 0.0) or 0.0) > 0.65 and float(getattr(top, "vector_score", 0.0) or 0.0) < 0.35:
            log_metric(
                "retrieval_mismatch",
                top_candidate=str(ranked[0].get("candidate_id") or ""),
                lexical=round(float(getattr(top, "lexical_score", 0.0) or 0.0), 4),
                vector=round(float(getattr(top, "vector_score", 0.0) or 0.0), 4),
                hybrid=round(float(getattr(top, "hybrid_score", 0.0) or 0.0), 4),
            )
    return ranked


def retrieval_explanation(attribution: RetrievalAttribution) -> dict[str, float | str]:
    return {
        "source": attribution.source,
        "vectorScore": round(attribution.vector_score, 4),
        "lexicalScore": round(attribution.lexical_score, 4),
        "structuredScore": round(attribution.structured_score, 4),
        "recruiterScore": round(attribution.recruiter_score, 4),
        "hybridScore": round(attribution.hybrid_score, 4),
    }
