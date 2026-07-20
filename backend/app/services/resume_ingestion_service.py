from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.config import EMBEDDING_VERSION, INTERNAL_CANDIDATE_COLLECTION_NAME
from app.db.repositories import InternalCandidateResumeRepository
from app.services.candidate_text import build_candidate_text
from app.services.embedding_service import embed
from app.services.llm_service import generate
from app.services.metrics_service import log_metric
from app.services.qdrant_service import ensure_collection_indexes, upsert_internal_candidate_chunks
from app.services.skill_normalizer import parse_experience

logger = logging.getLogger(__name__)
_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_LINKEDIN_PATTERN = re.compile(r"https?://(?:www\.)?linkedin\.com/[^\s)>\]]+", re.IGNORECASE)
_GITHUB_PATTERN = re.compile(r"https?://(?:www\.)?github\.com/[^\s)>\]]+", re.IGNORECASE)


class ResumeStructuredProfile(BaseModel):
    full_name: str = ""
    headline: str = ""
    years_experience: float = 0.0
    skills: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    location: str = ""
    summary: str = ""
    domain_experience: list[str] = Field(default_factory=list)

    @field_validator(
        "full_name",
        "headline",
        "location",
        "summary",
        mode="before",
    )
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @field_validator(
        "skills",
        "companies",
        "education",
        "projects",
        "certifications",
        "domain_experience",
        mode="before",
    )
    @classmethod
    def _normalize_list(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    @field_validator("years_experience", mode="before")
    @classmethod
    def _normalize_experience(cls, value: Any) -> float:
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        parsed = parse_experience(value)
        return float(parsed)

    @model_validator(mode="after")
    def _cap_lengths(self) -> "ResumeStructuredProfile":
        self.skills = self.skills[:24]
        self.companies = self.companies[:12]
        self.education = self.education[:12]
        self.projects = self.projects[:12]
        self.certifications = self.certifications[:12]
        self.domain_experience = self.domain_experience[:12]
        return self


class ResumeContactDetails(BaseModel):
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    github_url: str = ""

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: Any) -> str:
        candidate = re.sub(r"\s+", "", str(value or "")).strip().lower()
        if not candidate or len(candidate) > 320:
            return ""
        if ".." in candidate or "@" not in candidate:
            return ""
        local, _, domain = candidate.rpartition("@")
        if not local or not domain or domain.startswith(".") or domain.endswith("."):
            return ""
        return candidate if _EMAIL_PATTERN.fullmatch(candidate) else ""

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_phone(cls, value: Any) -> str:
        if value is None:
            return ""
        candidate = re.sub(r"[^0-9+]", "", str(value))
        return candidate if len(candidate) >= 8 else ""

    @field_validator("linkedin_url", "github_url", mode="before")
    @classmethod
    def _normalize_url(cls, value: Any) -> str:
        candidate = re.sub(r"\s+", "", str(value or "")).strip()
        return candidate.rstrip(").,;]") if candidate else ""


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract_emails_from_text(text: str) -> list[str]:
    if not text:
        return []
    emails: list[str] = []
    seen: set[str] = set()
    for match in _EMAIL_PATTERN.findall(text):
        normalized = match.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        emails.append(normalized)
    return emails


def _extract_phone_number(text: str) -> str:
    match = _PHONE_PATTERN.search(text or "")
    if not match:
        return ""
    value = re.sub(r"[^0-9+]", "", match.group(0))
    return value if len(value) >= 8 else ""


def _extract_social_url(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text or "")
    return match.group(0).rstrip(").,;]") if match else ""


def _pdf_file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(path.resolve()).encode("utf-8"))
    try:
        digest.update(path.read_bytes())
    except Exception:
        digest.update(str(path.name).encode("utf-8"))
    return digest.hexdigest()


def extract_pdf_text(pdf_path: Path) -> tuple[str, dict[str, Any]]:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("PyMuPDF (fitz) is required for resume ingestion") from exc

    pages: list[str] = []
    metadata: dict[str, Any] = {}
    try:
        with fitz.open(pdf_path) as doc:
            metadata = dict(getattr(doc, "metadata", {}) or {})
            for index, page in enumerate(doc, start=1):
                try:
                    text = page.get_text("text", sort=True)
                except Exception as page_exc:
                    logger.warning(
                        "resume_page_extraction_failed file=%s page=%s error=%s",
                        pdf_path.name,
                        index,
                        str(page_exc),
                    )
                    continue
                if text.strip():
                    pages.append(f"--- Page {index} ---\n{text.strip()}")
    except Exception as exc:
        logger.warning("resume_pdf_extraction_failed file=%s error=%s", pdf_path.name, str(exc))
        raise

    combined = "\n\n".join(pages).strip()
    return combined, metadata


