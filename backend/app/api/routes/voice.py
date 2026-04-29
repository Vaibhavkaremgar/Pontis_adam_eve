from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.job import VoiceRefineRequest
from app.services.voice_service import refine_job_with_voice
from app.utils.responses import success_response

router = APIRouter(tags=["voice"])


@router.post("/voice/refine")
def refine_voice_notes(payload: VoiceRefineRequest, db: Session = Depends(get_db)):
    data = refine_job_with_voice(
        db=db,
        job_id=payload.jobId,
        voice_notes=payload.voiceNotes,
        transcript=payload.transcript,
    )
    return success_response(data)
