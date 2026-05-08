from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.schemas.candidate import OutreachReplyRequest, OutreachRequest
from app.services.audit_service import record_audit_event
from app.services.outreach_service import (
    build_email_preview,
    handle_email_reply,
    list_outreach_status,
    record_outreach_open,
    queue_outreach_delivery,
)
from app.services.ownership import assert_job_ownership
from app.services.webhook_security import WEBHOOK_SIGNATURE_HEADER, WEBHOOK_TIMESTAMP_HEADER, verify_shared_secret_webhook
from app.utils.responses import success_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["outreach"])


@router.post("/outreach")
def send_outreach(payload: OutreachRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    data = queue_outreach_delivery(
        job_id=payload.jobId,
        selected_candidates=payload.selectedCandidates,
        custom_body=payload.customBody,
    )
    record_audit_event(
        db=db,
        actor_id=request.state.user["id"],
        action="outreach_queued",
        entity_type="job",
        entity_id=payload.jobId,
        metadata={"selected_candidates": len(payload.selectedCandidates)},
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    db.commit()
    return success_response(data)


@router.post("/outreach/queue")
def queue_outreach(payload: OutreachRequest, request: Request, _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=payload.jobId, user_id=request.state.user["id"])
    data = queue_outreach_delivery(
        job_id=payload.jobId,
        selected_candidates=payload.selectedCandidates,
        custom_body=payload.customBody,
    )
    record_audit_event(
        db=db,
        actor_id=request.state.user["id"],
        action="outreach_queued",
        entity_type="job",
        entity_id=payload.jobId,
        metadata={"selected_candidates": len(payload.selectedCandidates), "queue_only": True},
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    db.commit()
    return success_response(data)


@router.get("/outreach/status")
def get_outreach_status(request: Request, jobId: str = Query(...), _: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    assert_job_ownership(db=db, job_id=jobId, user_id=request.state.user["id"])
    rows = list_outreach_status(db=db, job_id=jobId)
    return success_response(rows)


@router.get("/outreach/preview")
def get_email_preview(
    request: Request,
    jobId: str = Query(...),
    candidateId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=request.state.user["id"])
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
    raw_body = await request.body()
    signature = request.headers.get(WEBHOOK_SIGNATURE_HEADER, "")
    timestamp = request.headers.get(WEBHOOK_TIMESTAMP_HEADER, "")
    if not verify_shared_secret_webhook(raw_body=raw_body, signature=signature, timestamp=timestamp):
        logger.warning("reply_webhook_rejected reason=signature_validation_failed")
        return JSONResponse(status_code=401, content={"ok": False})

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


@router.get("/outreach/open")
def track_outreach_open(eventId: str = Query(...), token: str = Query(...), db: Session = Depends(get_db)):
    result = record_outreach_open(db=db, event_id=eventId, token=token)
    pixel = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    if result.get("status") == "opened":
        return Response(content=pixel, media_type="image/gif")
    return Response(status_code=204)