def _resume_prompt(text: str, *, file_name: str) -> str:
    clipped_text = text[:12000]
    return (
        "You are extracting a structured candidate profile from a resume PDF.\n"
        "Return only valid JSON with the following schema:\n"
        "{\n"
        '  "full_name": "",\n'
        '  "headline": "",\n'
        '  "years_experience": 0,\n'
        '  "skills": [],\n'
        '  "companies": [],\n'
        '  "education": [],\n'
        '  "projects": [],\n'
        '  "certifications": [],\n'
        '  "location": "",\n'
        '  "summary": "",\n'
        '  "domain_experience": []\n'
        "}\n"
        "Use concise normalized values. If a field is unavailable, return an empty string or empty array.\n"
        "Do not invent facts.\n\n"
        f"Resume file: {file_name}\n\n"
        f"Resume text:\n{clipped_text}\n"
    )


def _resume_contact_prompt(text: str, *, file_name: str, current_values: ResumeContactDetails) -> str:
    clipped_text = text[:12000]
    return (
        "You are extracting contact details from a resume PDF.\n"
        "Return only valid JSON with this schema:\n"
        '{\n'
        '  "email": "",\n'
        '  "phone": "",\n'
        '  "linkedin_url": "",\n'
        '  "github_url": ""\n'
        "}\n"
        "Only fill fields that are actually present in the resume. Do not invent facts.\n"
        "If a field is already present in the current extracted values, keep it unchanged.\n\n"
        f"Current extracted values: {json.dumps(current_values.model_dump(), ensure_ascii=True)}\n\n"
        f"Resume file: {file_name}\n\n"
        f"Resume text:\n{clipped_text}\n"
    )


def _coerce_profile_payload(raw: Any, resume_text: str, file_name: str) -> dict[str, Any]:
    if isinstance(raw, ResumeStructuredProfile):
        return raw.model_dump()
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            return first
    logger.warning("resume_profile_payload_invalid file=%s type=%s", file_name, type(raw).__name__)
    return {}


def _coerce_contact_payload(raw: Any, file_name: str) -> dict[str, Any]:
    if isinstance(raw, ResumeContactDetails):
        return raw.model_dump()
    if isinstance(raw, dict):
        return raw
    logger.warning("resume_contact_payload_invalid file=%s type=%s", file_name, type(raw).__name__)
    return {}


def _heuristic_profile_from_text(text: str) -> dict[str, Any]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    headline = next((line for line in lines[:10] if 8 <= len(line) <= 120), "")
    top_lines = " ".join(lines[:12])
    skills = []
    for token in re.split(r"[,/•|]", top_lines):
        clean = _normalize_text(token)
        if clean and len(clean) <= 40:
            skills.append(clean)
    return {
        "full_name": lines[0] if lines else "",
        "headline": headline,
        "years_experience": parse_experience(top_lines),
        "skills": skills[:12],
        "companies": [],
        "education": [],
        "projects": [],
        "certifications": [],
        "location": "",
        "summary": " ".join(lines[:5])[:500],
        "domain_experience": [],
    }


def _merge_contact_details(
    local_details: ResumeContactDetails,
    llm_payload: dict[str, Any],
) -> ResumeContactDetails:
    llm_details = ResumeContactDetails.model_validate(llm_payload or {})
    return ResumeContactDetails(
        email=local_details.email or llm_details.email,
        phone=local_details.phone or llm_details.phone,
        linkedin_url=local_details.linkedin_url or llm_details.linkedin_url,
        github_url=local_details.github_url or llm_details.github_url,
    )


def parse_resume_profile(
    *,
    resume_text: str,
    file_name: str,
    allow_fallback: bool = True,
) -> ResumeStructuredProfile:
    prompt = _resume_prompt(resume_text, file_name=file_name)
    raw_output = generate(prompt, expect_json=True)
    payload = _coerce_profile_payload(raw_output, resume_text, file_name)
    try:
        return ResumeStructuredProfile.model_validate(payload)
    except ValidationError as exc:
        logger.warning("resume_profile_validation_failed file=%s error=%s", file_name, str(exc))
        log_metric("parsing_failures", file_name=file_name, reason="validation_error")

    repair_prompt = (
        "Fix the JSON so it matches the exact schema and contains only valid JSON.\n"
        f"{prompt}"
    )
    repaired = generate(repair_prompt, expect_json=True)
    payload = _coerce_profile_payload(repaired, resume_text, file_name)
    try:
        return ResumeStructuredProfile.model_validate(payload)
    except ValidationError as exc:
        logger.warning("resume_profile_repair_failed file=%s error=%s", file_name, str(exc))
        log_metric("parsing_failures", file_name=file_name, reason="repair_failed")

    if allow_fallback:
        fallback = _heuristic_profile_from_text(resume_text)
        return ResumeStructuredProfile.model_validate(fallback)
    raise RuntimeError(f"Unable to parse resume profile for {file_name}")


