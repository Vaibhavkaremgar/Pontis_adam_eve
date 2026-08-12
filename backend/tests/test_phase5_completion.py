from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.linkedin.models  # noqa: F401

from app.db.repositories import CandidateProfileRepository, CompanyRepository, JobRepository, UserRepository
from app.models.entities import Base, CandidateRequestEntity, NotificationEventEntity, NotificationWorkflowTokenEntity, RecruiterInterestRequestEntity
from app.services.candidate_response_service import (
    get_pending_requests_for_candidate,
    respond_to_candidate_request,
)
from app.services.candidate_access_service import can_view_full_profile, get_candidate_profile
from app.services.candidate_request_service import create_interest_request, record_not_interested
from app.utils.exceptions import APIError

PRIVATE_FIELDS = {"email", "phone", "resume_text", "raw_data", "parsed_resume_json"}


class Phase5CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        db_path = Path("./test_phase5_completion.db")
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
            user_id=self.user.id, name="Acme", website="https://acme.test", description="Test",
        )
        self.job = JobRepository(self.db).create(
            company_id=self.company.id, created_by=self.user.id, source_app="ui",
            title="Engineer", description="Build things.", location="Remote",
            compensation="$180k", work_authorization="required", remote_policy="remote",
            experience_required="5+ years", skills_required=["Python"], responsibilities=["Ship"],
        )
        CandidateProfileRepository(self.db).upsert(
            job_id=self.job.id, candidate_id="candidate-1", name="Avery",
            role="Engineer", company="Northstar", summary="Strong engineer.",
            skills=["Python"],
            raw_data={"name": "Avery", "email": "avery@example.com", "phone": "+1-555-0100"},
            fit_score=4.7, decision="strong_match", strategy="HIGH",
        )
        self.db.commit()

        # Second agency for tenant isolation
        self.user_b = UserRepository(self.db).create("recruiter_b@example.com", role="admin")
        self.company_b = CompanyRepository(self.db).create(
            user_id=self.user_b.id, name="Beta Corp", website="https://beta.test", description="Beta",
        )
        self.job_b = JobRepository(self.db).create(
            company_id=self.company_b.id, created_by=self.user_b.id, source_app="ui",
            title="Backend Engineer", description="Build APIs.", location="Remote",
            compensation="$150k", work_authorization="required", remote_policy="remote",
            experience_required="3+ years", skills_required=["Python"], responsibilities=["Build"],
        )
        CandidateProfileRepository(self.db).upsert(
            job_id=self.job_b.id, candidate_id="candidate-b1", name="Beta Candidate",
            role="Backend Engineer", company="Beta Corp", summary="Backend engineer.",
            skills=["Python"], raw_data={"name": "Beta Candidate"},
            fit_score=3.5, decision="potential", strategy="MEDIUM",
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _make_pending(self) -> CandidateRequestEntity:
        """Helper: create a PENDING request via the service."""
        create_interest_request(
            db=self.db, job_id=self.job.id, candidate_id="candidate-1",
            agency_id=self.company.id, recruiter_id=self.user.id,
        )
        self.db.commit()
        from sqlalchemy import select
        return self.db.scalar(
            select(CandidateRequestEntity).where(
                CandidateRequestEntity.candidate_id == "candidate-1",
                CandidateRequestEntity.job_id == self.job.id,
            )
        )

    # ── 1. PENDING → ACCEPTED ─────────────────────────────────────────────────

    def test_pending_to_accepted(self) -> None:
        req = self._make_pending()
        with patch("app.services.candidate_response_service.send_email", lambda **kw: None):
            result = respond_to_candidate_request(
                self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept",
            )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertIsNotNone(result["responded_at"])

        interest_row = self.db.query(RecruiterInterestRequestEntity).filter_by(
            candidate_id="candidate-1",
            job_id=self.job.id,
            agency_id=self.company.id,
            recruiter_id=self.user.id,
        ).one_or_none()
        self.assertIsNotNone(interest_row)
        self.assertEqual(interest_row.candidate_response, "accept")
        self.assertIsNotNone(interest_row.candidate_response_at)

        profile = CandidateProfileRepository(self.db).get(job_id=self.job.id, candidate_id="candidate-1")
        self.assertIsNotNone(profile)
        token_row = self.db.query(NotificationWorkflowTokenEntity).filter_by(
            job_id=self.job.id,
            token_type="slot_selection",
        ).first()
        self.assertIsNotNone(token_row)
        self.assertEqual(token_row.agency_id, self.company.id)
        self.assertEqual(token_row.user_id, self.user.id)
        self.assertEqual(token_row.candidate_id, str(profile.id))
        notification_row = self.db.query(NotificationEventEntity).filter_by(
            job_id=self.job.id,
            notification_type="slot_selection_ready",
        ).first()
        self.assertIsNotNone(notification_row)

    # ── 2. PENDING → DECLINED ─────────────────────────────────────────────────

    def test_pending_to_declined(self) -> None:
        req = self._make_pending()
        result = respond_to_candidate_request(
            self.db, request_id=str(req.id), candidate_id="candidate-1", action="decline",
        )
        self.assertEqual(result["status"], "DECLINED")
        self.assertIsNotNone(result["responded_at"])

    # ── 3. responded_at is set on first valid response ────────────────────────

    def test_responded_at_set_on_accept(self) -> None:
        req = self._make_pending()
        self.assertIsNone(req.responded_at)
        respond_to_candidate_request(
            self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept",
        )
        self.db.refresh(req)
        self.assertIsNotNone(req.responded_at)

    # ── 4. Idempotency: same action on already-transitioned request ───────────

    def test_accept_idempotent(self) -> None:
        req = self._make_pending()
        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept")
        # Second accept should not raise
        result = respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept")
        self.assertEqual(result["status"], "ACCEPTED")

    def test_decline_idempotent(self) -> None:
        req = self._make_pending()
        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="decline")
        result = respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="decline")
        self.assertEqual(result["status"], "DECLINED")

    # ── 5. Invalid transitions ────────────────────────────────────────────────

    def test_accepted_to_declined_raises_409(self) -> None:
        req = self._make_pending()
        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept")
        with self.assertRaises(APIError) as ctx:
            respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="decline")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_declined_to_accepted_raises_409(self) -> None:
        req = self._make_pending()
        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="decline")
        with self.assertRaises(APIError) as ctx:
            respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept")
        self.assertEqual(ctx.exception.status_code, 409)

    # ── 6. Invalid action value ───────────────────────────────────────────────

    def test_invalid_action_raises_400(self) -> None:
        req = self._make_pending()
        with self.assertRaises(APIError) as ctx:
            respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="approve")
        self.assertEqual(ctx.exception.status_code, 400)

    # ── 7. Wrong candidate_id raises 403 ─────────────────────────────────────

    def test_wrong_candidate_id_raises_403(self) -> None:
        req = self._make_pending()
        with self.assertRaises(APIError) as ctx:
            respond_to_candidate_request(
                self.db, request_id=str(req.id), candidate_id="wrong-candidate", action="accept",
            )
        self.assertEqual(ctx.exception.status_code, 403)

    # ── 8. Unknown request_id raises 404 ─────────────────────────────────────

    def test_unknown_request_id_raises_404(self) -> None:
        with self.assertRaises(APIError) as ctx:
            respond_to_candidate_request(
                self.db, request_id="00000000-0000-0000-0000-000000000000",
                candidate_id="candidate-1", action="accept",
            )
        self.assertEqual(ctx.exception.status_code, 404)

    # ── 9. Full profile unlocked only after ACCEPTED ──────────────────────────

    def test_full_profile_unlocked_after_accept(self) -> None:
        req = self._make_pending()
        # Before accept: LIMITED
        profile_before = get_candidate_profile(
            self.db, candidate_id="candidate-1", job_id=self.job.id, agency_id=self.company.id,
        )
        self.assertEqual(profile_before["profile_access"], "LIMITED")

        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept")

        # After accept: FULL
        profile_after = get_candidate_profile(
            self.db, candidate_id="candidate-1", job_id=self.job.id, agency_id=self.company.id,
        )
        self.assertEqual(profile_after["profile_access"], "FULL")
        self.assertIn("email", profile_after)

    def test_profile_remains_limited_after_decline(self) -> None:
        req = self._make_pending()
        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="decline")
        profile = get_candidate_profile(
            self.db, candidate_id="candidate-1", job_id=self.job.id, agency_id=self.company.id,
        )
        self.assertEqual(profile["profile_access"], "LIMITED")
        for field in PRIVATE_FIELDS:
            self.assertNotIn(field, profile)

    # ── 10. can_view_full_profile reflects transition ─────────────────────────

    def test_can_view_full_profile_true_after_accept(self) -> None:
        req = self._make_pending()
        self.assertFalse(can_view_full_profile(
            self.db, candidate_id="candidate-1", job_id=self.job.id, agency_id=self.company.id,
        ))
        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept")
        self.assertTrue(can_view_full_profile(
            self.db, candidate_id="candidate-1", job_id=self.job.id, agency_id=self.company.id,
        ))

    # ── 11. get_pending_requests_for_candidate ────────────────────────────────

    def test_get_pending_requests_for_candidate(self) -> None:
        req = self._make_pending()
        results = get_pending_requests_for_candidate(self.db, candidate_id="candidate-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["request_id"], str(req.id))
        self.assertEqual(results[0]["status"], "PENDING")

    def test_get_pending_requests_empty_after_accept(self) -> None:
        req = self._make_pending()
        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="accept")
        results = get_pending_requests_for_candidate(self.db, candidate_id="candidate-1")
        self.assertEqual(results, [])

    # ── 12. PENDING + NOT_INTERESTED → 409 (business rule regression) ─────────

    def test_not_interested_on_pending_raises_409(self) -> None:
        """Business rule: recruiter cannot mark not-interested while a PENDING
        consent request is outstanding. The candidate must respond first.
        This preserves the integrity of the consent workflow."""
        self._make_pending()
        with self.assertRaises(APIError) as ctx:
            record_not_interested(
                db=self.db, job_id=self.job.id, candidate_id="candidate-1",
                agency_id=self.company.id, recruiter_id=self.user.id,
            )
        self.assertEqual(ctx.exception.status_code, 409)

    # ── 13. Recruiter cannot self-accept via respond service ──────────────────

    def test_respond_service_requires_correct_candidate_id(self) -> None:
        """The respond service validates candidate_id against the stored row.
        A recruiter passing their own user ID as candidate_id is rejected."""
        req = self._make_pending()
        with self.assertRaises(APIError) as ctx:
            respond_to_candidate_request(
                self.db, request_id=str(req.id),
                candidate_id=self.user.id,  # recruiter's user ID, not the candidate
                action="accept",
            )
        self.assertEqual(ctx.exception.status_code, 403)

    # ── 14. Route ordering: static routes resolve before dynamic ─────────────

    def test_route_ordering_static_before_dynamic(self) -> None:
        """Verify that /candidates/accepted and /candidates/pending-acceptance
        are registered before /{candidate_id} routes in the router."""
        from app.api.routes.candidates import router
        routes = [r.path for r in router.routes]
        accepted_idx = next((i for i, p in enumerate(routes) if p == "/candidates/accepted"), None)
        pending_idx = next((i for i, p in enumerate(routes) if p == "/candidates/pending-acceptance"), None)
        dynamic_idx = next((i for i, p in enumerate(routes) if "{candidate_id}" in p), None)

        self.assertIsNotNone(accepted_idx, "/candidates/accepted route not found")
        self.assertIsNotNone(pending_idx, "/candidates/pending-acceptance route not found")
        self.assertIsNotNone(dynamic_idx, "/{candidate_id} route not found")
        self.assertLess(accepted_idx, dynamic_idx,
            "/candidates/accepted must be registered before /{candidate_id}")
        self.assertLess(pending_idx, dynamic_idx,
            "/candidates/pending-acceptance must be registered before /{candidate_id}")

    # ── 15. Respond endpoint exists in router ─────────────────────────────────

    def test_respond_endpoint_registered(self) -> None:
        from app.api.routes.candidates import router
        paths = [r.path for r in router.routes]
        self.assertIn("/candidates/{candidate_id}/respond", paths)

    # ── 16. Privacy: PENDING never exposes private fields ────────────────────

    def test_pending_profile_never_exposes_private_fields(self) -> None:
        self._make_pending()
        profile = get_candidate_profile(
            self.db, candidate_id="candidate-1", job_id=self.job.id, agency_id=self.company.id,
        )
        self.assertEqual(profile["profile_access"], "LIMITED")
        for field in PRIVATE_FIELDS:
            self.assertNotIn(field, profile, f"Private field '{field}' must not appear in PENDING profile")

    # ── 17. Privacy: DECLINED never exposes private fields ───────────────────

    def test_declined_profile_never_exposes_private_fields(self) -> None:
        req = self._make_pending()
        respond_to_candidate_request(self.db, request_id=str(req.id), candidate_id="candidate-1", action="decline")
        profile = get_candidate_profile(
            self.db, candidate_id="candidate-1", job_id=self.job.id, agency_id=self.company.id,
        )
        self.assertEqual(profile["profile_access"], "LIMITED")
        for field in PRIVATE_FIELDS:
            self.assertNotIn(field, profile, f"Private field '{field}' must not appear in DECLINED profile")

    # ── 18. Tenant isolation on respond ──────────────────────────────────────

    def test_respond_wrong_candidate_id_is_rejected(self) -> None:
        """Passing a different candidate_id than what is stored on the request
        is rejected with 403 — prevents cross-candidate manipulation."""
        req = self._make_pending()
        with self.assertRaises(APIError) as ctx:
            respond_to_candidate_request(
                self.db, request_id=str(req.id),
                candidate_id="candidate-b1",  # different candidate
                action="accept",
            )
        self.assertEqual(ctx.exception.status_code, 403)

    # ── 19. Migration chain verification ─────────────────────────────────────

    def test_candidate_requests_table_exists(self) -> None:
        """Verify the candidate_requests table was created by the migration
        (or by Base.metadata.create_all in test setup)."""
        from sqlalchemy import inspect
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        self.assertIn("candidate_requests", tables)

    def test_candidate_requests_has_responded_at_column(self) -> None:
        from sqlalchemy import inspect
        inspector = inspect(self.engine)
        columns = {col["name"] for col in inspector.get_columns("candidate_requests")}
        self.assertIn("responded_at", columns)
        self.assertIn("status", columns)
        self.assertIn("agency_id", columns)
        self.assertIn("job_id", columns)
        self.assertIn("candidate_id", columns)


if __name__ == "__main__":
    unittest.main()
