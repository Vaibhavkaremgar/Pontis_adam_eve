from fastapi import APIRouter

from app.schemas.job import JobCreatePayload, JobCreateResponse
from app.services.db_service import create_job

router = APIRouter(tags=["hiring"])


@router.post("/hiring/create", response_model=JobCreateResponse)
def create_hiring_job(payload: JobCreatePayload) -> JobCreateResponse:
    company_data = payload.company.model_dump()
    job_data = payload.job.model_dump()
    job_id = create_job(company=company_data, job=job_data)
    return JobCreateResponse(jobId=job_id)
