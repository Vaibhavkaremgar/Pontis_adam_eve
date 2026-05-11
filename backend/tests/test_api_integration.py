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
from app.db.repositories import CandidateProfileRepository, CompanyRepository, JobRepository, UserRepository
from app.db.session import SessionLocal, engine
from app.models.entities import Base
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
            title=f"Platform Engineer {suffix}",
            description="Build retrieval, queues, and AI observability.",
            location="Remote",
            compensation="$180k",
            work_authorization="required",
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
        self.db.commit()
        self.token = create_access_token(user_id=self.user.id, email=self.user.email, role=self.user.role)
        self.csrf = create_csrf_token(user_id=self.user.id)
        self.client.cookies.set(AUTH_COOKIE_NAME, self.token)
        self.client.cookies.set(CSRF_COOKIE_NAME, self.csrf)

    def tearDown(self) -> None:
        self.db.close()
        self.client.close()

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
        self.assertEqual(inbound[4], "processed")

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
