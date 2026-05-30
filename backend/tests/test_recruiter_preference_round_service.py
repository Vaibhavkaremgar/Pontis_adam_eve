from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_recruiter_preference_round.db")

from app.services.recruiter_preference_round_service import _experience_band_from_sources  # noqa: E402


class RecruiterPreferenceRoundServiceTests(unittest.TestCase):
    def test_experience_band_is_derived_from_actual_source_text(self) -> None:
        job = SimpleNamespace(
            title="Senior Platform Engineer",
            experience_level="7-10 years",
            description="Build reliable platform services.",
            structured_data={
                "experience_level": "7-10 years",
                "role": "Senior Platform Engineer",
            },
        )

        band, midpoint = _experience_band_from_sources(
            job=job,
            voice_summary="We need someone with 7-10 years of platform experience.",
            gap_analysis={"summary": "Looking for 7-10 years on backend platform work."},
            intent_profile={"average_experience_years": 8},
        )

        self.assertEqual(band, "7-10 years")
        self.assertGreater(midpoint, 0.0)

    def test_experience_band_returns_empty_when_no_evidence_exists(self) -> None:
        job = SimpleNamespace(title="Backend Engineer", experience_level="", description="", structured_data={})

        band, midpoint = _experience_band_from_sources(
            job=job,
            voice_summary="",
            gap_analysis={},
            intent_profile={},
        )

        self.assertEqual(band, "")
        self.assertEqual(midpoint, 0.0)


if __name__ == "__main__":
    unittest.main()
