from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from app.schemas.job import JobInput
from app.services.internal_candidate_semantic_service import _filter_candidate_rows_by_opportunity_type


def _job_input(opportunity_type: str) -> dict:
    return {
        "jobId": "REQ-1",
        "opportunityType": opportunity_type,
        "title": "Backend Engineer",
        "description": "Build production APIs",
        "location": "Remote",
        "compensation": "",
        "workAuthorization": "required",
    }


class OpportunityTypeMatchingTests(unittest.TestCase):
    def test_full_time_value_is_stored_as_jobs(self) -> None:
        self.assertEqual(JobInput(**_job_input("jobs")).opportunityType, "jobs")

    def test_intern_value_is_stored_as_intern(self) -> None:
        self.assertEqual(JobInput(**_job_input("intern")).opportunityType, "intern")

    def test_unknown_value_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JobInput(**_job_input("full-time"))

    def test_full_time_pool_excludes_intern_candidates(self) -> None:
        rows = [SimpleNamespace(id="job", opportunity_type="jobs"), SimpleNamespace(id="intern", opportunity_type="intern")]
        self.assertEqual([row.id for row in _filter_candidate_rows_by_opportunity_type(rows, "jobs")], ["job"])

    def test_intern_pool_excludes_full_time_candidates(self) -> None:
        rows = [SimpleNamespace(id="job", opportunity_type="jobs"), SimpleNamespace(id="intern", opportunity_type="intern")]
        self.assertEqual([row.id for row in _filter_candidate_rows_by_opportunity_type(rows, "intern")], ["intern"])

    def test_mismatched_only_pool_returns_no_candidates(self) -> None:
        full_time_only = [SimpleNamespace(id="job", opportunity_type="jobs")]
        intern_only = [SimpleNamespace(id="intern", opportunity_type="intern")]
        self.assertEqual(_filter_candidate_rows_by_opportunity_type(full_time_only, "intern"), [])
        self.assertEqual(_filter_candidate_rows_by_opportunity_type(intern_only, "jobs"), [])

    def test_filter_preserves_existing_semantic_order(self) -> None:
        rows = [
            SimpleNamespace(id="high", opportunity_type="intern", score=0.91),
            SimpleNamespace(id="wrong-pool", opportunity_type="jobs", score=0.99),
            SimpleNamespace(id="lower", opportunity_type="intern", score=0.72),
        ]
        filtered = _filter_candidate_rows_by_opportunity_type(rows, "intern")
        self.assertEqual([(row.id, row.score) for row in filtered], [("high", 0.91), ("lower", 0.72)])


if __name__ == "__main__":
    unittest.main()
