from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.entities import Base, CandidateProfileEntity, CandidateRequestEntity, CompanyEntity, InterviewEntity, InterviewSessionEntity, JobEntity, UserEntity
from app.services.first_round_interview_service import request_first_round_interview
from app.services.results_service import list_ready_candidates


def _make_agency(db, name: str = "Agency A"):
    agency = CompanyEntity(id=str(uuid4()), name=name, slug=name.lower().replace(" ", "-"))
    db.add(agency)
    db.flush()
    return agency


def _make_recruiter(db, agency_id: str):
    recruiter = UserEntity(id=str(uuid4()), email=f"recruiter-{uuid4().hex[:8]}@test.com", role="recruiter", agency_id=agency_id)
    db.add(recruiter)
    db.flush()
    return recruiter


def _make_job(db, agency_id: str, recruiter_id: str):
    job = JobEntity(
        id=str(uuid4()),
        title="Backend Engineer",
        agency_id=agency_id,
        created_by=recruiter_id,
        source_app="ui",
        job_status="active",
        vetting_mode="volume",
        created_by_source="PONTIS",
        updated_by_source="PONTIS",
    )
    db.add(job)
    db.flush()
    return job


def _make_candidate(db, job_id: str, agency_id: str, *, name: str, email: str):
    candidate_id = f"cand-{uuid4().hex[:8]}"
    profile = CandidateProfileEntity(
        id=str(uuid4()),
        candidate_id=candidate_id,
        job_id=job_id,
        agency_id=agency_id,
        name=name,
        email=email,
        phone="+1 555 0100",
        current_company="Pontis",
        current_role="Software Engineer",
        location="Remote",
        linkedin_url="https://linkedin.example/candidate",
        skills=["Python", "FastAPI"],
        summary="Strong backend engineer.",
        raw_data={"email": email, "work_email": email, "linkedin_url": "https://linkedin.example/candidate"},
        created_by_source="PONTIS",
        updated_by_source="PONTIS",
    )
    db.add(profile)
    db.flush()
    return profile


def _make_request(db, *, candidate_id: str, job_id: str, agency_id: str, recruiter_id: str, status: str):
    request = CandidateRequestEntity(
        id=str(uuid4()),
        candidate_id=candidate_id,
        job_id=job_id,
        agency_id=agency_id,
        status=status,
        created_by=recruiter_id,
    )
    db.add(request)
    db.flush()
    return request


def _make_completed_interview(db, *, job_id: str, candidate_id: str, agency_id: str):
    interview = InterviewEntity(
        id=str(uuid4()),
        job_id=job_id,
        candidate_id=candidate_id,
        agency_id=agency_id,
        status="completed",
        transcript="Candidate explained the system well.",
        ai_summary="Strong performance.",
        interview_score=8.7,
        technical_score=8.5,
        communication_score=8.0,
        culture_fit_score=9.0,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        updated_at=datetime.now(timezone.utc),
        created_by_source="PONTIS",
        updated_by_source="PONTIS",
        source_app="ui",
    )
    interview.completed_at = datetime.now(timezone.utc)
    db.add(interview)
    db.flush()
    return interview


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def test_ready_candidates_are_split_into_three_buckets(db, monkeypatch):
    monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kwargs: None)
    agency = _make_agency(db)
    recruiter = _make_recruiter(db, agency.id)
    job = _make_job(db, agency.id, recruiter.id)

    pending = _make_candidate(db, job.id, agency.id, name="Pending Candidate", email="pending@test.com")
    accepted = _make_candidate(db, job.id, agency.id, name="Accepted Candidate", email="accepted@test.com")
    interviewed = _make_candidate(db, job.id, agency.id, name="Interviewed Candidate", email="interviewed@test.com")
    completed = _make_candidate(db, job.id, agency.id, name="Completed Candidate", email="completed@test.com")

    _make_request(db, candidate_id=pending.candidate_id, job_id=job.id, agency_id=agency.id, recruiter_id=recruiter.id, status="PENDING")
    _make_request(db, candidate_id=accepted.candidate_id, job_id=job.id, agency_id=agency.id, recruiter_id=recruiter.id, status="ACCEPTED")
    _make_request(db, candidate_id=interviewed.candidate_id, job_id=job.id, agency_id=agency.id, recruiter_id=recruiter.id, status="ACCEPTED")
    _make_request(db, candidate_id=completed.candidate_id, job_id=job.id, agency_id=agency.id, recruiter_id=recruiter.id, status="ACCEPTED")
    db.commit()

    request_first_round_interview(
        db,
        candidate_id=interviewed.candidate_id,
        job_id=job.id,
        recruiter_id=recruiter.id,
        available_slots=[(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()],
    )
    _make_completed_interview(db, job_id=job.id, candidate_id=completed.candidate_id, agency_id=agency.id)
    db.commit()

    ready = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)

    assert ready["counts"] == {"toBeAccepted": 1, "accepted": 1, "toBeInterviewed": 1}
    assert [item["candidate_id"] for item in ready["ready"]["toBeAccepted"]] == [pending.candidate_id]
    assert [item["candidate_id"] for item in ready["ready"]["accepted"]] == [accepted.candidate_id]
    assert [item["candidate_id"] for item in ready["ready"]["toBeInterviewed"]] == [interviewed.candidate_id]
    assert ready["ready"]["accepted"][0]["profile_access"] == "FULL"
    assert ready["ready"]["toBeAccepted"][0]["profile_access"] == "LIMITED"
