from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_serpapi_sourcing.db")
os.environ.setdefault("SERPAPI_API_KEY", "test-serpapi-key")
os.environ.setdefault("SERPAPI_ENABLED", "true")

from app.services.serpapi_sourcing_service import (  # noqa: E402
    SerpApiClient,
    build_linkedin_xray_queries,
    discover_linkedin_xray_candidates,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class SerpApiSourcingTests(unittest.TestCase):
    def test_build_linkedin_queries_combines_role_skills_location_and_stage(self) -> None:
        queries = build_linkedin_xray_queries(
            role="Senior Backend Engineer",
            seniority="Senior",
            skills=["Python", "FastAPI", "AWS", "Kubernetes"],
            location="San Francisco",
            company_stage="Series A",
            hiring_preferences="startup ownership",
            industry="fintech",
            leadership_expectations="technical leadership",
        )

        self.assertEqual(len(queries), 1)
        query = queries[0]
        self.assertIn("site:linkedin.com/in/", query)
        self.assertIn("Senior Backend Engineer", query)
        self.assertIn("Python", query)
        self.assertIn("AWS", query)
        self.assertIn("San Francisco", query)
        self.assertTrue("Series A" in query or "Series A".lower() in query.lower())
        self.assertIn("fintech", query.lower())

    @patch("app.services.serpapi_sourcing_service.is_serpapi_disabled", return_value=False)
    def test_discovery_normalizes_and_dedupes_linkedin_results(self, _mock_disabled: object) -> None:
        job = SimpleNamespace(
            title="Senior Backend Engineer",
            location="San Francisco",
            experience_level="Senior",
            skills_required=["Python", "FastAPI", "AWS"],
            structured_data={
                "role": "Senior Backend Engineer",
                "location": "San Francisco",
                "experience_level": "Senior",
                "skills": ["Python", "FastAPI", "AWS"],
                "company_stage": "Series A",
            },
        )
        raw_results = [
            {
                "title": "Jane Doe - LinkedIn",
                "link": "https://www.linkedin.com/in/janedoe/",
                "snippet": "Senior Backend Engineer at Acme. San Francisco. Python, FastAPI, AWS.",
                "displayed_link": "linkedin.com/in/janedoe",
            },
            {
                "title": "Jane Doe - LinkedIn",
                "link": "https://www.linkedin.com/in/janedoe/",
                "snippet": "Duplicate profile result",
                "displayed_link": "linkedin.com/in/janedoe",
            },
        ]

        with patch("app.services.serpapi_sourcing_service.SERPAPI_API_KEY", "test-serpapi-key"), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_ENABLED", True
        ), patch.object(
            SerpApiClient,
            "search",
            side_effect=[raw_results, [], [], []],
        ):
            candidates = discover_linkedin_xray_candidates(job=job, intake=job.structured_data, limit=5)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["linkedin_url"], "https://www.linkedin.com/in/janedoe")
        self.assertEqual(candidate["full_name"], "Jane Doe")
        self.assertIn("Python", candidate["skills"])
        self.assertGreater(candidate["score"], 0.0)
        self.assertEqual(candidate["source_type"], "linkedin_xray")

    def test_client_retries_after_rate_limit(self) -> None:
        client = SerpApiClient()
        payload = {"organic_results": [{"title": "Jane Doe", "link": "https://www.linkedin.com/in/janedoe/"}]}
        responses = [_FakeResponse(429, {}), _FakeResponse(200, payload)]
        seen_starts: list[int] = []

        def fake_get(url: str, params: dict, timeout: int) -> _FakeResponse:
            seen_starts.append(int(params.get("start") or 0))
            return responses.pop(0)

        with patch("app.services.serpapi_sourcing_service.SERPAPI_API_KEY", "test-serpapi-key"), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_ENABLED", True
        ), patch("app.services.serpapi_sourcing_service.SERPAPI_MIN_REQUEST_INTERVAL_SECONDS", 0.0), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_RETRY_ATTEMPTS", 2
        ), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_REQUEST_TIMEOUT_SECONDS", 1
        ), patch.object(
            client._session, "get", side_effect=fake_get
        ):
            results = client.search(query='site:linkedin.com/in/ "Senior Backend Engineer"', pages=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(seen_starts, [0, 0])

    def test_client_supports_pagination_offsets(self) -> None:
        client = SerpApiClient()
        payload_page_1 = {"organic_results": [{"title": "Jane Doe", "link": "https://www.linkedin.com/in/janedoe/"}]}
        payload_page_2 = {"organic_results": [{"title": "John Smith", "link": "https://www.linkedin.com/in/johnsmith/"}]}
        responses = [_FakeResponse(200, payload_page_1), _FakeResponse(200, payload_page_2)]
        seen_starts: list[int] = []

        def fake_get(url: str, params: dict, timeout: int) -> _FakeResponse:
            seen_starts.append(int(params.get("start") or 0))
            return responses.pop(0)

        with patch("app.services.serpapi_sourcing_service.SERPAPI_API_KEY", "test-serpapi-key"), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_ENABLED", True
        ), patch("app.services.serpapi_sourcing_service.SERPAPI_MIN_REQUEST_INTERVAL_SECONDS", 0.0), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_RETRY_ATTEMPTS", 1
        ), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_REQUEST_TIMEOUT_SECONDS", 1
        ), patch.object(
            client._session, "get", side_effect=fake_get
        ):
            results = client.search(query='site:linkedin.com/in/ "Backend Engineer"', pages=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(seen_starts, [0, 10])


if __name__ == "__main__":
    unittest.main()
