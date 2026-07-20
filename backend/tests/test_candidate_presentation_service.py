from __future__ import annotations

import unittest

from app.services.candidate_presentation_service import build_candidate_view_model


class CandidatePresentationServiceTests(unittest.TestCase):
    def test_build_candidate_view_model_humanizes_headline_snippet(self) -> None:
        candidate = {
            "id": "cand-1",
            "name": "Kopparthi Amulya",
            "role": "Python Backend Developer",
            "company": "FastAPI",
            "location": "",
            "skills": ["Python", "FastAPI", "PostgreSQL", "SQL"],
            "summary": "Python Backend Developer | FastAPI | REST API Development | SQLAlchemy | MySQL | JWT Authentication | Backend System Design | Open to Backend Developer Roles.",
        }

        vm = build_candidate_view_model(candidate)

        self.assertIn("Kopparthi", vm["recruiter_summary"])
        self.assertNotIn("Python Backend Developer | FastAPI | REST API Development", vm["recruiter_summary"])
        self.assertGreaterEqual(len(vm["summary_lines"]), 1)
        self.assertLessEqual(len(vm["summary_lines"]), 4)

    def test_build_candidate_view_model_ignores_linkedin_noise(self) -> None:
        candidate = {
            "id": "cand-2",
            "name": "Arjun Magar",
            "role": "Sales Executive",
            "company": "Salesforce",
            "location": "Hyderabad",
            "skills": [],
            "summary": "Sales Executive at Salesforce · Experience: Salesforce · Location: Hyderabad. View Arjun Magar's profile on LinkedIn, a professional community of 1 billion.",
        }

        vm = build_candidate_view_model(candidate)

        self.assertIn("Arjun", vm["recruiter_summary"])
        self.assertIn("Sales Executive", vm["recruiter_summary"])
        self.assertIn("Salesforce", vm["recruiter_summary"])
        self.assertIn("Hyderabad", vm["recruiter_summary"])
        self.assertNotIn("View Arjun Magar's profile on LinkedIn", vm["recruiter_summary"])
        self.assertNotIn("professional community of 1 billion", vm["recruiter_summary"])
