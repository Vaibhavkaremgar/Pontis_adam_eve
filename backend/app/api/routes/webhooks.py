from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import WEBHOOK_SHARED_SECRET
from app.db.session import get_db
from app.services.resend_inbound_service import process_resend_inbound_webhook
from app.services.vapi_webhook_service import process_vapi_webhook
from app.utils.responses import success_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


def _validate_vapi_request(request: Request) -> None:
    expected_secret = (WEBHOOK_SHARED_SECRET or "").strip()
    if not expected_secret:
        logger.warning("vapi_webhook_validation_skipped reason=missing_shared_secret")
        return
    received_secret = (request.headers.get("x-vapi-webhook-secret") or request.headers.get("x-webhook-secret") or "").strip()
    if received_secret != expected_secret:
        logger.warning("vapi_webhook_rejected reason=invalid_secret")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


@router.post("/webhooks/resend")
async def resend_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    try:
        result = process_resend_inbound_webhook(db=db, raw_body=raw_body, headers=request.headers)
        return success_response(result)
    except RuntimeError as exc:
        message = str(exc)
        if message.startswith("invalid_webhook_signature"):
            logger.warning("resend_webhook_rejected reason=%s", message)
            raise HTTPException(status_code=401, detail="Invalid webhook signature") from exc
        if message.startswith("invalid_webhook_payload") or message.startswith("missing_email_id"):
            logger.warning("resend_webhook_rejected reason=%s", message)
            raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
        logger.error("resend_webhook_processing_failed error=%s", message, exc_info=exc)
        raise HTTPException(status_code=502, detail="Failed to process webhook") from exc


@router.post("/webhooks/vapi")
async def vapi_webhook(request: Request, db: Session = Depends(get_db)):
    _validate_vapi_request(request)
    raw_body = await request.body()
    try:
        result = process_vapi_webhook(db=db, raw_body=raw_body, headers=request.headers)
        db.commit()
        logger.info(
            "vapi_webhook_db_committed job_id=%s updated=%s",
            result.get("job_id", ""),
            result.get("updated", False),
        )
        return success_response(result)
    except ValueError as exc:
        db.rollback()
        message = str(exc)
        if message.startswith("invalid_vapi_payload"):
            logger.warning("vapi_webhook_rejected reason=%s", message)
            raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
        logger.error("vapi_webhook_processing_failed error=%s", message, exc_info=exc)
        raise HTTPException(status_code=502, detail="Failed to process webhook") from exc
    except Exception as exc:
        db.rollback()
        logger.error("vapi_webhook_unhandled_error error=%s", str(exc), exc_info=exc)
        raise HTTPException(status_code=502, detail="Failed to process webhook") from exc
