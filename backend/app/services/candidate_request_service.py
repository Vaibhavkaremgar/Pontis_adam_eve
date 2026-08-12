from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import CandidateFeedbackEntity, CandidateProfileEntity, CandidateRequestEntity, JobEntity, RecruiterInterestRequestEntity
from app.utils.exceptions import APIError


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _validate_scope(db: Session, *, job_id: str, candidate_id: str, agency_id: str) -> tuple[JobEntity, CandidateProfileEntity]:
    job = db.get(JobEntity, job_id)
    if not job:
        raise APIError("Job not found", status_code=404)
    if str(job.agency_id or "") != str(agency_id):
        raise APIError("Forbidden", status_code=403)
    candidate = db.scalar(
        select(CandidateProfileEntity).where(
            CandidateProfileEntity.candidate_id == candidate_id,
            CandidateProfileEntity.agency_id == agency_id,
        ).limit(1)
    )
    if not candidate:
        raise APIError("Candidate not found", status_code=404)
    return job, candidate


def _serialize(row: CandidateRequestEntity, *, recruiter_action: str = "INTERESTED") -> dict:
    return {
        "request_id": str(row.id),
        "candidate_id": row.candidate_id,
        "job_id": str(row.job_id),
        "status": row.status,
        "recruiter_action": recruiter_action,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "responded_at": _iso(row.responded_at),
    }


def create_interest_request(db: Session, *, job_id: str, candidate_id: str, agency_id: str, recruiter_id: str) -> dict:
    _validate_scope(db, job_id=job_id, candidate_id=candidate_id, agency_id=agency_id)
    feedback = db.scalar(select(CandidateFeedbackEntity).where(
        CandidateFeedbackEntity.job_id == job_id, CandidateFeedbackEntity.candidate_id == candidate_id
    ))
    if feedback and feedback.rejected:
        raise APIError("Candidate is already marked not interested", status_code=409)
    existing = db.scalar(select(CandidateRequestEntity).where(
        CandidateRequestEntity.agency_id == agency_id,
        CandidateRequestEntity.job_id == job_id,
        CandidateRequestEntity.candidate_id == candidate_id,
    ))
    if existing:
        _upsert_recruiter_interest_request(
            db,
            job_id=job_id,
            candidate_id=candidate_id,
            agency_id=agency_id,
            recruiter_id=recruiter_id,
            request_status="interested",
        )
        return _serialize(existing)
    now = datetime.now(timezone.utc)
    row = CandidateRequestEntity(
        id=str(uuid4()), candidate_id=candidate_id, agency_id=agency_id, job_id=job_id,
        status="PENDING", created_by=recruiter_id, created_at=now, updated_at=now,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(CandidateRequestEntity).where(
            CandidateRequestEntity.agency_id == agency_id,
            CandidateRequestEntity.job_id == job_id,
            CandidateRequestEntity.candidate_id == candidate_id,
        ))
        if existing:
            _upsert_recruiter_interest_request(
                db,
                job_id=job_id,
                candidate_id=candidate_id,
                agency_id=agency_id,
                recruiter_id=recruiter_id,
                request_status="interested",
            )
            return _serialize(existing)
        raise
    _upsert_recruiter_interest_request(
        db,
        job_id=job_id,
        candidate_id=candidate_id,
        agency_id=agency_id,
        recruiter_id=recruiter_id,
        request_status="interested",
    )
    return _serialize(row)


def _upsert_recruiter_interest_request(
    db: Session,
    *,
    job_id: str,
    candidate_id: str,
    agency_id: str,
    recruiter_id: str,
    request_status: str,
) -> RecruiterInterestRequestEntity:
    now = datetime.now(timezone.utc)
    row = db.scalar(
        select(RecruiterInterestRequestEntity).where(
            RecruiterInterestRequestEntity.job_id == job_id,
            RecruiterInterestRequestEntity.candidate_id == candidate_id,
            RecruiterInterestRequestEntity.agency_id == agency_id,
            RecruiterInterestRequestEntity.recruiter_id == recruiter_id,
        )
    )
    if not row:
        row = RecruiterInterestRequestEntity(
            id=str(uuid4()),
            candidate_id=candidate_id,
            job_id=job_id,
            agency_id=agency_id,
            recruiter_id=recruiter_id,
            request_status=request_status,
            recruiter_requested_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.request_status = request_status
        row.recruiter_requested_at = row.recruiter_requested_at or now
        row.updated_at = now
    db.flush()
    return row


def record_not_interested(db: Session, *, job_id: str, candidate_id: str, agency_id: str, recruiter_id: str) -> dict:
    _validate_scope(db, job_id=job_id, candidate_id=candidate_id, agency_id=agency_id)
    request = db.scalar(select(CandidateRequestEntity).where(
        CandidateRequestEntity.agency_id == agency_id,
        CandidateRequestEntity.job_id == job_id,
        CandidateRequestEntity.candidate_id == candidate_id,
    ))
    if request and request.status == "PENDING":
        raise APIError("Cannot mark a pending interest request not interested", status_code=409)
    now = datetime.now(timezone.utc)
    feedback = db.scalar(select(CandidateFeedbackEntity).where(
        CandidateFeedbackEntity.job_id == job_id, CandidateFeedbackEntity.candidate_id == candidate_id
    ))
    if not feedback:
        feedback = CandidateFeedbackEntity(id=str(uuid4()), job_id=job_id, candidate_id=candidate_id, created_at=now)
        db.add(feedback)
    feedback.feedback = "reject"
    feedback.accepted = False
    feedback.rejected = True
    feedback.company_id = agency_id
    feedback.recruiter_id = recruiter_id
    feedback.updated_at = now
    db.flush()
    return {"candidate_id": candidate_id, "job_id": job_id, "recruiter_action": "NOT_INTERESTED", "recorded_at": _iso(now)}


def get_request_status(db: Session, *, job_id: str, candidate_id: str, agency_id: str) -> dict:
    _validate_scope(db, job_id=job_id, candidate_id=candidate_id, agency_id=agency_id)
    request = db.scalar(select(CandidateRequestEntity).where(
        CandidateRequestEntity.agency_id == agency_id,
        CandidateRequestEntity.job_id == job_id,
        CandidateRequestEntity.candidate_id == candidate_id,
    ))
    if request:
        return _serialize(request)
    feedback = db.scalar(select(CandidateFeedbackEntity).where(
        CandidateFeedbackEntity.job_id == job_id, CandidateFeedbackEntity.candidate_id == candidate_id
    ))
    return {"candidate_id": candidate_id, "job_id": job_id, "recruiter_action": "NOT_INTERESTED" if feedback and feedback.rejected else "NONE", "request_status": None}


def request_state_map(db: Session, *, job_id: str, agency_id: str) -> dict[str, dict]:
    requests = db.scalars(select(CandidateRequestEntity).where(
        CandidateRequestEntity.job_id == job_id, CandidateRequestEntity.agency_id == agency_id
    )).all()
    feedback = db.scalars(select(CandidateFeedbackEntity).where(CandidateFeedbackEntity.job_id == job_id, CandidateFeedbackEntity.company_id == agency_id)).all()
    result = {row.candidate_id: _serialize(row) for row in requests}
    for row in feedback:
        if row.rejected and row.candidate_id not in result:
            result[row.candidate_id] = {"recruiter_action": "NOT_INTERESTED", "request_status": None}
    return result