def extract_resume_contact_details(
    *,
    resume_text: str,
    file_name: str,
) -> ResumeContactDetails:
    extracted_emails = _extract_emails_from_text(resume_text)
    local_details = ResumeContactDetails(
        email=(extracted_emails[0] if extracted_emails else ""),
        phone=_extract_phone_number(resume_text),
        linkedin_url=_extract_social_url(resume_text, _LINKEDIN_PATTERN),
        github_url=_extract_social_url(resume_text, _GITHUB_PATTERN),
    )
    if local_details.email and local_details.phone and local_details.linkedin_url and local_details.github_url:
        return local_details

    prompt = _resume_contact_prompt(resume_text, file_name=file_name, current_values=local_details)
    raw_output = generate(prompt, expect_json=True)
    payload = _coerce_contact_payload(raw_output, file_name)
    try:
        return _merge_contact_details(local_details, payload)
    except ValidationError as exc:
        logger.warning("resume_contact_validation_failed file=%s error=%s", file_name, str(exc))
        log_metric("parsing_failures", file_name=file_name, reason="contact_validation_error")
        return local_details


def _build_embedding_text(profile: ResumeStructuredProfile, resume_text: str) -> str:
    return build_candidate_text(
        {
            "full_name": profile.full_name,
            "headline": profile.headline,
            "role": profile.headline or "",
            "skills": profile.skills,
            "experience": profile.years_experience,
            "years_experience": profile.years_experience,
            "summary": profile.summary,
            "location": profile.location,
            "companies": profile.companies,
            "projects": profile.projects,
            "education": profile.education,
            "certifications": profile.certifications,
            "domain_experience": profile.domain_experience,
            "raw_resume_text": resume_text[:8000],
        }
    )


