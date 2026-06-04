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
    build_linkedin_xray_query_layers,
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

        self.assertEqual(len(queries), 6)
        self.assertEqual(len({query.lower() for query in queries}), 6)
        self.assertTrue(all("site:linkedin.com/in" in query for query in queries))
        self.assertTrue(all("linkedin.com/company" in query.lower() for query in queries))
        self.assertTrue(all("view profile" in query.lower() for query in queries))
        self.assertTrue(all("about" in query.lower() or "experience" in query.lower() or "skills" in query.lower() for query in queries))
        self.assertTrue(any("backend" in query.lower() and "engineer" in query.lower() for query in queries))
        self.assertTrue(any("python" in query.lower() and "fastapi" in query.lower() for query in queries))
        self.assertTrue(any("aws" in query.lower() for query in queries))
        self.assertTrue(any("san francisco" in query.lower() for query in queries))

    def test_build_linkedin_query_layers_uses_selected_archetypes_and_keeps_six_queries(self) -> None:
        layers = build_linkedin_xray_query_layers(
            role="Backend Engineer",
            seniority="Senior",
            skills=["Python", "FastAPI", "MongoDB"],
            location="Hyderabad",
            company_stage="Series A",
            hiring_preferences="startup ownership",
            industry="platform",
            leadership_expectations="technical leadership",
            job_description="Build backend APIs and integrations for internal tooling.",
            voice_summary="Candidate should have strong REST API experience.",
            voice_transcript="We need someone who can own microservices and integrations.",
            selected_archetypes=[
                {
                    "profile_title": "Backend API Engineer",
                    "preferred_project_type": "API integrations",
                    "core_skills": ["Python", "FastAPI", "MongoDB"],
                    "experience_range": "7-10 years",
                }
            ],
        )

        self.assertEqual(len(layers), 6)
        self.assertEqual(len({layer.query.lower() for layer in layers}), 6)
        self.assertTrue(any("Backend API Engineer" in layer.query or "backend engineer" in layer.query.lower() for layer in layers))
        self.assertTrue(any("hyderabad" in layer.query.lower() for layer in layers))
        self.assertTrue(any("hyderabad" not in layer.query.lower() for layer in layers))
        self.assertTrue(any("linkedin.com/company" in layer.query.lower() for layer in layers))
        self.assertTrue(any("python" in layer.query.lower() or "mongodb" in layer.query.lower() for layer in layers))

    def test_build_linkedin_query_layers_strips_raw_archetype_text_and_stays_short(self) -> None:
        layers = build_linkedin_xray_query_layers(
            role="SaaS Sales Executive",
            seniority="Mid-level",
            skills=["SaaS sales", "pipeline management", "CRM"],
            location="Hyderabad",
            company_stage="Series A",
            hiring_preferences="startup ownership",
            industry="b2b software",
            leadership_expectations="quota ownership",
            selected_archetypes=[
                {
                    "profile_title": "The Enterprise Hunter",
                    "signal_keywords": [
                        "enterprise sales",
                        "C-suite",
                        "quota attainment",
                        "SaaS",
                    ],
                    "summary": "Preferred skills: enterprise sales | C-suite | quota attainment",
                    "typical_background": "Preferred roles: enterprise seller | senior AE | account manager",
                    "core_skills": ["enterprise sales", "quota attainment", "pipeline management"],
                    "query_bias": "precision",
                }
            ],
        )

        self.assertEqual(len(layers), 6)
        for layer in layers:
            self.assertNotIn("|", layer.query)
            self.assertNotIn("Preferred skills", layer.query)
            self.assertNotIn("Preferred roles", layer.query)
            self.assertNotIn("Technical Strengths", layer.query)
            self.assertLessEqual(layer.query.upper().count(" AND ") + 1, 12)
        self.assertTrue(any("hyderabad" in layer.query.lower() for layer in layers))
        self.assertTrue(any("hyderabad" not in layer.query.lower() for layer in layers))

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
            side_effect=[raw_results, [], [], [], [], []],
        ):
            candidates = discover_linkedin_xray_candidates(job=job, intake=job.structured_data, limit=5)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["linkedin_url"], "https://www.linkedin.com/in/janedoe")
        self.assertEqual(candidate["full_name"], "Jane Doe")
        self.assertIn("Python", candidate["skills"])
        self.assertGreater(candidate["score"], 0.0)
        self.assertEqual(candidate["source_type"], "xray")

    @patch("app.services.serpapi_sourcing_service.is_serpapi_disabled", return_value=False)
    def test_discovery_extracts_clean_company_role_and_location_from_sentence_snippets(self, _mock_disabled: object) -> None:
        job = SimpleNamespace(
            title="Software Engineer",
            location="Bengaluru",
            experience_level="Mid",
            skills_required=["Python", "FastAPI"],
            structured_data={
                "role": "Software Engineer",
                "location": "Bengaluru",
                "experience_level": "Mid",
                "skills": ["Python", "FastAPI"],
            },
        )
        raw_results = [
            {
                "title": "Riya Sharma - LinkedIn",
                "link": "https://www.linkedin.com/in/riya-sharma/",
                "snippet": "I am working at so and so company as a software developer in Bengaluru. Python, FastAPI, AWS.",
                "displayed_link": "linkedin.com/in/riya-sharma",
            }
        ]

        with patch("app.services.serpapi_sourcing_service.SERPAPI_API_KEY", "test-serpapi-key"), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_ENABLED", True
        ), patch.object(
            SerpApiClient,
            "search",
            side_effect=[raw_results, [], [], [], [], []],
        ):
            candidates = discover_linkedin_xray_candidates(job=job, intake=job.structured_data, limit=5)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["full_name"], "Riya Sharma")
        self.assertEqual(candidate["role"], "software developer")
        self.assertEqual(candidate["company"], "so and so company")
        self.assertEqual(candidate["location"], "Bengaluru")
        self.assertEqual(candidate["current_company"], "so and so company")

    @patch("app.services.serpapi_sourcing_service.is_serpapi_disabled", return_value=False)
    def test_discovery_supports_archetype_ids_after_selection(self, _mock_disabled: object) -> None:
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

        with patch("app.services.serpapi_sourcing_service.SERPAPI_API_KEY", "test-serpapi-key"), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_ENABLED", True
        ), patch.object(
            SerpApiClient,
            "search",
            side_effect=[[], [], [], [], [], []],
        ):
            candidates = discover_linkedin_xray_candidates(
                job=job,
                intake=job.structured_data,
                limit=5,
                recruiter_preferences={"preferredTechnicalStrengths": ["Python"]},
                archetype_ids=["archetype-1", "archetype-2"],
            )

        self.assertEqual(candidates, [])

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
        ), patch(
            "app.services.serpapi_sourcing_service.SERPAPI_RESULTS_PER_PAGE", 1
        ), patch.object(
            client._session, "get", side_effect=fake_get
        ):
            results = client.search(query='site:linkedin.com/in/ "Backend Engineer"', pages=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(seen_starts, [0, 1])


if __name__ == "__main__":
    unittest.main()
