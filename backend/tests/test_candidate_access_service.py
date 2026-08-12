from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.linkedin.models  # noqa: F401  — registers linkedin_accounts table

from app.db.repositories import CandidateProfileRepository, CompanyRepository, JobRepository, UserRepository
from app.models.entities import Base, CandidateRequestEntity
from app.services.candidate_access_service import (
    can_view_full_profile,
    get_accepted_candidates,
    get_candidate_profile,
    get_pending_candidates,
)
from app.utils.exceptions import APIError

PRIVATE_FIELDS = {"email", "phone", "resume_text", "raw_data", "parsed_resume_json"}


class CandidateAccessServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db_path = Path("./test_candidate_access_service.db")
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
                "email": "avery@example.com",
                "phone": "+1-555-0100",
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
            raw_data={"name": "Beta Candidate", "email": "beta@example.com"},
            fit_score=3.5,
            decision="potential",
            strategy="MEDIUM",
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _insert_request(self, *, candidate_id: str, job_id: str, agency_id: str, status: str) -> CandidateRequestEntity:
        import uuid
        req = CandidateRequestEntity(
            id=str(uuid.uuid4()),
            agency_id=agency_id,
            job_id=job_id,
            candidate_id=candidate_id,
            created_by=self.user.id,
            status=status,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    # ── 1. can_view_full_profile ──────────────────────────────────────────────

    def test_can_view_full_profile_false_when_no_request(self) -> None:
        result = can_view_full_profile(
            self.db,
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
        )
        self.assertFalse(result)

    def test_can_view_full_profile_false_when_pending(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="PENDING",
        )
        result = can_view_full_profile(
            self.db,
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
        )
        self.assertFalse(result)

    def test_can_view_full_profile_false_when_declined(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="DECLINED",
        )
        result = can_view_full_profile(
            self.db,
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
        )
        self.assertFalse(result)

    def test_can_view_full_profile_true_when_accepted(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="ACCEPTED",
        )
        result = can_view_full_profile(
            self.db,
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
        )
        self.assertTrue(result)

    # ── 2. get_candidate_profile — limited when no request ────────────────────

    def test_get_profile_returns_limited_when_no_request(self) -> None:
        profile = get_candidate_profile(
            self.db,
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
        )
        self.assertEqual(profile["profile_access"], "LIMITED")
        for field in PRIVATE_FIELDS:
            self.assertNotIn(field, profile, f"Private field '{field}' must not appear in limited profile")

    def test_get_profile_returns_limited_when_pending(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="PENDING",
        )
        profile = get_candidate_profile(
            self.db,
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
        )
        self.assertEqual(profile["profile_access"], "LIMITED")
        self.assertEqual(profile["request_status"], "PENDING")
        for field in PRIVATE_FIELDS:
            self.assertNotIn(field, profile)

    def test_get_profile_returns_full_when_accepted(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="ACCEPTED",
        )
        profile = get_candidate_profile(
            self.db,
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
        )
        self.assertEqual(profile["profile_access"], "FULL")
        self.assertEqual(profile["request_status"], "ACCEPTED")
        # Contact fields must be present in full profile
        self.assertIn("email", profile)
        self.assertIn("phone", profile)
        self.assertIn("resume_text", profile)

    # ── 3. Job-scoped consent ─────────────────────────────────────────────────

    def test_accepted_for_job_a_does_not_unlock_job_b(self) -> None:
        """ACCEPTED for job A must NOT grant access to the same candidate under job B."""
        # Create a second job under the same agency
        job_2 = JobRepository(self.db).create(
            company_id=self.company.id,
            created_by=self.user.id,
            source_app="ui",
            title="Senior Engineer",
            description="Senior role.",
            location="Remote",
            compensation="$200k",
            work_authorization="required",
            remote_policy="remote",
            experience_required="7+ years",
            skills_required=["Python"],
            responsibilities=["Lead"],
        )
        CandidateProfileRepository(self.db).upsert(
            job_id=job_2.id,
            candidate_id="candidate-1",
            name="Avery",
            role="Senior Engineer",
            company="Northstar",
            summary="Strong backend engineer.",
            skills=["Python"],
            raw_data={"name": "Avery"},
            fit_score=4.9,
            decision="strong_match",
            strategy="HIGH",
        )
        self.db.commit()

        # Accept for job 1 only
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="ACCEPTED",
        )

        # Job 1 → FULL
        self.assertTrue(can_view_full_profile(
            self.db, candidate_id="candidate-1", job_id=self.job.id, agency_id=self.company.id,
        ))
        # Job 2 → still LIMITED
        self.assertFalse(can_view_full_profile(
            self.db, candidate_id="candidate-1", job_id=job_2.id, agency_id=self.company.id,
        ))
        profile_2 = get_candidate_profile(
            self.db, candidate_id="candidate-1", job_id=job_2.id, agency_id=self.company.id,
        )
        self.assertEqual(profile_2["profile_access"], "LIMITED")

    # ── 4. get_accepted_candidates ────────────────────────────────────────────

    def test_get_accepted_candidates_empty_when_none_accepted(self) -> None:
        results = get_accepted_candidates(self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertEqual(results, [])

    def test_get_accepted_candidates_returns_full_profiles(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="ACCEPTED",
        )
        results = get_accepted_candidates(self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["profile_access"], "FULL")
        self.assertEqual(results[0]["candidate_id"], "candidate-1")

    def test_get_accepted_candidates_excludes_pending(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="PENDING",
        )
        results = get_accepted_candidates(self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertEqual(results, [])

    # ── 5. get_pending_candidates ─────────────────────────────────────────────

    def test_get_pending_candidates_empty_when_none_pending(self) -> None:
        results = get_pending_candidates(self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertEqual(results, [])

    def test_get_pending_candidates_returns_limited_profiles(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="PENDING",
        )
        results = get_pending_candidates(self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["profile_access"], "LIMITED")
        self.assertEqual(results[0]["request_status"], "PENDING")
        for field in PRIVATE_FIELDS:
            self.assertNotIn(field, results[0])

    def test_get_pending_candidates_excludes_accepted(self) -> None:
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="ACCEPTED",
        )
        results = get_pending_candidates(self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertEqual(results, [])

    # ── 6. Tenant isolation ───────────────────────────────────────────────────

    def test_get_profile_raises_403_for_wrong_agency(self) -> None:
        with self.assertRaises(APIError) as ctx:
            get_candidate_profile(
                self.db,
                candidate_id="candidate-b1",
                job_id=self.job_b.id,
                agency_id=self.company.id,  # Agency A trying to access Agency B's job
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_get_accepted_candidates_raises_403_for_wrong_agency(self) -> None:
        with self.assertRaises(APIError) as ctx:
            get_accepted_candidates(
                self.db,
                job_id=self.job_b.id,
                agency_id=self.company.id,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_get_pending_candidates_raises_403_for_wrong_agency(self) -> None:
        with self.assertRaises(APIError) as ctx:
            get_pending_candidates(
                self.db,
                job_id=self.job_b.id,
                agency_id=self.company.id,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    # ── 7. 404 for unknown job / candidate ────────────────────────────────────

    def test_get_profile_raises_404_for_unknown_job(self) -> None:
        with self.assertRaises(APIError) as ctx:
            get_candidate_profile(
                self.db,
                candidate_id="candidate-1",
                job_id="00000000-0000-0000-0000-000000000000",
                agency_id=self.company.id,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_profile_raises_404_for_unknown_candidate(self) -> None:
        with self.assertRaises(APIError) as ctx:
            get_candidate_profile(
                self.db,
                candidate_id="nonexistent-candidate",
                job_id=self.job.id,
                agency_id=self.company.id,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    # ── 8. Accepted list does not bleed across agencies ───────────────────────

    def test_accepted_list_isolated_per_agency(self) -> None:
        """Agency A's ACCEPTED request must not appear in Agency B's accepted list."""
        self._insert_request(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            status="ACCEPTED",
        )
        results_b = get_accepted_candidates(self.db, job_id=self.job_b.id, agency_id=self.company_b.id)
        self.assertEqual(results_b, [])

        results_a = get_accepted_candidates(self.db, job_id=self.job.id, agency_id=self.company.id)
        self.assertEqual(len(results_a), 1)


if __name__ == "__main__":
    unittest.main()
