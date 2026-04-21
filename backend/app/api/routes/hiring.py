from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.job import JobCreatePayload
from app.services.hiring_service import create_hiring_job
from app.utils.responses import success_response

router = APIRouter(tags=["hiring"])


@router.post("/hiring/create")
def create_job(
    payload: JobCreatePayload,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = request.state.user["id"]
    job_id = create_hiring_job(
        db=db,
        user_id=user_id,
        company=payload.company.model_dump(),
        job=payload.job.model_dump(),
    )
    return success_response({"jobId": job_id})
