from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI
from sqlalchemy.orm import Session

from app.core.config import OPENAI_API_KEY, OPENAI_MODEL
from app.db.repositories import CompanyRepository, JobRepository
from app.services.candidate_service import build_job_text
from app.services.embedding_service import get_embedding
from app.services.metrics_service import log_metric
from app.services.openai_service import refine_description
from app.services.qdrant_service import delete_job_vectors, ensure_all_collections, upsert_job_chunks
from app.utils.exceptions import APIError
from app.utils.text import chunk_text

logger = logging.getLogger(__name__)

ROLE_KEYWORDS = [
    "backend engineer",
    "backend developer",
    "frontend engineer",
    "frontend developer",
    "full stack engineer",
    "full stack developer",
    "data engineer",
    "machine learning engineer",
    "ml engineer",
    "devops engineer",
    "platform engineer",
    "product manager",
    "product designer",
    "qa engineer",
    "security engineer",
    "cloud engineer",
    "mobile engineer",
    "ios engineer",
    "android engineer",
    "recruiter",
]

SKILL_KEYWORDS = [
    "python",
    "fastapi",
    "django",
    "flask",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "node.js",
    "postgres",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "aws",
    "amazon web services",
    "gcp",
    "azure",
    "docker",
    "kubernetes",
    "terraform",
    "spark",
    "airflow",
    "sql",
    "nosql",
    "machine learning",
    "ml",
    "llm",
    "rag",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "go",
    "rust",
    "ruby",
    "rails",
    "php",
    "c#",
    "c++",
    "linux",
    "git",
]


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_list(values: Any, *, max_items: int = 20) -> list[str]:
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
        if len(normalized) >= max_items:
            break
    return normalized


def _contains_keyword(text: str, keyword: str) -> bool:
    if not text or not keyword:
        return False
    escaped = re.escape(keyword)
    if keyword.isalnum() and len(keyword) > 1:
        pattern = rf"\b{escaped}\b"
    else:
        pattern = escaped
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def extract_structured_data_fallback(transcript: str) -> dict[str, Any]:
    text = _normalize_text(transcript)
    lowered = text.lower()

    role = ""
    for keyword in ROLE_KEYWORDS:
        if _contains_keyword(lowered, keyword):
            role = keyword
            break

    skills = [skill for skill in SKILL_KEYWORDS if _contains_keyword(lowered, skill)]
    experience_match = re.search(r"\b\d+\s*[-\u2013]\s*\d+\s+years\b", text, flags=re.IGNORECASE)
    if not experience_match:
        experience_match = re.search(r"\b\d+\+?\s+years\b", text, flags=re.IGNORECASE)

    return {
        "role": role,
        "skills": skills,
        "experience": experience_match.group(0) if experience_match else "",
    }


def _extract_structured_hiring_data(*, transcript: str) -> dict[str, Any] | None:
    if not OPENAI_API_KEY:
        log_metric("fallback", source="voice_structured_extraction", reason="unconfigured")
        logger.info("voice_extraction_skipped reason=OPENAI_API_KEY_missing")
        return None

    prompt = (
        "Extract structured hiring information from the following conversation transcript.\n"
        "Return ONLY valid JSON.\n"
        "Use this exact schema:\n"
        "{\n"
        '  "job": {\n'
        '    "title": "",\n'
        '    "responsibilities": [],\n'
        '    "skills_required": [],\n'
        '    "experience_level": "",\n'
        '    "location": "",\n'
        '    "salary_range": ""\n'
        "  },\n"
        '  "company": {\n'
        '    "name": "",\n'
        '    "industry": "",\n'
        '    "description": ""\n'
        "  },\n"
        '  "confidence": 0.0\n'
        "}\n"
        "If a field is missing in transcript, use empty string or empty array.\n\n"
        f"Transcript:\n{transcript}\n"
    )

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
            temperature=0,
        )
        payload = _extract_json_object((response.output_text or "").strip())
        if payload is None:
            log_metric("error", source="voice_structured_extraction", kind="invalid_json")
            logger.warning("voice_extraction_failed reason=invalid_json")
            return None
        return payload
    except Exception as exc:
        log_metric("error", source="voice_structured_extraction", kind="request_failed")
        logger.warning("voice_extraction_failed reason=request_failed", exc_info=exc)
        return None


def _merge_unique(existing: list[str], incoming: list[str], *, limit: int = 30) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
        if len(merged) >= limit:
            break
    return merged


