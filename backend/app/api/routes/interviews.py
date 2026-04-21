from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.services.interview_service import list_interviews
from app.utils.responses import success_response

router = APIRouter(tags=["interviews"])


@router.get("/interviews")
def get_interviews(jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = list_interviews(db=db, job_id=jobId)
    return success_response([row.model_dump() for row in rows])
