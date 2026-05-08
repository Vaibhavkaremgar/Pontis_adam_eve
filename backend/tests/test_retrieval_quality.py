from __future__ import annotations

import os
import types
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_retrieval.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

from app.services.retrieval_quality_service import hybrid_retrieval_score, rerank_candidates


class RetrievalQualityTests(unittest.TestCase):
    def test_lexical_signal_can_rescue_niche_skill_match(self) -> None:
        job = types.SimpleNamespace(
            title="Platform Engineer",
            description="Looking for Postgres CDC and Redis queue expertise.",
            location="Remote",
            experience_level="5+ years",
            skills_required=["Postgres", "Redis", "CDC"],
            responsibilities=["operate queues", "migrate data"],
        )
        candidate = {
            "name": "Avery",
            "role": "Infrastructure Engineer",
            "company": "Northstar",
            "summary": "Built change data capture pipelines with Postgres and Redis backed queues.",
            "skills": ["Go", "Postgres", "Redis", "CDC"],
        }

        attribution = hybrid_retrieval_score(job=job, candidate=candidate, vector_score=0.2)
        self.assertGreater(attribution.lexical_score, 0.2)
        self.assertGreater(attribution.hybrid_score, attribution.vector_score)

    def test_rerank_candidates_orders_by_hybrid_score(self) -> None:
        job = types.SimpleNamespace(
            title="Machine Learning Engineer",
            description="Ranking, embeddings, and retrieval.",
            location="Remote",
            experience_level="3+ years",
            skills_required=["Python", "Embeddings"],
            responsibilities=[],
        )
        rows = [
            {"candidate_id": "vector-first", "semantic": 0.9, "payload": {"name": "A", "skills": ["JavaScript"]}},
            {"candidate_id": "lexical-fit", "semantic": 0.1, "payload": {"name": "B", "skills": ["Python", "Embeddings"]}},
        ]

        ranked = rerank_candidates(job=job, rows=rows)
        self.assertEqual(ranked[0]["candidate_id"], "lexical-fit")


if __name__ == "__main__":
    unittest.main()