def _enhance_description(
    *,
    refined_description: str,
    responsibilities: list[str],
    skills_required: list[str],
    experience_level: str,
    company_name: str,
    company_description: str,
) -> str:
    base = _normalize_text(refined_description)
    lines = [base] if base else []

    if responsibilities:
        lines.append("Responsibilities:")
        lines.extend(f"- {item}" for item in responsibilities)

    if skills_required:
        lines.append("Required Skills:")
        lines.extend(f"- {item}" for item in skills_required)

    if experience_level:
        lines.append(f"Experience Level: {experience_level}")

    if company_name:
        lines.append(f"Company: {company_name}")

    if company_description:
        lines.append(f"Company Context: {company_description}")

    return "\n".join(lines).strip()


def _build_job_vector_source(job) -> str:
    skills_text = ", ".join(skill for skill in (job.skills_required or []) if skill) or "Not specified"
    responsibilities_text = "\n".join(f"- {item}" for item in (job.responsibilities or []) if item) or "- Not specified"
    company_name = _normalize_text(getattr(job.company, "name", "")) if getattr(job, "company", None) else ""
    company_industry = _normalize_text(getattr(job.company, "industry", "")) if getattr(job, "company", None) else ""
    company_description = _normalize_text(getattr(job.company, "description", "")) if getattr(job, "company", None) else ""

    return (
        f"Title: {job.title}\n"
        f"Skills Required: {skills_text}\n"
        f"Responsibilities:\n{responsibilities_text}\n"
        f"Description: {job.description}\n"
        f"Experience Level: {job.experience_level}\n"
        f"Location: {job.location}\n"
        f"Compensation: {job.compensation}\n"
        f"Work Authorization: {job.work_authorization}\n"
        f"Company: {company_name}\n"
        f"Industry: {company_industry}\n"
        f"Company Description: {company_description}"
    )


def _sanitize_structured_payload(payload: dict[str, Any]) -> dict[str, Any]:
    job_raw = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    company_raw = payload.get("company") if isinstance(payload.get("company"), dict) else {}
    if not job_raw and any(key in payload for key in ("role", "skills", "experience")):
        job_raw = {
            "title": payload.get("role", ""),
            "skills_required": payload.get("skills", []),
            "experience_level": payload.get("experience", ""),
        }
    if not company_raw and any(key in payload for key in ("companyName", "industry")):
        company_raw = {
            "name": payload.get("companyName", ""),
            "industry": payload.get("industry", ""),
            "description": payload.get("companyDescription", ""),
        }
    confidence_raw = payload.get("confidence")

    confidence = 0.0
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "job": {
            "title": _normalize_text(job_raw.get("title")),
            "responsibilities": _normalize_list(job_raw.get("responsibilities")),
            "skills_required": _normalize_list(job_raw.get("skills_required")),
            "experience_level": _normalize_text(job_raw.get("experience_level")),
            "location": _normalize_text(job_raw.get("location")),
            "salary_range": _normalize_text(job_raw.get("salary_range")),
        },
        "company": {
            "name": _normalize_text(company_raw.get("name")),
            "industry": _normalize_text(company_raw.get("industry")),
            "description": _normalize_text(company_raw.get("description")),
        },
        "confidence": confidence,
    }


