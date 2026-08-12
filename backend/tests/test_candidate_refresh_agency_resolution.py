from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import candidate_service as refresh_service
from app.services import internal_candidate_semantic_service as matcher


class _Db:
    pass


def _job(**overrides):
    values = {
        "id": "job-1",
        "company_id": "agency-1",
        "agency_id": "agency-1",
        "agency": SimpleNamespace(id="agency-1"),
        "job_status": "active",
        "vetting_mode": "volume",
        "created_by": "recruiter-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_resolve_job_agency_id_prefers_company_id():
    job = _job(company_id="agency-a", agency_id="agency-b", agency=SimpleNamespace(id="agency-c"))

    assert refresh_service.resolve_job_agency_id(job) == "agency-a"


def test_resolve_job_agency_id_falls_back_to_relationship():
    job = _job(company_id="", agency_id="", agency=SimpleNamespace(id="agency-c"))

    assert refresh_service.resolve_job_agency_id(job) == "agency-c"


@pytest.mark.parametrize(
    "company_id, agency_id, relationship_id",
    [
        (None, None, None),
        ("", "", None),
    ],
)
def test_fetch_ranked_candidates_skips_safely_without_agency(monkeypatch, company_id, agency_id, relationship_id):
    job = _job(company_id=company_id, agency_id=agency_id, agency=SimpleNamespace(id=relationship_id) if relationship_id else None)
    called = {"value": False}

    def _unexpected_match(**_kwargs):
        called["value"] = True
        raise AssertionError("match_internal_candidates_for_job must not run without a valid agency UUID")

    monkeypatch.setattr(refresh_service.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "match_internal_candidates_for_job", _unexpected_match)

    with pytest.raises(refresh_service.APIError) as error:
        refresh_service.fetch_ranked_candidates(db=_Db(), job_id="job-1", request_source="api")

    assert error.value.code == "missing_agency_id"
    assert called["value"] is False


def test_fetch_ranked_candidates_uses_resolved_agency(monkeypatch):
    job = _job(company_id="", agency_id="", agency=SimpleNamespace(id="agency-9"))
    captured: dict[str, str] = {}

    def _match(*, db, job_id, agency_id, limit=None):
        captured["job_id"] = job_id
        captured["agency_id"] = agency_id
        return {"candidates": [SimpleNamespace(id="candidate-1")]}

    monkeypatch.setattr(refresh_service.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "match_internal_candidates_for_job", _match)

    result = refresh_service.fetch_ranked_candidates(db=_Db(), job_id="job-1", request_source="api")

    assert [item.id for item in result] == ["candidate-1"]
    assert captured == {"job_id": "job-1", "agency_id": "agency-9"}
