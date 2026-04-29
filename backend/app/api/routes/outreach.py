from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.candidate import OutreachReplyRequest, OutreachRequest
from app.services.outreach_service import (
    build_email_preview,
    handle_email_reply,
    list_outreach_status,
    process_outreach,
    queue_outreach_delivery,
)
from app.utils.responses import success_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["outreach"])


@router.post("/outreach")
def send_outreach(payload: OutreachRequest, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    data = process_outreach(
        db=db,
        job_id=payload.jobId,
        selected_candidates=payload.selectedCandidates,
        custom_body=payload.customBody,
    )
    return success_response(data)


@router.post("/outreach/queue")
def queue_outreach(payload: OutreachRequest, _: dict = Depends(get_current_user)):
    data = queue_outreach_delivery(
        job_id=payload.jobId,
        selected_candidates=payload.selectedCandidates,
        custom_body=payload.customBody,
    )
    return success_response(data)


@router.get("/outreach/status")
def get_outreach_status(jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = list_outreach_status(db=db, job_id=jobId)
    return success_response(rows)


@router.get("/outreach/preview")
def get_email_preview(
    jobId: str = Query(...),
    candidateId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = build_email_preview(db=db, job_id=jobId, candidate_id=candidateId)
    return success_response(data)


@router.post("/outreach/reply")
def reply_webhook(payload: OutreachReplyRequest, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    data = handle_email_reply(payload.model_dump(), db=db)
    return success_response(data)


@router.post("/outreach/webhook/reply")
async def reply_webhook_public(request: Request, db: Session = Depends(get_db)):
    """
    Public webhook endpoint for Resend inbound emails.
    Accepts raw payload (no schema, no auth).
    """
    logger.info("request_started reply_webhook_public")
    try:
        payload = await request.json()
    except Exception as exc:
        logger.error("error_occurred reply_webhook_invalid_json error=%s", str(exc), exc_info=exc)
        payload = {}

    logger.info("decision_taken reply_webhook_payload_received")

    try:
        handle_email_reply(payload, db=db)
    except Exception as e:
        logger.error("error_occurred reply_webhook_processing_failed error=%s", str(e), exc_info=e)

    return {"ok": True}
