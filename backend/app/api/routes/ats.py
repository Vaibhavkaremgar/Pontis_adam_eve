from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.params import Query
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_ATS_PROVIDER
from app.core.security import get_current_user
from app.db.repositories import CandidateProfileRepository, CompanyRepository, JobRepository, NotificationEventRepository
from app.db.session import get_db
from app.schemas.ats import ATSConnectRequest, ATSExportRequest
from app.services.audit_service import record_audit_event
from app.services.ats.service import export_candidate_to_ats
from app.services.ats_lifecycle_service import candidate_timeline
from app.services.ownership import assert_job_ownership
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
    record_audit_event(
        db=db,
        actor_id=user_id,
        action="ats_connect",
        entity_type="company",
        entity_id=company.id,
        metadata={"provider": payload.provider},
        request_id=str(getattr(request.state, "request_id", "") or ""),
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
    record_audit_event(
        db=db,
        actor_id=user_id,
        action="ats_disconnect",
        entity_type="company",
        entity_id=company.id,
        metadata={},
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    db.commit()
    return success_response({"connected": False})


@router.post("/ats/export")
def export_to_ats(
    payload: ATSExportRequest,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=payload.job_id, user_id=request.state.user["id"])
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
    record_audit_event(
        db=db,
        actor_id=request.state.user["id"],
        action="ats_export",
        entity_type="job",
        entity_id=payload.job_id,
        metadata={"candidate_id": payload.candidate_id},
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    db.commit()
    return success_response(result)


@router.get("/ats/timeline")
def get_candidate_timeline(
    request: Request,
    jobId: str = Query(...),
    candidateId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=request.state.user["id"])
    return success_response(candidate_timeline(db=db, job_id=jobId, candidate_id=candidateId))


@router.get("/ats/notifications")
def get_notifications(
    request: Request,
    jobId: str = Query(...),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assert_job_ownership(db=db, job_id=jobId, user_id=request.state.user["id"])
    rows = NotificationEventRepository(db).list_for_job(jobId)
    return success_response(
        [
            {
                "id": row.id,
                "candidateId": row.candidate_id,
                "recipientType": row.recipient_type,
                "recipient": row.recipient,
                "channel": row.channel,
                "title": row.title,
                "body": row.body,
                "status": row.status,
                "notificationType": row.notification_type,
                "notificationKey": row.notification_key,
                "deliveryReference": row.delivery_reference,
                "metadata": row.notification_metadata,
                "createdAt": row.created_at.isoformat(),
                "updatedAt": row.updated_at.isoformat(),
            }
            for row in rows
        ]
    )
