from types import SimpleNamespace

from app.services.candidate_text import STRUCTURED_CANDIDATE_TEXT_MAX_CHARS, build_structured_candidate_text
from app.services import internal_candidate_embedding_service as indexer


def _candidate(**overrides):
    values = {
        "id": "record-1", "candidate_id": "candidate-1", "agency_id": "agency-1",
        "current_role": "Python Backend Developer", "current_company": "Acme",
        "location": "Hyderabad", "skills": ["Python", "FastAPI", "PostgreSQL", "Redis"],
        "total_experience_years": 5.0, "summary": "Backend engineer building APIs.",
        "education": ["B.Tech Computer Science"], "work_experience": [{"role": "Backend Developer", "company": "Acme"}],
        "parsed_resume_json": {}, "parsed_resume_text": "", "resume_text": "", "raw_data": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_structured_text_supports_profile_without_resume_and_prioritizes_signals():
    text = build_structured_candidate_text(_candidate())
    assert "Role: Python Backend Developer" in text
    assert "Skills: Python, FastAPI, PostgreSQL, Redis" in text
    assert "Experience: 5 years" in text
    assert len(text) <= STRUCTURED_CANDIDATE_TEXT_MAX_CHARS


def test_long_resume_is_bounded_after_structured_fields():
    text = build_structured_candidate_text(_candidate(resume_text="rare-resume-marker " * 1000))
    assert text.startswith("Role: Python Backend Developer")
    assert len(text) <= STRUCTURED_CANDIDATE_TEXT_MAX_CHARS


def test_indexing_hashes_exact_text_and_is_idempotent(monkeypatch):
    row = _candidate(resume_text="old resume")
    class DB:
        def get(self, *_): return row
        def flush(self): pass
    embedded = []
    monkeypatch.setattr(indexer, "_embed_with_retries", lambda text: embedded.append(text) or [0.1, 0.2])
    monkeypatch.setattr(indexer, "delete_internal_candidate_vectors", lambda **_: None)
    monkeypatch.setattr(indexer, "upsert_internal_candidate_embeddings", lambda points: None)
    monkeypatch.setattr(indexer, "internal_candidate_vector_exists", lambda **_: True)

    assert indexer.index_candidate_embedding(db=DB(), candidate_record_id="record-1")["status"] == "indexed"
    assert row.embedding_status == "EMBEDDED"
    assert row.embedding_version == indexer.EMBEDDING_VERSION
    assert row.embedding_text_hash
    assert row.embedding_indexed_at is not None
    assert indexer.index_candidate_embedding(db=DB(), candidate_record_id="record-1")["status"] == "already_indexed"
    assert len(embedded) == 1

    row.skills = ["Python", "FastAPI", "Kafka"]
    assert indexer.index_candidate_embedding(db=DB(), candidate_record_id="record-1")["status"] == "indexed"
    assert len(embedded) == 2


def test_qdrant_failure_marks_candidate_failed(monkeypatch):
    row = _candidate()
    class DB:
        def get(self, *_): return row
        def flush(self): pass
    monkeypatch.setattr(indexer, "_embed_with_retries", lambda _text: [0.1])
    monkeypatch.setattr(indexer, "delete_internal_candidate_vectors", lambda **_: None)
    def fail(_points):
        raise RuntimeError("qdrant unavailable")
    monkeypatch.setattr(indexer, "upsert_internal_candidate_embeddings", fail)

    try:
        indexer.index_candidate_embedding(db=DB(), candidate_record_id="record-1")
    except RuntimeError:
        pass
    assert row.embedding_status == "FAILED"
    assert getattr(row, "embedding_version", None) is None


def test_missing_qdrant_vector_reindexes(monkeypatch):
    row = _candidate(embedding_status="EMBEDDED", embedding_version=indexer.EMBEDDING_VERSION, embedding_text_hash="old")
    class DB:
        def get(self, *_): return row
        def flush(self): pass
    embedded = []
    monkeypatch.setattr(indexer, "internal_candidate_vector_exists", lambda **_: False)
    monkeypatch.setattr(indexer, "_embed_with_retries", lambda text: embedded.append(text) or [0.1])
    monkeypatch.setattr(indexer, "delete_internal_candidate_vectors", lambda **_: None)
    monkeypatch.setattr(indexer, "upsert_internal_candidate_embeddings", lambda _points: None)

    result = indexer.index_candidate_embedding(db=DB(), candidate_record_id="record-1")
    assert result["status"] == "indexed"
    assert len(embedded) == 1
