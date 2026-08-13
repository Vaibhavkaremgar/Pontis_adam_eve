from types import SimpleNamespace
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import internal_candidate_semantic_service as matcher
from app.services.qdrant_service import QdrantUnavailableError


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _query):
        return _Scalars(self.rows)


def _row(**overrides):
    values = {
        "id": "record-1", "candidate_id": "candidate-1", "agency_id": "agency-1",
        "name": "Ada Lovelace", "current_role": "Senior Python Engineer",
        "current_company": "Analytical Engines", "email": "ada@example.com",
        "location": "Bengaluru", "resume_text": "Python Django PostgreSQL",
        "parsed_resume_text": "", "skills": ["Python", "Django"], "raw_data": {"years_experience": 8},
        "parsed_resume_json": {}, "total_experience_years": 8.0, "summary": "Backend engineer",
        "embedding_status": "EMBEDDED", "embedding_version": matcher.EMBEDDING_VERSION,
        "education": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _job():
    return SimpleNamespace(
        agency_id="agency-1", company_id="agency-1",
        structured_data={"skills_required": ["Python", "Django"], "experience_required": "5 years", "location": "Bengaluru"},
        skills_required=["Python", "Django"], location="Bengaluru", remote_policy="",
    )


def test_internal_match_returns_explainable_ranked_result(monkeypatch):
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: _job())
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "Senior Python Django engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1, 0.2])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.9, "payload": {"candidateRecordId": "record-1", "embeddingVersion": "v-test"}}
    ])

    result = matcher.match_internal_candidates_for_job(db=_Db([_row()]), job_id="job-1", agency_id="agency-1")

    assert result["source"] == "internal"
    assert result["fallback_eligible"] is False
    candidate = result["candidates"][0]
    assert candidate.source == "internal"
    assert candidate.explanation.semanticScore == 0.9
    assert candidate.explanation.skillsMatched
    assert {"backend", "data"} & set(candidate.explanation.skillsMatched)
    assert candidate.explanation.locationMatch == 1.0
    assert candidate.explanation.roleMatch > 0
    assert candidate.explanation.retrievalAttribution["source"] == "internal"


def test_qdrant_failure_is_not_external_fallback(monkeypatch):
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: _job())
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])

    def fail(**_kwargs):
        raise QdrantUnavailableError("down")

    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", fail)

    with pytest.raises(matcher.APIError) as error:
        matcher.match_internal_candidates_for_job(db=_Db([]), job_id="job-1", agency_id="agency-1")
    assert error.value.status_code == 503
    assert error.value.code == "internal_search_unavailable"


def test_empty_internal_index_is_explicitly_not_ready(monkeypatch):
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: _job())
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 0)

    result = matcher.match_internal_candidates_for_job(db=_Db([]), job_id="job-1", agency_id="agency-1")

    assert result["status"] == "index_not_ready"
    assert result["fallback_eligible"] is False
    assert result["fallback_reason"] == "internal_index_not_ready"


def test_structured_only_embedded_candidate_is_matchable(monkeypatch):
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: _job())
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "Python backend engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.8, "payload": {"candidateRecordId": "record-1", "embeddingVersion": matcher.EMBEDDING_VERSION}}
    ])
    row = _row(resume_text="", parsed_resume_text="", summary="Python backend engineer")
    result = matcher.match_internal_candidates_for_job(db=_Db([row]), job_id="job-1", agency_id="agency-1")
    assert len(result["candidates"]) == 1
    assert result["candidates"][0].resumeText is None
    assert result["candidates"][0].email is None


def test_stale_embedding_is_not_matchable(monkeypatch):
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: _job())
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.99, "payload": {"candidateRecordId": "record-1", "embeddingVersion": matcher.EMBEDDING_VERSION}}
    ])
    row = _row(embedding_status="PROCESSING")
    result = matcher.match_internal_candidates_for_job(db=_Db([row]), job_id="job-1", agency_id="agency-1")
    assert result["candidates"] == []
    assert result["fallback_eligible"] is True


@pytest.mark.parametrize("semantic_score,qualified", [(0.59, False), (0.60, True), (0.61, True)])
def test_qualification_threshold_is_sixty_percent(monkeypatch, semantic_score, qualified):
    job = _job()
    job.title = "Senior Python Engineer"
    job.structured_data["location"] = "remote"
    row = _row(current_role="Senior Python Engineer")
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "Senior Python Engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "INTERNAL_CANDIDATE_MATCH_THRESHOLD", 0.60)
    monkeypatch.setattr(matcher, "INTERNAL_CANDIDATE_MATCH_WEIGHTS", {"semantic_similarity": 1.0, "skill_match": 0.0, "experience_match": 0.0})
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": semantic_score, "payload": {"candidateRecordId": "record-1", "embeddingVersion": matcher.EMBEDDING_VERSION}}
    ])
    result = matcher.match_internal_candidates_for_job(db=_Db([row]), job_id="job-1", agency_id="agency-1")
    assert bool(result["candidates"]) is qualified
    assert result["fallback_eligible"] is (not qualified)


