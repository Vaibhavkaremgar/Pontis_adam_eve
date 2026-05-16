from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import types
import time
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integration.db")
os.environ.setdefault("JWT_SECRET", "integration-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "integration-internal-key")
os.environ.setdefault("WEBHOOK_SHARED_SECRET", "webhook-secret")
os.environ.setdefault("RESEND_WEBHOOK_SECRET", f"whsec_{base64.b64encode(b'integration-resend-secret').decode('ascii')}")

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
from sqlalchemy import text

from app.core.config import AUTH_COOKIE_NAME, CSRF_COOKIE_NAME, WEBHOOK_SHARED_SECRET
from app.core.security import create_access_token, create_csrf_token
from app.db.repositories import CandidateProfileRepository, CandidateSelectionSessionRepository, CompanyRepository, InterviewRepository, JobRepository, NotificationWorkflowTokenRepository, OutreachEventRepository, UserRepository
from app.db.session import SessionLocal, engine
from app.models.entities import Base, ScoringProfileEntity
from app.services.resend_inbound_service import process_resend_inbound_webhook
from app.services.webhook_security import verify_resend_webhook
from app.services.webhook_security import WEBHOOK_SIGNATURE_HEADER, WEBHOOK_TIMESTAMP_HEADER, verify_shared_secret_webhook

import app.main as main_module


class IntegrationTests(unittest.TestCase):
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
            patch.object(main_module, "run_startup_connectivity_check", lambda: None),
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
        suffix = self._testMethodName.replace("test_", "")
        self.user = UserRepository(self.db).create(f"recruiter-{suffix}@example.com", role="admin")
        self.company = CompanyRepository(self.db).create(
            user_id=self.user.id,
            name=f"Acme-{suffix}",
            website="https://acme.test",
            description="Test company",
        )
        self.job = JobRepository(self.db).create(
            company_id=self.company.id,
            created_by=self.user.id,
            title=f"Platform Engineer {suffix}",
            description="Build retrieval, queues, and AI observability.",
            location="Remote",
            compensation="$180k",
            work_authorization="required",
            remote_policy="remote",
            experience_required="5+ years",
            skills_required=["Python", "Redis", "Qdrant"],
            responsibilities=["Operate queues", "Improve retrieval"],
        )
        CandidateProfileRepository(self.db).upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            name="Avery",
            role="Platform Engineer",
            company="Northstar",
            raw_data={
                "name": "Avery",
                "role": "Platform Engineer",
                "company": "Northstar",
                "summary": "Built queue-backed retrieval systems with Python, Redis, and Qdrant.",
                "skills": ["Python", "Redis", "Qdrant"],
                "work_email": "candidate@example.com",
                "email": "candidate@example.com",
                "personal_email": "candidate@example.com",
            },
            summary="Built queue-backed retrieval systems with Python, Redis, and Qdrant.",
            skills=["Python", "Redis", "Qdrant"],
            fit_score=4.8,
            decision="strong_match",
            strategy="HIGH",
        )
        OutreachEventRepository(self.db).upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            provider="resend",
            to_email="candidate@example.com",
            subject="Opportunity: Platform Engineer",
            body="<p>Initial outreach</p>",
            status="sent",
            sent_at=None,
            next_follow_up_at=None,
            provider_message_id="outreach-message-id-123",
        )
        self.db.commit()
        self.token = create_access_token(user_id=self.user.id, email=self.user.email, role=self.user.role)
        self.csrf = create_csrf_token(user_id=self.user.id)
        self.client.cookies.set(AUTH_COOKIE_NAME, self.token)
        self.client.cookies.set(CSRF_COOKIE_NAME, self.csrf)

    def tearDown(self) -> None:
        self.db.close()
        self.client.close()

    def _post_resend_inbound_reply(
        self,
        *,
        webhook_id: str,
        email_id: str,
        reply_email: dict,
        attachments_list: dict,
        attachment_bytes: dict[str, bytes] | None = None,
        created_at: str = "2026-05-11T00:00:00Z",
    ):
        from app.services import resend_inbound_service as inbound_service

        event_payload = {
            "type": "email.received",
            "created_at": created_at,
            "data": {"email_id": email_id},
        }
        body = json.dumps(event_payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        secret = base64.b64decode(os.environ["RESEND_WEBHOOK_SECRET"].removeprefix("whsec_"))
        signed = f"{webhook_id}.{timestamp}.".encode("utf-8") + body
        signature = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode("ascii")
        headers = {
            "svix-id": webhook_id,
            "svix-timestamp": timestamp,
            "svix-signature": f"v1,{signature}",
        }

        attachment_bytes = attachment_bytes or {}

        class _FakeResponse:
            def __init__(self, status_code: int, payload=None, content: bytes | None = None):
                self.status_code = status_code
                self._payload = payload
                self.content = content or b""
                self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else ""

            def json(self):
                return self._payload

        def _fake_get(url, headers=None, timeout=None):
            if url.endswith(f"/emails/receiving/{email_id}"):
                return _FakeResponse(200, reply_email)
            if url.endswith(f"/emails/receiving/{email_id}/attachments"):
                return _FakeResponse(200, attachments_list)
            if url in attachment_bytes:
                return _FakeResponse(200, content=attachment_bytes[url])
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(inbound_service.requests, "get", side_effect=_fake_get):
            return self.client.post("/api/webhooks/resend", content=body, headers=headers)

    def test_auth_cookie_and_csrf_lifecycle(self) -> None:
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["data"]["user"]["email"], self.user.email)

        blocked = self.client.post("/api/candidates/swipe", json={"jobId": self.job.id, "candidateId": "candidate-1", "action": "accept"})
        self.assertEqual(blocked.status_code, 403)

        allowed = self.client.post(
            "/api/candidates/swipe",
            headers={"X-CSRF-Token": self.csrf},
            json={"jobId": self.job.id, "candidateId": "candidate-1", "action": "accept"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["success"], True)

        logout = self.client.post("/api/auth/logout", headers={"X-CSRF-Token": self.csrf})
        self.assertEqual(logout.status_code, 200)
        self.assertIn(AUTH_COOKIE_NAME, logout.headers.get("set-cookie", ""))

    def test_ranked_candidates_route_uses_hybrid_attribution(self) -> None:
        fake_hit = [
            {
                "candidateId": "candidate-1",
                "score": 0.88,
                "payload": {
                    "candidateId": "candidate-1",
                    "name": "Avery",
                    "role": "Platform Engineer",
                    "company": "Northstar",
                    "summary": "Built queue-backed retrieval systems with Python, Redis, and Qdrant.",
                    "skills": ["Python", "Redis", "Qdrant"],
                },
            }
        ]
        with patch("app.services.candidate_service.ensure_all_collections", lambda: None), \
            patch("app.services.candidate_service.is_pdl_disabled", lambda: True), \
            patch("app.services.candidate_service.search_candidate_chunks", lambda **_: fake_hit), \
            patch("app.services.candidate_service.embed", lambda text: [0.1] * 384):
            resp = self.client.get(f"/api/candidates?jobId={self.job.id}")

        self.assertEqual(resp.status_code, 200)
        items = resp.json()["data"]
        self.assertGreaterEqual(len(items), 1)
        self.assertIn("retrievalAttribution", items[0]["explanation"])

    def test_webhook_verification_and_replay_helpers(self) -> None:
        timestamp = str(int(time.time()))
        body = b'{"message":"hello"}'
        signature = hmac.new(WEBHOOK_SHARED_SECRET.encode("utf-8"), f"{timestamp}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_shared_secret_webhook(raw_body=body, signature=signature, timestamp=timestamp))

    def test_resend_svix_webhook_verification(self) -> None:
        webhook_id = "msg_test_123"
        timestamp = str(int(time.time()))
        body = b'{"type":"email.received","data":{"email_id":"email-123"}}'
        secret = base64.b64decode(os.environ["RESEND_WEBHOOK_SECRET"].removeprefix("whsec_"))
        signed = f"{webhook_id}.{timestamp}.".encode("utf-8") + body
        signature = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode("ascii")
        result = verify_resend_webhook(
            raw_body=body,
            webhook_id=webhook_id,
            timestamp=timestamp,
            signature=f"v1,{signature}",
        )
        self.assertTrue(result.is_valid)

    def test_resend_inbound_reply_processing_and_deduplication(self) -> None:
        from app.services import resend_inbound_service as inbound_service

        webhook_id = "msg_reply_123"
        event_payload = {
            "type": "email.received",
            "created_at": "2026-05-11T00:00:00Z",
            "data": {"email_id": "email-reply-123"},
        }
        body = json.dumps(event_payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        secret = base64.b64decode(os.environ["RESEND_WEBHOOK_SECRET"].removeprefix("whsec_"))
        signed = f"{webhook_id}.{timestamp}.".encode("utf-8") + body
        signature = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode("ascii")
        headers = {
            "svix-id": webhook_id,
            "svix-timestamp": timestamp,
            "svix-signature": f"v1,{signature}",
        }

        reply_email = {
            "object": "email",
            "id": "email-reply-123",
            "to": ["inbound@pontis.one"],
            "from": "Avery Candidate <candidate@example.com>",
            "created_at": "2026-05-11T00:00:00+00:00",
            "subject": "Re: Opportunity at Acme",
            "html": "<p>Please see attached resume.</p>",
            "text": "Please see attached resume.",
            "headers": {
                "message-id": "<reply-message-id>",
                "in-reply-to": "<outreach-message-id>",
            },
        }
        attachments_list = {
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "att-1",
                    "filename": "resume.pdf",
                    "size": 64,
                    "content_type": "application/pdf",
                    "content_disposition": "attachment",
                    "download_url": "https://inbound-cdn.resend.com/email-reply-123/attachments/att-1",
                    "expires_at": "2026-05-11T01:00:00Z",
                }
            ],
        }

        class _FakeResponse:
            def __init__(self, status_code: int, payload=None, content: bytes | None = None):
                self.status_code = status_code
                self._payload = payload
                self.content = content or b""
                self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else ""

            def json(self):
                return self._payload

        def _fake_get(url, headers=None, timeout=None):
            if url.endswith("/emails/receiving/email-reply-123"):
                return _FakeResponse(200, reply_email)
            if url.endswith("/emails/receiving/email-reply-123/attachments"):
                return _FakeResponse(200, attachments_list)
            if url == "https://inbound-cdn.resend.com/email-reply-123/attachments/att-1":
                return _FakeResponse(200, content=b"%PDF-1.4 fake resume")
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(inbound_service.requests, "get", side_effect=_fake_get):
            response = self.client.post("/api/webhooks/resend", content=body, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["status"], "processed")
            self.assertEqual(response.json()["data"]["matchStatus"], "matched")
            self.assertEqual(response.json()["data"]["attachmentsStored"], 1)

            duplicate = self.client.post("/api/webhooks/resend", content=body, headers=headers)
            self.assertEqual(duplicate.status_code, 200)
            self.assertEqual(duplicate.json()["data"]["status"], "duplicate")

        inbound = self.db.execute(
            text("SELECT svix_id, email_id, sender_email, match_status, processing_status FROM inbound_email_replies WHERE svix_id = :svix_id"),
            {"svix_id": webhook_id},
        ).fetchone()
        self.assertIsNotNone(inbound)
        self.assertEqual(inbound[2], "candidate@example.com")
        self.assertEqual(inbound[3], "matched")
        self.assertEqual(inbound[4], "completed")

        attachment = self.db.execute(
            text("SELECT filename, content_type FROM inbound_email_attachments WHERE reply_id = (SELECT id FROM inbound_email_replies WHERE svix_id = :svix_id)"),
            {"svix_id": webhook_id},
        ).fetchone()
        self.assertIsNotNone(attachment)
        self.assertTrue(str(attachment[0]).endswith("resume.pdf"))

    def test_resend_inbound_creates_unmatched_reply_record(self) -> None:
        from app.services import resend_inbound_service as inbound_service

        webhook_id = "msg_unmatched_123"
        event_payload = {
            "type": "email.received",
            "created_at": "2026-05-11T00:10:00Z",
            "data": {"email_id": "email-unmatched-123"},
        }
        body = json.dumps(event_payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        secret = base64.b64decode(os.environ["RESEND_WEBHOOK_SECRET"].removeprefix("whsec_"))
        signed = f"{webhook_id}.{timestamp}.".encode("utf-8") + body
        signature = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode("ascii")
        headers = {
            "svix-id": webhook_id,
            "svix-timestamp": timestamp,
            "svix-signature": f"v1,{signature}",
        }

        reply_email = {
            "object": "email",
            "id": "email-unmatched-123",
            "to": ["inbound@pontis.one"],
            "from": "Unknown Sender <unknown@example.org>",
            "created_at": "2026-05-11T00:10:00+00:00",
            "subject": "Resume attached",
            "html": "<p>Hi</p>",
            "text": "Hi",
            "headers": {"message-id": "<reply-missing-match>"},
        }
        attachments_list = {"object": "list", "has_more": False, "data": []}

        class _FakeResponse:
            def __init__(self, status_code: int, payload=None):
                self.status_code = status_code
                self._payload = payload
                self.content = b""
                self.text = json.dumps(payload) if isinstance(payload, (dict, list)) else ""

            def json(self):
                return self._payload

        def _fake_get(url, headers=None, timeout=None):
            if url.endswith("/emails/receiving/email-unmatched-123"):
                return _FakeResponse(200, reply_email)
            if url.endswith("/emails/receiving/email-unmatched-123/attachments"):
                return _FakeResponse(200, attachments_list)
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(inbound_service.requests, "get", side_effect=_fake_get):
            response = self.client.post("/api/webhooks/resend", content=body, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["matchStatus"], "unmatched")

        inbound = self.db.execute(
            text("SELECT match_status, candidate_id FROM inbound_email_replies WHERE svix_id = :svix_id"),
            {"svix_id": webhook_id},
        ).fetchone()
        self.assertIsNotNone(inbound)
        self.assertEqual(inbound[0], "unmatched")
        self.assertIsNone(inbound[1])

    def test_resend_inbound_interested_with_resume_updates_profile_and_qualifies(self) -> None:
        from app.services import resend_inbound_service as inbound_service
        from app.services.resume_ingestion_service import ResumeStructuredProfile

        webhook_id = "msg_interested_resume_123"
        reply_email = {
            "object": "email",
            "id": "email-interested-resume-123",
            "to": ["inbound@pontis.one"],
            "from": "Avery Candidate <candidate@example.com>",
            "created_at": "2026-05-11T00:00:00+00:00",
            "subject": "Re: Opportunity at Acme",
            "html": "<p>Yes, I am interested.</p>",
            "text": "Yes, I am interested.",
            "headers": {
                "message-id": "<reply-message-id-123>",
                "in-reply-to": "<outreach-message-id-123>",
            },
        }
        attachments_list = {
            "object": "list",
            "has_more": False,
            "data": [
                {
                    "id": "att-resume-1",
                    "filename": "resume.pdf",
                    "size": 64,
                    "content_type": "application/pdf",
                    "content_disposition": "attachment",
                    "download_url": "https://inbound-cdn.resend.com/email-interested-resume-123/attachments/att-resume-1",
                    "expires_at": "2026-05-11T01:00:00Z",
                }
            ],
        }
        parsed_profile = ResumeStructuredProfile(
            full_name="Avery Candidate",
            headline="Senior Platform Engineer",
            years_experience=8,
            skills=["Python", "Redis", "Qdrant"],
            companies=["Northstar"],
            education=["B.Tech"],
            projects=["Search platform"],
            certifications=["AWS"],
            location="Remote",
            summary="Built queue-backed recruiting systems.",
            domain_experience=["Recruiting"],
        )

        with patch("app.services.resume_ingestion_service.extract_pdf_text", return_value=("sample resume text", {})), patch.object(
            inbound_service, "parse_resume_profile", return_value=parsed_profile
        ), patch.object(inbound_service, "send_interview_invite", return_value={"status": "queued"}) as invite_mock:
            response = self._post_resend_inbound_reply(
                webhook_id=webhook_id,
                email_id="email-interested-resume-123",
                reply_email=reply_email,
                attachments_list=attachments_list,
                attachment_bytes={
                    "https://inbound-cdn.resend.com/email-interested-resume-123/attachments/att-resume-1": b"%PDF-1.4 fake resume",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "processed")
        invite_mock.assert_called_once()
        invite_args = invite_mock.call_args.kwargs
        self.assertEqual(invite_args["candidate_id"], "candidate-1")
        self.assertEqual(invite_args["job_id"], self.job.id)
        outreach_row = self.db.execute(
            text("SELECT id FROM outreach_events WHERE provider_message_id = :provider_message_id"),
            {"provider_message_id": "outreach-message-id-123"},
        ).fetchone()
        self.assertIsNotNone(outreach_row)
        self.assertEqual(invite_args["outreach_event_id"], outreach_row.id)

        profile = CandidateProfileRepository(self.db).get(job_id=self.job.id, candidate_id="candidate-1")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.candidate_status, "qualified")
        self.assertEqual(profile.current_title, "Senior Platform Engineer")
        self.assertEqual(profile.current_company, "Northstar")
        self.assertEqual(profile.total_experience_years, 8.0)
        self.assertIsNotNone(profile.resume_received_at)
        self.assertEqual(profile.parsed_resume_json["full_name"], "Avery Candidate")

        inbound = self.db.execute(
            text("SELECT processing_status, intent FROM inbound_email_replies WHERE svix_id = :svix_id"),
            {"svix_id": webhook_id},
        ).fetchone()
        self.assertEqual(inbound[0], "completed")
        self.assertEqual(inbound[1], "interested")

        outreach = self.db.execute(
            text("SELECT status, reply_intent FROM outreach_events WHERE job_id = :job_id AND candidate_id = :candidate_id"),
            {"job_id": self.job.id, "candidate_id": "candidate-1"},
        ).fetchone()
        self.assertEqual(outreach[0], "replied")
        self.assertEqual(outreach[1], "interested")

    def test_resend_inbound_interested_without_resume_sends_followup(self) -> None:
        from app.services import resend_inbound_service as inbound_service

        webhook_id = "msg_interested_nor_resume_123"
        reply_email = {
            "object": "email",
            "id": "email-interested-no-resume-123",
            "to": ["inbound@pontis.one"],
            "from": "Avery Candidate <candidate@example.com>",
            "created_at": "2026-05-11T00:00:00+00:00",
            "subject": "Re: Opportunity at Acme",
            "html": "<p>Yes, I am interested.</p>",
            "text": "Yes, I am interested.",
            "headers": {
                "message-id": "<reply-message-id-124>",
                "in-reply-to": "<outreach-message-id-124>",
            },
        }
        attachments_list = {"object": "list", "has_more": False, "data": []}

        with patch.object(inbound_service, "send_email") as send_email_mock:
            response = self._post_resend_inbound_reply(
                webhook_id=webhook_id,
                email_id="email-interested-no-resume-123",
                reply_email=reply_email,
                attachments_list=attachments_list,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "processed")
        send_email_mock.assert_called_once()

        profile = CandidateProfileRepository(self.db).get(job_id=self.job.id, candidate_id="candidate-1")
        self.assertEqual(profile.candidate_status, "awaiting_resume")

        outreach = self.db.execute(
            text("SELECT status, reply_intent, follow_up_count FROM outreach_events WHERE job_id = :job_id AND candidate_id = :candidate_id"),
            {"job_id": self.job.id, "candidate_id": "candidate-1"},
        ).fetchone()
        self.assertEqual(outreach[0], "replied")
        self.assertEqual(outreach[1], "interested")
        self.assertEqual(outreach[2], 1)

    def test_job_intake_outreach_and_interview_session_persist_new_schema_fields(self) -> None:
        from app.services import outreach_service as outreach_module
        from app.services import voice_service as voice_module
        from app.services import interview_invite_service as invite_module

        candidate_id = "candidate-schema-fields"
        CandidateProfileRepository(self.db).upsert(
            job_id=self.job.id,
            candidate_id=candidate_id,
            name="Schema Candidate",
            role="Platform Engineer",
            company="Example Candidate Company",
            summary="Candidate profile created for persistence verification.",
            skills=["Python", "Redis"],
            raw_data={
                "full_name": "Schema Candidate",
                "email": "schema-candidate@example.com",
                "work_email": "schema-candidate@example.com",
            },
            fit_score=0.82,
            decision="shortlisted",
            strategy="balanced",
        )

        with patch.object(voice_module, "ensure_all_collections", lambda: None), \
            patch.object(voice_module, "delete_job_vectors", lambda *_args, **_kwargs: None), \
            patch.object(voice_module, "upsert_job_chunks", lambda *_args, **_kwargs: None), \
            patch.object(voice_module, "get_embedding", lambda *_args, **_kwargs: [0.0] * 384):
            result = voice_module.refine_job_with_voice(
                db=self.db,
                job_id=self.job.id,
                voice_notes=["Build a remote platform team", "5+ years experience"],
                transcript="Recruiter: Build a remote platform team with Python and Redis.",
            )

        self.assertTrue(result["refined"])

        job_row = self.db.execute(
            text(
                "SELECT company_id, created_by, remote_policy, experience_required, updated_at "
                "FROM jobs WHERE id = :job_id"
            ),
            {"job_id": self.job.id},
        ).fetchone()
        self.assertIsNotNone(job_row)
        self.assertEqual(job_row[0], self.company.id)
        self.assertEqual(job_row[1], self.user.id)
        self.assertEqual(job_row[2], "remote")
        self.assertEqual(job_row[3], "5+ years")
        self.assertIsNotNone(job_row[4])

        job_intake_row = self.db.execute(
            text(
                "SELECT job_id, company_id, intake_status, completed_at, transcript "
                "FROM job_intakes WHERE job_id = :job_id"
            ),
            {"job_id": self.job.id},
        ).fetchone()
        self.assertIsNotNone(job_intake_row)
        self.assertEqual(job_intake_row[1], self.company.id)
        self.assertEqual(job_intake_row[2], "completed")
        self.assertIsNotNone(job_intake_row[3])
        self.assertIn("remote platform team", job_intake_row[4])

        with patch.object(outreach_module, "_send_shortlist_outreach_email", return_value=(True, "", "msg-123")):
            outreach_module._trigger_candidate_outreach_sync(candidate_id=candidate_id, job_id=self.job.id)

        outreach_row = self.db.execute(
            text(
                "SELECT id, company_id, sent_at, status, provider_message_id "
                "FROM outreach_events WHERE job_id = :job_id AND candidate_id = :candidate_id"
            ),
            {"job_id": self.job.id, "candidate_id": candidate_id},
        ).fetchone()
        self.assertIsNotNone(outreach_row)
        outreach_event_id = str(outreach_row[0])
        self.assertEqual(outreach_row[1], self.company.id)
        self.assertIsNotNone(outreach_row[2])
        self.assertEqual(outreach_row[3], "sent")
        self.assertEqual(outreach_row[4], "msg-123")

        with patch.object(invite_module, "send_email", return_value=None), \
            patch("app.services.interview_session_service.get_interview_link", return_value="https://book.example.com/interview"):
            invite_module.send_interview_invite(
                candidate_id=candidate_id,
                job_id=self.job.id,
                outreach_event_id=outreach_event_id,
            )

        session_row = self.db.execute(
            text(
                "SELECT company_id, outreach_event_id, booking_url, status "
                "FROM interview_sessions WHERE job_id = :job_id AND candidate_id = :candidate_id"
            ),
            {"job_id": self.job.id, "candidate_id": candidate_id},
        ).fetchone()
        self.assertIsNotNone(session_row)
        self.assertEqual(session_row[0], self.company.id)
        self.assertEqual(session_row[1], outreach_event_id)
        self.assertTrue(str(session_row[2] or "").startswith("http"))
        self.assertEqual(session_row[3], "pending")

    def test_process_outreach_uses_completed_selection_session_candidates(self) -> None:
        from app.services import outreach_service as outreach_module

        CandidateProfileRepository(self.db).upsert(
            job_id=self.job.id,
            candidate_id="candidate-2",
            name="Blair",
            role="Platform Engineer",
            company="Northstar",
            raw_data={
                "name": "Blair",
                "role": "Platform Engineer",
                "company": "Northstar",
                "summary": "Built queue-backed retrieval systems with Python, Redis, and Qdrant.",
                "skills": ["Python", "Redis", "Qdrant"],
                "work_email": "candidate2@example.com",
                "email": "candidate2@example.com",
                "personal_email": "candidate2@example.com",
            },
            summary="Built queue-backed retrieval systems with Python, Redis, and Qdrant.",
            skills=["Python", "Redis", "Qdrant"],
            fit_score=4.7,
            decision="strong_match",
            strategy="HIGH",
        )

        session = CandidateSelectionSessionRepository(self.db).create(
            job_id=self.job.id,
            candidate_pool_snapshot=[],
            batch_plan=[],
        )
        session.status = "completed"
        session.selected_candidate_ids = ["candidate-2"]
        session.completed_at = session.updated_at
        self.db.commit()

        with patch.object(outreach_module, "OUTREACH_DRY_RUN", False), \
            patch.object(outreach_module, "ENABLE_REAL_EMAIL_SENDING", True), \
            patch.object(outreach_module, "_send_outreach_email", return_value=(True, "", "msg-789")):
            result = outreach_module.process_outreach(
                db=self.db,
                job_id=self.job.id,
                selected_candidates=[],
                custom_body="",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["sent"], 1)

        outreach_row = self.db.execute(
            text("SELECT status, provider_message_id FROM outreach_events WHERE job_id = :job_id AND candidate_id = :candidate_id"),
            {"job_id": self.job.id, "candidate_id": "candidate-2"},
        ).fetchone()
        self.assertIsNotNone(outreach_row)
        self.assertEqual(outreach_row[0], "sent")
        self.assertEqual(outreach_row[1], "msg-789")

    def test_adam_source_app_isolated_from_dashboard_rows(self) -> None:
        job_repo = JobRepository(self.db)
        interview_repo = InterviewRepository(self.db)
        outreach_repo = OutreachEventRepository(self.db)
        token_repo = NotificationWorkflowTokenRepository(self.db)

        adam_job = job_repo.create(
            company_id=self.company.id,
            created_by=self.user.id,
            title="Isolation Check",
            description="Verify source app isolation.",
            location="Remote",
            compensation="$1",
            work_authorization="required",
            responsibilities=[],
            skills_required=[],
        )
        self.assertEqual(adam_job.source_app, "adam")

        adam_interview = interview_repo.upsert_status(
            job_id=self.job.id,
            candidate_id="candidate-source-app",
            status="shortlisted",
            create_default="shortlisted",
        )
        self.assertEqual(adam_interview.source_app, "adam")

        adam_outreach = outreach_repo.upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            provider="resend",
            to_email="candidate@example.com",
            subject="Hello",
            body="Hi",
            status="sent",
            sent_at=None,
            next_follow_up_at=None,
            provider_message_id="source-app-msg-1",
        )
        self.assertEqual(adam_outreach.source_app, "adam")

        adam_token = token_repo.create(
            job_id=self.job.id,
            candidate_id="candidate-1",
            workflow_name="interview_invite",
            token="token-source-app-1",
            payload={"step": "invite"},
            source_app="adam",
        )
        self.assertEqual(adam_token.source_app, "adam")

        self.db.execute(text("UPDATE jobs SET source_app = 'dashboard' WHERE id = :id"), {"id": adam_job.id})
        self.db.execute(text("UPDATE interviews SET source_app = 'dashboard' WHERE id = :id"), {"id": adam_interview.id})
        self.db.execute(text("UPDATE outreach_events SET source_app = 'dashboard' WHERE id = :id"), {"id": adam_outreach.id})
        self.db.execute(text("UPDATE notification_workflow_tokens SET source_app = 'dashboard' WHERE id = :id"), {"id": adam_token.id})
        self.db.commit()

        self.assertIsNone(job_repo.get(adam_job.id))
        self.assertNotIn(adam_job.id, [row.id for row in job_repo.list_recent(limit=20)])
        self.assertEqual(interview_repo.list_for_job(self.job.id), [])
        self.assertEqual(outreach_repo.list_for_job(self.job.id), [])
        self.assertIsNone(outreach_repo.get_by_provider_message_id("source-app-msg-1"))
        self.assertIsNone(token_repo.get_by_token("token-source-app-1", source_app="adam"))
        self.assertIsNotNone(token_repo.get_by_token("token-source-app-1", source_app="dashboard"))

    def test_shortlisted_candidates_create_adam_workflow_tokens(self) -> None:
        interview_repo = InterviewRepository(self.db)
        interview_repo.upsert_status(
            job_id=self.job.id,
            candidate_id="candidate-1",
            status="shortlisted",
            create_default="shortlisted",
        )

        CandidateProfileRepository(self.db).upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            name="Sai Vignesh",
            role="Software Engineer",
            company="Tech Solutions Pvt Ltd",
            summary="Full-stack engineer with strong product delivery experience.",
            skills=["Python", "React", "PostgreSQL"],
            raw_data={
                "name": "Sai Vignesh",
                "email": "suramsaivignesh@gmail.com",
                "phone": "+91 90000 00000",
                "linkedin_url": "https://linkedin.com/in/saivignesh",
                "github_url": "https://github.com/saivignesh",
                "current_company": "Tech Solutions Pvt Ltd",
                "current_title": "Software Engineer",
                "total_experience_years": 2.0,
                "skills": ["Python", "React", "PostgreSQL"],
                "parsed_resume_text": "Seasoned software engineer.",
            },
            fit_score=4.25,
            decision="strong_match",
            strategy="HIGH",
        )

        response = self.client.get(f"/api/candidates/shortlisted?jobId={self.job.id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(body["data"]), 1)

        token_row = self.db.execute(
            text(
                "SELECT source_app, token_type, is_active, payload, token "
                "FROM notification_workflow_tokens WHERE job_id = :job_id AND candidate_id = :candidate_id"
            ),
            {"job_id": self.job.id, "candidate_id": "candidate-1"},
        ).fetchone()
        self.assertIsNotNone(token_row)
        self.assertEqual(token_row[0], "adam")
        self.assertEqual(token_row[1], "slot_booking")
        self.assertTrue(bool(token_row[2]))

        payload = json.loads(token_row[3])
        self.assertEqual(payload["name"], "Sai Vignesh")
        self.assertEqual(payload["email"], "suramsaivignesh@gmail.com")
        self.assertEqual(payload["phone"], "+91 90000 00000")
        self.assertEqual(payload["linkedin_url"], "https://linkedin.com/in/saivignesh")
        self.assertEqual(payload["github_url"], "https://github.com/saivignesh")
        self.assertEqual(payload["current_company"], "Tech Solutions Pvt Ltd")
        self.assertEqual(payload["current_title"], "Software Engineer")
        self.assertEqual(payload["total_experience_years"], 2.0)
        self.assertEqual(payload["skills"], ["Python", "React", "PostgreSQL"])
        self.assertEqual(payload["resume_text"], "Seasoned software engineer.")
        self.assertEqual(payload["fit_score"], 4.25)
        self.assertEqual(payload["job_title"], self.job.title)
        self.assertEqual(payload["company_name"], self.company.name)

    def test_notification_workflow_token_booking_links_use_token_query_param(self) -> None:
        from app.services import interview_session_service as interview_session_module

        interview_repo = InterviewRepository(self.db)
        interview_repo.upsert_status(
            job_id=self.job.id,
            candidate_id="candidate-1",
            status="shortlisted",
            create_default="shortlisted",
        )

        CandidateProfileRepository(self.db).upsert(
            job_id=self.job.id,
            candidate_id="candidate-1",
            name="Book Me",
            role="Software Engineer",
            company="Tech Solutions Pvt Ltd",
            summary="Ready to book.",
            skills=["Python"],
            raw_data={
                "name": "Book Me",
                "email": "bookme@example.com",
                "phone": "+91 91111 11111",
                "linkedin_url": "https://linkedin.com/in/bookme",
                "github_url": "https://github.com/bookme",
                "current_company": "Tech Solutions Pvt Ltd",
                "current_title": "Software Engineer",
                "total_experience_years": 3.5,
                "skills": ["Python"],
                "parsed_resume_text": "Bookable candidate.",
            },
            fit_score=4.9,
            decision="strong_match",
            strategy="HIGH",
        )

        session_data = interview_session_module.create_interview_session(
            db=self.db,
            job_id=self.job.id,
            candidate_id="candidate-1",
        )

        token = str(session_data["token"])
        booking_link = str(session_data["bookingLink"])
        self.assertTrue(booking_link.startswith("https://interview.pontis.one/booking.html?token="))
        self.assertIn(token, booking_link)
        self.assertEqual(str(session_data["slot_link"]), booking_link)

        token_row = self.db.execute(
            text(
                "SELECT source_app, token_type, is_active, payload FROM notification_workflow_tokens "
                "WHERE token = :token"
            ),
            {"token": token},
        ).fetchone()
        self.assertIsNotNone(token_row)
        self.assertEqual(token_row[0], "adam")
        self.assertEqual(token_row[1], "slot_booking")
        self.assertTrue(bool(token_row[2]))
        payload = json.loads(token_row[3])
        self.assertEqual(payload["email"], "bookme@example.com")
        self.assertEqual(payload["job_title"], self.job.title)

        token_repo = NotificationWorkflowTokenRepository(self.db)
        self.assertIsNone(token_repo.get_by_token(token, source_app="dashboard"))
        self.assertIsNotNone(token_repo.get_by_token(token, source_app="adam"))

    def test_scoring_profile_get_or_create_returns_existing_row(self) -> None:
        from app.db.repositories import ScoringProfileRepository

        existing = ScoringProfileEntity(id=str(uuid4()), job_id=self.job.id)
        self.db.add(existing)
        self.db.commit()

        repo = ScoringProfileRepository(self.db)
        row = repo.get_or_create(job_id=self.job.id)

        self.assertEqual(row.id, existing.id)
        self.assertEqual(row.job_id, self.job.id)

    def test_interviews_endpoint_handles_non_dict_candidate_raw_data(self) -> None:
        interview_repo = InterviewRepository(self.db)
        interview_repo.upsert_status(
            job_id=self.job.id,
            candidate_id="candidate-1",
            status="shortlisted",
            create_default="shortlisted",
        )
        self.db.execute(
            text("UPDATE candidate_profiles SET raw_data = :raw_data WHERE job_id = :job_id AND candidate_id = :candidate_id"),
            {
                "raw_data": json.dumps(["legacy", "payload"]),
                "job_id": self.job.id,
                "candidate_id": "candidate-1",
            },
        )
        self.db.commit()

        response = self.client.get(f"/api/interviews?jobId={self.job.id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(body["data"]), 1)
        self.assertEqual(body["data"][0]["candidateId"], "candidate-1")

    def test_interviews_endpoint_serializes_uuid_candidate_ids(self) -> None:
        from app.services import interview_service as interview_module

        candidate_uuid = uuid4()
        interview_row = types.SimpleNamespace(candidate_id=candidate_uuid, status="shortlisted")
        profile_row = types.SimpleNamespace(candidate_id=candidate_uuid, name="UUID Candidate")

        with patch.object(interview_module.InterviewRepository, "list_for_job", return_value=[interview_row]), \
            patch.object(interview_module.CandidateProfileRepository, "list_for_job", return_value=[profile_row]):
            response = self.client.get(f"/api/interviews?jobId={self.job.id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(body["data"]), 1)
        self.assertEqual(body["data"][0]["candidateId"], str(candidate_uuid))
        self.assertEqual(body["data"][0]["name"], "UUID Candidate")

    def test_voice_transcript_cleanup_removes_duplicate_words(self) -> None:
        from app.services.voice_service import clean_transcript

        self.assertEqual(clean_transcript("a good idea a good idea actually"), "A good idea actually")

    def test_voice_transcript_cleanup_removes_duplicate_phrases(self) -> None:
        from app.services.voice_service import clean_transcript

        self.assertEqual(clean_transcript("the familiarity with the familiarity with Django"), "The familiarity with Django")

    def test_voice_refine_cleans_repeated_recruiter_noise(self) -> None:
        from app.services import voice_service as voice_module

        noisy_transcript = (
            "Recruiter: Yes. Thanks for the message. We are looking for a message. "
            "We are looking for our sales executive with 2 years of experience 2 years of experience. "
            "In those sales of Yeah."
        )

        with patch.object(voice_module, "ensure_all_collections", lambda: None), \
            patch.object(voice_module, "delete_job_vectors", lambda *_args, **_kwargs: None), \
            patch.object(voice_module, "upsert_job_chunks", lambda *_args, **_kwargs: None), \
            patch.object(voice_module, "get_embedding", lambda *_args, **_kwargs: [0.0] * 384):
            result = voice_module.refine_job_with_voice(
                db=self.db,
                job_id=self.job.id,
                voice_notes=[noisy_transcript],
                transcript=noisy_transcript,
            )

        self.assertTrue(result["refined"])

        job = JobRepository(self.db).get(self.job.id)
        self.assertIsNotNone(job)
        structured = dict(job.structured_data or {})
        clean_transcript = str(structured.get("voiceTranscriptClean") or "")

        self.assertIn("sales executive", clean_transcript.lower())
        self.assertIn("2 years of experience", clean_transcript.lower())
        self.assertNotIn("we are looking for a message. we are looking for our sales executive", clean_transcript.lower())
        self.assertNotIn("experience experience", clean_transcript.lower())

        job_intake_row = self.db.execute(
            text("SELECT transcript FROM job_intakes WHERE job_id = :job_id"),
            {"job_id": self.job.id},
        ).fetchone()
        self.assertIsNotNone(job_intake_row)
        self.assertIn("sales executive", str(job_intake_row[0]).lower())
        self.assertNotIn("experience experience", str(job_intake_row[0]).lower())

    def test_outreach_uses_nested_extracted_email_before_fallback(self) -> None:
        from app.services import outreach_service as outreach_module

        candidate_id = "candidate-nested-email"
        CandidateProfileRepository(self.db).upsert(
            job_id=self.job.id,
            candidate_id=candidate_id,
            name="Nested Email Candidate",
            role="Software Engineer",
            company="Tech Solutions Pvt Ltd",
            summary="Candidate with email nested in parsed data.",
            skills=["Python"],
            raw_data={
                "name": "Nested Email Candidate",
                "parsedData": {
                    "contact": {
                        "email": "suramsaivignesh@gmail.com",
                    }
                },
                "profileData": {
                    "email": "suramsaivignesh@gmail.com",
                },
            },
            fit_score=4.0,
            decision="shortlisted",
            strategy="HIGH",
        )
        InterviewRepository(self.db).upsert_status(
            job_id=self.job.id,
            candidate_id=candidate_id,
            status="shortlisted",
            create_default="shortlisted",
        )
        self.db.commit()

        with patch.object(outreach_module, "_send_shortlist_outreach_email", return_value=(True, "", "msg-nested-1")):
            result = outreach_module._trigger_candidate_outreach_sync(candidate_id=candidate_id, job_id=self.job.id)

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["candidateEmail"], "suramsaivignesh@gmail.com")
        self.assertFalse(result["fallbackUsed"])

        outreach_row = self.db.execute(
            text("SELECT to_email, status, last_error FROM outreach_events WHERE job_id = :job_id AND candidate_id = :candidate_id"),
            {"job_id": self.job.id, "candidate_id": candidate_id},
        ).fetchone()
        self.assertIsNotNone(outreach_row)
        self.assertEqual(outreach_row[0], "suramsaivignesh@gmail.com")
        self.assertEqual(outreach_row[1], "sent")
        self.assertEqual(outreach_row[2], "")

    def test_latest_by_candidate_ids_converts_uuid_inputs_to_text(self) -> None:
        repo = CandidateProfileRepository(self.db)
        candidate_a = str(uuid4())
        candidate_b = str(uuid4())

        repo.upsert(
            job_id=self.job.id,
            candidate_id=candidate_a,
            name="Avery",
            role="Platform Engineer",
            company="Northstar",
            summary="First profile for UUID lookup regression.",
            skills=["Python"],
            raw_data={"name": "Avery"},
            fit_score=4.2,
            decision="strong_match",
            strategy="HIGH",
        )
        repo.upsert(
            job_id=self.job.id,
            candidate_id=candidate_b,
            name="Blair",
            role="Platform Engineer",
            company="Northstar",
            summary="Second profile for UUID lookup regression.",
            skills=["Python"],
            raw_data={"name": "Blair"},
            fit_score=4.1,
            decision="strong_match",
            strategy="HIGH",
        )

        result = repo.latest_by_candidate_ids(
            job_id=self.job.id,
            candidate_ids=[UUID(candidate_a), UUID(candidate_b)],
        )

        self.assertEqual(set(result.keys()), {candidate_a, candidate_b})
        self.assertEqual(result[candidate_a].candidate_id, candidate_a)
        self.assertEqual(result[candidate_b].candidate_id, candidate_b)

    def test_resend_inbound_not_interested_updates_declined(self) -> None:
        reply_email = {
            "object": "email",
            "id": "email-not-interested-123",
            "to": ["inbound@pontis.one"],
            "from": "Avery Candidate <candidate@example.com>",
            "created_at": "2026-05-11T00:00:00+00:00",
            "subject": "Re: Opportunity at Acme",
            "html": "<p>Not interested.</p>",
            "text": "Not interested.",
            "headers": {
                "message-id": "<reply-message-id-125>",
                "in-reply-to": "<outreach-message-id-125>",
            },
        }
        attachments_list = {"object": "list", "has_more": False, "data": []}

        response = self._post_resend_inbound_reply(
            webhook_id="msg_not_interested_123",
            email_id="email-not-interested-123",
            reply_email=reply_email,
            attachments_list=attachments_list,
        )

        self.assertEqual(response.status_code, 200)
        profile = CandidateProfileRepository(self.db).get(job_id=self.job.id, candidate_id="candidate-1")
        self.assertEqual(profile.candidate_status, "declined")

        inbound = self.db.execute(
            text("SELECT processing_status, intent FROM inbound_email_replies WHERE email_id = :email_id"),
            {"email_id": "email-not-interested-123"},
        ).fetchone()
        self.assertEqual(inbound[0], "completed")
        self.assertEqual(inbound[1], "not_interested")

    def test_resend_inbound_needs_more_info_updates_recruiter_queue_state(self) -> None:
        reply_email = {
            "object": "email",
            "id": "email-needs-info-123",
            "to": ["inbound@pontis.one"],
            "from": "Avery Candidate <candidate@example.com>",
            "created_at": "2026-05-11T00:00:00+00:00",
            "subject": "Re: Opportunity at Acme",
            "html": "<p>Can you share the salary and job description?</p>",
            "text": "Can you share the salary and job description?",
            "headers": {
                "message-id": "<reply-message-id-126>",
                "in-reply-to": "<outreach-message-id-126>",
            },
        }
        attachments_list = {"object": "list", "has_more": False, "data": []}

        response = self._post_resend_inbound_reply(
            webhook_id="msg_needs_info_123",
            email_id="email-needs-info-123",
            reply_email=reply_email,
            attachments_list=attachments_list,
        )

        self.assertEqual(response.status_code, 200)
        profile = CandidateProfileRepository(self.db).get(job_id=self.job.id, candidate_id="candidate-1")
        self.assertEqual(profile.candidate_status, "awaiting_recruiter_response")

        inbound = self.db.execute(
            text("SELECT intent FROM inbound_email_replies WHERE email_id = :email_id"),
            {"email_id": "email-needs-info-123"},
        ).fetchone()
        self.assertEqual(inbound[0], "needs_more_info")

    def test_resend_inbound_unsubscribe_blocks_future_outreach(self) -> None:
        reply_email = {
            "object": "email",
            "id": "email-unsubscribe-123",
            "to": ["inbound@pontis.one"],
            "from": "Avery Candidate <candidate@example.com>",
            "created_at": "2026-05-11T00:00:00+00:00",
            "subject": "Re: Opportunity at Acme",
            "html": "<p>Please unsubscribe me.</p>",
            "text": "Please unsubscribe me.",
            "headers": {
                "message-id": "<reply-message-id-127>",
                "in-reply-to": "<outreach-message-id-127>",
            },
        }
        attachments_list = {"object": "list", "has_more": False, "data": []}

        response = self._post_resend_inbound_reply(
            webhook_id="msg_unsubscribe_123",
            email_id="email-unsubscribe-123",
            reply_email=reply_email,
            attachments_list=attachments_list,
        )

        self.assertEqual(response.status_code, 200)
        profile = CandidateProfileRepository(self.db).get(job_id=self.job.id, candidate_id="candidate-1")
        self.assertEqual(profile.candidate_status, "do_not_contact")

        inbound = self.db.execute(
            text("SELECT intent FROM inbound_email_replies WHERE email_id = :email_id"),
            {"email_id": "email-unsubscribe-123"},
        ).fetchone()
        self.assertEqual(inbound[0], "unsubscribe")

    def test_resend_inbound_ambiguous_sets_manual_review(self) -> None:
        reply_email = {
            "object": "email",
            "id": "email-ambiguous-123",
            "to": ["inbound@pontis.one"],
            "from": "Avery Candidate <candidate@example.com>",
            "created_at": "2026-05-11T00:00:00+00:00",
            "subject": "Re: Opportunity at Acme",
            "html": "<p>Thanks for reaching out.</p>",
            "text": "Thanks for reaching out.",
            "headers": {
                "message-id": "<reply-message-id-128>",
                "in-reply-to": "<outreach-message-id-128>",
            },
        }
        attachments_list = {"object": "list", "has_more": False, "data": []}

        response = self._post_resend_inbound_reply(
            webhook_id="msg_ambiguous_123",
            email_id="email-ambiguous-123",
            reply_email=reply_email,
            attachments_list=attachments_list,
        )

        self.assertEqual(response.status_code, 200)
        profile = CandidateProfileRepository(self.db).get(job_id=self.job.id, candidate_id="candidate-1")
        self.assertEqual(profile.candidate_status, "manual_review")

        inbound = self.db.execute(
            text("SELECT intent FROM inbound_email_replies WHERE email_id = :email_id"),
            {"email_id": "email-ambiguous-123"},
        ).fetchone()
        self.assertEqual(inbound[0], "ambiguous")

    def test_queue_dead_letter_replay_and_scheduler_snapshot(self) -> None:
        try:
            from app.services.job_queue_service import classify_queue_failure, list_dead_letter_jobs, replay_dead_letter_job
        except ModuleNotFoundError:
            self.skipTest("redis dependency is not installed in this shell")
        from app.services.refresh_scheduler import scheduler_status

        class FakePipeline:
            def __init__(self, redis):
                self.redis = redis
                self.ops = []

            def hdel(self, key, field):
                self.ops.append(("hdel", key, field))
                return self

            def set(self, *args, **kwargs):
                self.ops.append(("set", args, kwargs))
                return self

            def lpush(self, *args, **kwargs):
                self.ops.append(("lpush", args, kwargs))
                return self

            def zadd(self, *args, **kwargs):
                self.ops.append(("zadd", args, kwargs))
                return self

            def execute(self):
                for op in self.ops:
                    if op[0] == "hdel":
                        self.redis.hdel(op[1], op[2])

        class FakeRedis:
            def __init__(self):
                self.data = {
                    "pontis:queue:outreach_send:dead": {"dead-1": '{"status":"dead_letter","attempts":2,"updated_at":"2026-01-01T00:00:00+00:00"}'},
                    "pontis:queue:outreach_send:dead_meta": {"dead-1": '{"status":"dead_letter","attempts":2,"error":"timeout","updated_at":"2026-01-01T00:00:00+00:00"}'},
                }

            def hgetall(self, key):
                return dict(self.data.get(key, {}))

            def hget(self, key, field):
                return self.data.get(key, {}).get(field)

            def hdel(self, key, field):
                self.data.get(key, {}).pop(field, None)

            def set(self, *args, **kwargs):
                return True

            def get(self, *args, **kwargs):
                return None

            def pipeline(self):
                return FakePipeline(self)

            def lpush(self, *args, **kwargs):
                return 1

            def zadd(self, *args, **kwargs):
                return 1

            def llen(self, *args, **kwargs):
                return 0

            def zcard(self, *args, **kwargs):
                return 0

            def hlen(self, *args, **kwargs):
                return 0

            def lindex(self, *args, **kwargs):
                return None

            def ping(self):
                return True

        fake_redis = FakeRedis()
        with patch("app.services.job_queue_service.get_redis", lambda: fake_redis):
            dead_letters = list_dead_letter_jobs(queue_type="outreach_send", limit=10)
            self.assertEqual(len(dead_letters), 1)
            replay = replay_dead_letter_job("outreach_send", "dead-1")
            self.assertTrue(replay["replayed"])

        status = scheduler_status()
        self.assertIn("running", status)
        self.assertIn("candidate_refresh_interval_minutes", status)


if __name__ == "__main__":
    unittest.main()
