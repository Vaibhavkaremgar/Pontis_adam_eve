from types import SimpleNamespace

import pytest

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
        company_id="agency-1", structured_data={"skills_required": ["Python", "Django"], "experience_required": "5 years", "location": "Bengaluru"},
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