def build_internal_candidate_payload(
    *,
    profile: ResumeStructuredProfile,
    resume_text: str,
    file_name: str,
    source_path: str,
    resume_fingerprint: str,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_id = str(uuid5(NAMESPACE_URL, f"pontis-internal-resume:{resume_fingerprint}"))
    local_emails = _extract_emails_from_text(resume_text)
    contact_details = extract_resume_contact_details(resume_text=resume_text, file_name=file_name)
    extracted_emails = local_emails[:]
    if not extracted_emails and contact_details.email:
        extracted_emails = [contact_details.email]
    primary_email = extracted_emails[0] if extracted_emails else contact_details.email
    return {
        "candidate_id": candidate_id,
        "resume_fingerprint": resume_fingerprint,
        "source_filename": file_name,
        "source_path": source_path,
        "source_metadata": {
            "source": "backend/resumes",
            "file_name": file_name,
            "source_path": source_path,
            **(source_metadata or {}),
        },
        "full_name": profile.full_name,
        "headline": profile.headline,
        "role": profile.headline or "",
        "company": profile.companies[0] if profile.companies else "",
        "years_experience": profile.years_experience,
        "skills": profile.skills,
        "companies": profile.companies,
        "education": profile.education,
        "projects": profile.projects,
        "certifications": profile.certifications,
        "location": profile.location,
        "summary": profile.summary,
        "domain_experience": profile.domain_experience,
        "raw_resume_text": resume_text,
        "email": primary_email,
        "work_email": primary_email,
        "personal_email": primary_email,
        "emails_primary": primary_email,
        "emails": extracted_emails,
        "contact_emails": extracted_emails,
        "phone": contact_details.phone,
        "linkedin_url": contact_details.linkedin_url,
        "github_url": contact_details.github_url,
        "email_source": "resume_text" if local_emails else ("groq" if primary_email else ""),
        "parsed_data": profile.model_dump(),
        "embedding_version": EMBEDDING_VERSION,
        "vector_version": EMBEDDING_VERSION,
        "qdrant_point_id": candidate_id,
    }


def ingest_resume_file(db: Session, pdf_path: Path) -> dict[str, Any]:
    pdf_path = Path(pdf_path)
    resume_text, pdf_metadata = extract_pdf_text(pdf_path)
    if not resume_text.strip():
        raise RuntimeError("empty resume text")

    resume_fingerprint = hashlib.sha256(resume_text.encode("utf-8")).hexdigest()
    parsed_profile = parse_resume_profile(resume_text=resume_text, file_name=pdf_path.name)
    payload = build_internal_candidate_payload(
        profile=parsed_profile,
        resume_text=resume_text,
        file_name=pdf_path.name,
        source_path=str(pdf_path),
        resume_fingerprint=resume_fingerprint,
        source_metadata=pdf_metadata,
    )
    candidate_id = str(payload["candidate_id"])
    candidate_repo = InternalCandidateResumeRepository(db)
    existing = candidate_repo.get_by_fingerprint(resume_fingerprint) or candidate_repo.get_by_candidate_id(candidate_id)
    if existing:
        log_metric("duplicate_candidates_detected", file_name=pdf_path.name, candidate_id=candidate_id)

    # _extract_emails_from_text was already called inside build_internal_candidate_payload;
    # reuse the result stored in payload to avoid a second regex scan.
    embedding_text = _build_embedding_text(parsed_profile, resume_text)
    vector = embed(embedding_text)
    ensure_collection_indexes(INTERNAL_CANDIDATE_COLLECTION_NAME)
    upsert_internal_candidate_chunks(
        candidate_id=candidate_id,
        vectors=[vector],
        chunks=[embedding_text[:4000]],
        payload={
            "candidateId": candidate_id,
            "resumeFingerprint": resume_fingerprint,
            "sourceFilename": pdf_path.name,
            "sourcePath": str(pdf_path),
            "name": parsed_profile.full_name,
            "headline": parsed_profile.headline,
            "yearsExperience": parsed_profile.years_experience,
            "skills": parsed_profile.skills,
            "companies": parsed_profile.companies,
            "education": parsed_profile.education,
            "projects": parsed_profile.projects,
            "certifications": parsed_profile.certifications,
            "location": parsed_profile.location,
            "summary": parsed_profile.summary,
            "domainExperience": parsed_profile.domain_experience,
            "embeddingVersion": EMBEDDING_VERSION,
            "vectorVersion": EMBEDDING_VERSION,
            "sourceType": "internal_resume",
            "role": parsed_profile.headline or "",
            "company": parsed_profile.companies[0] if parsed_profile.companies else "",
            "skillTokens": [skill.lower() for skill in parsed_profile.skills[:8]],
            "rolePattern": _normalize_text(parsed_profile.headline or parsed_profile.summary).lower(),
        },
    )
    row = candidate_repo.upsert(**payload)
    log_metric("embeddings_generated", file_name=pdf_path.name, candidate_id=candidate_id)
    log_metric("vectors_inserted", file_name=pdf_path.name, candidate_id=candidate_id)
    log_metric("resumes_processed", file_name=pdf_path.name, candidate_id=candidate_id)
    logger.info(
        "resume_ingested file=%s candidate_id=%s fingerprint=%s name=%s",
        pdf_path.name,
        candidate_id,
        resume_fingerprint[:12],
        parsed_profile.full_name,
    )
    return {
        "candidate_id": candidate_id,
        "resume_fingerprint": resume_fingerprint,
        "created": existing is None,
        "candidate_row_id": row.id,
        "pdf_metadata": pdf_metadata,
        "parsed_profile": parsed_profile.model_dump(),
    }


def ingest_resume_directory(db: Session, resumes_dir: Path) -> dict[str, int]:
    resumes_dir = Path(resumes_dir)
    results = {
        "processed": 0,
        "failed": 0,
        "duplicates": 0,
    }
    pdfs = sorted(path for path in resumes_dir.glob("*.pdf") if path.is_file())
    logger.info("resume_directory_scan directory=%s count=%s", resumes_dir, len(pdfs))
    for index, pdf_path in enumerate(pdfs, start=1):
        try:
            outcome = ingest_resume_file(db, pdf_path)
            results["processed"] += 1
            if not outcome.get("created", True):
                results["duplicates"] += 1
        except Exception as exc:
            db.rollback()
            results["failed"] += 1
            log_metric("resumes_failed", file_name=pdf_path.name, error_type=type(exc).__name__)
            logger.warning("resume_ingestion_failed file=%s error=%s", pdf_path.name, str(exc), exc_info=exc)
        if index % 5 == 0 or index == len(pdfs):
            logger.info(
                "resume_directory_progress processed=%s failed=%s duplicates=%s total=%s",
                results["processed"],
                results["failed"],
                results["duplicates"],
                len(pdfs),
            )
    return results


def validate_ingestion_state(db: Session) -> dict[str, int]:
    from app.services.qdrant_service import count_collection_points

    repo = InternalCandidateResumeRepository(db)
    return {
        "postgres_count": repo.count(),
        "qdrant_count": count_collection_points(INTERNAL_CANDIDATE_COLLECTION_NAME),
    }
