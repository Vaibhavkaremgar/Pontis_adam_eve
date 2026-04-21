from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.repositories import CompanyRepository, JobRepository
from app.services.embedding_service import get_embedding
from app.services.qdrant_service import delete_job_vectors, ensure_all_collections, upsert_job_chunks
from app.utils.exceptions import APIError
from app.utils.text import chunk_text


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

    title = (job.get("title") or "").strip()
    job_description = (job.get("description") or "").strip()
    location = (job.get("location") or "").strip()
    compensation = (job.get("compensation") or "").strip()
    work_authorization = (job.get("workAuthorization") or "required").strip()

    if not company_name:
        raise APIError("company.name is required", status_code=400)
    if not title or not job_description:
        raise APIError("job.title and job.description are required", status_code=400)

    company_repo = CompanyRepository(db)
    job_repo = JobRepository(db)

    company_row = company_repo.create(
        user_id=user_id,
        name=company_name,
        website=website or "https://example.com",
        description=description,
    )
    job_row = job_repo.create(
        company_id=company_row.id,
        title=title,
        description=job_description,
        location=location,
        compensation=compensation,
        work_authorization=work_authorization,
    )
    db.commit()

    vector_source = _build_job_vector_source(title, job_description, location, compensation, work_authorization)
    chunks = chunk_text(vector_source)
    vectors = [get_embedding(chunk) for chunk in chunks]
    ensure_all_collections()
    delete_job_vectors(job_row.id)
    upsert_job_chunks(job_row.id, vectors, chunks)
    return job_row.id