def test_candidates_are_ordered_by_final_hybrid_score(monkeypatch):
    job = _job()
    rows = [_row(id="record-1", candidate_id="candidate-1", total_experience_years=2.0), _row(id="record-2", candidate_id="candidate-2", total_experience_years=8.0)]
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "Senior Python Engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 2)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.70, "payload": {"candidateRecordId": "record-1", "embeddingVersion": matcher.EMBEDDING_VERSION}},
        {"score": 0.90, "payload": {"candidateRecordId": "record-2", "embeddingVersion": matcher.EMBEDDING_VERSION}},
    ])
    result = matcher.match_internal_candidates_for_job(db=_Db(rows), job_id="job-1", agency_id="agency-1")
    assert [item.id for item in result["candidates"]] == ["candidate-2", "candidate-1"]


def test_missing_agency_id_is_rejected_before_query(monkeypatch):
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: _job())
    called = {"value": False}

    def _unexpected_query(**_kwargs):
        called["value"] = True
        raise AssertionError("search should not run when agency_id is missing")

    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", _unexpected_query)

    with pytest.raises(matcher.APIError) as error:
        matcher.match_internal_candidates_for_job(db=_Db([]), job_id="job-1", agency_id="")

    assert error.value.code == "missing_agency_id"
    assert called["value"] is False


def test_agency_ownership_check_uses_job_agency_id(monkeypatch):
    """Regression: ownership check must use job.agency_id, not job.company_id.

    - agency-A owns the job  → allowed
    - agency-B does not      → 403 Forbidden
    """
    def _make_job(agency_id: str):
        return SimpleNamespace(
            agency_id=agency_id, company_id="unrelated-company",
            structured_data={"skills_required": ["Python"], "experience_required": "3 years", "location": "remote"},
            skills_required=["Python"], location="remote", remote_policy="remote",
        )

    # agency-A is the owner → match must succeed
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: _make_job("agency-a"))
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "Python engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.9, "payload": {"candidateRecordId": "record-1", "embeddingVersion": matcher.EMBEDDING_VERSION}}
    ])
    result = matcher.match_internal_candidates_for_job(
        db=_Db([_row(agency_id="agency-a")]), job_id="job-1", agency_id="agency-a"
    )
    assert result["status"] == "ok"

    # agency-B is NOT the owner → must be rejected with 403
    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: _make_job("agency-a"))
    with pytest.raises(matcher.APIError) as exc_info:
        matcher.match_internal_candidates_for_job(
            db=_Db([]), job_id="job-1", agency_id="agency-b"
        )
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# New tests for shared-pool behaviour
# ---------------------------------------------------------------------------

def test_candidate_from_different_agency_is_returned(monkeypatch):
    """A candidate owned by agency-B must be returned when matching a job owned by agency-A."""
    job = _job()  # company_id = "agency-1"
    # Candidate belongs to a completely different agency.
    cross_agency_row = _row(id="record-x", candidate_id="candidate-x", agency_id="agency-other")

    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "Senior Python Django engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1, 0.2])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.85, "payload": {"candidateRecordId": "record-x", "embeddingVersion": matcher.EMBEDDING_VERSION}}
    ])

    result = matcher.match_internal_candidates_for_job(
        db=_Db([cross_agency_row]), job_id="job-1", agency_id="agency-1"
    )

    assert len(result["candidates"]) == 1
    assert result["candidates"][0].id == "candidate-x"
    assert result["fallback_eligible"] is False


def test_qdrant_filter_does_not_include_agency_id(monkeypatch):
    """The Qdrant metadata filter must contain only embeddingVersion, not agencyId."""
    job = _job()
    captured_filters: dict = {}

    def capture_search(**kwargs):
        captured_filters.update(kwargs.get("metadata_filters") or {})
        return [{"score": 0.9, "payload": {"candidateRecordId": "record-1", "embeddingVersion": matcher.EMBEDDING_VERSION}}]

    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", capture_search)

    matcher.match_internal_candidates_for_job(db=_Db([_row()]), job_id="job-1", agency_id="agency-1")

    assert "agencyId" not in captured_filters
    assert "embeddingVersion" in captured_filters
    assert captured_filters["embeddingVersion"] == matcher.EMBEDDING_VERSION


def test_wrong_embedding_version_candidate_is_excluded(monkeypatch):
    """A DB row whose embedding_version does not match EMBEDDING_VERSION must be dropped."""
    job = _job()
    stale_row = _row(embedding_version="v1_legacy")

    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.99, "payload": {"candidateRecordId": "record-1", "embeddingVersion": "v1_legacy"}}
    ])

    result = matcher.match_internal_candidates_for_job(
        db=_Db([stale_row]), job_id="job-1", agency_id="agency-1"
    )

    assert result["candidates"] == []
    assert result["fallback_eligible"] is True


