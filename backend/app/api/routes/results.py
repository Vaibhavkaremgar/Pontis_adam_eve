from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.params import Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import get_current_user, is_super_admin_role
from app.db.session import get_db
from app.models.entities import JobEntity
from app.services.results_service import get_result_by_workflow_token, list_results, resolve_result_context, stream_result_video
from app.services.result_operations_service import advance_result_candidate, record_result_decision
from app.db.repositories import JobRepository
from app.utils.exceptions import APIError
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


def _current_agency_id(request: Request) -> str:
    agency_id = str(
        getattr(request.state, "agency_id", "")
        or getattr(request.state, "company_id", "")
        or (request.state.user.get("agency_id") if isinstance(getattr(request.state, "user", {}), dict) else "")
        or (request.state.user.get("company_id") if isinstance(getattr(request.state, "user", {}), dict) else "")
        or ""
    ).strip()
    return agency_id


@router.get("/results/jobs")
def results_jobs_list(
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_super_admin = is_super_admin_role(getattr(request.state, "user", {}).get("role") if isinstance(getattr(request.state, "user", {}), dict) else "")
    agency_id = _current_agency_id(request)
    if not agency_id and is_super_admin:
        jobs = db.scalars(select(JobEntity)).all()
    else:
        if not agency_id:
            raise APIError("Forbidden", status_code=403)
        jobs = JobRepository(db).list_by_company(agency_id)
    return success_response({
        "jobs": [
            {"jobId": job.id, "title": job.title or "Untitled", "location": job.location or "", "createdAt": job.created_at.isoformat() if job.created_at else None}
            for job in jobs
        ]
    })


@router.get("/results")
def results_list(
    request: Request,
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_super_admin = is_super_admin_role(getattr(request.state, "user", {}).get("role") if isinstance(getattr(request.state, "user", {}), dict) else "")
    agency_id = _current_agency_id(request)
    job = JobRepository(db).get(jobId)
    if not job:
        raise APIError("Job not found", status_code=404)
    if not agency_id and is_super_admin:
        agency_id = str(getattr(job, "company_id", "") or "").strip()
    if str(getattr(job, "company_id", "") or "").strip() != agency_id:
        raise APIError("Forbidden", status_code=403)
    return success_response(list_results(db=db, job_id=jobId, recruiter_id=request.state.user["id"], agency_id=agency_id))


@router.get("/results/{workflowToken}")
def results_detail(
    workflowToken: str,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_super_admin = is_super_admin_role(getattr(request.state, "user", {}).get("role") if isinstance(getattr(request.state, "user", {}), dict) else "")
    agency_id = _current_agency_id(request)
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    if not agency_id and is_super_admin:
        job = JobRepository(db).get(context["jobId"])
        agency_id = str(getattr(job, "company_id", "") or "").strip()
    payload = get_result_by_workflow_token(
        db=db,
        workflow_token=workflowToken,
        recruiter_id=request.state.user["id"],
        agency_id=agency_id,
    )
    return success_response(payload)


@router.get("/results/video/{workflowToken}")
def results_video(
    workflowToken: str,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_super_admin = is_super_admin_role(getattr(request.state, "user", {}).get("role") if isinstance(getattr(request.state, "user", {}), dict) else "")
    agency_id = _current_agency_id(request)
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    if not agency_id and is_super_admin:
        job = JobRepository(db).get(context["jobId"])
        agency_id = str(getattr(job, "company_id", "") or "").strip()
    return stream_result_video(
        db=db,
        workflow_token=workflowToken,
        recruiter_id=request.state.user["id"],
        agency_id=agency_id,
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
    is_super_admin = is_super_admin_role(getattr(request.state, "user", {}).get("role") if isinstance(getattr(request.state, "user", {}), dict) else "")
    agency_id = _current_agency_id(request)
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    if not agency_id and is_super_admin:
        job = JobRepository(db).get(context["jobId"])
        agency_id = str(getattr(job, "company_id", "") or "").strip()
    return success_response(
        record_result_decision(
            db=db,
            workflow_token=workflowToken,
            recruiter_id=request.state.user["id"],
            agency_id=agency_id,
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
    is_super_admin = is_super_admin_role(getattr(request.state, "user", {}).get("role") if isinstance(getattr(request.state, "user", {}), dict) else "")
    agency_id = _current_agency_id(request)
    context = resolve_result_context(db=db, workflow_token=workflowToken)
    if not agency_id and is_super_admin:
        job = JobRepository(db).get(context["jobId"])
        agency_id = str(getattr(job, "company_id", "") or "").strip()
    return success_response(
        advance_result_candidate(
            db=db,
            workflow_token=workflowToken,
            recruiter_id=request.state.user["id"],
            agency_id=agency_id,
            payload=payload.model_dump(),
        )
    )
