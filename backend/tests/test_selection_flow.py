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
from app.services.candidate_service import _candidate_identity_key, build_selection_candidate_snapshot
import app.services.candidate_service as candidate_service
import app.services.recruiter_preference_round_service as calibration_service
from app.services import candidate_selection_service
from app.services.slack_integration import build_calibration_blocks, extract_button_action
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

    @patch("app.services.candidate_service.fetch_ranked_candidates")
    def test_selection_snapshot_skips_candidates_without_real_email(self, mock_fetch_ranked_candidates) -> None:
        mock_fetch_ranked_candidates.return_value = [
            CandidateResult(
                id="candidate-no-email",
                name="No Email",
                role="Engineer",
                company="Company",
                email="",
                skills=["Python"],
                summary="No email",
                fitScore=4.8,
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
            ),
            CandidateResult(
                id="candidate-mock-email",
                name="Mock Email",
                role="Engineer",
                company="Company",
                email="mock@test.local",
                isMockEmail=True,
                skills=["Python"],
                summary="Mock email",
                fitScore=4.7,
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
            ),
            CandidateResult(
                id="candidate-real-1",
                name="Real One",
                role="Engineer",
                company="Company",
                email="real-1@example.com",
                skills=["Python"],
                summary="Real email",
                fitScore=4.6,
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
            ),
        ]

        snapshot = build_selection_candidate_snapshot(db=object(), job_id="job-1", limit=2)

        self.assertEqual([candidate.id for candidate in snapshot], ["candidate-real-1"])

    def test_candidate_identity_key_prefers_linkedin_then_github(self) -> None:
        linkedin_candidate = {
            "email": "",
            "linkedin_url": "https://www.linkedin.com/in/Example/",
            "github_url": "https://github.com/example",
            "full_name": "Example Person",
        }
        github_candidate = {
            "email": "",
            "github_url": "https://github.com/Example/",
            "full_name": "Example Person",
        }

        self.assertEqual(_candidate_identity_key(linkedin_candidate), "linkedin:linkedin.com/in/example")
        self.assertEqual(_candidate_identity_key(github_candidate), "github:github.com/example")

    def test_final_shortlist_limit_differs_by_mode(self) -> None:
        elite_job = types.SimpleNamespace(vetting_mode="elite")
        volume_job = types.SimpleNamespace(vetting_mode="volume")

        self.assertEqual(candidate_selection_service._final_shortlist_limit(elite_job), 15)
        self.assertEqual(candidate_selection_service._final_shortlist_limit(volume_job), 30)

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

    def test_recruiter_memory_changes_ranking_weights(self) -> None:
        low_existing, low_recruiter, low_session, low_strength = candidate_service._dynamic_ranking_weights(
            recruiter_feedback_count=0
        )
        high_existing, high_recruiter, high_session, high_strength = candidate_service._dynamic_ranking_weights(
            recruiter_feedback_count=6
        )
        low_score, _, _ = candidate_service._blend_final_score(
            existing_score=0.62,
            recruiter_score=0.58,
            session_signal=0.12,
            recruiter_feedback_count=0,
        )
        high_score, _, _ = candidate_service._blend_final_score(
            existing_score=0.62,
            recruiter_score=0.92,
            session_signal=0.12,
            recruiter_feedback_count=6,
        )

        self.assertLess(low_recruiter, high_recruiter)
        self.assertLess(low_strength, high_strength)
        self.assertGreater(low_existing, high_existing)
        self.assertNotEqual(low_score, high_score)
        self.assertGreater(high_score, low_score)

    def test_calibration_blocks_render_full_fields(self) -> None:
        blocks = build_calibration_blocks(
            job_id="job-1",
            current_index=1,
            total_sets=3,
            calibration_set={
                "set_title": "Startup Depth vs Scaled Reliability",
                "set_theme": "Contrast a startup joiner and a scaled systems owner.",
                "archetypes": [
                    {
                        "id": "archetype-a",
                        "name": "Senior Backend Engineer",
                        "summary": "6 years building API-heavy products at a Series B startup.",
                        "profileData": {
                            "candidateHeadline": "Senior Backend Engineer",
                            "experienceSnapshot": "6 years building API-heavy products at a Series B startup.",
                            "careerPattern": "Early startup joiner",
                            "technicalStrengths": ["Python", "FastAPI", "Postgres"],
                            "ownershipStyle": "Highly autonomous",
                            "idealEnvironment": "High-growth startup",
                            "executionStyle": "Fast iterative shipping",
                            "leadershipProfile": ["Mentors juniors", "Handles ambiguity well"],
                            "hiringTradeoffs": ["Startup depth over pedigree"],
                        },
                    }
                ],
            },
        )

        rendered = "\n".join(
            str(block.get("text", {}).get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "section"
        )

        self.assertIn("Senior Backend Engineer", rendered)
        self.assertIn("6 years building API-heavy products at a Series B startup.", rendered)
        self.assertIn("*Technical strengths:* Python, FastAPI, Postgres", rendered)
        self.assertIn("*Leadership profile:* Mentors juniors, Handles ambiguity well", rendered)
        self.assertNotIn("P, y, t, h, o, n", rendered)

    def test_slack_calibration_payload_uses_stable_calibration_set_id(self) -> None:
        blocks = build_calibration_blocks(
            job_id="job-1",
            current_index=1,
            total_sets=3,
            calibration_set={
                "calibration_set_id": "calibration-set-1",
                "set_title": "Startup Depth vs Scaled Reliability",
                "archetypes": [
                    {
                        "id": "calibration-set-1-archetype-1",
                        "profileData": {"candidateHeadline": "Senior Backend Engineer"},
                    }
                ],
            },
        )

        action_block = next(block for block in blocks if isinstance(block, dict) and block.get("type") == "actions")
        button = action_block["elements"][0]

        self.assertEqual(button["value"], "calibration_select:calibration-set-1:calibration-set-1-archetype-1:job-1")
        self.assertEqual(
            extract_button_action({"actions": [{"value": button["value"], "action_id": "calibration_select"}]}),
            ("calibration_select", "calibration-set-1-archetype-1", "job-1", "calibration-set-1"),
        )

    def test_calibration_choice_is_stable_and_replay_safe(self) -> None:
        selected_id = "calibration-set-1-archetype-1"
        other_id = "calibration-set-1-archetype-2"
        calibration_state = {
            "job_id": "job-1",
            "recruiter_id": "recruiter-1",
            "status": "active",
            "stage": "archetype_calibration",
            "current_round_index": 1,
            "current_pair": {
                "round_index": 1,
                "calibration_set_id": "calibration-set-1",
                "set_title": "Startup Depth vs Scaled Reliability",
                "archetypes": [
                    {"id": selected_id, "name": "Senior Backend Engineer"},
                    {"id": other_id, "name": "Staff Platform Engineer"},
                ],
            },
            "current_calibration_set_id": "calibration-set-1",
            "archetype_sets": [
                {
                    "round_index": 1,
                    "calibration_set_id": "calibration-set-1",
                    "set_title": "Startup Depth vs Scaled Reliability",
                    "archetypes": [
                        {"id": selected_id, "name": "Senior Backend Engineer"},
                        {"id": other_id, "name": "Staff Platform Engineer"},
                    ],
                },
                {
                    "round_index": 2,
                    "calibration_set_id": "calibration-set-2",
                    "set_title": "Product Focus vs Delivery Muscle",
                    "archetypes": [
                        {"id": "calibration-set-2-archetype-1", "name": "Product-Minded Fullstack Engineer"},
                        {"id": "calibration-set-2-archetype-2", "name": "Startup GTM Operator"},
                    ],
                },
            ],
            "archetype_pool": [],
            "rounds": [],
            "selected_candidate_ids": [],
            "selected_archetype_ids": [],
            "rejected_candidate_ids": [],
            "history": [],
            "gap_analysis": {},
            "recommended_questions": [],
            "vetting_mode": "volume",
            "candidate_source": "groq_archetypes",
            "intent_profile": {},
            "voice_summary": "",
            "telemetry": {},
            "orchestration_session_id": "",
        }

        class _DummyJob:
            id = "job-1"

        with patch.object(calibration_service.JobRepository, "get", return_value=_DummyJob()), patch.object(
            calibration_service, "_load_calibration_state", return_value=calibration_state
        ), patch.object(calibration_service, "_save_calibration_state", side_effect=lambda **kwargs: kwargs["state"]), patch.object(
            calibration_service, "_persist_calibration_snapshot", return_value=None
        ), patch.object(
            calibration_service, "update_recruiter_preferences", return_value={}
        ) as mock_update_preferences, patch.object(
            calibration_service, "save_cached_intent_profile", return_value=None
        ), patch.object(
            calibration_service, "log_metric", return_value=None
        ):
            first_result = calibration_service.record_preference_calibration_choice(
                db=object(),
                recruiter_id="recruiter-1",
                job_id="job-1",
                selected_candidate_id=selected_id,
                calibration_set_id="calibration-set-1",
            )
            replay_result = calibration_service.record_preference_calibration_choice(
                db=object(),
                recruiter_id="recruiter-1",
                job_id="job-1",
                selected_candidate_id=selected_id,
                calibration_set_id="calibration-set-1",
            )

        self.assertEqual(first_result["current_round_index"], 2)
        self.assertEqual(first_result["history"][0]["calibration_set_id"], "calibration-set-1")
        self.assertEqual(first_result["history"][0]["selected_archetype_id"], selected_id)
        self.assertEqual(replay_result["history"][0]["calibration_set_id"], "calibration-set-1")
        self.assertEqual(mock_update_preferences.call_count, 1)

    def test_stale_calibration_set_is_rejected_when_not_replayed(self) -> None:
        selected_id = "calibration-set-1-archetype-1"
        calibration_state = {
            "job_id": "job-1",
            "recruiter_id": "recruiter-1",
            "status": "active",
            "stage": "archetype_calibration",
            "current_round_index": 2,
            "current_pair": {
                "round_index": 2,
                "calibration_set_id": "calibration-set-2",
                "set_title": "Product Focus vs Delivery Muscle",
                "archetypes": [
                    {"id": "calibration-set-2-archetype-1", "name": "Product-Minded Fullstack Engineer"},
                    {"id": "calibration-set-2-archetype-2", "name": "Startup GTM Operator"},
                ],
            },
            "current_calibration_set_id": "calibration-set-2",
            "archetype_sets": [
                {
                    "round_index": 1,
                    "calibration_set_id": "calibration-set-1",
                    "set_title": "Startup Depth vs Scaled Reliability",
                    "archetypes": [
                        {"id": selected_id, "name": "Senior Backend Engineer"},
                        {"id": "calibration-set-1-archetype-2", "name": "Staff Platform Engineer"},
                    ],
                },
                {
                    "round_index": 2,
                    "calibration_set_id": "calibration-set-2",
                    "set_title": "Product Focus vs Delivery Muscle",
                    "archetypes": [
                        {"id": "calibration-set-2-archetype-1", "name": "Product-Minded Fullstack Engineer"},
                        {"id": "calibration-set-2-archetype-2", "name": "Startup GTM Operator"},
                    ],
                },
            ],
            "archetype_pool": [],
            "rounds": [],
            "selected_candidate_ids": [],
            "selected_archetype_ids": [],
            "rejected_candidate_ids": [],
            "history": [],
            "gap_analysis": {},
            "recommended_questions": [],
            "vetting_mode": "volume",
            "candidate_source": "groq_archetypes",
            "intent_profile": {},
            "voice_summary": "",
            "telemetry": {},
            "orchestration_session_id": "",
        }

        class _DummyJob:
            id = "job-1"

        with patch.object(calibration_service.JobRepository, "get", return_value=_DummyJob()), patch.object(
            calibration_service, "_load_calibration_state", return_value=calibration_state
        ), patch.object(calibration_service, "_save_calibration_state", side_effect=lambda **kwargs: kwargs["state"]), patch.object(
            calibration_service, "_persist_calibration_snapshot", return_value=None
        ), patch.object(
            calibration_service, "update_recruiter_preferences", return_value={}
        ), patch.object(
            calibration_service, "save_cached_intent_profile", return_value=None
        ), patch.object(
            calibration_service, "log_metric", return_value=None
        ):
            with self.assertRaisesRegex(ValueError, "Archetype is not part of the active calibration set"):
                calibration_service.record_preference_calibration_choice(
                    db=object(),
                    recruiter_id="recruiter-1",
                    job_id="job-1",
                    selected_candidate_id=selected_id,
                    calibration_set_id="calibration-set-1",
                )

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

    def test_invalid_email_requires_manual_entry(self) -> None:
        result = outreach_service._resolve_outreach_recipient(raw_data={"email": "not-an-email"})

        self.assertEqual(result["original_email"], "not-an-email")
        self.assertEqual(result["to_email"], "")
        self.assertTrue(result["manual_required"])
        self.assertIn(result["reason"], {"missing_email", "invalid_email", "invalid_email_domain"})

    def test_manual_override_email_can_be_used_when_provided(self) -> None:
        result = outreach_service._resolve_outreach_recipient(
            raw_data={"email": "not-an-email"},
            recipient_email="candidate@example.com",
        )

        self.assertEqual(result["original_email"], "not-an-email")
        self.assertEqual(result["to_email"], "candidate@example.com")
        self.assertFalse(result["manual_required"])

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
