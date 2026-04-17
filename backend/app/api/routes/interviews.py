from fastapi import APIRouter, HTTPException

from app.schemas.candidate import InterviewRequest, InterviewResponse
from app.services.db_service import get_job

router = APIRouter(tags=["interviews"])


@router.post("/interviews", response_model=InterviewResponse)
def schedule_interview(payload: InterviewRequest) -> InterviewResponse:
    if not get_job(payload.jobId):
        raise HTTPException(status_code=404, detail="Job not found")

    return InterviewResponse(scheduled=True)
