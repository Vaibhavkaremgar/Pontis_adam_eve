from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.job import VoiceRefineRequest
from app.services.job_queue_service import enqueue_job
from app.services.ownership import assert_job_ownership
from app.utils.responses import success_response

router = APIRouter(tags=["voice"])


@router.post("/voice/refine")
def refine_voice_notes(payload: VoiceRefineRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    queue_result = enqueue_job(
        "voice_intake_finalize",
        {
            "job_id": payload.jobId,
            "voice_notes": list(payload.voiceNotes or []),
            "transcript": payload.transcript,
        },
        idempotency_key=hashlib.sha256(
            f"voice-intake-finalize:{payload.jobId}:{payload.transcript or ''}:{'|'.join(payload.voiceNotes or [])}".encode("utf-8")
        ).hexdigest(),
    )
    return success_response(
        {
            "refined": True,
            "queued": bool(queue_result.get("queued", False)),
            "jobId": payload.jobId,
            "queueJobId": queue_result.get("job_id", ""),
            "queueType": queue_result.get("queue_type", "voice_intake_finalize"),
        }
    )
