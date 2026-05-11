from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.resend_inbound_service import process_resend_inbound_webhook
from app.utils.responses import success_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


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
