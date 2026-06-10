from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", f"sqlite:///./test_apollo_selective_{os.getpid()}.db")
os.environ.setdefault("JWT_SECRET", "apollo-selective-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "apollo-selective-internal-key")

if "redis" not in sys.modules:
    redis_module = types.ModuleType("redis")

    class _RedisError(Exception):
        pass

    class _FakeRedisClient:
        def ping(self):
            return True

        def close(self):
            return None

        def pipeline(self):
            return self

        def execute(self):
            return []

    redis_module.Redis = _FakeRedisClient
    redis_module.from_url = lambda *args, **kwargs: _FakeRedisClient()
    redis_exceptions = types.ModuleType("redis.exceptions")
    redis_exceptions.RedisError = _RedisError
    redis_module.exceptions = redis_exceptions
    sys.modules["redis"] = redis_module
    sys.modules["redis.exceptions"] = redis_exceptions

if "slack_sdk" not in sys.modules:
    slack_sdk = types.ModuleType("slack_sdk")

    class _FakeWebClient:
        def __init__(self, *args, **kwargs):
            pass

        def chat_postMessage(self, *args, **kwargs):
            return {"ok": True}

    slack_sdk.WebClient = _FakeWebClient
    slack_errors = types.ModuleType("slack_sdk.errors")

    class _SlackApiError(Exception):
        pass

    slack_errors.SlackApiError = _SlackApiError
    sys.modules["slack_sdk"] = slack_sdk
    sys.modules["slack_sdk.errors"] = slack_errors

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.db.repositories import CandidateProfileRepository, CandidateSelectionSessionRepository, CompanyRepository, InterviewRepository, JobRepository, UserRepository
from app.db.session import SessionLocal, engine
from app.models.entities import Base
from app.services import apollo_enrichment_service as apollo_module
import app.services.automation_service as automation_service


class ApolloSelectiveEnrichmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        engine.dispose()
        Base.metadata.create_all(bind=engine)

    def setUp(self) -> None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.db = SessionLocal()
        self.user = UserRepository(self.db).create("apollo-selective@example.com", role="admin")
        self.company = CompanyRepository(self.db).create(
            user_id=self.user.id,
            name="Selective Co",
            website="https://selective.example",
            description="Selective enrichment company",
        )
        self.job = JobRepository(self.db).create(
            company_id=self.company.id,
            created_by=self.user.id,
            source_app="ui",
            title="Backend Engineer",
            description="Build backend systems.",
            location="Remote",
            compensation="$180k",
            work_authorization="required",
            remote_policy="remote",
            experience_required="5+ years",
            skills_required=["Python", "FastAPI"],
            responsibilities=["Ship backend features"],
        )
        self.profile_repo = CandidateProfileRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _select_candidate(self, candidate_id: str = "candidate-1") -> CandidateSelectionSessionRepository:
        session_repo = CandidateSelectionSessionRepository(self.db)
        session = session_repo.create(
            job_id=self.job.id,
            candidate_pool_snapshot=[{"id": candidate_id}],
            batch_plan=[[candidate_id]],
        )
        session.selected_candidate_ids = [candidate_id]
        self.db.commit()
        return session_repo

    def test_enrichment_runs_only_after_selection_and_reuses_cached_result(self) -> None:
        self.profile_repo.upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            name="Jane Doe",
            role="Backend Engineer",
            company="Selective Co",
            summary="Backend engineer.",
            skills=["Python", "FastAPI"],
            raw_data={
                "name": "Jane Doe",
                "full_name": "Jane Doe",
                "email": "jane.doe@example.com",
                "phone": "+1 415 555 0100",
                "linkedin_url": "https://www.linkedin.com/in/janedoe",
                "current_company": "Selective Co",
                "current_title": "Backend Engineer",
                "location": "Remote",
            },
            fit_score=4.8,
            decision="strong_match",
            strategy="HIGH",
        )
        session = self._select_candidate("candidate-1")
        apollo_response = {
            "people": [
                {
                    "id": "apollo-person-1",
                    "name": "Jane Doe",
                    "linkedin_url": "https://www.linkedin.com/in/janedoe",
                    "organization_name": "Selective Co",
                    "title": "Backend Engineer",
                    "email": "jane.doe@example.com",
                    "phone": "+1 415 555 0100",
                    "location": "Remote",
                }
            ]
        }

        def _post(*args, **kwargs):
            return types.SimpleNamespace(status_code=200, json=lambda: apollo_response, raise_for_status=lambda: None)

        with patch.object(apollo_module, "APOLLO_API_KEY", "apollo-test-key"), patch.object(apollo_module.requests, "post", side_effect=_post) as mock_post:
            first = apollo_module.enrich_candidate_with_apollo(
                db=self.db,
                job_id=self.job.id,
                candidate_id="candidate-1",
                workflow_token="workflow-1",
                selection_session_id=session.get_by_job(self.job.id).id,
                automation_job_id="automation-1",
            )
            second = apollo_module.enrich_candidate_with_apollo(
                db=self.db,
                job_id=self.job.id,
                candidate_id="candidate-1",
                workflow_token="workflow-1",
                selection_session_id=session.get_by_job(self.job.id).id,
                automation_job_id="automation-2",
            )

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(first["status"], "enriched")
        self.assertTrue(first["shouldOutreach"])
        self.assertEqual(second["status"], "enriched")
        self.assertTrue(second["duplicate"])

        profile = self.profile_repo.get(job_id=self.job.id, candidate_id="candidate-1")
        enrichment = dict(profile.raw_data or {}).get("enrichment") or {}
        self.assertEqual(enrichment.get("status"), "enriched")
        self.assertEqual(enrichment.get("apolloPersonId"), "apollo-person-1")
        self.assertEqual(profile.ats_metadata.get("enrichmentStatus"), "enriched")
        self.assertEqual(profile.ats_metadata.get("apolloPersonId"), "apollo-person-1")

    def test_ambiguous_match_is_persisted_without_outreach(self) -> None:
        self.profile_repo.upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            name="Sam Patel",
            role="Data Engineer",
            company="Northwind",
            summary="Data engineer.",
            skills=["Python", "SQL"],
            raw_data={
                "name": "Sam Patel",
                "full_name": "Sam Patel",
                "email": "sam.patel@example.com",
                "phone": "+1 212 555 0199",
                "current_company": "Northwind",
                "current_title": "Data Engineer",
                "location": "New York",
            },
            fit_score=4.1,
            decision="strong_match",
            strategy="HIGH",
        )
        session = self._select_candidate("candidate-1")
        apollo_response = {
            "people": [
                {
                    "id": "apollo-person-1",
                    "name": "Sam Patel",
                    "organization_name": "Northwind",
                    "title": "Data Engineer",
                    "email": "sam.patel@example.com",
                    "phone": "+1 212 555 0199",
                    "location": "New York",
                },
                {
                    "id": "apollo-person-2",
                    "name": "Sam Patel",
                    "organization_name": "Northwind",
                    "title": "Data Engineer",
                    "email": "sam.alt@example.com",
                    "phone": "+1 212 555 0198",
                    "location": "New York",
                },
            ]
        }

        def _post(*args, **kwargs):
            return types.SimpleNamespace(status_code=200, json=lambda: apollo_response, raise_for_status=lambda: None)

        with patch.object(apollo_module, "APOLLO_API_KEY", "apollo-test-key"), patch.object(apollo_module.requests, "post", side_effect=_post):
            result = apollo_module.enrich_candidate_with_apollo(
                db=self.db,
                job_id=self.job.id,
                candidate_id="candidate-1",
                workflow_token="workflow-2",
                selection_session_id=session.get_by_job(self.job.id).id,
                automation_job_id="automation-ambiguous",
            )

        self.assertEqual(result["status"], "ambiguous_match")
        self.assertFalse(result["shouldOutreach"])
        self.assertIsNone(self.db.execute(
            text("SELECT id FROM outreach_events WHERE job_id = :job_id AND candidate_id = :candidate_id"),
            {"job_id": self.job.id, "candidate_id": "candidate-1"},
        ).fetchone())

        profile = self.profile_repo.get(job_id=self.job.id, candidate_id="candidate-1")
        enrichment = dict(profile.raw_data or {}).get("enrichment") or {}
        self.assertEqual(enrichment.get("status"), "ambiguous_match")
        self.assertEqual(enrichment.get("apolloPersonId"), "apollo-person-1")

    def test_automation_seeder_skips_candidate_enrichment_jobs(self) -> None:
        interview_repo = InterviewRepository(self.db)
        interview_repo.upsert_status(
            job_id=self.job.id,
            candidate_id="candidate-1",
            status="shortlisted",
            create_default="shortlisted",
        )
        profile = self.profile_repo.upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            name="Jane Doe",
            role="Backend Engineer",
            company="Selective Co",
            summary="Backend engineer.",
            skills=["Python", "FastAPI"],
            raw_data={
                "name": "Jane Doe",
                "email": "jane.doe@example.com",
                "phone": "+1 415 555 0100",
            },
            fit_score=4.8,
            decision="strong_match",
            strategy="HIGH",
        )
        profile.ats_status = "shortlisted"
        self.db.commit()

        seed_result = automation_service.seed_automation_jobs(db=self.db, job_id=self.job.id, limit=10)
        self.assertIsInstance(seed_result, dict)
        count = self.db.execute(
            text("SELECT COUNT(*) FROM automation_jobs WHERE automation_type = 'candidate_enrichment' AND job_id = :job_id"),
            {"job_id": self.job.id},
        ).scalar_one()
        self.assertEqual(int(count or 0), 0)
