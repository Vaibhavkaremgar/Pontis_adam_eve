"""
Phase 6 tests: First-round interview request workflow.

Covers:
- Authorization: PENDING/DECLINED/ACCEPTED guard
- Tenant isolation: cross-agency rejection
- Idempotency: multiple YES clicks → one session
- Session: correct fields, secure token, first-round designation
- Booking: valid/invalid/expired token, slot validation, SCHEDULED transition
- Notifications: no duplicates on retry
- Email: no duplicate sends on retry
- Existing interview behavior: not broken
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.repositories import AutomationJobRepository
from app.models.entities import (
    Base,
    CandidateProfileEntity,
    CandidateRequestEntity,
    CompanyEntity,
    InterviewSessionEntity,
    JobEntity,
    NotificationEventEntity,
    NotificationWorkflowTokenEntity,
    UserEntity,
)
from app.services.automation_service import run_automation_cycle
from app.services.first_round_interview_service import request_first_round_interview
from app.services.interview_session_service import book_interview_session, get_interview_session
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


# ── Authorization tests ───────────────────────────────────────────────────────

class TestAuthorization:
    def test_pending_candidate_cannot_request_interview(self, db):
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="PENDING")
        db.commit()

        with pytest.raises(APIError) as exc:
            request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        assert exc.value.status_code == 409
        assert "not yet accepted" in str(exc.value).lower()

    def test_declined_candidate_cannot_request_interview(self, db):
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="DECLINED")
        db.commit()

        with pytest.raises(APIError) as exc:
            request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        assert exc.value.status_code == 409
        assert "declined" in str(exc.value).lower()

    def test_accepted_candidate_can_request_interview(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        result = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        assert result.get("token") or result.get("workflowToken")

    def test_no_request_row_raises_404(self, db):
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        db.commit()

        with pytest.raises(APIError) as exc:
            request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        assert exc.value.status_code == 404


# ── Tenant isolation tests ────────────────────────────────────────────────────

class TestTenantIsolation:
    def test_cross_agency_recruiter_rejected(self, db):
        agency_a = _make_agency(db, "Agency A")
        agency_b = _make_agency(db, "Agency B")
        recruiter_b = _make_recruiter(db, agency_b.id)
        job_a = _make_job(db, agency_a.id, recruiter_b.id)  # job belongs to A
        profile = _make_candidate(db, job_a.id, agency_a.id)
        _make_request(db, profile.candidate_id, job_a.id, agency_a.id, recruiter_b.id, status="ACCEPTED")
        db.commit()

        # recruiter_b belongs to agency_b, job belongs to agency_a → forbidden
        with pytest.raises(APIError) as exc:
            request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job_a.id, recruiter_id=recruiter_b.id)
        assert exc.value.status_code == 403

    def test_same_agency_recruiter_allowed(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        result = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        assert result.get("token") or result.get("workflowToken")


# ── Idempotency tests ─────────────────────────────────────────────────────────

class TestIdempotency:
    def test_multiple_yes_clicks_create_one_session(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        r1 = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        r2 = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        r3 = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)

        sessions = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).all()
        assert len(sessions) == 1
        assert r1["token"] == r2["token"] == r3["token"]

    def test_duplicate_requests_do_not_create_duplicate_notifications(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)

        notifs = db.query(NotificationEventEntity).filter_by(
            job_id=job.id, candidate_id=profile.candidate_id,
            notification_type="first_round_interview_requested"
        ).all()
        assert len(notifs) == 1

    def test_duplicate_requests_do_not_send_duplicate_emails(self, db, monkeypatch):
        email_calls = []
        monkeypatch.setattr(
            "app.services.first_round_interview_service._send_booking_email",
            lambda **kw: email_calls.append(kw)
        )
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)

        assert len(email_calls) == 1


# ── Session tests ─────────────────────────────────────────────────────────────

class TestSession:
    def test_session_has_correct_fields(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        result = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)

        assert result["jobId"] == job.id
        assert result["candidateId"] == profile.candidate_id
        assert result["interviewRound"] == "first_round"
        assert result.get("token") or result.get("workflowToken")

        session = db.query(InterviewSessionEntity).filter_by(job_id=job.id, candidate_id=profile.candidate_id).first()
        assert session is not None
        assert session.agency_id == agency.id
        assert session.session_token  # secure token present
        assert len(session.session_token) >= 20  # cryptographically long

    def test_session_token_is_secure(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        result = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id)
        token = result.get("token") or result.get("workflowToken")
        # Token must not be a raw DB id
        assert token != job.id
        assert token != profile.candidate_id
        assert token != agency.id
        assert len(token) >= 20


# ── Booking tests ─────────────────────────────────────────────────────────────

class TestBooking:
    def _setup(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()
        slot = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        result = request_first_round_interview(
            db, candidate_id=profile.candidate_id, job_id=job.id,
            recruiter_id=recruiter.id, available_slots=[slot]
        )
        return result, slot

    def test_valid_token_returns_session(self, db, monkeypatch):
        result, _ = self._setup(db, monkeypatch)
        token = result.get("token") or result.get("workflowToken")
        session_data = get_interview_session(db=db, token=token)
        assert session_data["token"] == token or session_data["workflowToken"] == token

    def test_invalid_token_raises_404(self, db):
        with pytest.raises(APIError) as exc:
            get_interview_session(db=db, token="totally-invalid-token-xyz")
        assert exc.value.status_code == 404

    def test_booking_with_valid_slot_transitions_to_scheduled(self, db, monkeypatch):
        result, slot = self._setup(db, monkeypatch)
        token = result.get("token") or result.get("workflowToken")
        booked = book_interview_session(db=db, token=token, scheduled_at=slot)
        assert booked["status"] == "interview_scheduled"
        assert booked["scheduledAt"] is not None

    def test_booking_with_unavailable_slot_raises_conflict(self, db, monkeypatch):
        result, slot = self._setup(db, monkeypatch)
        token = result.get("token") or result.get("workflowToken")
        bad_slot = (datetime.now(timezone.utc) + timedelta(days=99)).isoformat()
        with pytest.raises(APIError) as exc:
            book_interview_session(db=db, token=token, scheduled_at=bad_slot)
        assert exc.value.status_code == 409

    def test_duplicate_booking_raises_error(self, db, monkeypatch):
        result, slot = self._setup(db, monkeypatch)
        token = result.get("token") or result.get("workflowToken")
        book_interview_session(db=db, token=token, scheduled_at=slot)
        with pytest.raises(APIError):
            book_interview_session(db=db, token=token, scheduled_at=slot)

    def test_session_stage_becomes_scheduled_after_booking(self, db, monkeypatch):
        result, slot = self._setup(db, monkeypatch)
        token = result.get("token") or result.get("workflowToken")
        book_interview_session(db=db, token=token, scheduled_at=slot)
        session = db.query(InterviewSessionEntity).filter_by(session_token=token).first()
        if session is None:
            # token may be workflow token; find by job
            wt_row = db.query(NotificationWorkflowTokenEntity).filter_by(token=token).first()
            if wt_row:
                payload = wt_row.payload or {}
                session_token = payload.get("currentInterviewToken")
                session = db.query(InterviewSessionEntity).filter_by(session_token=session_token).first()
        assert session is not None
        assert session.stage == "scheduled"
        assert session.booking_status == "confirmed"


# ── Existing interview behavior ───────────────────────────────────────────────

class TestExistingInterviewBehavior:
    def test_existing_session_endpoint_still_works(self, db, monkeypatch):
        """Existing POST /interview/session still creates sessions normally."""
        from app.services.interview_session_service import create_interview_session
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        db.commit()

        result = create_interview_session(db=db, job_id=job.id, candidate_id=profile.candidate_id)
        assert result.get("token") or result.get("workflowToken")

    def test_existing_book_endpoint_still_works(self, db, monkeypatch):
        from app.services.interview_session_service import create_interview_session
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        db.commit()

        slot = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        result = create_interview_session(db=db, job_id=job.id, candidate_id=profile.candidate_id, available_slots=[slot])
        token = result.get("token") or result.get("workflowToken")
        booked = book_interview_session(db=db, token=token, scheduled_at=slot)
        assert booked["status"] == "interview_scheduled"


class TestAutomationBridge:
    def test_booking_creates_interview_execution_automation_job(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        slot = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        result = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id, available_slots=[slot])
        token = result.get("token") or result.get("workflowToken")
        booked = book_interview_session(db=db, token=token, scheduled_at=slot)

        job_row = AutomationJobRepository(db).get_by_key(f"interview-execution:{booked['token']}")
        assert job_row is not None
        assert job_row.automation_type == "interview_execution"
        assert job_row.job_id == job.id
        assert job_row.candidate_id == profile.candidate_id

    def test_automation_cycle_triggers_due_interview_execution(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        slot = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id, available_slots=[slot])
        token = result.get("token") or result.get("workflowToken")
        book_interview_session(db=db, token=token, scheduled_at=slot)

        trigger_calls = []

        def fake_trigger(*, db, session, workflow_token):
            trigger_calls.append((session.session_token, workflow_token))
            return {"status": "triggered"}

        monkeypatch.setattr("app.services.automation_service._trigger_interview_execution", fake_trigger)
        summary = run_automation_cycle(db=db, scan_limit=10)

        assert summary["executed"] >= 1
        assert trigger_calls

    def test_failed_trigger_marks_job_retryable(self, db, monkeypatch):
        monkeypatch.setattr("app.services.first_round_interview_service._send_booking_email", lambda **kw: None)
        agency = _make_agency(db)
        recruiter = _make_recruiter(db, agency.id)
        job = _make_job(db, agency.id, recruiter.id)
        profile = _make_candidate(db, job.id, agency.id)
        _make_request(db, profile.candidate_id, job.id, agency.id, recruiter.id, status="ACCEPTED")
        db.commit()

        slot = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = request_first_round_interview(db, candidate_id=profile.candidate_id, job_id=job.id, recruiter_id=recruiter.id, available_slots=[slot])
        token = result.get("token") or result.get("workflowToken")
        book_interview_session(db=db, token=token, scheduled_at=slot)

        def fake_trigger(*, db, session, workflow_token):
            raise RuntimeError("boom")

        monkeypatch.setattr("app.services.automation_service._trigger_interview_execution", fake_trigger)
        summary = run_automation_cycle(db=db, scan_limit=10)

        assert summary["failed"] >= 1
        row = AutomationJobRepository(db).get_by_key(f"interview-execution:{token}")
        assert row is not None
        assert row.status in {"retryable", "failed"}
