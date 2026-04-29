from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.services.reply_polling_service import poll_candidate_replies, resolve_attachment_path
from app.utils.responses import success_response

router = APIRouter(tags=["replies"])


@router.post("/replies/poll")
def poll_replies(_: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    data = poll_candidate_replies(db=db)
    return success_response(data)


@router.get("/replies/attachments/{reply_id}/{filename}")
def get_reply_attachment(reply_id: str, filename: str):
    path = resolve_attachment_path(reply_id, filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(
        path=str(path),
        filename=Path(filename).name,
        media_type="application/octet-stream",
    )
