from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_selection_flow.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

fake_redis_module = types.ModuleType("redis")
fake_redis_exceptions = types.ModuleType("redis.exceptions")


class _FakeRedisError(Exception):
    pass


class _FakeRedisClient:
    @staticmethod
    def from_url(*args, **kwargs):
        return _FakeRedisClient()

    def ping(self):
        return True


fake_redis_module.Redis = _FakeRedisClient
fake_redis_module.from_url = lambda *args, **kwargs: _FakeRedisClient()
fake_redis_exceptions.RedisError = _FakeRedisError
sys.modules.setdefault("redis", fake_redis_module)
sys.modules.setdefault("redis.exceptions", fake_redis_exceptions)

fake_slack_sdk = types.ModuleType("slack_sdk")
fake_slack_sdk_errors = types.ModuleType("slack_sdk.errors")


class _FakeWebClient:
    def __init__(self, *args, **kwargs):
        pass


class _FakeSlackApiError(Exception):
    pass


fake_slack_sdk.WebClient = _FakeWebClient
fake_slack_sdk_errors.SlackApiError = _FakeSlackApiError
sys.modules.setdefault("slack_sdk", fake_slack_sdk)
sys.modules.setdefault("slack_sdk.errors", fake_slack_sdk_errors)

from app.schemas.candidate import CandidateExplanation, CandidateResult
from app.db.repositories import _candidate_email_value
from app.services.candidate_service import build_selection_candidate_snapshot
from app.services import candidate_selection_service
from app.services.preference_pair_service import generate_three_round_plan
from app.services import outreach_service


