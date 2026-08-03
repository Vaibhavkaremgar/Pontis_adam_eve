from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import GROQ_API_KEY, OPEN_ROUTER_API
from app.db.repositories import CompanyRepository, JobIntakeRepository, JobRepository
from app.services.embedding_service import get_embedding
from app.services.llm_service import generate
from app.services.metrics_service import log_metric
from app.services.job_text_service import build_job_text
from app.services.prompt_sanitizer import sanitize_prompt_block, sanitize_prompt_text
from app.services.qdrant_service import delete_job_vectors, ensure_all_collections, upsert_job_chunks
from app.utils.exceptions import APIError
from app.utils.text import chunk_text

logger = logging.getLogger(__name__)

SENIORITY_HINTS: list[tuple[str, str]] = [
    ("head", "leadership"),
    ("director", "leadership"),
    ("vp", "leadership"),
    ("principal", "leadership"),
    ("senior", "senior"),
    ("lead", "senior"),
    ("5+", "senior"),
    ("6+", "senior"),
    ("mid", "mid-level"),
    ("3-5 years", "mid-level"),
    ("intermediate", "mid-level"),
    ("junior", "junior"),
    ("entry", "junior"),
    ("fresher", "junior"),
    ("0-2 years", "junior"),
]

COMPANY_STAGE_HINTS: list[tuple[str, str]] = [
    ("enterprise", "enterprise"),
    ("large", "enterprise"),
    ("mnc", "enterprise"),
    ("fortune", "enterprise"),
    ("growth", "growth-stage"),
    ("scale", "growth-stage"),
    ("series c", "growth-stage"),
    ("series a", "series-b"),
    ("series b", "series-b"),
    ("seed", "early-stage"),
    ("early stage", "early-stage"),
    ("pre-series", "early-stage"),
]

SKILL_KEYWORD_HINTS: list[tuple[str, str]] = [
    ("saas sales", "SaaS sales"),
    ("pipeline management", "pipeline management"),
    ("crm", "CRM"),
    ("salesforce", "Salesforce"),
    ("cold calling", "cold calling"),
    ("enterprise sales", "enterprise sales"),
    ("account management", "account management"),
    ("business development", "business development"),
    ("prospecting", "prospecting"),
    ("qualification", "qualification"),
    ("product-led growth", "product-led growth"),
    ("inbound", "inbound"),
    ("outbound", "outbound"),
    ("python", "Python"),
    ("fastapi", "FastAPI"),
    ("postgresql", "PostgreSQL"),
    ("postgres", "PostgreSQL"),
    ("redis", "Redis"),
    ("docker", "Docker"),
    ("kubernetes", "Kubernetes"),
    ("sql", "SQL"),
    ("figma", "Figma"),
    ("agile", "Agile"),
    ("user research", "user research"),
    ("roadmap", "product roadmap"),
]


def _detect_label_from_transcript(text: str, hints: list[tuple[str, str]], default: str = "") -> str:
    lowered = _normalize_text(text).lower()
    for needle, label in hints:
        if needle in lowered:
            return label
    return default


def _extract_skill_keywords_from_transcript(text: str, *, existing: list[str] | None = None) -> list[str]:
    lowered = _normalize_text(text).lower()
    detected: list[str] = []
    seen: set[str] = {item.lower() for item in (existing or []) if _normalize_text(item)}

    for needle, label in SKILL_KEYWORD_HINTS:
        if needle in lowered and label.lower() not in seen:
            detected.append(label)
            seen.add(label.lower())
    return detected


def _extract_nice_to_have_skills_from_transcript(text: str, *, existing: list[str] | None = None) -> list[str]:
    lowered = _normalize_text(text).lower()
    candidates: list[str] = []
    seen: set[str] = {item.lower() for item in (existing or []) if _normalize_text(item)}

    marker_spans = [
        "nice to have",
        "preferred",
        "bonus",
        "would be nice",
        "would be good to have",
    ]
    for marker in marker_spans:
        start = lowered.find(marker)
        if start < 0:
            continue
        window = lowered[start : start + 240]
        for needle, label in SKILL_KEYWORD_HINTS:
            if needle in window and label.lower() not in seen and label not in candidates:
                candidates.append(label)
                seen.add(label.lower())

    return candidates

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


