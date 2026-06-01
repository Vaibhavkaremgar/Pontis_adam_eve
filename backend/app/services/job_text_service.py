from __future__ import annotations

import logging
import re
from typing import Any

from app.services.embedding_service import embed

logger = logging.getLogger(__name__)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_list(values: Any) -> list[str]:
    if isinstance(values, list):
        items = values
    elif isinstance(values, str) and values.strip():
        items = [values]
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _extract_voice_transcript(structured_data: Any) -> str:
    if not isinstance(structured_data, dict):
        return ""
    voice_extraction = structured_data.get("voice_extraction") or structured_data.get("transcript") or {}
    if not isinstance(voice_extraction, dict):
        return ""
    transcript = (
        voice_extraction.get("transcript")
        or voice_extraction.get("notes")
        or voice_extraction.get("summary")
        or ""
    )
    return _normalize_text(transcript)


def _normalize_job_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        value = [value]
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def build_job_text(job, structured_data: Any | None = None, transcript: str = "") -> str:
    resolved_structured_data = structured_data if isinstance(structured_data, dict) else getattr(job, "structured_data", None)
    if not isinstance(resolved_structured_data, dict):
        resolved_structured_data = {}

    transcript_text = _normalize_text(transcript) or _extract_voice_transcript(resolved_structured_data)
    role = _normalize_text(
        resolved_structured_data.get("role")
        or resolved_structured_data.get("title")
        or getattr(job, "title", "")
    )
    skills = _normalize_job_list(
        resolved_structured_data.get("skills")
        or resolved_structured_data.get("skills_required")
        or getattr(job, "skills_required", None)
    )
    experience = _normalize_text(
        resolved_structured_data.get("experience")
        or resolved_structured_data.get("experience_level")
        or resolved_structured_data.get("experienceRequired")
        or getattr(job, "experience_level", "")
        or getattr(job, "experience_required", "")
    )
    location = _normalize_text(
        resolved_structured_data.get("location")
        or getattr(job, "location", "")
    )
    compensation = _normalize_text(
        resolved_structured_data.get("compensation")
        or resolved_structured_data.get("salary_range")
        or getattr(job, "compensation", "")
    )
    work_authorization = _normalize_text(
        resolved_structured_data.get("workAuthorization")
        or resolved_structured_data.get("work_authorization")
        or getattr(job, "work_authorization", "")
    )
    remote_policy = _normalize_text(
        resolved_structured_data.get("remotePolicy")
        or resolved_structured_data.get("remote_policy")
        or getattr(job, "remote_policy", "")
    )
    responsibilities = _normalize_job_list(
        resolved_structured_data.get("responsibilities")
        or getattr(job, "responsibilities", None)
    )
    company_name = _normalize_text(
        resolved_structured_data.get("companyName")
        or resolved_structured_data.get("company")
        or getattr(getattr(job, "company", None), "name", "")
    )
    company_industry = _normalize_text(
        resolved_structured_data.get("industry")
        or getattr(getattr(job, "company", None), "industry", "")
    )
    company_description = _normalize_text(
        resolved_structured_data.get("companyDescription")
        or getattr(getattr(job, "company", None), "description", "")
    )
    original_jd = _normalize_text(getattr(job, "description", ""))
    if not original_jd:
        original_jd = _normalize_text(resolved_structured_data.get("description") or "")

    role_line = role or _normalize_text(getattr(job, "title", ""))
    skill_line = ", ".join(skills)
    job_text = (
        f"Title: {role_line}\n"
        f"Role: {role_line}\n"
        f"Experience: {experience}\n"
        f"Skills: {skill_line}\n\n"
        f"Responsibilities:\n" + ("\n".join(f"- {item}" for item in responsibilities) if responsibilities else "- Not specified") + "\n\n"
        f"Job Description:\n{original_jd}\n\n"
        f"Location: {location}\n"
        f"Compensation: {compensation}\n"
        f"Work Authorization: {work_authorization}\n"
        f"Remote Policy: {remote_policy}\n"
        f"Company: {company_name}\n"
        f"Industry: {company_industry}\n"
        f"Company Description: {company_description}\n\n"
        f"Voice Input:\n{transcript_text}"
    ).strip()
    if not job_text:
        job_text = original_jd or transcript_text or " "

    source = "structured_data" if role or skills or experience or location or compensation else "transcript" if transcript_text else "description"
    logger.info(
        "job_text_built job_id=%s source=%s has_structured_data=%s transcript_present=%s length=%s",
        getattr(job, "id", "unknown"),
        source,
        bool(role or skills or experience or location or compensation),
        bool(transcript_text),
        len(job_text),
    )
    return job_text


def build_job_embedding(job, structured_data: Any | None = None, transcript: str = "") -> list[float]:
    return embed(build_job_text(job, structured_data=structured_data, transcript=transcript))
