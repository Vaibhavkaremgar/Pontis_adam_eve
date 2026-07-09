from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.params import Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.services.ownership import assert_job_company_ownership, resolve_company_id_for_user
from app.services.results_service import get_result_by_workflow_token, list_results, resolve_result_context, stream_result_video
from app.services.result_operations_service import advance_result_candidate, record_result_decision
from app.utils.responses import success_response

router = APIRouter(tags=["results"])


class InterviewerPayload(BaseModel):
    name: str = Field(default="")
    email: str = Field(default="")


class AdvanceResultPayload(BaseModel):
    roundType: str = Field(default="Second Round")
    mode: str = Field(default="Online")
    meetUrl: str = Field(default="")
    officeAddress: str = Field(default="")
    interviewer: InterviewerPayload = Field(default_factory=InterviewerPayload)
    recruiterEmail: str = Field(default="")
    slots: list[str] = Field(default_factory=list)
    notes: str = Field(default="")
    timezone: str = Field(default="")
    duration: str = Field(default="")
    panelInterviewers: list[str] = Field(default_factory=list)


class DecisionPayload(BaseModel):
    decision: str


@router.get("/results")
def results_list(
    request: Request,
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_company_ownership(db=db, job_id=jobId, user_id=request.state.user["id"])
    company_id = resolve_company_id_for_user(db=db, user_id=request.state.user["id"])
    return success_response(list_results(db=db, job_id=jobId, recruiter_id=request.state.user["id"], company_id=company_id))


@router.get("/results/{workflowToken}")
def results_detail(
    workflowToken: str,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    assert_job_company_ownership(db=db, job_id=context["jobId"], user_id=request.state.user["id"])
    payload = get_result_by_workflow_token(
        db=db,
        workflow_token=workflowToken,
        recruiter_id=request.state.user["id"],
        company_id=resolve_company_id_for_user(db=db, user_id=request.state.user["id"]),
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
    assert_job_company_ownership(db=db, job_id=context["jobId"], user_id=request.state.user["id"])
    return stream_result_video(
        db=db,
        workflow_token=workflowToken,
        recruiter_id=request.state.user["id"],
        company_id=resolve_company_id_for_user(db=db, user_id=request.state.user["id"]),
        range_header=request.headers.get("range", ""),
    )


@router.post("/results/{workflowToken}/decision")
def results_decision(
    workflowToken: str,
    request: Request,
    payload: DecisionPayload = Body(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    assert_job_company_ownership(db=db, job_id=context["jobId"], user_id=request.state.user["id"])
    return success_response(
        record_result_decision(
            db=db,
            workflow_token=workflowToken,
            recruiter_id=request.state.user["id"],
            company_id=resolve_company_id_for_user(db=db, user_id=request.state.user["id"]),
            decision=payload.decision,
        )
    )


@router.post("/results/{workflowToken}/advance")
def results_advance(
    workflowToken: str,
    request: Request,
    payload: AdvanceResultPayload = Body(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    assert_job_company_ownership(db=db, job_id=context["jobId"], user_id=request.state.user["id"])
    return success_response(
        advance_result_candidate(
            db=db,
            workflow_token=workflowToken,
            recruiter_id=request.state.user["id"],
            company_id=resolve_company_id_for_user(db=db, user_id=request.state.user["id"]),
            payload=payload.model_dump(),
        )
    )
