from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_recruiter_questions.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.recruiter_question_service import generate_recruiter_questions


class RecruiterQuestionServiceTests(unittest.TestCase):
    def test_generate_recruiter_questions_uses_groq_output(self) -> None:
        job = SimpleNamespace(
            title="Backend Engineer",
            description="Build APIs and systems.",
            location="Remote",
            compensation="Competitive",
            work_authorization="required",
            experienceRequired="5+ years",
            skills=["Python", "FastAPI"],
            responsibilities=["API development"],
        )

        llm_payload = {
            "questions": [
                "How important is startup experience?",
                "What kind of candidate background should we bias toward?",
                "Are there any must-have skills we should treat as non-negotiable?",
            ]
        }

        with patch("app.services.recruiter_question_service.generate", return_value=llm_payload) as mock_generate:
            questions = generate_recruiter_questions(gap_analysis={}, job=job, voice_summary="", max_questions=3)

        self.assertEqual(questions, llm_payload["questions"])
        self.assertTrue(mock_generate.called)

    def test_generate_recruiter_questions_falls_back_when_llm_returns_no_questions(self) -> None:
        job = SimpleNamespace(
            title="Backend Engineer",
            description="Build APIs and systems.",
            location="Remote",
            compensation="Competitive",
            work_authorization="required",
            experienceRequired="5+ years",
            skills=["Python", "FastAPI"],
            responsibilities=["API development"],
        )

        gap_analysis = {
            "missing_fields": [],
            "ambiguous_fields": [],
            "missing_preferences": ["startup", "seniority"],
            "confidence_scores": {"startup": 0.1, "seniority": 0.2},
        }

        with patch("app.services.recruiter_question_service.generate", return_value={}):
            questions = generate_recruiter_questions(gap_analysis=gap_analysis, job=job, voice_summary="", max_questions=3)

        self.assertGreaterEqual(len(questions), 1)
        self.assertEqual(questions[0], "How important is startup experience?")

