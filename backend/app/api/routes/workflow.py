from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.services.ownership import assert_job_company_ownership
from app.services.voice_workflow_service import resolve_eve_workflow_context
from app.utils.exceptions import APIError
from app.utils.responses import success_response

router = APIRouter(tags=["workflow"])


@router.get("/workflow/{workflowToken}")
def get_workflow_context(
    workflowToken: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        context = resolve_eve_workflow_context(db=db, workflow_token=workflowToken)
    except ValueError as exc:
        raise APIError(str(exc), status_code=404) from exc
    assert_job_company_ownership(db=db, job_id=context["jobId"], user_id=current_user["id"])
    return success_response(context)
