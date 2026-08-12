"""
test_ready_candidate_profile.py
================================
Tests for the recruiter-safe Ready candidate profile experience.

Covers:
1.  Ready card contains candidate summary fields.
2.  Expanded profile contains work_experience, education, skills, certifications,
    location, role/company, summary.
3.  Ready API does NOT return email, phone, raw_data, parsed_resume_json,
    unrestricted resume_text.
4.  Resume contact header is not exposed.
5.  TO_BE_ACCEPTED profile is recruiter-safe.
6.  ACCEPTED profile is recruiter-safe.
7.  TO_BE_INTERVIEWED profile is recruiter-safe.
8.  Cross-agency candidate access is rejected.
9.  Cross-job candidate access is rejected.
10. Existing Ready classification tests still pass (bucket split).
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
    InterviewSessionEntity,
    JobEntity,
    UserEntity,
)
from app.services.first_round_interview_service import request_first_round_interview
from app.services.ready_profile_serializer import build_ready_card, build_ready_profile
from app.services.results_service import list_ready_candidates
from app.utils.exceptions import APIError

# Fields that must NEVER appear in any Ready response
_PRIVATE_FIELDS = {"email", "phone", "mobile", "raw_data", "parsed_resume_json",
                   "resume_text", "rawData", "parsedResumeJson", "resumeText",
                   "contactEmail", "contactPhone", "linkedin_url", "github_url"}


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


def _candidate(db, job_id, agency_id, *, name="Test Candidate", email="cand@test.com"):
    cid = f"cand-{uuid4().hex[:8]}"
    profile = CandidateProfileEntity(
        id=str(uuid4()),
        candidate_id=cid,
        job_id=job_id,
        agency_id=agency_id,
        name=name,
        email=email,
        phone="+1 555 0100",
        current_role="Software Engineer",
        current_company="Acme Corp",
        location="Remote, US",
        summary="Strong backend engineer with Python expertise.",
        skills=["Python", "FastAPI", "PostgreSQL"],
        total_experience_years=5.0,
        fit_score=0.82,
        raw_data={"email": email, "work_email": email, "phone": "+1 555 0100"},
        parsed_resume_json={
            "work_experience": [
                {"title": "Senior Engineer", "company": "Acme Corp", "dates": "2020-2024",
                 "description": "Led backend services."}
            ],
            "education": [
                {"degree": "B.Sc. Computer Science", "institution": "State University", "year": "2018"}
            ],
            "certifications": [
                {"name": "AWS Certified Developer", "issuer": "Amazon", "issued_date": "2022"}
            ],
        },
        created_by_source="PONTIS",
        updated_by_source="PONTIS",
    )
    db.add(profile); db.flush(); return profile


def _request(db, candidate_id, job_id, agency_id, recruiter_id, status="PENDING"):
    r = CandidateRequestEntity(
        id=str(uuid4()), candidate_id=candidate_id, job_id=job_id,
        agency_id=agency_id, status=status, created_by=recruiter_id,
    )
    db.add(r); db.flush(); return r


def _assert_no_private_fields(data: dict):
    """Recursively assert no private fields appear anywhere in the dict."""
    for key in data:
        assert key not in _PRIVATE_FIELDS, f"Private field '{key}' found in response"
    for value in data.values():
        if isinstance(value, dict):
            _assert_no_private_fields(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _assert_no_private_fields(item)


# ── 1. Ready card contains candidate summary ──────────────────────────────────

def test_ready_card_contains_summary_fields(db):
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    req = _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "PENDING")
    db.commit()

    card = build_ready_card(profile, req)

    assert card["candidate_id"] == profile.candidate_id
    assert card["name"] == profile.name
    assert card["role"] == profile.current_role
    assert card["company"] == profile.current_company
    assert card["location"] == profile.location
    assert card["years_experience"] == profile.total_experience_years
    assert "Python" in card["skills"]
    assert card["summary"] == profile.summary
    assert card["match_score"] == profile.fit_score


# ── 2. Expanded profile contains required fields ──────────────────────────────

def test_expanded_profile_contains_required_fields(db):
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    req = _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    db.commit()

    expanded = build_ready_profile(profile, req)

    assert expanded["name"] == profile.name
    assert expanded["role"] == profile.current_role
    assert expanded["company"] == profile.current_company
    assert expanded["location"] == profile.location
    assert expanded["years_experience"] == profile.total_experience_years
    assert "Python" in expanded["skills"]
    assert expanded["summary"] == profile.summary
    assert len(expanded["work_experience"]) == 1
    assert expanded["work_experience"][0]["title"] == "Senior Engineer"
    assert len(expanded["education"]) == 1
    assert len(expanded["certifications"]) == 1
    assert expanded["certifications"][0]["name"] == "AWS Certified Developer"


# ── 3. Ready API does NOT return private fields ───────────────────────────────

def test_ready_api_does_not_return_private_fields(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.first_round_interview_service._send_booking_email",
        lambda **kw: None,
    )
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "PENDING")
    db.commit()

    result = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)

    all_cards = (
        result["ready"]["toBeAccepted"]
        + result["ready"]["accepted"]
        + result["ready"]["toBeInterviewed"]
    )
    assert len(all_cards) == 1
    card = all_cards[0]
    _assert_no_private_fields(card)

    # Explicitly check top-level card
    for field in _PRIVATE_FIELDS:
        assert field not in card, f"Private field '{field}' in card"

    # Check nested profile too
    if "profile" in card:
        for field in _PRIVATE_FIELDS:
            assert field not in card["profile"], f"Private field '{field}' in profile"


# ── 4. Resume contact header not exposed ─────────────────────────────────────

def test_resume_contact_header_not_exposed(db):
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id, email="secret@private.com")
    req = _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    db.commit()

    card = build_ready_card(profile, req)
    expanded = build_ready_profile(profile, req)

    for data in (card, expanded):
        assert "email" not in data
        assert "phone" not in data
        assert "resume_text" not in data
        assert "raw_data" not in data
        assert "parsed_resume_json" not in data
        # Ensure the actual email value doesn't appear in summary/role fields
        for v in data.values():
            if isinstance(v, str):
                assert "secret@private.com" not in v


# ── 5. TO_BE_ACCEPTED profile is recruiter-safe ───────────────────────────────

def test_to_be_accepted_profile_is_recruiter_safe(db):
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    req = _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "PENDING")
    db.commit()

    result = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    cards = result["ready"]["toBeAccepted"]
    assert len(cards) == 1
    _assert_no_private_fields(cards[0])
    assert cards[0]["lifecycle_state"] == "TO_BE_ACCEPTED"


# ── 6. ACCEPTED profile is recruiter-safe ────────────────────────────────────

def test_accepted_profile_is_recruiter_safe(db):
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    db.commit()

    result = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    cards = result["ready"]["accepted"]
    assert len(cards) == 1
    _assert_no_private_fields(cards[0])
    assert cards[0]["lifecycle_state"] == "ACCEPTED"


# ── 7. TO_BE_INTERVIEWED profile is recruiter-safe ───────────────────────────

def test_to_be_interviewed_profile_is_recruiter_safe(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.first_round_interview_service._send_booking_email",
        lambda **kw: None,
    )
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    db.commit()

    slot = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    request_first_round_interview(
        db, candidate_id=profile.candidate_id, job_id=job.id,
        recruiter_id=recruiter.id, available_slots=[slot],
    )
    db.commit()

    result = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    cards = result["ready"]["toBeInterviewed"]
    assert len(cards) == 1
    _assert_no_private_fields(cards[0])
    assert cards[0]["lifecycle_state"] == "TO_BE_INTERVIEWED"


# ── 8. Cross-agency candidate access is rejected ─────────────────────────────

def test_cross_agency_access_rejected(db):
    agency_a = _agency(db, "Agency A")
    agency_b = _agency(db, "Agency B")
    recruiter_a = _recruiter(db, agency_a.id)
    job_a = _job(db, agency_a.id, recruiter_a.id)
    _candidate(db, job_a.id, agency_a.id)
    db.commit()

    with pytest.raises(APIError) as exc:
        list_ready_candidates(db=db, job_id=job_a.id, agency_id=agency_b.id)
    assert exc.value.status_code == 403


# ── 9. Cross-job candidate access is rejected ────────────────────────────────

def test_cross_job_candidate_not_returned(db):
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job_a = _job(db, agency.id, recruiter.id)
    job_b = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job_a.id, agency.id)
    _request(db, profile.candidate_id, job_a.id, agency.id, recruiter.id, "PENDING")
    db.commit()

    # Querying job_b should return no candidates (candidate belongs to job_a)
    result = list_ready_candidates(db=db, job_id=job_b.id, agency_id=agency.id)
    total = sum(len(v) for v in result["ready"].values())
    assert total == 0


# ── 10. Existing bucket-split classification still passes ────────────────────

def test_ready_candidates_bucket_split(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.first_round_interview_service._send_booking_email",
        lambda **kw: None,
    )
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)

    pending = _candidate(db, job.id, agency.id, name="Pending", email="p@test.com")
    acc = _candidate(db, job.id, agency.id, name="Accepted", email="a@test.com")
    interviewed = _candidate(db, job.id, agency.id, name="Interviewed", email="i@test.com")

    _request(db, pending.candidate_id, job.id, agency.id, recruiter.id, "PENDING")
    _request(db, acc.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    _request(db, interviewed.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    db.commit()

    slot = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    request_first_round_interview(
        db, candidate_id=interviewed.candidate_id, job_id=job.id,
        recruiter_id=recruiter.id, available_slots=[slot],
    )
    db.commit()

    result = list_ready_candidates(db=db, job_id=job.id, agency_id=agency.id)
    assert result["counts"] == {"toBeAccepted": 1, "accepted": 1, "toBeInterviewed": 1}
    assert result["ready"]["toBeAccepted"][0]["candidate_id"] == pending.candidate_id
    assert result["ready"]["accepted"][0]["candidate_id"] == acc.candidate_id
    assert result["ready"]["toBeInterviewed"][0]["candidate_id"] == interviewed.candidate_id


# ── Serializer unit: blocked fields never leak ───────────────────────────────

def test_serializer_never_leaks_blocked_fields(db):
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id, email="leak@test.com")
    req = _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    db.commit()

    card = build_ready_card(profile, req)
    expanded = build_ready_profile(profile, req)

    for data in (card, expanded):
        _assert_no_private_fields(data)


# ── Certifications come from parsed_resume_json, not a new table ─────────────

def test_certifications_sourced_from_parsed_resume_json(db):
    agency = _agency(db)
    recruiter = _recruiter(db, agency.id)
    job = _job(db, agency.id, recruiter.id)
    profile = _candidate(db, job.id, agency.id)
    req = _request(db, profile.candidate_id, job.id, agency.id, recruiter.id, "ACCEPTED")
    db.commit()

    expanded = build_ready_profile(profile, req)
    assert len(expanded["certifications"]) == 1
    cert = expanded["certifications"][0]
    assert cert["name"] == "AWS Certified Developer"
    assert cert["issuer"] == "Amazon"
    # No file paths or private URLs
    assert "file_path" not in cert
    assert "storage_path" not in cert
