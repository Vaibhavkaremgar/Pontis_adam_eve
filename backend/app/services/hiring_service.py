from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.repositories import CompanyRepository, JobRepository
from app.services.candidate_service import build_job_text
from app.services.embedding_service import get_embedding
from app.services.qdrant_service import delete_job_vectors, ensure_all_collections, upsert_job_chunks
from app.utils.exceptions import APIError
from app.utils.text import chunk_text


def get_or_create_company(
    *,
    db: Session,
    user_id: str,
    name: str,
    website: str,
    description: str,
    industry: str = "",
):
    return CompanyRepository(db).get_or_create(
        user_id=user_id,
        name=name,
        website=website,
        description=description,
        industry=industry,
    )


def _build_job_vector_source(
    title: str,
    description: str,
    location: str,
    compensation: str,
    work_authorization: str,
    skills_required: list[str] | None = None,
    responsibilities: list[str] | None = None,
    experience_level: str = "",
) -> str:
    skills_text = ", ".join(skill.strip() for skill in (skills_required or []) if skill and skill.strip()) or "Not specified"
    responsibilities_text = (
        "\n".join(f"- {item.strip()}" for item in (responsibilities or []) if item and item.strip()) or "- Not specified"
    )
    return (
        f"Title: {title}\n"
        f"Skills Required: {skills_text}\n"
        f"Responsibilities:\n{responsibilities_text}\n"
        f"Description: {description}\n"
        f"Experience Level: {experience_level}\n"
        f"Location: {location}\n"
        f"Compensation: {compensation}\n"
        f"Work Authorization: {work_authorization}"
    )


def create_hiring_job(*, db: Session, user_id: str, company: dict, job: dict) -> str:
    company_name = (company.get("name") or "").strip()
    website = (company.get("website") or "").strip()
    description = (company.get("description") or "").strip()
    industry = (company.get("industry") or "").strip()

    title = (job.get("title") or "").strip()
    job_description = (job.get("description") or "").strip()
    location = (job.get("location") or "").strip()
    compensation = (job.get("compensation") or "").strip()
    work_authorization = (job.get("workAuthorization") or "required").strip()
    ats_job_id = (job.get("atsJobId") or job.get("ats_job_id") or "").strip()
    remote_policy = (job.get("remotePolicy") or job.get("remote_policy") or "").strip()
    experience_required = (job.get("experienceRequired") or job.get("experience_required") or "").strip()
    vetting_mode = (job.get("vettingMode") or job.get("vetting_mode") or "volume").strip().lower()
    auto_export_to_ats = bool(job.get("autoExportToAts") or job.get("auto_export_to_ats") or False)
    if vetting_mode not in {"volume", "elite"}:
        vetting_mode = "volume"

    if not company_name:
        raise APIError("company.name is required", status_code=400)
    if not title or not job_description:
        raise APIError("job.title and job.description are required", status_code=400)

    job_repo = JobRepository(db)

    company_row = get_or_create_company(
        db=db,
        user_id=user_id,
        name=company_name,
        website=website or "https://example.com",
        description=description,
        industry=industry,
    )
    job_row = job_repo.create(
        company_id=company_row.id,
        title=title,
        description=job_description,
        location=location,
        compensation=compensation,
        work_authorization=work_authorization,
        ats_job_id=ats_job_id or None,
        vetting_mode=vetting_mode,
        auto_export_to_ats=auto_export_to_ats,
        structured_data={
            "remotePolicy": remote_policy,
            "experienceRequired": experience_required,
            "autoExportToAts": auto_export_to_ats,
        },
    )
    db.commit()

    vector_source = build_job_text(job_row)
    chunks = chunk_text(vector_source)
    vectors = [get_embedding(chunk) for chunk in chunks]
    ensure_all_collections()
    delete_job_vectors(job_row.id)
    upsert_job_chunks(job_row.id, vectors, chunks)
    return job_row.id
