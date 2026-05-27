from __future__ import annotations

"""Backward-compatible shim for legacy imports.

The enrichment provider has been replaced with Apify. Keep this module as a
thin adapter so older imports continue to work while the codebase migrates.
"""

from app.services.apify_enrichment_service import (
    apify_health_snapshot as apollo_health_snapshot,
    enrich_candidate_with_apify as enrich_candidate_with_apollo,
    enrich_selected_candidate,
)

__all__ = [
    "apollo_health_snapshot",
    "enrich_candidate_with_apollo",
    "enrich_selected_candidate",
]
