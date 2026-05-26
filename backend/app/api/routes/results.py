from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.params import Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.services.ownership import assert_job_ownership
from app.services.results_service import get_result_by_workflow_token, list_results, resolve_result_context, stream_result_video
from app.utils.responses import success_response

router = APIRouter(tags=["results"])


@router.get("/results")
def results_list(
    request: Request,
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=request.state.user["id"])
    return success_response(list_results(db=db, job_id=jobId, recruiter_id=request.state.user["id"]))


@router.get("/results/{workflowToken}")
def results_detail(
    workflowToken: str,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    assert_job_ownership(db=db, job_id=context["jobId"], user_id=request.state.user["id"])
    payload = get_result_by_workflow_token(
        db=db,
        workflow_token=workflowToken,
        recruiter_id=request.state.user["id"],
    )
    return success_response(payload)


@router.get("/results/video/{workflowToken}")
def results_video(
    workflowToken: str,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    assert_job_ownership(db=db, job_id=context["jobId"], user_id=request.state.user["id"])
    return stream_result_video(
        db=db,
        workflow_token=workflowToken,
        recruiter_id=request.state.user["id"],
        range_header=request.headers.get("range", ""),
    )