def test_non_embedded_candidate_is_excluded(monkeypatch):
    """A DB row with embedding_status != EMBEDDED must be dropped even if Qdrant returns it."""
    job = _job()
    processing_row = _row(embedding_status="PROCESSING")

    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.99, "payload": {"candidateRecordId": "record-1", "embeddingVersion": matcher.EMBEDDING_VERSION}}
    ])

    result = matcher.match_internal_candidates_for_job(
        db=_Db([processing_row]), job_id="job-1", agency_id="agency-1"
    )

    assert result["candidates"] == []
    assert result["fallback_eligible"] is True


def test_contact_information_is_not_exposed_in_match_results(monkeypatch):
    """email, phone, and resumeText must be None/absent in initial matching results."""
    job = _job()
    row = _row(email="ada@example.com", phone="+1-555-0100", resume_text="secret resume content")

    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "Senior Python Django engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 1)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: [
        {"score": 0.9, "payload": {"candidateRecordId": "record-1", "embeddingVersion": matcher.EMBEDDING_VERSION}}
    ])

    result = matcher.match_internal_candidates_for_job(
        db=_Db([row]), job_id="job-1", agency_id="agency-1"
    )

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate.email is None
    assert candidate.resumeText is None
    # profileData may carry candidateRecordId for internal linking but must not carry contact fields
    profile_data = candidate.profileData or {}
    assert "email" not in profile_data
    assert "phone" not in profile_data


@pytest.mark.parametrize(
    "raw_education, expected",
    [
        ("bachelor's degree", ["bachelor's degree"]),
        (["B.Tech", "M.Tech"], ["B.Tech", "M.Tech"]),
        (
            [
                {"degree": "B.Tech", "institution": "JNTU", "year": 2020},
                {"degree": "M.Tech", "institution": "IIT", "year": 2022},
            ],
            ["B.Tech - JNTU - 2020", "M.Tech - IIT - 2022"],
        ),
        ({"degree": "B.Tech", "institution": "JNTU"}, ["B.Tech - JNTU"]),
        (None, []),
    ],
)
def test_education_normalization_handles_common_shapes(raw_education, expected):
    normalized, normalized_required, invalid = matcher._normalize_education(raw_education)

    assert normalized == expected
    if raw_education is None:
        assert normalized_required is False
        assert invalid is False
    elif isinstance(raw_education, list) and all(isinstance(item, str) for item in raw_education):
        assert normalized_required is False
        assert invalid is False
    elif isinstance(raw_education, str):
        assert normalized_required is True
        assert invalid is False
    else:
        assert normalized_required is True
        assert invalid is False


def test_malformed_education_does_not_drop_other_candidates(monkeypatch):
    job = _job()
    rows = []
    hits = []
    for index in range(20):
        record_id = f"record-{index + 1}"
        candidate_id = f"candidate-{index + 1}"
        education = [{"degree": "B.Tech", "institution": "ABC"}] if index == 0 else ["B.Tech"]
        rows.append(
            _row(
                id=record_id,
                candidate_id=candidate_id,
                education=education,
                current_role="Senior Python Engineer",
                current_company="Analytical Engines",
                skills=["Python", "Django"],
            )
        )
        hits.append({"score": 0.9, "payload": {"candidateRecordId": record_id, "embeddingVersion": matcher.EMBEDDING_VERSION}})

    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "Senior Python Django engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1, 0.2])
    monkeypatch.setattr(matcher, "count_collection_points", lambda _name: 20)
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", lambda **_kwargs: hits)

    result = matcher.match_internal_candidates_for_job(db=_Db(rows), job_id="job-1", agency_id="agency-1")

    assert len(result["candidates"]) == 20
    assert result["candidates"][0].education == ["B.Tech - ABC"]


def test_ownership_guard_fires_before_search(monkeypatch):
    """The ownership guard must fire before any Qdrant search."""
    job = _job()  # agency_id = "agency-1"
    search_called = {"value": False}

    def _unexpected_search(**_kwargs):
        search_called["value"] = True
        raise AssertionError("search must not run when agency mismatch")

    monkeypatch.setattr(matcher.JobRepository, "get", lambda _self, _job_id: job)
    monkeypatch.setattr(matcher, "build_job_text", lambda _job: "engineer")
    monkeypatch.setattr(matcher, "get_embedding", lambda _text: [0.1])
    monkeypatch.setattr(matcher, "search_internal_candidate_chunks", _unexpected_search)

    with pytest.raises(matcher.APIError) as error:
        matcher.match_internal_candidates_for_job(
            db=_Db([]), job_id="job-1", agency_id="wrong-agency"
        )

    assert error.value.status_code == 403
    assert search_called["value"] is False