def _comparison_token(token: str) -> str:
    return re.sub(r"^[^\w]+|[^\w]+$", "", token).lower()




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


def _extract_questions(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        items = None
        for key in ("questions", "async_questions", "asyncQuestions", "recommended_questions", "recommendedQuestions"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        if items is None:
            return []
    elif isinstance(payload, list):
        items = payload
    else:
        return []

    questions: list[str] = []
    for item in items:
        if isinstance(item, str):
            text = _normalize_text(item)
        elif isinstance(item, dict):
            text = _normalize_text(item.get("question") or item.get("text") or item.get("content"))
        else:
            text = ""
        if text:
            questions.append(text)
    return _normalize_list(questions, max_items=10)


def _normalize_list(values: Any, *, max_items: int = 10) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value)
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
    if not (GROQ_API_KEY or OPEN_ROUTER_API):
        log_metric("fallback", source="voice_structured_extraction", reason="unconfigured")
        logger.info("voice_extraction_skipped reason=LLM_PROVIDER_missing")
        return None

    transcript_text = _normalize_text(transcript)
    if len(transcript_text) > 20000:
        logger.info("transcript_truncated original_len=%s truncated_to=20000", len(transcript_text))
        transcript_text = transcript_text[:20000]

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
        '  "remote_policy": "remote|hybrid|onsite",\n'
        '  "experience_required": "",\n'
        '  "education_level": "",\n'
        '  "preferred_institutions": [],\n'
        '  "certifications": [],\n'
        '  "compensation": "",\n'
        '  "company": {\n'
        '    "name": "",\n'
        '    "industry": "",\n'
        '    "description": ""\n'
        "  },\n"
        '  "confidence": 0.0\n'
        "}\n"
        "If a field is missing in transcript, use empty string or empty array.\n\n"
        f"{sanitize_prompt_block('Transcript', transcript_text, max_length=20000)}\n"
    )

    try:
        payload = generate(prompt, expect_json=True)
        if not isinstance(payload, dict):
            payload = _extract_json_object(str(payload).strip())
        if payload is None:
            log_metric("error", source="voice_structured_extraction", kind="invalid_json")
            logger.warning("voice_extraction_failed reason=invalid_json")
            return None
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception as exc:
        log_metric("error", source="voice_structured_extraction", kind="request_failed")
        logger.warning("voice_extraction_failed reason=request_failed error=%s", str(exc))
        return None


def _extract_async_questions(*, transcript: str, job_title: str = "", company_name: str = "") -> list[str]:
    transcript_text = _normalize_text(transcript)
    if not transcript_text:
        return []
    if not (GROQ_API_KEY or OPEN_ROUTER_API):
        return []

    prompt = (
        "You are generating asynchronous screening questions from a recruiter voice intake transcript.\n"
        "Return ONLY valid JSON.\n"
        "Use this schema: {\"questions\": [\"question 1\", \"question 2\"]}\n"
        "Rules:\n"
        "- Return 3 to 7 concise questions\n"
        "- Focus on must-haves, dealbreakers, experience requirements, and role clarity\n"
        "- Make questions natural and suitable for async candidate screening\n"
        "- Do not add numbering, bullets, markdown, or extra text\n\n"
        f"{sanitize_prompt_block('Job title', job_title, max_length=160)}\n"
        f"{sanitize_prompt_block('Company name', company_name, max_length=160)}\n"
        f"{sanitize_prompt_block('Transcript', transcript_text, max_length=12000)}\n"
    )

    try:
        payload = generate(prompt, expect_json=True)
        questions = _extract_questions(payload)
        if questions:
            logger.info("voice_async_questions_generated source=groq count=%s", len(questions))
            return questions
    except Exception as exc:
        logger.warning("voice_async_questions_failed error=%s", str(exc))
    return []


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
        "remote_policy": _normalize_text(payload.get("remote_policy") or job_raw.get("remote_policy")),
        "experience_required": _normalize_text(payload.get("experience_required") or job_raw.get("experience_required")),
        "education_level": _normalize_text(payload.get("education_level") or job_raw.get("education_level")),
        "preferred_institutions": _normalize_list(payload.get("preferred_institutions") or job_raw.get("preferred_institutions")),
        "certifications": _normalize_list(payload.get("certifications") or job_raw.get("certifications")),
        "compensation": _normalize_text(payload.get("compensation") or job_raw.get("compensation")),
        "company": {
            "name": _normalize_text(company_raw.get("name")),
            "industry": _normalize_text(company_raw.get("industry")),
            "description": _normalize_text(company_raw.get("description")),
        },
        "confidence": confidence,
    }


