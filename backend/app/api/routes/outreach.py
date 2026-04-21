from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.candidate import OutreachRequest
from app.services.outreach_service import list_outreach_status, process_outreach
from app.utils.responses import success_response

router = APIRouter(tags=["outreach"])


@router.post("/outreach")
def send_outreach(payload: OutreachRequest, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    data = process_outreach(db=db, job_id=payload.jobId, selected_candidates=payload.selectedCandidates)
    return success_response(data)


@router.get("/outreach/status")
def get_outreach_status(jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = list_outreach_status(db=db, job_id=jobId)
    return success_response(rows)
