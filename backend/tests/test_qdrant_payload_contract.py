"""
Fix #2 — Qdrant candidate payload / semantic matching mismatch.

Verifies:
1. upsert_internal_candidate_embeddings stores the correct payload fields.
2. skillTokens and rolePattern are NOT in the payload (they were never stored).
3. _metadata_filter never emits a FieldCondition referencing skillTokens or rolePattern.
4. Agency isolation (agencyId must-filter) is preserved.
5. embeddingVersion must-filter is preserved.
6. preferredSkills / preferredRoles in metadata_filters are silently ignored
   (no KeyError, no filter on absent fields).
7. QDRANT_SCHEMA for INTERNAL_CANDIDATE_COLLECTION_NAME has no skillTokens/rolePattern index.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from app.services.qdrant_service import (
    INTERNAL_CANDIDATE_COLLECTION_NAME,
    QDRANT_SCHEMA,
    _metadata_filter,
    upsert_internal_candidate_embeddings,
)


# ── 1. Payload contract ───────────────────────────────────────────────────────

def test_upsert_stores_required_payload_fields(monkeypatch):
    captured = []

    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True
    mock_client.upsert.side_effect = lambda **kwargs: captured.extend(kwargs["points"])

    monkeypatch.setattr("app.services.qdrant_service._client", mock_client)
    monkeypatch.setattr("app.services.qdrant_service._client_disabled", False)

    upsert_internal_candidate_embeddings([{
        "candidateRecordId": "record-1",
        "candidateId": "candidate-1",
        "agencyId": "agency-1",
        "embeddingVersion": "v1",
        "textHash": "abc123",
        "indexedAt": "2024-01-01T00:00:00+00:00",
        "resumeFingerprint": "fp-1",
        "vector": [0.1, 0.2, 0.3],
    }])

    assert len(captured) == 1
    payload = captured[0].payload

    # Required fields must be present
    assert payload["candidateId"] == "candidate-1"
    assert payload["candidateRecordId"] == "record-1"
    assert payload["agencyId"] == "agency-1"
    assert payload["embeddingVersion"] == "v1"
    assert payload["textHash"] == "abc123"
    assert payload["resumeFingerprint"] == "fp-1"
    assert payload["sourceType"] == "internal"

    # skillTokens and rolePattern must NOT be in the payload
    assert "skillTokens" not in payload
    assert "rolePattern" not in payload


# ── 2. Schema has no stale indexes ───────────────────────────────────────────

def test_internal_candidate_schema_has_no_skill_tokens_or_role_pattern_index():
    indexes = QDRANT_SCHEMA[INTERNAL_CANDIDATE_COLLECTION_NAME].get("indexes", {})
    assert "skillTokens" not in indexes, "skillTokens index must be removed from schema"
    assert "rolePattern" not in indexes, "rolePattern index must be removed from schema"


def test_candidate_collection_schema_has_no_skill_tokens_or_role_pattern_index():
    from app.core.config import CANDIDATE_COLLECTION_NAME
    indexes = QDRANT_SCHEMA[CANDIDATE_COLLECTION_NAME].get("indexes", {})
    assert "skillTokens" not in indexes
    assert "rolePattern" not in indexes


# ── 3. _metadata_filter never references absent payload fields ────────────────

def _condition_keys(f) -> list[str]:
    """Collect all FieldCondition key values from a Filter."""
    keys = []
    if f is None:
        return keys
    for cond in (f.must or []) + (f.should or []):
        if hasattr(cond, "key"):
            keys.append(cond.key)
    return keys


def test_metadata_filter_with_agency_and_version_only():
    f = _metadata_filter({"agencyId": "agency-1", "embeddingVersion": "v1"})
    assert f is not None
    keys = _condition_keys(f)
    assert "agencyId" in keys
    assert "embeddingVersion" in keys
    assert "skillTokens" not in keys
    assert "rolePattern" not in keys


def test_metadata_filter_ignores_preferred_skills_and_roles():
    """preferredSkills/preferredRoles must not produce a filter on absent fields."""
    f = _metadata_filter({
        "agencyId": "agency-1",
        "embeddingVersion": "v1",
        "preferredSkills": ["python", "fastapi"],
        "preferredRoles": ["backend engineer"],
    })
    assert f is not None
    keys = _condition_keys(f)
    assert "skillTokens" not in keys
    assert "rolePattern" not in keys
    # Core isolation filters still present
    assert "agencyId" in keys
    assert "embeddingVersion" in keys


def test_metadata_filter_returns_none_when_no_must_conditions():
    """Empty or skills-only filters must not produce a filter (no must conditions)."""
    f = _metadata_filter({"preferredSkills": ["python"], "preferredRoles": ["engineer"]})
    assert f is None


def test_metadata_filter_none_input():
    assert _metadata_filter(None) is None


def test_metadata_filter_empty_dict():
    assert _metadata_filter({}) is None


# ── 4. Agency isolation is preserved ─────────────────────────────────────────

def test_agency_isolation_filter_is_must_not_should():
    f = _metadata_filter({"agencyId": "agency-42", "embeddingVersion": "v1"})
    assert f is not None
    must_keys = [c.key for c in (f.must or [])]
    assert "agencyId" in must_keys
    # should must be empty / None so isolation is hard, not soft
    assert not (f.should or [])
