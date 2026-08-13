from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_slack_multi_company.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("SLACK_CLIENT_ID", "test-slack-client-id")
os.environ.setdefault("SLACK_CLIENT_SECRET", "test-slack-client-secret")
os.environ.setdefault("SLACK_REDIRECT_URI", "http://localhost:3000/slack/oauth/callback")
os.environ.setdefault("SLACK_OAUTH_SCOPES", "commands,chat:write")
os.environ.setdefault("SLACK_STATE_SECRET", "test-slack-state-secret")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-bot-token")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-slack-signing-secret")

if "redis" not in sys.modules:
    redis_module = types.ModuleType("redis")

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

    class _RedisError(Exception):
        pass

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

        def chat_update(self, *args, **kwargs):
            return {"ok": True}

        def conversations_open(self, *args, **kwargs):
            return {"ok": True, "channel": {"id": "D123"}}

        def users_info(self, *args, **kwargs):
            return {"ok": True, "user": {"profile": {"email": "", "display_name": ""}, "name": "test"}}

    slack_sdk.WebClient = _FakeWebClient
    slack_errors = types.ModuleType("slack_sdk.errors")

    class _SlackApiError(Exception):
        pass

    slack_errors.SlackApiError = _SlackApiError
    sys.modules["slack_sdk"] = slack_sdk
    sys.modules["slack_sdk.errors"] = slack_errors

if "qdrant_client" not in sys.modules:
    qdrant_client = types.ModuleType("qdrant_client")

    class _FakeQdrantClient:
        def __init__(self, *args, **kwargs):
            pass

    qdrant_client.QdrantClient = _FakeQdrantClient
    qdrant_models = types.ModuleType("qdrant_client.models")

    class _Distance:
        COSINE = "cosine"

    class _PayloadSchemaType:
        KEYWORD = "keyword"
        UUID = "uuid"

    class _FieldCondition:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class _Filter:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class _MatchValue:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class _PointStruct:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class _VectorParams:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    qdrant_models.Distance = _Distance
    qdrant_models.PayloadSchemaType = _PayloadSchemaType
    qdrant_models.FieldCondition = _FieldCondition
    qdrant_models.Filter = _Filter
    qdrant_models.MatchValue = _MatchValue
    qdrant_models.PointStruct = _PointStruct
    qdrant_models.VectorParams = _VectorParams
    sys.modules["qdrant_client"] = qdrant_client
    sys.modules["qdrant_client.models"] = qdrant_models

if "openai" not in sys.modules:
    openai_module = types.ModuleType("openai")

    class _FakeOpenAI:
        def __init__(self, *args, **kwargs):
            pass

    openai_module.OpenAI = _FakeOpenAI
    sys.modules["openai"] = openai_module

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import SLACK_SIGNING_SECRET
from app.db.repositories import (
    CandidateFeedbackRepository,
    CandidateProfileRepository,
    CompanyRepository,
    JobRepository,
    OrchestrationSessionRepository,
    SlackInstallationRepository,
    SlackUserRepository,
    UserRepository,
)
from app.db.session import SessionLocal, engine
from app.models.entities import Base, AuditEventEntity, CandidateLifecycleEventEntity
from app.services.candidate_service import apply_feedback
from app.services.orchestration_service import _finalize_sourcing, start_or_resume_slack_intake
from app.services.slack_tenant_service import SlackCompanyResolver, build_slack_oauth_state

import app.main as main_module


class SlackMultiCompanyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        engine.dispose()
        db_path = getattr(engine.url, "database", None)
        if db_path:
            Path(db_path).unlink(missing_ok=True)
        Base.metadata.create_all(bind=engine)
        cls._patchers = [
            patch.object(main_module, "init_db", lambda: None),
            patch.object(main_module, "ensure_qdrant_indexes", lambda: None),
            patch.object(main_module, "ensure_embedding_version_registry", lambda: None),
            patch.object(main_module, "warm_candidate_retrieval", lambda: 0),
            patch.object(main_module, "start_job_queue_workers", lambda: None),
            patch.object(main_module, "start_scheduler", lambda: None),
        ]
        for patcher in cls._patchers:
            patcher.start()

    @classmethod
    def tearDownClass(cls) -> None:
        for patcher in reversed(cls._patchers):
            patcher.stop()

    def setUp(self) -> None:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.client = TestClient(main_module.app)
        self.db = SessionLocal()
        self.owner_a = UserRepository(self.db).create("owner-a@example.com", role="admin")
        self.owner_b = UserRepository(self.db).create("owner-b@example.com", role="admin")
        self.company_a = CompanyRepository(self.db).create(
            user_id=self.owner_a.id,
            name="Vaibhav Tech",
            website="https://vaibhav.tech",
            description="Customer A",
        )
        self.company_b = CompanyRepository(self.db).create(
            user_id=self.owner_b.id,
            name="Akshay Tech",
            website="https://akshay.tech",
            description="Customer B",
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _sign_slack_body(self, body: bytes, timestamp: str = "1710000000") -> dict[str, str]:
        base = f"v0:{timestamp}:{body.decode('utf-8')}"
        signature = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    @patch("app.api.routes.slack.fetch_slack_user_profile")
    @patch("app.api.routes.slack.exchange_slack_oauth_code")
    def test_oauth_install_maps_each_workspace_to_the_correct_company(self, mock_exchange, mock_profile) -> None:
        mock_exchange.return_value = {
            "ok": True,
            "access_token": "xoxb-team-111",
            "scope": "commands,chat:write",
            "team": {"id": "T111", "name": "Vaibhav Workspace"},
            "enterprise": {"id": "E111"},
            "bot": {"bot_user_id": "B111"},
            "authed_user": {"id": "U111"},
        }
        mock_profile.return_value = {"email": "john@vaibhav.tech", "display_name": "John"}
        state_a = build_slack_oauth_state(company_id=self.company_a.id)
        response_a = self.client.get("/slack/oauth/callback", params={"code": "code-a", "state": state_a}, follow_redirects=False)
        self.assertEqual(response_a.status_code, 302)

        mock_exchange.return_value = {
            "ok": True,
            "access_token": "xoxb-team-222",
            "scope": "commands,chat:write",
            "team": {"id": "T222", "name": "Akshay Workspace"},
            "enterprise": {"id": "E222"},
            "bot": {"bot_user_id": "B222"},
            "authed_user": {"id": "U222"},
        }
        mock_profile.return_value = {"email": "jane@akshay.tech", "display_name": "Jane"}
        state_b = build_slack_oauth_state(company_id=self.company_b.id)
        response_b = self.client.get("/slack/oauth/callback", params={"code": "code-b", "state": state_b}, follow_redirects=False)
        self.assertEqual(response_b.status_code, 302)

        resolver = SlackCompanyResolver(self.db)
        ctx_a = resolver.resolve_workspace_context(team_id="T111", slack_user_id="U111")
        ctx_b = resolver.resolve_workspace_context(team_id="T222", slack_user_id="U222")
        self.assertEqual(ctx_a.company.id, self.company_a.id)
        self.assertEqual(ctx_b.company.id, self.company_b.id)
        self.assertNotEqual(ctx_a.company.id, ctx_b.company.id)

        install_a = SlackInstallationRepository(self.db).get_active_by_team_id("T111")
        install_b = SlackInstallationRepository(self.db).get_active_by_team_id("T222")
        self.assertIsNotNone(install_a)
        self.assertIsNotNone(install_b)
        self.assertEqual(install_a.company_id, self.company_a.id)
        self.assertEqual(install_b.company_id, self.company_b.id)

    def test_slack_commands_requires_a_valid_signature(self) -> None:
        response = self.client.post(
            "/slack/commands",
            data={"text": "hire", "user_id": "U111", "channel_id": "C111", "team_id": "T111"},
        )
        self.assertEqual(response.status_code, 401)

    @patch(
        "app.services.orchestration_service.bootstrap_preference_calibration_session",
        return_value={"stage": "archetype_calibration", "current_round_index": 1, "profile_sets": [], "archetype_sets": []},
    )
    @patch("app.services.hiring_service.get_embedding", return_value=[0.0] * 384)
    def test_job_creation_uses_the_real_slack_user_and_logs_audit_event(self, _mock_get_embedding, _mock_bootstrap) -> None:
        installation = SlackInstallationRepository(self.db).upsert(
            company_id=self.company_a.id,
            agency_id=self.company_a.id,
            team_id="T111",
            team_name="Vaibhav Workspace",
            enterprise_id="E111",
            bot_user_id="B111",
            bot_access_token="xoxb-team-111",
            scope_list=["commands", "chat:write"],
        )
        slack_user = SlackUserRepository(self.db).upsert(
            company_id=self.company_a.id,
            slack_installation_id=installation.id,
            slack_user_id="U111",
            email="john@vaibhav.tech",
            display_name="John",
        )
        session = OrchestrationSessionRepository(self.db).create(
            session_token="session-111",
            source="slack",
            slack_team_id="T111",
            slack_channel_id="C111",
            slack_user_id="U111",
            company_id=self.company_a.id,
            selected_path="slack",
            structured_context={"question_plan": []},
            raw_conversation=[],
            normalized_intake={
                "company_name": "Vaibhav Tech",
                "role_title": "Backend Engineer",
                "must_have_requirements": "Python",
                "success_profile": "Strong builder",
                "compensation": "$200k",
                "urgency": "High",
                "team_structure": "Small team",
            },
            slack_context={"teamId": "T111", "channelId": "C111", "userId": "U111"},
        )

        result = _finalize_sourcing(self.db, session)
        job = JobRepository(self.db).get(result["jobId"])
        self.assertIsNotNone(job)
        self.assertEqual(job.company_id, self.company_a.id)
        self.assertEqual(job.created_by, slack_user.internal_user_id)
        self.assertEqual(job.slack_team_id, "T111")
        self.assertEqual(job.slack_user_id, "U111")

        audit = self.db.scalar(
            select(AuditEventEntity).where(
                AuditEventEntity.entity_type == "job",
                AuditEventEntity.entity_id == job.id,
            )
        )
        self.assertIsNotNone(audit)
        self.assertEqual(audit.company_id, self.company_a.id)
        self.assertEqual(audit.slack_user_id, "U111")
        self.assertEqual(audit.action_type, "job_create")

    @patch("app.services.candidate_service.update_recruiter_preferences", return_value=None)
    def test_candidate_feedback_records_slack_actor_metadata(self, _mock_update_recruiter_preferences) -> None:
        installation = SlackInstallationRepository(self.db).upsert(
            company_id=self.company_a.id,
            team_id="T111",
            team_name="Vaibhav Workspace",
            enterprise_id="E111",
            bot_user_id="B111",
            bot_access_token="xoxb-team-111",
            scope_list=["commands", "chat:write"],
        )
        slack_user = SlackUserRepository(self.db).upsert(
            company_id=self.company_a.id,
            slack_installation_id=installation.id,
            slack_user_id="U111",
            email="john@vaibhav.tech",
            display_name="John",
        )
        job = JobRepository(self.db).create(
            company_id=self.company_a.id,
            created_by=slack_user.internal_user_id or self.owner_a.id,
            source_app="slack",
            title="Backend Engineer",
            description="Build APIs",
            location="Remote",
            compensation="$180k",
            work_authorization="required",
            remote_policy="remote",
            experience_required="5+ years",
            skills_required=["Python"],
            responsibilities=["Ship features"],
            structured_data={},
        )
        CandidateProfileRepository(self.db).upsert(
            job_id=job.id,
            candidate_id="candidate-1",
            name="Candidate One",
            role="Backend Engineer",
            company="Northstar",
            raw_data={"name": "Candidate One", "email": "candidate@example.com"},
            summary="Strong backend engineer",
            skills=["Python"],
            fit_score=4.5,
            decision="potential",
            strategy="HIGH",
        )

        result = apply_feedback(
            db=self.db,
            job_id=job.id,
            candidate_id="candidate-1",
            action="accept",
            actor_id=slack_user.internal_user_id,
            company_id=self.company_a.id,
            slack_team_id="T111",
            slack_user_id="U111",
            slack_installation_id=installation.id,
        )
        self.db.commit()

        feedback = CandidateFeedbackRepository(self.db).get(job_id=job.id, candidate_id="candidate-1")
        self.assertIsNotNone(feedback)
        self.assertEqual(feedback.company_id, self.company_a.id)
        self.assertEqual(feedback.recruiter_id, slack_user.internal_user_id)
        self.assertEqual(feedback.slack_team_id, "T111")
        self.assertEqual(feedback.slack_user_id, "U111")
        self.assertEqual(result["action"], "accept")

        lifecycle = self.db.scalar(
            select(CandidateLifecycleEventEntity).where(
                CandidateLifecycleEventEntity.job_id == job.id,
                CandidateLifecycleEventEntity.candidate_id == "candidate-1",
            )
        )
        self.assertIsNotNone(lifecycle)
        self.assertEqual(lifecycle.company_id, self.company_a.id)
        self.assertEqual(lifecycle.slack_team_id, "T111")
        self.assertEqual(lifecycle.slack_user_id, "U111")
        self.assertEqual(lifecycle.slack_installation_id, installation.id)

    @patch("app.api.routes.slack.complete_voice_handoff")
    @patch("app.api.routes.slack.post_slack_message", new_callable=AsyncMock)
    def test_voice_completion_uses_workspace_token_and_logs_audit(self, mock_post_message, mock_complete_voice_handoff) -> None:
        installation = SlackInstallationRepository(self.db).upsert(
            company_id=self.company_a.id,
            team_id="T111",
            team_name="Vaibhav Workspace",
            enterprise_id="E111",
            bot_user_id="B111",
            bot_access_token="xoxb-team-111",
            scope_list=["commands", "chat:write"],
        )
        SlackUserRepository(self.db).upsert(
            company_id=self.company_a.id,
            slack_installation_id=installation.id,
            slack_user_id="U111",
            email="john@vaibhav.tech",
            display_name="John",
        )
        self.db.commit()
        mock_complete_voice_handoff.return_value = {
            "completed": True,
            "session": {
                "id": "session-voice-1",
                "slackTeamId": "T111",
                "slackUserId": "U111",
                "slackChannelId": "C111",
                "companyId": self.company_a.id,
            },
            "calibration": {
                "current_pair": {"id": "pair-1", "archetypes": []},
                "current_round_index": 1,
                "profile_sets": [],
                "archetype_sets": [],
            },
            "finalization": {"jobId": "job-voice-1", "companyId": self.company_a.id},
        }

        response = self.client.post("/slack/orchestration/voice/complete/token-voice-1", json={"transcript": "hello"})
        self.assertEqual(response.status_code, 200)

        self.assertTrue(mock_post_message.await_count >= 1)
        posted_kwargs = mock_post_message.await_args.kwargs
        self.assertEqual(posted_kwargs["bot_token"], "xoxb-team-111")
        self.assertEqual(posted_kwargs["channel_id"], "C111")

        audit = self.db.scalar(
            select(AuditEventEntity).where(AuditEventEntity.action_type == "voice_intake_completion")
        )
        self.assertIsNotNone(audit)
        self.assertEqual(audit.company_id, self.company_a.id)
        self.assertEqual(audit.slack_user_id, "U111")


if __name__ == "__main__":
    unittest.main()
