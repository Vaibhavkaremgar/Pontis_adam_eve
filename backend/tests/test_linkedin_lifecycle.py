from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_linkedin_lifecycle.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "redis" not in sys.modules:
    redis_module = types.ModuleType("redis")

    class _FakeRedisClient:
        def ping(self):
            return True

        def close(self):
            return None

    redis_module.Redis = _FakeRedisClient
    redis_module.from_url = lambda *args, **kwargs: _FakeRedisClient()
    redis_exceptions = types.ModuleType("redis.exceptions")

    class _RedisError(Exception):
        pass

    redis_exceptions.RedisError = _RedisError
    redis_module.exceptions = redis_exceptions
    sys.modules["redis"] = redis_module
    sys.modules["redis.exceptions"] = redis_exceptions

from app.services.job_queue_service import enqueue_job
import app.services.refresh_scheduler as refresh_scheduler
import app.services.candidate_engagement_service as candidate_engagement_service
from app.linkedin.playwright.browser_exceptions import BrowserLaunchError


class LinkedInLifecycleTests(unittest.TestCase):
    def test_linkedin_queue_defers_when_redis_is_unavailable(self) -> None:
        with patch("app.services.job_queue_service.get_redis", return_value=None):
            result = enqueue_job(
                "linkedin_message_queue",
                {"job_id": "job-1", "candidate_id": "candidate-1"},
                idempotency_key="linkedin-message:test",
            )

        self.assertFalse(result["queued"])
        self.assertTrue(result["deferred"])
        self.assertEqual(result["mode"], "deferred")
        self.assertEqual(result["reason"], "redis_unavailable")

    def test_acceptance_cycle_skips_when_no_pending_connections_exist(self) -> None:
        fake_db = object()

        class _FakeSession:
            def __enter__(self):
                return fake_db

            def __exit__(self, exc_type, exc, tb):
                return False

            def close(self):
                return None

            def close(self):
                return None

            def close(self):
                return None

            def close(self):
                return None

        with patch("app.db.session.SessionLocal", return_value=_FakeSession()), patch(
            "app.linkedin.repository.LinkedInConnectionRepository"
        ) as mock_repo, patch("app.services.refresh_scheduler.enqueue_job") as mock_enqueue:
            mock_repo.return_value.list_pending.return_value = []
            refresh_scheduler._run_linkedin_acceptance_check_cycle()

        mock_enqueue.assert_not_called()

    def test_acceptance_cycle_skips_when_linkedin_not_configured(self) -> None:
        class _Row:
            def __init__(self, account_id: str) -> None:
                self.account_id = account_id

        fake_db = object()

        class _FakeSession:
            def __enter__(self):
                return fake_db

            def __exit__(self, exc_type, exc, tb):
                return False

            def close(self):
                return None

        with patch("app.db.session.SessionLocal", return_value=_FakeSession()), patch(
            "app.linkedin.repository.LinkedInConnectionRepository"
        ) as mock_repo, patch("app.services.refresh_scheduler.has_linkedin_configuration", return_value=False), patch(
            "app.services.refresh_scheduler.enqueue_job"
        ) as mock_enqueue:
            mock_repo.return_value.list_pending.return_value = [_Row("account-1")]
            refresh_scheduler._run_linkedin_acceptance_check_cycle()

        mock_enqueue.assert_not_called()

    def test_acceptance_check_skips_unconfigured_account_without_playwright(self) -> None:
        class _Row:
            def __init__(self, account_id: str, candidate_id: str = "candidate-1", linkedin_url: str = "https://linkedin.com/in/candidate") -> None:
                self.account_id = account_id
                self.id = "connection-1"
                self.candidate_id = candidate_id
                self.linkedin_url = linkedin_url

        fake_db = object()

        class _FakeSession:
            def __enter__(self):
                return fake_db

            def __exit__(self, exc_type, exc, tb):
                return False

            def close(self):
                return None

        with patch("app.db.session.SessionLocal", return_value=_FakeSession()), patch(
            "app.linkedin.repository.LinkedInConnectionRepository"
        ) as mock_repo, patch("app.services.candidate_engagement_service.has_linkedin_configuration", return_value=False), patch(
            "app.services.candidate_engagement_service.BrowserManager"
        ) as mock_browser:
            mock_repo.return_value.list_pending.return_value = [_Row("account-1")]
            result = candidate_engagement_service.process_linkedin_acceptance_check_queue_job({})

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "linkedin_not_configured")
        mock_browser.assert_not_called()

    def test_acceptance_check_browser_failure_stays_retryable(self) -> None:
        class _Row:
            def __init__(self, account_id: str, candidate_id: str = "candidate-1", linkedin_url: str = "https://linkedin.com/in/candidate") -> None:
                self.account_id = account_id
                self.id = "connection-1"
                self.candidate_id = candidate_id
                self.linkedin_url = linkedin_url

        fake_db = object()

        class _FakeSession:
            def __enter__(self):
                return fake_db

            def __exit__(self, exc_type, exc, tb):
                return False

            def close(self):
                return None

        class _FakeBrowserManager:
            def __init__(self, account_id: str) -> None:
                self.account_id = account_id

            async def get_browser(self):
                raise BrowserLaunchError("Failed to start LinkedIn browser")

            async def stop(self):
                return None

        with patch("app.db.session.SessionLocal", return_value=_FakeSession()), patch(
            "app.linkedin.repository.LinkedInConnectionRepository"
        ) as mock_repo, patch(
            "app.services.candidate_engagement_service.has_linkedin_configuration",
            return_value=True,
        ), patch(
            "app.services.candidate_engagement_service.BrowserManager",
            side_effect=_FakeBrowserManager,
        ):
            mock_repo.return_value.list_pending.return_value = [_Row("account-1")]
            with self.assertRaises(BrowserLaunchError):
                candidate_engagement_service.process_linkedin_acceptance_check_queue_job({})

        self.assertTrue(mock_repo.return_value.list_pending.called)


if __name__ == "__main__":
    unittest.main()