def refine_job_with_voice(*, db: Session, job_id: str, voice_notes: list[str], transcript: str = "") -> dict:
    jobs = JobRepository(db)
    companies = CompanyRepository(db)

    job = jobs.get(job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    # Prefer the full structured transcript if provided; fall back to voice_notes list.
    if transcript.strip():
        raw_text = transcript.strip()
    else:
        transcript_parts = [_normalize_text(note) for note in voice_notes if _normalize_text(note)]
        raw_text = "\n".join(transcript_parts).strip()

    if not raw_text:
        raise APIError("voiceNotes must include at least one non-empty transcript", status_code=400)

    logger.info("voice_refine_start job_id=%s transcript_length=%s", job_id, len(raw_text))

    extraction_raw = _extract_structured_hiring_data(transcript=raw_text)
    used_fallback = extraction_raw is None
    fallback_raw = extract_structured_data_fallback(raw_text)
    structured_payload = extraction_raw or fallback_raw
    structured = _sanitize_structured_payload(structured_payload or {})

    extracted_job = structured["job"]
    extracted_company = structured["company"]
    confidence = structured["confidence"]

    existing_company_name = _normalize_text(getattr(job.company, "name", "")) if getattr(job, "company", None) else ""
    existing_company_description = (
        _normalize_text(getattr(job.company, "description", "")) if getattr(job, "company", None) else ""
    )
    existing_company_industry = _normalize_text(getattr(job.company, "industry", "")) if getattr(job, "company", None) else ""

    merged_title = job.title if _normalize_text(job.title) else extracted_job["title"]
    merged_location = job.location if _normalize_text(job.location) else extracted_job["location"]
    merged_compensation = job.compensation if _normalize_text(job.compensation) else extracted_job["salary_range"]
    merged_experience_level = job.experience_level if _normalize_text(job.experience_level) else extracted_job["experience_level"]
    merged_skills = _merge_unique(job.skills_required or [], extracted_job["skills_required"])
    merged_responsibilities = _merge_unique(job.responsibilities or [], extracted_job["responsibilities"])

    merged_company_name = existing_company_name or extracted_company["name"]
    merged_company_industry = existing_company_industry or extracted_company["industry"]
    merged_company_description = existing_company_description or extracted_company["description"]

    # Use the full transcript (both sides) for richer description refinement.
    notes_for_refinement = [raw_text] if raw_text else voice_notes
    refined_description = refine_description(description=job.description, voice_notes=notes_for_refinement)
    enriched_description = _enhance_description(
        refined_description=refined_description,
        responsibilities=merged_responsibilities,
        skills_required=merged_skills,
        experience_level=merged_experience_level,
        company_name=merged_company_name,
        company_description=merged_company_description,
    )

    if used_fallback:
        log_metric("fallback", source="voice_structured_extraction", reason="fallback_extraction_used")
        fallback_fields = [
            name
            for name, value in {
                "role": fallback_raw.get("role"),
                "skills": fallback_raw.get("skills"),
                "experience": fallback_raw.get("experience"),
            }.items()
            if value
        ]
        logger.info(
            "fallback_extraction_used job_id=%s role=%s skills=%s experience=%s",
            job_id,
            fallback_raw.get("role") or "unknown",
            "|".join(fallback_raw.get("skills") or []) or "none",
            fallback_raw.get("experience") or "none",
        )
    else:
        extracted_fields = [
            name
            for name, value in {
                "job.title": extracted_job["title"],
                "job.skills_required": extracted_job["skills_required"],
                "job.responsibilities": extracted_job["responsibilities"],
                "job.experience_level": extracted_job["experience_level"],
                "job.location": extracted_job["location"],
                "job.salary_range": extracted_job["salary_range"],
                "company.name": extracted_company["name"],
                "company.industry": extracted_company["industry"],
                "company.description": extracted_company["description"],
            }.items()
            if value
        ]
        log_metric(
            "voice_extraction",
            success=True,
            job_id=job_id,
            extracted_fields="|".join(extracted_fields) or "none",
            confidence=round(confidence, 3),
        )
        logger.info(
            "voice_extraction_success job_id=%s confidence=%.3f fields=%s",
            job_id,
            confidence,
            ",".join(extracted_fields) or "none",
        )

    updated = jobs.update_structured_fields(
        job_id=job_id,
        title=merged_title,
        description=enriched_description,
        responsibilities=merged_responsibilities,
        skills_required=merged_skills,
        experience_level=merged_experience_level,
        location=merged_location,
        compensation=merged_compensation,
        structured_data={
            "voiceExtraction": {
                "source": "fallback" if used_fallback else "openai",
                "job": extracted_job,
                "company": extracted_company,
                "confidence": confidence,
                "success": True,
                "transcript": raw_text,
                "fallback": fallback_raw,
            },
            "voiceTranscript": raw_text,
        },
    )
    if not updated:
        raise APIError("Job not found", status_code=404)

    companies.update_profile(
        company_id=job.company_id,
        name=merged_company_name if not existing_company_name else None,
        industry=merged_company_industry if not existing_company_industry else None,
        description=merged_company_description if not existing_company_description else None,
    )
    db.commit()
    db.refresh(updated)

    # Re-embed the enriched job and upsert to Qdrant.
    # Do NOT call fetch_ranked_candidates here — frontend triggers that separately with refresh=true.
    vector_source = build_job_text(updated, structured_data=updated.structured_data, transcript=raw_text)
    chunks = chunk_text(vector_source)
    vectors = [get_embedding(chunk) for chunk in chunks]
    ensure_all_collections()
    delete_job_vectors(job_id)
    upsert_job_chunks(job_id, vectors, chunks)

    logger.info(
        "voice_refine_complete job_id=%s chunks=%s skills=%s responsibilities=%s",
        job_id,
        len(chunks),
        len(merged_skills),
        len(merged_responsibilities),
    )

    return {
        "refined": True,
        "job": {
            "title": updated.title,
            "description": updated.description,
            "location": updated.location,
            "compensation": updated.compensation,
            "skills_required": updated.skills_required or [],
            "responsibilities": updated.responsibilities or [],
            "experience_level": updated.experience_level or "",
        },
        "extraction": {
            "success": True,
            "usedFallback": used_fallback,
            "confidence": confidence,
            "fields": extracted_fields if not used_fallback else fallback_fields,
        },
    }

