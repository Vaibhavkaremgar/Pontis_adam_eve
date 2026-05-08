from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_resume_ingestion.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.resume_ingestion_service import ResumeStructuredProfile, build_internal_candidate_payload, parse_resume_profile
from app.db.repositories import InternalCandidateResumeRepository


class ResumeIngestionTests(unittest.TestCase):
    def test_parse_resume_profile_normalizes_llm_json(self) -> None:
        fake_llm_output = {
            "full_name": "  Priya Sharma ",
            "headline": "Senior Backend Engineer",
            "years_experience": "7+ years",
            "skills": ["Python", "FastAPI", "Python"],
            "companies": ["Pontis"],
            "education": ["B.Tech"],
            "projects": ["Retrieval system"],
            "certifications": ["AWS"],
            "location": "  Bengaluru, India ",
            "summary": "Built candidate systems.",
            "domain_experience": ["Recruiting"],
        }
        with patch("app.services.resume_ingestion_service.generate", lambda *_args, **_kwargs: fake_llm_output):
            profile = parse_resume_profile(resume_text="sample resume text", file_name="resume.pdf")

        self.assertIsInstance(profile, ResumeStructuredProfile)
        self.assertEqual(profile.full_name, "Priya Sharma")
        self.assertEqual(profile.headline, "Senior Backend Engineer")
        self.assertEqual(profile.years_experience, 7.0)
        self.assertEqual(profile.skills, ["Python", "FastAPI"])
        self.assertEqual(profile.location, "Bengaluru, India")

    def test_internal_candidate_payload_uses_stable_identifier(self) -> None:
        profile = ResumeStructuredProfile(
            full_name="Priya Sharma",
            headline="Senior Backend Engineer",
            years_experience=7,
            skills=["Python", "FastAPI"],
            companies=["Pontis"],
            education=["B.Tech"],
            projects=["Retrieval system"],
            certifications=["AWS"],
            location="Bengaluru, India",
            summary="Built candidate systems.",
            domain_experience=["Recruiting"],
        )

        payload = build_internal_candidate_payload(
            profile=profile,
            resume_text="sample resume text",
            file_name="resume.pdf",
            source_path="backend/resumes/resume.pdf",
            resume_fingerprint="abc123",
        )

        self.assertEqual(payload["candidate_id"], payload["qdrant_point_id"])
        self.assertEqual(payload["role"], "Senior Backend Engineer")
        self.assertEqual(payload["company"], "Pontis")
        self.assertEqual(payload["embedding_version"], "v2_structured")

    def test_repository_upsert_accepts_rich_payload(self) -> None:
        class _FakeNested:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class _FakeSession:
            def __init__(self):
                self.added = []
                self.flushed = 0

            def scalar(self, _stmt):
                return None

            def begin_nested(self):
                return _FakeNested()

            def add(self, row):
                self.added.append(row)

            def flush(self):
                self.flushed += 1

        session = _FakeSession()
        repo = InternalCandidateResumeRepository(session)

        row = repo.upsert(
            candidate_id="candidate-123",
            resume_fingerprint="fingerprint-abc",
            source_filename="resume.pdf",
            source_path="backend/resumes/resume.pdf",
            source_metadata={"source": "backend/resumes"},
            full_name="Priya Sharma",
            headline="Senior Backend Engineer",
            role="Senior Backend Engineer",
            rolePattern="senior backend engineer",
            years_experience=7,
            skills=["Python", "FastAPI"],
            companies=["Pontis"],
            education=["B.Tech"],
            projects=["Retrieval system"],
            certifications=["AWS"],
            location="Bengaluru, India",
            summary="Built candidate systems.",
            domain_experience=["Recruiting"],
            raw_resume_text="resume text",
            parsed_data={"foo": "bar"},
            embedding_version="v2_structured",
            vector_version="v2_structured",
            qdrant_point_id="candidate-123",
            sourceType="internal_resume",
        )

        self.assertEqual(row.candidate_id, "candidate-123")
        self.assertEqual(row.headline, "Senior Backend Engineer")
        self.assertEqual(row.summary, "Built candidate systems.")
        self.assertEqual(row.parsed_data["rolePattern"], "senior backend engineer")
        self.assertEqual(row.parsed_data["sourceType"], "internal_resume")
        self.assertGreaterEqual(session.flushed, 1)


if __name__ == "__main__":
    unittest.main()
