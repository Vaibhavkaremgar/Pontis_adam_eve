from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_job_sourcing_state.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import candidate_service


class _FakeCandidateProfileRepo:
    def __init__(self, count: int) -> None:
        self._count = count

    def count_for_job(self, job_id: str) -> int:
        return self._count


class _FakeJobsRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def update_candidate_sourcing_state(self, *, job_id: str, job_status: str, last_candidate_attempt_at=None):
        self.calls.append((job_id, job_status, last_candidate_attempt_at))
        return SimpleNamespace(id=job_id, job_status=job_status, last_candidate_attempt_at=last_candidate_attempt_at)


class JobSourcingStateTests(unittest.TestCase):
    def _patch_noop_observability(self):
        return patch.multiple(
            candidate_service,
            log_metric=lambda *args, **kwargs: None,
            record_candidate_fetch=lambda *args, **kwargs: None,
            _record_ranking_run=lambda *args, **kwargs: None,
            _ranking_run_metrics_for_candidates=lambda *args, **kwargs: [],
            _safe_commit=lambda *args, **kwargs: None,
        )

    def test_zero_candidates_marks_job_no_candidates(self) -> None:
        jobs = _FakeJobsRepo()
        job = SimpleNamespace(id="job-1", job_status="processing")

        with patch.object(candidate_service, "CandidateProfileRepository", lambda _db: _FakeCandidateProfileRepo(0)), \
             patch.object(candidate_service, "_fallback_stored_candidates", return_value=[]), \
             self._patch_noop_observability():
            results = candidate_service._finalize_candidate_sourcing_state(
                db=object(),
                jobs=jobs,
                job=job,
                previous_status="processing",
                source="local",
                reason="no_candidates_after_filter",
                local_count=0,
                pdl_count=0,
                swiped_ids=frozenset(),
                run_type="standard",
                recruiter_id=None,
                combined_run_metrics={},
            )

        self.assertEqual(results, [])
        self.assertEqual(jobs.calls[-1][1], "no_candidates")

    def test_profiles_present_marks_active_and_triggers_outreach(self) -> None:
        jobs = _FakeJobsRepo()
        job = SimpleNamespace(id="job-2", job_status="processing")
        sentinel_candidates = [SimpleNamespace(id="candidate-1"), SimpleNamespace(id="candidate-2")]

        with patch.object(candidate_service, "CandidateProfileRepository", lambda _db: _FakeCandidateProfileRepo(2)), \
             patch.object(candidate_service, "_fallback_stored_candidates", return_value=sentinel_candidates), \
             self._patch_noop_observability():
            results = candidate_service._finalize_candidate_sourcing_state(
                db=object(),
                jobs=jobs,
                job=job,
                previous_status="processing",
                source="pdl",
                reason="pdl_empty_or_filtered",
                local_count=0,
                pdl_count=0,
                swiped_ids=frozenset(),
                run_type="standard",
                recruiter_id="recruiter-1",
                combined_run_metrics={},
            )

        self.assertEqual(results, sentinel_candidates)
        self.assertEqual(jobs.calls[-1][1], "active")

    def test_profiles_present_never_return_no_candidates(self) -> None:
        jobs = _FakeJobsRepo()
        job = SimpleNamespace(id="job-3", job_status="processing")

        with patch.object(candidate_service, "CandidateProfileRepository", lambda _db: _FakeCandidateProfileRepo(5)), \
             patch.object(candidate_service, "_fallback_stored_candidates", return_value=[]), \
             self._patch_noop_observability():
            results = candidate_service._finalize_candidate_sourcing_state(
                db=object(),
                jobs=jobs,
                job=job,
                previous_status="processing",
                source="local",
                reason="no_candidates_after_filter",
                local_count=0,
                pdl_count=0,
                swiped_ids=frozenset(),
                run_type="standard",
                recruiter_id=None,
                combined_run_metrics={},
            )

        self.assertEqual(results, [])
        self.assertNotEqual(jobs.calls[-1][1], "no_candidates")


if __name__ == "__main__":
    unittest.main()
