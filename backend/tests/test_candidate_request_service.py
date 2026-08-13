from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import LinkedIn models so linkedin_accounts table is registered in metadata
import app.linkedin.models  # noqa: F401

from app.db.repositories import CandidateProfileRepository, CompanyRepository, JobRepository, UserRepository
from app.models.entities import Base, RecruiterInterestRequestEntity
from app.services.candidate_request_service import create_interest_request, get_request_status, record_not_interested, request_state_map
from app.utils.exceptions import APIError

PRIVATE_FIELDS = {"email", "phone", "resume_text", "raw_resume_text", "parsed_data"}


class CandidateRequestServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db_path = Path("./test_candidate_request_service.db")
        db_path.unlink(missing_ok=True)
        cls.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=cls.engine)
        cls.SessionLocal = sessionmaker(bind=cls.engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def setUp(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

        self.user = UserRepository(self.db).create("recruiter@example.com", role="admin")
        self.company = CompanyRepository(self.db).create(
            user_id=self.user.id,
            name="Acme",
            website="https://acme.test",
            description="Test company",
        )
        self.job = JobRepository(self.db).create(
            company_id=self.company.id,
            agency_id=self.company.id,
            created_by=self.user.id,
            source_app="ui",
            title="Platform Engineer",
            description="Build reliable systems.",
            location="Remote",
            compensation="$180k",
            work_authorization="required",
            remote_policy="remote",
            experience_required="5+ years",
            skills_required=["Python", "FastAPI"],
            responsibilities=["Ship product"],
        )
        CandidateProfileRepository(self.db).upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            name="Avery",
            role="Platform Engineer",
            company="Northstar",
            summary="Strong backend engineer.",
            skills=["Python", "FastAPI"],
            raw_data={
                "name": "Avery",
                "current_company": "Northstar",
                "current_title": "Platform Engineer",
                "skills": ["Python", "FastAPI"],
                "summary": "Strong backend engineer.",
            },
            fit_score=4.7,
            decision="strong_match",
            strategy="HIGH",
        )
        self.db.commit()

        # Second agency + job for tenant isolation tests
        self.user_b = UserRepository(self.db).create("recruiter_b@example.com", role="admin")
        self.company_b = CompanyRepository(self.db).create(
            user_id=self.user_b.id,
            name="Beta Corp",
            website="https://beta.test",
            description="Another company",
        )
        self.job_b = JobRepository(self.db).create(
            company_id=self.company_b.id,
            agency_id=self.company_b.id,
            created_by=self.user_b.id,
            source_app="ui",
            title="Backend Engineer",
            description="Build APIs.",
            location="Remote",
            compensation="$150k",
            work_authorization="required",
            remote_policy="remote",
            experience_required="3+ years",
            skills_required=["Python"],
            responsibilities=["Build APIs"],
        )
        CandidateProfileRepository(self.db).upsert(
            job_id=self.job_b.id,
            candidate_id="candidate-b1",
            name="Beta Candidate",
            role="Backend Engineer",
            company="Beta Corp",
            summary="Backend engineer.",
            skills=["Python"],
            raw_data={"name": "Beta Candidate"},
            fit_score=3.5,
            decision="potential",
            strategy="MEDIUM",
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    # ── 1. Interested → PENDING + deduplication ──────────────────────────────

    def test_interest_request_deduplicates_and_persists(self) -> None:
        first = create_interest_request(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        )
        second = create_interest_request(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        )

        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(first["status"], "PENDING")
        count = self.db.execute(
            text("SELECT COUNT(*) FROM candidate_requests WHERE job_id = :job_id AND candidate_id = :candidate_id"),
            {"job_id": self.job.id, "candidate_id": "candidate-1"},
        ).scalar_one()
        self.assertEqual(count, 1)

        interest_row = self.db.query(RecruiterInterestRequestEntity).filter_by(
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        ).one_or_none()
        self.assertIsNotNone(interest_row)
        self.assertEqual(interest_row.request_status, "interested")
        self.assertEqual(interest_row.candidate_response, None)

    # ── 2. Not interested + blocks subsequent interest ────────────────────────

    def test_not_interested_persists_and_blocks_pending_interest(self) -> None:
        result = record_not_interested(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        )

        self.assertEqual(result["recruiter_action"], "NOT_INTERESTED")
        state = get_request_status(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
        )
        self.assertEqual(state["recruiter_action"], "NOT_INTERESTED")
        self.assertIsNone(state["request_status"])

        with self.assertRaises(APIError):
            create_interest_request(
                db=self.db,
                job_id=self.job.id,
                candidate_id="candidate-1",
                agency_id=self.company.id,
                recruiter_id=self.user.id,
            )

        mapped = request_state_map(db=self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertEqual(mapped["candidate-1"]["recruiter_action"], "NOT_INTERESTED")

    # ── 3. Persistence: create → GET → PENDING ───────────────────────────────

    def test_get_request_status_returns_pending_after_interest(self) -> None:
        create_interest_request(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        )
        status = get_request_status(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
        )
        self.assertEqual(status["status"], "PENDING")
        self.assertEqual(status["recruiter_action"], "INTERESTED")
        self.assertIsNotNone(status["request_id"])

    def test_get_request_status_returns_none_when_no_action(self) -> None:
        status = get_request_status(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
        )
        self.assertEqual(status["recruiter_action"], "NONE")
        self.assertIsNone(status["request_status"])

    # ── 4. Tenant isolation ───────────────────────────────────────────────────

    def test_agency_a_cannot_create_request_for_agency_b_job(self) -> None:
        """Agency A recruiter cannot create a request against Agency B's job."""
        with self.assertRaises(APIError) as ctx:
            create_interest_request(
                db=self.db,
                job_id=self.job_b.id,
                candidate_id="candidate-b1",
                agency_id=self.company.id,   # Agency A's ID against Agency B's job
                recruiter_id=self.user.id,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_agency_a_cannot_read_agency_b_request_status(self) -> None:
        """Agency A cannot read request status for Agency B's candidate/job."""
        with self.assertRaises(APIError) as ctx:
            get_request_status(
                db=self.db,
                job_id=self.job_b.id,
                candidate_id="candidate-b1",
                agency_id=self.company.id,   # Agency A's ID
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_agency_a_cannot_mark_not_interested_on_agency_b_candidate(self) -> None:
        """Agency A cannot mark not-interested on Agency B's candidate."""
        with self.assertRaises(APIError) as ctx:
            record_not_interested(
                db=self.db,
                job_id=self.job_b.id,
                candidate_id="candidate-b1",
                agency_id=self.company.id,
                recruiter_id=self.user.id,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    # ── 5. Invalid job / candidate → 404 ─────────────────────────────────────

    def test_interest_request_invalid_job_returns_404(self) -> None:
        with self.assertRaises(APIError) as ctx:
            create_interest_request(
                db=self.db,
                job_id="00000000-0000-0000-0000-000000000000",
                candidate_id="candidate-1",
                agency_id=self.company.id,
                recruiter_id=self.user.id,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_interest_request_invalid_candidate_returns_404(self) -> None:
        with self.assertRaises(APIError) as ctx:
            create_interest_request(
                db=self.db,
                job_id=self.job.id,
                candidate_id="nonexistent-candidate",
                agency_id=self.company.id,
                recruiter_id=self.user.id,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    # ── 6. Candidate privacy ─────────────────────────────────────────────────

    def test_interest_response_does_not_expose_private_fields(self) -> None:
        result = create_interest_request(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        )
        for field in PRIVATE_FIELDS:
            self.assertNotIn(field, result, f"Private field '{field}' must not appear in interest response")

    def test_request_status_does_not_expose_private_fields(self) -> None:
        create_interest_request(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        )
        status = get_request_status(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
        )
        for field in PRIVATE_FIELDS:
            self.assertNotIn(field, status, f"Private field '{field}' must not appear in request status response")

    # ── 7. request_state_map isolation ───────────────────────────────────────

    def test_request_state_map_only_returns_own_agency_data(self) -> None:
        create_interest_request(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        )
        # Agency B's map should be empty (no requests for job_b yet)
        map_b = request_state_map(db=self.db, job_id=self.job_b.id, agency_id=self.company_b.id)
        self.assertNotIn("candidate-1", map_b)

        # Agency A's map should contain candidate-1
        map_a = request_state_map(db=self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertIn("candidate-1", map_a)
        self.assertEqual(map_a["candidate-1"]["status"], "PENDING")
