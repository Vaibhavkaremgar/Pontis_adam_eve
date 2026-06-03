from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_recruiter_preference_round.db")

from app.services.recruiter_preference_round_service import (  # noqa: E402
    _build_fallback_archetype_entries,
    _generate_archetype_sets,
)


class RecruiterPreferenceRoundServiceTests(unittest.TestCase):
    def test_fallback_persona_cards_are_role_specific_for_sales_roles(self) -> None:
        job = SimpleNamespace(
            title="SaaS Sales Executive",
            description="Sell SaaS to enterprise accounts and grow pipeline.",
            location="Hyderabad",
            experience_level="Mid-level",
            company_stage="Growth stage",
            skills_required=["SaaS sales", "pipeline management", "CRM"],
            nice_to_have_skills=["Salesforce", "cold calling"],
            voice_summary="MBA preferred for the hire.",
        )

        entries = _build_fallback_archetype_entries(job=job, voice_summary="MBA preferred for the hire.")

        self.assertEqual(len(entries), 6)
        current_roles = [entry["current_role"] for entry in entries]
        highlights = [entry["career_highlight"] for entry in entries]
        works_best_at = [entry["works_best_at"] for entry in entries]
        educations = [entry["education"] for entry in entries]
        self.assertEqual(len(set(current_roles)), 6)
        self.assertEqual(len(set(highlights)), 6)
        self.assertEqual(len(set(works_best_at)), 6)
        self.assertTrue(all(len(entry["signal_keywords"]) == 3 for entry in entries))
        self.assertTrue(all(entry["location"] == "Hyderabad" for entry in entries))
        self.assertTrue(any("MBA" in education for education in educations))
        self.assertIn("Account Executive", current_roles[0])
        self.assertIn("Senior Account Executive", current_roles[1])

    @patch("app.services.recruiter_preference_round_service.generate")
    def test_generate_archetype_sets_retries_once_on_duplicate_descriptions(self, mock_generate: object) -> None:
        duplicate_payload = [
            {
                "current_role": "Account Executive",
                "career_highlight": "Closed $420K ARR from inbound SaaS leads.",
                "works_best_at": "Fast inbound cycles with quick follow-up.",
                "signal_keywords": ["inbound", "pipeline", "qualification"],
                "query_bias": "recall",
                "years_experience": "3 years",
                "current_company": "Growth stage B2B SaaS startup",
                "location": "Hyderabad",
                "top_skills": ["SaaS sales", "CRM", "pipeline management"],
                "education": "MBA, IIM Hyderabad",
            },
            {
                "current_role": "Senior Account Executive",
                "career_highlight": "Closed $420K ARR from inbound SaaS leads.",
                "works_best_at": "Enterprise conversations with multi-threaded buyers.",
                "signal_keywords": ["enterprise", "quota", "C-suite"],
                "query_bias": "balanced",
                "years_experience": "5 years",
                "current_company": "Growth stage mid-size SaaS company",
                "location": "Hyderabad",
                "top_skills": ["pipeline management", "CRM", "Salesforce"],
                "education": "BBA, NMIMS Mumbai",
            },
            {
                "current_role": "Revenue Executive",
                "career_highlight": "Converted 1,200 product trials into 84 opportunities.",
                "works_best_at": "Product-led growth motions with usage signals.",
                "signal_keywords": ["product-led", "conversion", "demo"],
                "query_bias": "balanced",
                "years_experience": "4 years",
                "current_company": "Growth stage product-led SaaS company",
                "location": "Hyderabad",
                "top_skills": ["CRM", "SaaS sales", "pipeline management"],
                "education": "B.Com, St. Xavier's College",
            },
            {
                "current_role": "Account Manager",
                "career_highlight": "Grew net revenue retention to 132% across 32 accounts.",
                "works_best_at": "Expansion-heavy books with renewal pressure.",
                "signal_keywords": ["renewals", "expansion", "accounts"],
                "query_bias": "precision",
                "years_experience": "6 years",
                "current_company": "Growth stage enterprise software company",
                "location": "Hyderabad",
                "top_skills": ["CRM", "Salesforce", "cold calling"],
                "education": "MBA, MICA Ahmedabad",
            },
            {
                "current_role": "Business Development Manager",
                "career_highlight": "Booked 180 meetings and earned AE promotion in 14 months.",
                "works_best_at": "Startup teams that need prospecting discipline.",
                "signal_keywords": ["outbound", "prospecting", "meetings"],
                "query_bias": "recall",
                "years_experience": "3 years",
                "current_company": "Growth stage high-growth startup",
                "location": "Hyderabad",
                "top_skills": ["cold calling", "CRM", "SaaS sales"],
                "education": "B.Tech, VIT Vellore",
            },
            {
                "current_role": "Solutions Sales Executive",
                "career_highlight": "Ran 22 technical demos and closed 5 implementation-heavy deals.",
                "works_best_at": "Complex product evaluations with technical credibility.",
                "signal_keywords": ["solutions", "technical", "demo"],
                "query_bias": "precision",
                "years_experience": "5 years",
                "current_company": "Growth stage complex-product SaaS company",
                "location": "Hyderabad",
                "top_skills": ["pipeline management", "CRM", "Salesforce"],
                "education": "PGDM, SPJIMR Mumbai",
            },
        ]
        unique_payload = [
            dict(item, career_highlight=f"{item['career_highlight']} (unique)") for item in duplicate_payload
        ]
        mock_generate.side_effect = [duplicate_payload, unique_payload]

        job = SimpleNamespace(
            title="SaaS Sales Executive",
            description="Sell SaaS to enterprise accounts and grow pipeline.",
            location="Hyderabad",
            experience_level="Mid-level",
            company_stage="Growth stage",
            skills_required=["SaaS sales", "pipeline management", "CRM"],
            nice_to_have_skills=["Salesforce", "cold calling"],
        )

        sets = _generate_archetype_sets(
            job=job,
            voice_summary="Need a close-focused seller with enterprise experience.",
            voice_transcript="Need a close-focused seller with enterprise experience.",
            gap_analysis={"missing_fields": [], "ambiguous_fields": []},
            intent_profile={"required_skills": ["SaaS sales", "pipeline management", "CRM"], "preferred_skills": ["enterprise sales"]},
        )

        self.assertEqual(mock_generate.call_count, 2)
        self.assertEqual(len(sets), 3)
        flattened_roles = [archetype["current_role"] for group in sets for archetype in group["archetypes"]]
        self.assertIn("Account Executive", flattened_roles[0])
        self.assertIn("Solutions Sales Executive", flattened_roles)


if __name__ == "__main__":
    unittest.main()
