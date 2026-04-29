from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_ATS_PROVIDER
from app.core.security import get_current_user
from app.db.repositories import CandidateProfileRepository, CompanyRepository, JobRepository
from app.db.session import get_db
from app.schemas.ats import ATSConnectRequest, ATSExportRequest
from app.services.ats.service import export_candidate_to_ats
from app.utils.exceptions import APIError
from app.utils.responses import success_response

router = APIRouter(tags=["ats"])


@router.post("/ats/connect")
def connect_ats(
    payload: ATSConnectRequest,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = request.state.user["id"]
    company = CompanyRepository(db).get_latest_for_user(user_id=user_id)
    if not company:
        raise APIError("Company not found", status_code=404)

    company = CompanyRepository(db).update_profile(
        company_id=company.id,
        ats_provider=payload.provider,
        ats_connected=True,
    )
    db.commit()
    provider = company.ats_provider or DEFAULT_ATS_PROVIDER
    return success_response(
        {
            "connected": True,
            "provider": provider,
            "atsProvider": provider,
            "atsConnected": True,
        }
    )


@router.post("/ats/disconnect")
def disconnect_ats(
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = request.state.user["id"]
    company = CompanyRepository(db).get_latest_for_user(user_id=user_id)
    if not company:
        raise APIError("Company not found", status_code=404)

    company = CompanyRepository(db).update_profile(
        company_id=company.id,
        ats_provider="",
        ats_connected=False,
    )
    db.commit()
    return success_response({"connected": False})


@router.post("/ats/export")
def export_to_ats(
    payload: ATSExportRequest,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = JobRepository(db).get(payload.job_id)
    if not job:
        raise APIError("Job not found", status_code=404)

    candidate = CandidateProfileRepository(db).get(job_id=payload.job_id, candidate_id=payload.candidate_id)
    if not candidate:
        raise APIError("Candidate not found", status_code=404)

    result = export_candidate_to_ats(
        candidate,
        job,
        provider=None,
        db=db,
    )
    return success_response(result)