def _transcript_fingerprint(transcript: str) -> str:
    return hashlib.sha256((transcript or "").strip().encode("utf-8")).hexdigest()


def _collapse_repeated_phrases(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""

    tokens = normalized.split(" ")
    if len(tokens) < 4:
        return normalized

    changed = True
    while changed and len(tokens) >= 4:
        changed = False
        max_window = min(12, len(tokens) // 2)
        for window in range(max_window, 1, -1):
            limit = len(tokens) - (window * 2) + 1
            for start in range(max(0, limit)):
                first = " ".join(_comparison_token(token) for token in tokens[start : start + window])
                second = " ".join(_comparison_token(token) for token in tokens[start + window : start + window * 2])
                if first != second:
                    continue
                tokens = tokens[:start] + tokens[start : start + window] + tokens[start + window * 2 :]
                changed = True
                break
            if changed:
                break

    return " ".join(tokens).replace("  ", " ").strip()


def _dedupe_consecutive_words(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""

    tokens = normalized.split(" ")
    deduped: list[str] = []
    for token in tokens:
        last = deduped[-1] if deduped else ""
        if last and last.lower() == token.lower():
            continue
        deduped.append(token)
    return " ".join(deduped).replace("  ", " ").strip()


def _common_prefix_length(left: list[str], right: list[str]) -> int:
    length = 0
    for left_token, right_token in zip(left, right):
        if left_token.lower() != right_token.lower():
            break
        length += 1
    return length


def clean_transcript(transcript: str) -> str:
    text = _normalize_text(transcript)
    if not text:
        return ""

    clauses = re.findall(r"[^.!?\n]+[.!?]?", text) or [text]
    cleaned: list[str] = []

    for raw_clause in clauses:
        clause = _collapse_repeated_phrases(raw_clause)
        clause = _dedupe_consecutive_words(clause)
        clause = re.sub(r"\s+", " ", clause).strip()
        if not clause:
            continue

        clause_core = re.sub(r"[.!?]+$", "", clause).lower()
        if not clause_core:
            continue

        last = cleaned[-1] if cleaned else ""
        if not last:
            cleaned.append(clause)
            continue

        last_core = re.sub(r"[.!?]+$", "", last).lower()
        if not last_core:
            cleaned[-1] = clause
            continue

        if last_core == clause_core:
            continue

        if last_core in clause_core and len(clause) > len(last):
            cleaned[-1] = clause
            continue

        if clause_core in last_core and len(last) >= len(clause):
            continue

        last_words = last_core.split()
        clause_words = clause_core.split()
        prefix_len = _common_prefix_length(last_words, clause_words)
        if prefix_len >= 3:
            cleaned[-1] = clause
            continue

        overlap = min(8, len(last_words), len(clause_words))
        merged = False
        for size in range(overlap, 2, -1):
            if " ".join(last_words[-size:]) == " ".join(clause_words[:size]):
                cleaned[-1] = " ".join(last_words + clause_words[size:]).strip()
                merged = True
                break
        if not merged:
            cleaned.append(clause)

    text = " ".join(cleaned)
    text = _collapse_repeated_phrases(text)
    text = _dedupe_consecutive_words(text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,.;:!?])(?!\s|$)", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


def _cleanup_transcript_text(transcript: str) -> str:
    return clean_transcript(transcript)


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

    transcript_hash = _transcript_fingerprint(raw_text)
    cleaned_text = _cleanup_transcript_text(raw_text) or raw_text
    structured_data = getattr(job, "structured_data", None)
    if isinstance(structured_data, dict):
        voice_extraction = structured_data.get("voiceExtraction")
        if isinstance(voice_extraction, dict) and voice_extraction.get("transcriptHash") == transcript_hash:
            logger.info("voice_refine_duplicate_skipped job_id=%s transcript_hash=%s", job_id, transcript_hash[:12])
            return {
                "refined": True,
                "duplicate": True,
                "job": {
                    "title": job.title,
                    "description": job.description,
                    "location": job.location,
                    "compensation": job.compensation,
                    "skills_required": job.skills_required or [],
                    "responsibilities": job.responsibilities or [],
                    "experience_level": job.experience_level or "",
                },
                "extraction": {
                    "success": True,
                    "usedFallback": False,
                    "confidence": float(voice_extraction.get("confidence") or 0.0),
                    "fields": list(voice_extraction.get("fields") or []),
                },
            }

    extraction_raw = _extract_structured_hiring_data(transcript=cleaned_text)
    used_fallback = extraction_raw is None
    fallback_raw = extract_structured_data_fallback(cleaned_text)
    structured_payload = extraction_raw or fallback_raw
    structured = _sanitize_structured_payload(structured_payload or {})

    extracted_job = structured["job"]
    extracted_company = structured["company"]
    confidence = structured["confidence"]
    extracted_remote_policy = _normalize_text(structured.get("remote_policy"))
    extracted_experience_required = _normalize_text(structured.get("experience_required"))
    extracted_education_level = _normalize_text(structured.get("education_level"))
    extracted_preferred_institutions = _normalize_list(structured.get("preferred_institutions"))
    extracted_certifications = _normalize_list(structured.get("certifications"))
    extracted_compensation = _normalize_text(structured.get("compensation"))

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
    transcript_skills = _extract_skill_keywords_from_transcript(cleaned_text, existing=merged_skills)
    merged_skills = _merge_unique(merged_skills, transcript_skills)
    nice_to_have_skills = _extract_nice_to_have_skills_from_transcript(cleaned_text, existing=merged_skills)
    detected_seniority = _detect_label_from_transcript(cleaned_text, SENIORITY_HINTS, default=_normalize_text(merged_experience_level))
    detected_company_stage = _detect_label_from_transcript(cleaned_text, COMPANY_STAGE_HINTS)
    if detected_seniority and not _normalize_text(merged_experience_level):
        merged_experience_level = detected_seniority

    merged_company_name = existing_company_name or extracted_company["name"]
    merged_company_industry = existing_company_industry or extracted_company["industry"]
    merged_company_description = existing_company_description or extracted_company["description"]
    async_questions = _extract_async_questions(
        transcript=cleaned_text,
        job_title=merged_title,
        company_name=merged_company_name,
    )

    # Use the full transcript (both sides) for richer description refinement.
    notes_for_refinement = [cleaned_text] if cleaned_text else voice_notes
    refined_description = _refine_description(description=job.description, voice_notes=notes_for_refinement)
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
                "remote_policy": extracted_remote_policy,
                "experience_required": extracted_experience_required,
                "education_level": extracted_education_level,
                "preferred_institutions": extracted_preferred_institutions,
                "certifications": extracted_certifications,
                "compensation": extracted_compensation,
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
        remote_policy=extracted_remote_policy or None,
        experience_required=extracted_experience_required or None,
        company_name=merged_company_name,
        industry=merged_company_industry,
        requirements="\n".join(merged_responsibilities) if merged_responsibilities else None,
        skills=merged_skills,
        remote=(extracted_remote_policy or "").strip().lower() == "remote" if extracted_remote_policy else None,
        structured_data={
            "skills": merged_skills,
            "nice_to_have_skills": nice_to_have_skills,
            "seniority": detected_seniority or merged_experience_level,
            "company_stage": detected_company_stage,
            "remote_policy": extracted_remote_policy,
            "experience_required": extracted_experience_required,
            "education_level": extracted_education_level,
            "preferred_institutions": extracted_preferred_institutions,
            "certifications": extracted_certifications,
            "compensation": extracted_compensation,
            "voiceExtraction": {
                "source": "fallback" if used_fallback else "openai",
                "job": extracted_job,
                "company": extracted_company,
                "confidence": confidence,
                "success": True,
                "transcript": cleaned_text,
                "cleanedTranscript": cleaned_text,
                "rawTranscript": raw_text,
                "transcriptHash": transcript_hash,
                "fallback": fallback_raw,
                "fields": extracted_fields if not used_fallback else fallback_fields,
            },
            "voiceTranscript": cleaned_text,
            "voiceTranscriptClean": cleaned_text,
            "voiceTranscriptRaw": raw_text,
            "voiceSummary": cleaned_text,
            "async_questions": async_questions,
            "asyncQuestions": async_questions,
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
    # Use merged_title (the updated value) rather than the stale job.structured_data
    # snapshot that existed before jobs.update_structured_fields was called.
    JobIntakeRepository(db).upsert_completed_intake(
        job_id=job_id,
        transcript=cleaned_text,
        structured_data_json={
            "title": merged_title,
            "location": job.location or structured_data.get("location") or "",
            "skills": merged_skills,
            "nice_to_have_skills": nice_to_have_skills,
            "seniority": detected_seniority or merged_experience_level,
            "company_stage": detected_company_stage,
            "remote_policy": extracted_remote_policy,
            "experience_required": extracted_experience_required,
            "education_level": extracted_education_level,
            "preferred_institutions": extracted_preferred_institutions,
            "certifications": extracted_certifications,
            "compensation": extracted_compensation,
            "voiceExtraction": {
                "source": "fallback" if used_fallback else "openai",
                "job": extracted_job,
                "company": extracted_company,
                "confidence": confidence,
                "success": True,
                "transcript": cleaned_text,
                "cleanedTranscript": cleaned_text,
                "rawTranscript": raw_text,
                "transcriptHash": transcript_hash,
                "fallback": fallback_raw,
                "fields": extracted_fields if not used_fallback else fallback_fields,
            },
            "voiceTranscript": cleaned_text,
            "voiceTranscriptClean": cleaned_text,
            "voiceTranscriptRaw": raw_text,
            "voiceSummary": cleaned_text,
            "async_questions": async_questions,
            "asyncQuestions": async_questions,
        },
        intake_status="completed",
        completed_at=datetime.now(timezone.utc),
    )
    if _normalize_text(getattr(updated, "job_status", "")).lower() == "draft":
        updated = jobs.update_candidate_sourcing_state(job_id=job_id, job_status="active") or updated
    try:
        # Re-embed the enriched job and upsert to Qdrant.
        # Do NOT call fetch_ranked_candidates here — frontend triggers that separately with refresh=true.
        vector_source = build_job_text(updated, structured_data=updated.structured_data, transcript=cleaned_text)
        chunks = chunk_text(vector_source)
        vectors = [get_embedding(chunk) for chunk in chunks]
        ensure_all_collections()
        delete_job_vectors(job_id)
        upsert_job_chunks(job_id, vectors, chunks)

        db.commit()
        db.refresh(updated)

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
    except Exception:
        db.rollback()
        try:
            delete_job_vectors(job_id)
        except Exception:
            pass
        raise


def _fallback_refinement(description: str, voice_notes: list[str]) -> str:
    cleaned_notes = [note.strip() for note in voice_notes if note and note.strip()]
    if not cleaned_notes:
        return description

    notes_block = "\n".join(f"- {note}" for note in cleaned_notes)
    base = description.strip() or "Role description provided by recruiter."
    return f"{base}\n\nAdditional recruiter notes:\n{notes_block}"


def _refine_description(*, description: str, voice_notes: list[str]) -> str:
    if not (GROQ_API_KEY or OPEN_ROUTER_API):
        logger.warning("Neither GROQ_API_KEY nor OPEN_ROUTER_API is configured; using local refinement fallback")
        return _fallback_refinement(description, voice_notes)

    notes_blob = sanitize_prompt_text("\n".join(f"- {note}" for note in voice_notes if note.strip()), max_length=12000)
    prompt = (
        "You are refining a hiring job description for candidate search.\n"
        "Return only the refined description text.\n\n"
        f"{sanitize_prompt_block('Current Description', description, max_length=4000)}\n\n"
        f"{sanitize_prompt_block('Voice Notes', notes_blob, max_length=12000)}\n"
    )

    try:
        refined = generate(prompt)
        if isinstance(refined, str) and refined.strip():
            return refined.strip()
    except Exception as exc:
        logger.warning("LLM refinement failed; using local refinement fallback error=%s", str(exc))

    return _fallback_refinement(description, voice_notes)

