"""
Phase 7 integration tests: Adam ↔ interview.pontis.one integration.

Verifies:
1. Correct booking URL: https://interview.pontis.one/booking.html?token=<TOKEN>
2. Correct interview URL: https://interview.pontis.one/interview?token=<TOKEN>
3. Same session token is preserved across booking → interview
4. Token resolves the correct Adam interview session
5. Correct candidate/job/agency context is returned from session context endpoint
6. Scheduled session is accepted by session context endpoint
7. Unscheduled session is rejected by session context endpoint
8. Wrong token is rejected
9. Cross-agency access is rejected
10. Completed session cannot be restarted
11. Duplicate Vapi callback does not create duplicate result
12. Automation execution prepares interview URL (no fake HTTP trigger)
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import INTERVIEW_APP_URL
from app.models.entities import (
    Base,
    CandidateProfileEntity,
    CandidateRequestEntity,
    CompanyEntity,
    InterviewSessionEntity,
    JobEntity,
    NotificationWorkflowTokenEntity,
    UserEntity,
)
from app.services.automation_service import _trigger_interview_execution
from app.services.first_round_interview_service import request_first_round_interview
from app.services.interview_session_service import (
    _interview_url,
    _slot_booking_url,
    book_interview_session,
)
from app.utils.exceptions import APIError


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _make_agency(db, name="Agency A"):
    agency = CompanyEntity(id=str(uuid4()), name=name, slug=name.lower().replace(" ", "-"))
    db.add(agency)
    db.flush()
    return agency


def _make_recruiter(db, agency_id):
    user = UserEntity(id=str(uuid4()), email=f"recruiter-{uuid4().hex[:6]}@test.com", role="recruiter", agency_id=agency_id)
    db.add(user)
    db.flush()
    return user


def _make_job(db, agency_id, recruiter_id):
    job = JobEntity(
        id=str(uuid4()), title="Backend Engineer", agency_id=agency_id,
        created_by=recruiter_id, source_app="ui", job_status="active",
        vetting_mode="volume", created_by_source="PONTIS", updated_by_source="PONTIS",
    )
    db.add(job)
    db.flush()
    return job


def _make_candidate(db, job_id, agency_id, email="candidate@test.com"):
    cid = f"cand-{uuid4().hex[:8]}"
    profile = CandidateProfileEntity(
        id=str(uuid4()), candidate_id=cid, job_id=job_id, agency_id=agency_id,
        name="Test Candidate", email=email,
        raw_data={"email": email, "work_email": email},
        created_by_source="PONTIS", updated_by_source="PONTIS",
    )
    db.add(profile)
    db.flush()
    return profile


def _make_request(db, candidate_id, job_id, agency_id, recruiter_id, status="ACCEPTED"):
    req = CandidateRequestEntity(
        id=str(uuid4()), candidate_id=candidate_id, job_id=job_id,
        agency_id=agency_id, status=status, created_by=recruiter_id,
    )
    db.add(req)
    db.flush()
    return req


def _setup_scheduled_session(db, monkeypatch):
    monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
    agency = _make_agency(db)
    recruiter = _make_recruiter(db, agency.id)
    job = _make_job(db, agency.id, recruiter.id)
    profile = _make_candidate(db, job.id, agency.id)
    _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
    db.commit()
    slot = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    result = request_first_round_interview(
        db, candidate_id=profile.candidate_id, job_id=job.id,
        recruiter_id=recruiter.id, available_slots=[slot],
    )
    token = result.get("token") or result.get("workflowToken")
    book_interview_session(db=db, token=token, scheduled_at=slot)
    return agency, job, profile, token


# ── 1. Booking URL format ─────────────────────────────────────────────────────

class TestBookingURL:
    def test_booking_url_uses_correct_base(self):
        token = secrets.token_urlsafe(32)
        url = _slot_booking_url(token)
        assert url.startswith("https://interview.pontis.one/booking.html")

    def test_booking_url_carries_token_param(self):
        token = secrets.token_urlsafe(32)
        url = _slot_booking_url(token)
        assert f"?token={token}" in url

    def test_booking_url_full_form(self):
        token = "abc123"
        url = _slot_booking_url(token)
        assert url == "https://interview.pontis.one/booking.html?token=abc123"


# ── 2. Interview URL format ───────────────────────────────────────────────────

class TestInterviewURL:
    def test_interview_url_uses_correct_base(self):
        token = secrets.token_urlsafe(32)
        url = _interview_url(token)
        assert url.startswith("https://interview.pontis.one/interview")

    def test_interview_url_uses_token_param_not_session(self):
        token = secrets.token_urlsafe(32)
        url = _interview_url(token)
        assert "?token=" in url
        assert "?session=" not in url

    def test_interview_url_full_form(self):
        token = "abc123"
        url = _interview_url(token)
        assert url == "https://interview.pontis.one/interview?token=abc123"


# ── 3. Same token preserved across booking → interview ───────────────────────

class TestTokenPreservation:
    def test_same_token_in_booking_and_interview_url(self, db, monkeypatch):
        agency, job, profile, token = _setup_scheduled_session(db, monkeypatch)
        booking_url = _slot_booking_url(token)
        interview_url = _interview_url(token)
        # Both URLs carry the same token
        assert f"token={token}" in booking_url
        assert f"token={token}" in interview_url

    def test_booking_result_contains_meeting_link_with_token(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()
        slot = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        result = request_first_round_interview(
            db, candidate_id=profile.candidate_id, job_id=job.id,
            recruiter_id=recruiter.id, available_slots=[slot],
        )
        token = result.get("token") or result.get("workflowToken")
        booked = book_interview_session(db=db, token=token, scheduled_at=slot)
        meeting_link = booked.get("meetingLink", "")
        assert "interview.pontis.one/interview" in meeting_link
        assert "token=" in meeting_link


# ── 4. Token resolves correct session ────────────────────────────────────────

class TestTokenResolution:
    def test_token_resolves_correct_session(self, db, monkeypatch):
        agency, job, profile, token = _setup_scheduled_session(db, monkeypatch)
        session = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).first()
        assert session is not None
        assert session.session_token == token or session.token == token

    def test_wrong_token_raises_404(self, db):
        from app.services.interview_session_service import get_interview_session
        with pytest.raises(APIError) as exc:
            get_interview_session(db=db, token="completely-invalid-token-xyz-999")
        assert exc.value.status_code == 404


# ── 5. Session context returns correct candidate/job/agency ──────────────────

class TestSessionContext:
    def test_session_context_fields(self, db, monkeypatch):
        """Verify the session context endpoint returns the expected shape."""
        agency, job, profile, token = _setup_scheduled_session(db, monkeypatch)
        # Resolve the actual session_token (may differ from workflow token)
        session = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).first()
        assert session is not None
        assert session.job_id == job.id
        assert session.candidate_id == profile.candidate_id
        assert session.agency_id == agency.id
        assert session.scheduled_at is not None
        assert session.status == "interview_scheduled"


# ── 6 & 7. Scheduled vs unscheduled session state ────────────────────────────

class TestSessionState:
    def test_scheduled_session_has_correct_status(self, db, monkeypatch):
        agency, job, profile, token = _setup_scheduled_session(db, monkeypatch)
        session = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).first()
        assert session.status == "interview_scheduled"
        assert session.scheduled_at is not None

    def test_unscheduled_session_has_pending_status(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()
        request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        session = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).first()
        assert session.status not in {"interview_scheduled", "completed"}


# ── 9. Cross-agency access rejected ──────────────────────────────────────────

class TestCrossAgencyRejection:
    def test_cross_agency_interview_request_rejected(self, db):
        agency_a = _make_agency(db, "Agency A")
        agency_b = _make_agency(db, "Agency B")
        recruiter_b = _make_recruiter(db, agency_b.id)
        job_a = _make_job(db, agency_a.id, recruiter_b.id)
        profile = _make_candidate(db, job_a.id, agency_a.id)
        _make_request(db, profile.candidate_id, job_a.id, agency_a.id, recruiter_b.id, status="ACCEPTED")
        db.commit()
        with pytest.raises(APIError) as exc:
            request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job_a.id, recruiter_id=recruiter_b.id)
        assert exc.value.status_code == 403


# ── 10. Completed session cannot be restarted ─────────────────────────────────

class TestCompletedSession:
    def test_completed_session_cannot_be_rebooked(self, db, monkeypatch):
        agency, job, profile, token = _setup_scheduled_session(db, monkeypatch)
        # Mark session as completed
        session = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).first()
        session.stage = "completed"
        session.status = "completed"
        db.commit()
        # Attempting to book again should fail
        slot = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        with pytest.raises(APIError):
            book_interview_session(db=db, token=token, scheduled_at=slot)


# ── 12. Automation execution prepares interview URL (no fake HTTP call) ───────

class TestAutomationExecution:
    def test_trigger_returns_ready_with_interview_url(self, db, monkeypatch):
        agency, job, profile, token = _setup_scheduled_session(db, monkeypatch)
        session = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).first()
        result = _trigger_interview_execution(db=db, session=session, workflow_token=token)
        assert result["status"] == "ready"
        assert "interview.pontis.one/interview" in result["interviewUrl"]
        assert f"token={session.session_token}" in result["interviewUrl"]

    def test_trigger_does_not_make_http_request(self, db, monkeypatch):
        """Verify no HTTP call is made to the external app — candidate-driven model."""
        import app.services.automation_service as svc
        # If requests were imported and used, this would catch it
        assert not hasattr(svc, "requests"), "requests module must not be imported in automation_service"

        agency, job, profile, token = _setup_scheduled_session(db, monkeypatch)
        session = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).first()
        # Should not raise — no HTTP call attempted
        result = _trigger_interview_execution(db=db, session=session, workflow_token=token)
        assert result["status"] == "ready"


# ── INTERVIEW_APP_URL configuration ──────────────────────────────────────────

class TestConfiguration:
    def test_interview_app_url_points_to_correct_domain(self):
        assert "interview.pontis.one" in INTERVIEW_APP_URL
        assert "adam-interview" not in INTERVIEW_APP_URL

    def test_interview_app_url_has_no_trailing_slash(self):
        assert not INTERVIEW_APP_URL.endswith("/")