class SelectionFlowTests(unittest.TestCase):
    def test_generate_three_round_plan_uses_unique_candidate_ids(self) -> None:
        candidates = [
            {"id": f"candidate-{index}", "name": f"Candidate {index}", "role": "Engineer", "company": f"Company {index}", "skills": ["Python", "FastAPI"], "summary": "Strong builder", "fitScore": 5 - index * 0.1}
            for index in range(6)
        ]

        plan = generate_three_round_plan(candidates=candidates, intent_profile={"preferred_skills": ["Python"]})
        all_ids = [candidate_id for pair in plan for candidate_id in pair.get("candidate_ids", [])]

        self.assertEqual(len(plan), 3)
        self.assertEqual(len(all_ids), 6)
        self.assertEqual(len(set(all_ids)), 6)
        self.assertTrue(all(len(pair.get("candidate_ids", [])) == 2 for pair in plan))

    @patch("app.services.candidate_service.fetch_ranked_candidates")
    def test_real_selection_snapshot_uses_retrieved_candidates(self, mock_fetch_ranked_candidates) -> None:
        mock_fetch_ranked_candidates.return_value = [
            CandidateResult(
                id=f"real-candidate-{index}",
                name=f"Real Candidate {index}",
                role="Engineer",
                company="Company",
                email=f"real-candidate-{index}@example.com",
                skills=["Python", "FastAPI"],
                summary="Strong builder",
                fitScore=5 - index * 0.1,
                decision="strong_match",
                explanation=CandidateExplanation(
                    semanticScore=0.9,
                    skillOverlap=0.8,
                    finalScore=0.9,
                    pdlRelevance=0.8,
                    recencyScore=0.7,
                    engineeringScore=0.85,
                    penalties={},
                ),
                strategy="HIGH",
            )
            for index in range(8)
        ]

        snapshot = build_selection_candidate_snapshot(db=object(), job_id="job-1")

        self.assertEqual(len(snapshot), 8)
        self.assertEqual([candidate.id for candidate in snapshot][:2], ["real-candidate-0", "real-candidate-1"])

    def test_final_shortlist_limit_differs_by_mode(self) -> None:
        elite_job = types.SimpleNamespace(vetting_mode="elite")
        volume_job = types.SimpleNamespace(vetting_mode="volume")

        self.assertEqual(candidate_selection_service._final_shortlist_limit(elite_job), 5)
        self.assertEqual(candidate_selection_service._final_shortlist_limit(volume_job), 10)

    def test_shortlist_email_does_not_bcc_test_mailbox(self) -> None:
        fake_send_calls: list[dict] = []

        def fake_send(payload):
            fake_send_calls.append(payload)
            return {"id": "msg-123"}

        fake_resend = types.SimpleNamespace(
            api_key=None,
            Emails=types.SimpleNamespace(send=fake_send),
            emails=types.SimpleNamespace(send=fake_send),
        )

        with patch.dict(sys.modules, {"resend": fake_resend}), patch.object(outreach_service, "RESEND_API_KEY", "test-key"):
            ok, error, message_id = outreach_service._send_shortlist_outreach_email(
                to_email="candidate@example.com",
                subject="Opportunity",
                html_body="<p>Hello</p>",
                text_body="Hello",
            )

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(message_id, "msg-123")
        self.assertEqual(len(fake_send_calls), 1)
        payload = fake_send_calls[0]
        self.assertEqual(payload["to"], ["candidate@example.com"])
        self.assertNotIn("bcc", payload)

    def test_regular_outreach_email_does_not_bcc_test_mailbox(self) -> None:
        fake_send_calls: list[dict] = []

        def fake_send(payload):
            fake_send_calls.append(payload)
            return {"id": "msg-456"}

        fake_resend = types.SimpleNamespace(
            api_key=None,
            Emails=types.SimpleNamespace(send=fake_send),
            emails=types.SimpleNamespace(send=fake_send),
        )

        with patch.dict(sys.modules, {"resend": fake_resend}), patch.object(outreach_service, "RESEND_API_KEY", "test-key"):
            ok, error, message_id = outreach_service._send_resend(
                to_email="candidate@example.com",
                subject="Opportunity",
                body="Hello",
                from_email="recruiter@example.com",
            )

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(message_id, "msg-456")
        self.assertEqual(len(fake_send_calls), 1)
        payload = fake_send_calls[0]
        self.assertEqual(payload["to"], ["candidate@example.com"])
        self.assertNotIn("bcc", payload)

    def test_regular_outreach_email_includes_html_payload(self) -> None:
        fake_send_calls: list[dict] = []

        def fake_send(payload):
            fake_send_calls.append(payload)
            return {"id": "msg-html"}

        fake_resend = types.SimpleNamespace(
            api_key=None,
            Emails=types.SimpleNamespace(send=fake_send),
            emails=types.SimpleNamespace(send=fake_send),
        )

        with patch.dict(sys.modules, {"resend": fake_resend}), patch.object(outreach_service, "RESEND_API_KEY", "test-key"):
            ok, error, message_id = outreach_service._send_outreach_email(
                to_email="candidate@example.com",
                subject="Opportunity",
                body="Hello there,\n\nPlease reply with your updated resume.",
            )

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(message_id, "msg-html")
        self.assertEqual(len(fake_send_calls), 1)
        payload = fake_send_calls[0]
        self.assertIn("<table", payload.get("html", ""))
        self.assertEqual(payload["text"], "Hello there,\n\nPlease reply with your updated resume.")

    def test_invalid_email_falls_back_to_debug_mailbox(self) -> None:
        result = outreach_service._resolve_outreach_recipient(raw_data={"email": "not-an-email"})

        self.assertEqual(result["original_email"], "")
        self.assertEqual(result["to_email"], "vaibhavkar0009@gmail.com")
        self.assertTrue(result["fallback_used"])
        self.assertIn("fallback", result["reason"])

    def test_nested_candidate_email_values_are_extracted(self) -> None:
        nested_payload = {
            "contact": {
                "primary": {
                    "work": "candidate@example.com",
                }
            },
            "profile": {
                "emails": [
                    {"address": "alt@example.com"},
                    {"value": "other@example.com"},
                ]
            },
        }

        self.assertEqual(_candidate_email_value(nested_payload), "candidate@example.com")


if __name__ == "__main__":
    unittest.main()
