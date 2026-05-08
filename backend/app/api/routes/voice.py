from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.job import VoiceRefineRequest
from app.services.voice_service import refine_job_with_voice
from app.services.ownership import assert_job_ownership
from app.utils.responses import success_response

router = APIRouter(tags=["voice"])


@router.post("/voice/refine")
def refine_voice_notes(payload: VoiceRefineRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    data = refine_job_with_voice(
        db=db,
        job_id=payload.jobId,
        voice_notes=payload.voiceNotes,
        transcript=payload.transcript,
    )
    return success_response(data)
