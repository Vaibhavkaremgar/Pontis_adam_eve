from fastapi import APIRouter, HTTPException

from app.schemas.candidate import OutreachRequest, OutreachResponse
from app.services.db_service import get_job

router = APIRouter(tags=["outreach"])


@router.post("/outreach", response_model=OutreachResponse)
def send_outreach(payload: OutreachRequest) -> OutreachResponse:
    if not get_job(payload.jobId):
        raise HTTPException(status_code=404, detail="Job not found")

    return OutreachResponse(success=True)
