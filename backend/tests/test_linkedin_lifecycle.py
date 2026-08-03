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

        with patch("app.db.session.SessionLocal", return_value=_FakeSession()), patch(
            "app.linkedin.repository.LinkedInConnectionRepository"
        ) as mock_repo, patch("app.services.refresh_scheduler.enqueue_job") as mock_enqueue:
            mock_repo.return_value.list_pending.return_value = []
            refresh_scheduler._run_linkedin_acceptance_check_cycle()

        mock_enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
