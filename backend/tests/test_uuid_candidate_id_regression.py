"""
Regression tests for the uuid = text operator mismatch in Results/Ready flow.

Covers:
- UUID candidate_id (str representation of UUID)
- TEXT/slug candidate_id (legacy format)
- Candidate with no interview → appears in Ready, not Results
- Candidate with interview_session → appears in Ready toBeInterviewed
- Candidate with completed interview → appears in Results, not Ready
- Cross-agency isolation (candidates from other agencies never leak)
- Ready endpoint correctness
- Results endpoint correctness
- SQL failure surfaces as error, not silent empty response
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.entities import (
    Base,
    CandidateProfileEntity,
    CandidateRequestEntity,
    CompanyEntity,
    InterviewEntity,
    InterviewSessionEntity,
    JobEntity,
    NotificationWorkflowTokenEntity,
    UserEntity,
)
from app.services.results_service import list_ready_candidates, list_results
from app.utils.exceptions import APIError


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _agency(db, name="Agency A"):
    a = CompanyEntity(id=str(uuid4()), name=name, slug=name.lower().replace(" ", "-"))
    db.add(a); db.flush(); return a


def _recruiter(db, agency_id):
    u = UserEntity(id=str(uuid4()), email=f"r-{uuid4().hex[:6]}@test.com",
                   role="recruiter", agency_id=agency_id)
    db.add(u); db.flush(); return u


def _job(db, agency_id, recruiter_id):
    j = JobEntity(
        id=str(uuid4()), title="Backend Engineer", agency_id=agency_id,
        created_by=recruiter_id, source_app="ui", job_status="active",
        vetting_mode="volume", created_by_source="PONTIS", updated_by_source="PONTIS",
    )
    db.add(j); db.flush(); return j


def _candidate(db, job_id, agency_id, *, candidate_id=None, name="Test Candidate"):
    cid = candidate_id or f"cand-{uuid4().hex[:8]}"
    profile = CandidateProfileEntity(
        id=str(uuid4()), candidate_id=cid, job_id=job_id, agency_id=agency_id,
        name=name, email=f"{cid}@test.com",
        raw_data={"email": f"{cid}@test.com"},
        created_by_source="PONTIS", updated_by_source="PONTIS",
    )
    db.add(profile); db.flush(); return profile


def _request(db, candidate_id, job_id, agency_id, recruiter_id, status="PENDING"):
    r = CandidateRequestEntity(
        id=str(uuid4()), candidate_id=candidate_id, job_id=job_id,
        agency_id=agency_id, status=status, created_by=recruiter_id,
    )
    db.add(r); db.flush(); return r


def _interview(db, job_id, candidate_id, agency_id, *, status="completed",
               transcript="Great candidate.", score=8.5):
    i = InterviewEntity(
        id=str(uuid4()), job_id=job_id, candidate_id=candidate_id,
        agency_id=agency_id, status=status,
        transcript=transcript, interview_score=score,
        ai_summary="Strong performance.", technical_score=8.0,
        communication_score=8.5, culture_fit_score=9.0,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        updated_at=datetime.now(timezone.utc),
        created_by_source="PONTIS", updated_by_source="PONTIS", source_app="ui",
    )
    db.add(i); db.flush(); return i


def _session(db, job_id, candidate_id, agency_id, *, token=None, status="pending"):
    s = InterviewSessionEntity(
        id=str(uuid4()), job_id=job_id, candidate_id=candidate_id,
        agency_id=agency_id, session_token=token or f"tok-{uuid4().hex[:16]}",
        status=status, booking_status="pending", stage="requested",
        created_by_source="PONTIS", updated_by_source="PONTIS",
    )
    db.add(s); db.flush(); return s


def _workflow_token(db, job_id, candidate_id, agency_id):
    tok = f"wf-{uuid4().hex}"
    t = NotificationWorkflowTokenEntity(
        id=str(uuid4()), job_id=job_id, candidate_id=candidate_id,
        agency_id=agency_id, token=tok, token_type="results",
        workflow_name="results", is_active=True, status="active",
        source_app="ui", payload={},
    )
    db.add(t); db.flush(); return t


# ── UUID candidate_id ─────────────────────────────────────────────────────────

def test_uuid_candidate_id_in_ready(db):
    """A candidate whose candidate_id is a UUID string appears in Ready."""
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    uuid_cid = str(uuid4())
    profile = _candidate(db, job.id, agency.id, candidate_id=uuid_cid)
    _request(db, uuid_cid, job.id, agency.id, recruiter.id, "PENDING")
    db.commit()

    result = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    all_ids = [c["candidate_id"] for c in result["ready"]["toBeAccepted"]]
    assert uuid_cid in all_ids


def test_uuid_candidate_id_in_results(db):
    """A candidate with UUID candidate_id and completed interview appears in Results."""
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    uuid_cid = str(uuid4())
    _candidate(db, job.id, agency.id, candidate_id=uuid_cid)
    _interview(db, job.id, uuid_cid, agency.id)
    _workflow_token(db, job.id, uuid_cid, agency.id)
    db.commit()

    result = list_results(db=db, job_id=job.id, recruiter_id=recruiter.id, agency_id=agency.id)
    all_ids = [c["candidateId"] for c in result["candidates"]]
    assert uuid_cid in all_ids


# ── TEXT/slug candidate_id ────────────────────────────────────────────────────

def test_text_candidate_id_in_ready(db):
    """A candidate with a slug-style candidate_id appears in Ready."""
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    slug_cid = "john-doe-linkedin-abc123"
    profile = _candidate(db, job.id, agency.id, candidate_id=slug_cid)
    _request(db, slug_cid, job.id, agency.id, recruiter.id, "PENDING")
    db.commit()

    result = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    all_ids = [c["candidate_id"] for c in result["ready"]["toBeAccepted"]]
    assert slug_cid in all_ids


def test_text_candidate_id_in_results(db):
    """A candidate with a slug candidate_id and completed interview appears in Results."""
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    slug_cid = "jane-smith-linkedin-xyz789"
    _candidate(db, job.id, agency.id, candidate_id=slug_cid)
    _interview(db, job.id, slug_cid, agency.id)
    _workflow_token(db, job.id, slug_cid, agency.id)
    db.commit()

    result = list_results(db=db, job_id=job.id, recruiter_id=recruiter.id, agency_id=agency.id)
    all_ids = [c["candidateId"] for c in result["candidates"]]
    assert slug_cid in all_ids


# ── Candidate with no interview → Ready only ──────────────────────────────────

def test_candidate_without_interview_in_ready_not_results(db):
    """Candidate with no interview appears in Ready but not Results."""
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    db.commit()

    ready = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    results = list_results(db=db, job_id=job.id, recruiter_id=recruiter.id, agency_id=agency.id)

    ready_ids = [c["candidate_id"] for c in ready["ready"]["accepted"]]
    result_ids = [c["candidateId"] for c in results["candidates"]]

    assert profile.candidate_id in ready_ids
    assert profile.candidate_id not in result_ids


# ── Candidate with interview_session → Ready toBeInterviewed ─────────────────

def test_candidate_with_session_in_to_be_interviewed(db):
    """Candidate with an interview_session (not completed) appears in toBeInterviewed."""
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    _session(db, job.id, profile.candidate_id, agency.id)
    db.commit()

    ready = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    ids = [c["candidate_id"] for c in ready["ready"]["toBeInterviewed"]]
    assert profile.candidate_id in ids


# ── Completed interview → Results only, not Ready ────────────────────────────

def test_completed_interview_in_results_not_ready(db):
    """Candidate with completed interview appears in Results and NOT in Ready."""
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    _interview(db, job.id, profile.candidate_id, agency.id)
    _workflow_token(db, job.id, profile.candidate_id, agency.id)
    db.commit()

    ready = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    results = list_results(db=db, job_id=job.id, recruiter_id=recruiter.id, agency_id=agency.id)

    ready_all = (
        ready["ready"]["toBeAccepted"]
        + ready["ready"]["accepted"]
        + ready["ready"]["toBeInterviewed"]
    )
    ready_ids = [c["candidate_id"] for c in ready_all]
    result_ids = [c["candidateId"] for c in results["candidates"]]

    assert profile.candidate_id not in ready_ids
    assert profile.candidate_id in result_ids


# ── Cross-agency isolation ────────────────────────────────────────────────────

def test_cross_agency_ready_rejected(db):
    """Ready endpoint raises 403 when agency_id does not own the job."""
    agency_a = _agency(db, "Agency A")
    agency_b = _agency(db, "Agency B")
    recruiter_a = _recruiter(db, agency_a.id)
    job_a = _job(db, agency_a.id, recruiter_a.id)
    db.commit()

    with pytest.raises(APIError) as exc:
        list_ready_candidates(db=db, job_id=job_a.id, agency_id=agency_b.id)
    assert exc.value.status_code == 403


def test_cross_agency_results_rejected(db):
    """Results endpoint raises 403 when agency_id does not own the job."""
    agency_a = _agency(db, "Agency A")
    agency_b = _agency(db, "Agency B")
    recruiter_a = _recruiter(db, agency_a.id)
    recruiter_b = _recruiter(db, agency_b.id)
    job_a = _job(db, agency_a.id, recruiter_a.id)
    db.commit()

    with pytest.raises(APIError) as exc:
        list_results(db=db, job_id=job_a.id, recruiter_id=recruiter_b.id, agency_id=agency_b.id)
    assert exc.value.status_code == 403


def test_cross_agency_candidates_do_not_leak_into_results(db):
    """Candidates from agency_b do not appear in agency_a's Results."""
    agency_a = _agency(db, "Agency A")
    agency_b = _agency(db, "Agency B")
    recruiter_a = _recruiter(db, agency_a.id)
    recruiter_b = _recruiter(db, agency_b.id)
    job_a = _job(db, agency_a.id, recruiter_a.id)
    job_b = _job(db, agency_b.id, recruiter_b.id)

    cid_a = str(uuid4())
    cid_b = str(uuid4())
    _candidate(db, job_a.id, agency_a.id, candidate_id=cid_a)
    _candidate(db, job_b.id, agency_b.id, candidate_id=cid_b)
    _interview(db, job_a.id, cid_a, agency_a.id)
    _interview(db, job_b.id, cid_b, agency_b.id)
    _workflow_token(db, job_a.id, cid_a, agency_a.id)
    _workflow_token(db, job_b.id, cid_b, agency_b.id)
    db.commit()

    result_a = list_results(db=db, job_id=job_a.id, recruiter_id=recruiter_a.id, agency_id=agency_a.id)
    ids_a = [c["candidateId"] for c in result_a["candidates"]]
    assert cid_a in ids_a
    assert cid_b not in ids_a


# ── SQL failure surfaces as error, not silent empty response ──────────────────

def test_results_sql_failure_raises_not_silently_empty(db, monkeypatch):
    """A DB error in _candidate_result_rows must propagate, not return []."""
    from app.services import results_service

    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    db.commit()

    original = results_service._candidate_result_rows

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(results_service, "_candidate_result_rows", _raise)

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        list_results(db=db, job_id=job.id, recruiter_id=recruiter.id, agency_id=agency.id)


# ── interview_sessions join does not break when session exists ────────────────

def test_results_with_session_and_completed_interview(db):
    """Candidate with both an interview_session and completed interview appears in Results."""
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    cid = str(uuid4())
    _candidate(db, job.id, agency.id, candidate_id=cid)
    _interview(db, job.id, cid, agency.id)
    _session(db, job.id, cid, agency.id)
    _workflow_token(db, job.id, cid, agency.id)
    db.commit()

    result = list_results(db=db, job_id=job.id, recruiter_id=recruiter.id, agency_id=agency.id)
    ids = [c["candidateId"] for c in result["candidates"]]
    assert cid in ids
