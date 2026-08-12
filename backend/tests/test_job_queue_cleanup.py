from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Column, String, Table, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.entities import Base, CandidateApplicationEntity, CandidateProfileEntity, JobEntity
from app.services import job_queue_service as queue_service


# Minimal stub for a foreign-key target that is present in the ORM metadata graph
# but not needed for these queue cleanup tests.
Table("linkedin_accounts", Base.metadata, Column("id", String(36), primary_key=True))


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    def _list(self, key: str) -> list[str]:
        value = self.store.setdefault(key, [])
        assert isinstance(value, list)
        return value

    def _hash(self, key: str) -> dict[str, str]:
        value = self.store.setdefault(key, {})
        assert isinstance(value, dict)
        return value

    def _zset(self, key: str) -> dict[str, float]:
        value = self.store.setdefault(key, {})
        assert isinstance(value, dict)
        return value

    def set(self, key: str, value: str, **_kwargs) -> bool:
        self.store[key] = value
        return True

    def get(self, key: str):
        value = self.store.get(key)
        return value if isinstance(value, str) else None

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                removed += 1
                self.store.pop(key, None)
        return removed

    def lpush(self, key: str, value: str) -> int:
        items = self._list(key)
        items.insert(0, value)
        return len(items)

    def lrange(self, key: str, start: int, end: int):
        items = list(self._list(key))
        if end == -1:
            end = len(items) - 1
        return items[start : end + 1]

    def lrem(self, key: str, count: int, value: str) -> int:
        items = self._list(key)
        removed = 0
        if count == 0:
            removed = items.count(value)
            self.store[key] = [item for item in items if item != value]
            return removed
        new_items: list[str] = []
        remaining = abs(count)
        for item in items:
            if item == value and remaining > 0:
                removed += 1
                remaining -= 1
                continue
            new_items.append(item)
        self.store[key] = new_items
        return removed

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        zset = self._zset(key)
        zset.update({str(member): float(score) for member, score in mapping.items()})
        return len(mapping)

    def zrange(self, key: str, start: int, end: int):
        items = sorted(self._zset(key).items(), key=lambda item: (item[1], item[0]))
        members = [member for member, _score in items]
        if end == -1:
            end = len(members) - 1
        return members[start : end + 1]

    def zrem(self, key: str, value: str) -> int:
        zset = self._zset(key)
        existed = 1 if value in zset else 0
        zset.pop(value, None)
        return existed

    def hset(self, key: str, field: str, value: str) -> int:
        hash_map = self._hash(key)
        hash_map[field] = value
        return 1

    def hget(self, key: str, field: str):
        return self._hash(key).get(field)

    def hgetall(self, key: str):
        return dict(self._hash(key))

    def hdel(self, key: str, field: str) -> int:
        hash_map = self._hash(key)
        existed = 1 if field in hash_map else 0
        hash_map.pop(field, None)
        return existed


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def fake_redis(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(queue_service, "get_redis", lambda: redis)
    return redis


@pytest.fixture()
def session_local(monkeypatch, db_session):
    Session = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr("app.db.session.SessionLocal", Session)
    return Session


def _job(db_session, job_id: str | None = None) -> JobEntity:
    row = JobEntity(
        id=job_id or str(uuid4()),
        title="Backend Engineer",
        source_app="ui",
        job_status="active",
        vetting_mode="volume",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _candidate(db_session, *, candidate_id: str, job_id: str | None = None) -> CandidateProfileEntity:
    if job_id:
        _job(db_session, job_id)
    row = CandidateProfileEntity(
        id=str(uuid4()),
        candidate_id=candidate_id,
        job_id=job_id,
        current_role="Engineer",
        current_company="Acme",
        summary="Strong backend engineer.",
        raw_data={},
    )
    db_session.add(row)
    db_session.commit()
    return row


def _application(db_session, *, application_id: str, job_id: str) -> CandidateApplicationEntity:
    _job(db_session, job_id)
    row = CandidateApplicationEntity(
        id=application_id,
        job_id=job_id,
        company_id=str(uuid4()),
        candidate_id=f"candidate-{uuid4().hex[:8]}",
        name="Applicant",
        email="applicant@example.com",
        phone="",
        resume_file_name="resume.pdf",
        resume_file_path="/tmp/resume.pdf",
        resume_text="Python backend engineer",
        resume_fingerprint="fingerprint",
        application_fingerprint=f"application-{uuid4().hex}",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _seed_queue(fake_redis: _FakeRedis, *, queue_type: str, job_id: str, payload: dict[str, object], state: str = "ready", score: float = 1.0) -> None:
    payload = dict(payload)
    payload.setdefault("queue_type", queue_type)
    payload.setdefault("job_id", job_id)
    payload.setdefault("status", "queued")
    payload.setdefault("attempts", 0)
    payload.setdefault("max_attempts", 5)
    payload.setdefault("idempotency_key", f"idempotency:{queue_type}:{job_id}")
    payload.setdefault("created_at", "2026-01-01T00:00:00+00:00")
    payload.setdefault("updated_at", "2026-01-01T00:00:00+00:00")
    fake_redis.set(f"pontis:queue:{queue_type}:job:{job_id}", json.dumps(payload, sort_keys=True))
    if state == "ready":
        fake_redis.lpush(f"pontis:queue:{queue_type}:ready", job_id)
    elif state == "processing":
        fake_redis.lpush(f"pontis:queue:{queue_type}:processing", job_id)
        fake_redis.hset(
            f"pontis:queue:{queue_type}:processing_meta",
            job_id,
            json.dumps({"claimed_at": payload.get("claimed_at", 0), "worker": "worker-1"}, sort_keys=True),
        )
    elif state == "delayed":
        fake_redis.zadd(f"pontis:queue:{queue_type}:delayed", {job_id: score})
    elif state == "dead":
        fake_redis.hset(f"pontis:queue:{queue_type}:dead", job_id, json.dumps(payload, sort_keys=True))
        fake_redis.hset(
            f"pontis:queue:{queue_type}:dead_meta",
            job_id,
            json.dumps({"status": "dead_letter", "attempts": int(payload.get("attempts") or 0)}, sort_keys=True),
        )
    else:
        raise AssertionError(f"unsupported queue state: {state}")


@pytest.mark.parametrize(
    "queue_type",
    [
        "voice_intake_finalize",
        "linkedin_job_posting",
        "linkedin_connection_queue",
        "linkedin_message_queue",
        "outreach_send",
        "embedding_generation",
    ],
)
def test_job_entity_backed_queue_is_preserved_when_job_exists(db_session, fake_redis, session_local, queue_type):
    job = _job(db_session, "job-1")
    job_id = job.id
    _seed_queue(fake_redis, queue_type=queue_type, job_id=job_id, payload={"job_id": job_id}, state="ready")

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 0
    assert fake_redis.lrange(f"pontis:queue:{queue_type}:ready", 0, -1) == [job_id]


@pytest.mark.parametrize(
    "queue_type",
    [
        "voice_intake_finalize",
        "linkedin_job_posting",
        "linkedin_connection_queue",
        "linkedin_message_queue",
        "outreach_send",
        "embedding_generation",
    ],
)
def test_job_entity_backed_queue_is_cleaned_when_job_is_missing(db_session, fake_redis, session_local, queue_type):
    job_id = "missing-job-1"
    _seed_queue(fake_redis, queue_type=queue_type, job_id=job_id, payload={"job_id": job_id}, state="ready")

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 1
    assert fake_redis.lrange(f"pontis:queue:{queue_type}:ready", 0, -1) == []
    assert fake_redis.get(f"pontis:queue:{queue_type}:job:{job_id}") is None


def test_candidate_application_processing_is_preserved_when_application_exists(db_session, fake_redis, session_local):
    application_id = "application-1"
    job_id = "job-1"
    _application(db_session, application_id=application_id, job_id=job_id)
    _seed_queue(
        fake_redis,
        queue_type="candidate_application_processing",
        job_id=application_id,
        payload={"application_id": application_id, "job_id": job_id, "candidate_id": "candidate-1"},
        state="ready",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 0
    assert fake_redis.lrange("pontis:queue:candidate_application_processing:ready", 0, -1) == [application_id]


def test_candidate_application_processing_is_cleaned_when_application_is_missing(db_session, fake_redis, session_local):
    application_id = "application-missing"
    _seed_queue(
        fake_redis,
        queue_type="candidate_application_processing",
        job_id=application_id,
        payload={"application_id": application_id, "job_id": "job-1", "candidate_id": "candidate-1"},
        state="dead",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 1
    assert fake_redis.hgetall("pontis:queue:candidate_application_processing:dead") == {}


def test_candidate_embedding_index_is_preserved_when_profile_exists(db_session, fake_redis, session_local):
    candidate = _candidate(db_session, candidate_id="candidate-embed", job_id="job-embed")
    queue_job_id = "queue-candidate-embedding-index-1"
    _seed_queue(
        fake_redis,
        queue_type="candidate_embedding_index",
        job_id=queue_job_id,
        payload={"candidate_record_id": candidate.id},
        state="ready",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 0
    assert fake_redis.lrange("pontis:queue:candidate_embedding_index:ready", 0, -1) == [queue_job_id]


def test_candidate_embedding_index_is_cleaned_when_profile_is_missing(db_session, fake_redis, session_local):
    queue_job_id = "queue-candidate-embedding-index-missing"
    _seed_queue(
        fake_redis,
        queue_type="candidate_embedding_index",
        job_id=queue_job_id,
        payload={"candidate_record_id": "missing-record"},
        state="dead",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 1
    assert fake_redis.hgetall("pontis:queue:candidate_embedding_index:dead") == {}


def test_candidate_refresh_flywheel_batch_is_preserved_without_job_entity(db_session, fake_redis, session_local):
    queue_job_id = "refresh-batch-queue-job"
    _seed_queue(
        fake_redis,
        queue_type="candidate_refresh",
        job_id=queue_job_id,
        payload={"batch_size": 100, "stale_days": 7},
        state="ready",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 0
    assert fake_redis.lrange("pontis:queue:candidate_refresh:ready", 0, -1) == [queue_job_id]


def test_candidate_refresh_candidate_job_is_preserved_when_profile_exists(db_session, fake_redis, session_local):
    _candidate(db_session, candidate_id="candidate-refresh", job_id="job-refresh")
    queue_job_id = "refresh-candidate-queue-job"
    _seed_queue(
        fake_redis,
        queue_type="candidate_refresh",
        job_id=queue_job_id,
        payload={"candidate_id": "candidate-refresh", "job_id": "job-refresh"},
        state="ready",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 0
    assert fake_redis.lrange("pontis:queue:candidate_refresh:ready", 0, -1) == [queue_job_id]


def test_outreach_send_after_enrichment_is_preserved_when_candidate_exists(db_session, fake_redis, session_local):
    _candidate(db_session, candidate_id="candidate-outreach", job_id="job-outreach")
    queue_job_id = "outreach-after-enrichment-job"
    _seed_queue(
        fake_redis,
        queue_type="outreach_send_after_enrichment",
        job_id=queue_job_id,
        payload={"candidate_id": "candidate-outreach", "job_id": "job-outreach", "email": "candidate@example.com"},
        state="ready",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 0
    assert fake_redis.lrange("pontis:queue:outreach_send_after_enrichment:ready", 0, -1) == [queue_job_id]


def test_candidate_enrichment_is_preserved_when_candidate_exists(db_session, fake_redis, session_local):
    _candidate(db_session, candidate_id="candidate-enrichment", job_id="job-enrichment")
    queue_job_id = "candidate-enrichment-job"
    _seed_queue(
        fake_redis,
        queue_type="candidate_enrichment",
        job_id=queue_job_id,
        payload={"candidate_id": "candidate-enrichment", "job_id": "job-enrichment"},
        state="ready",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 0
    assert fake_redis.lrange("pontis:queue:candidate_enrichment:ready", 0, -1) == [queue_job_id]


def test_outreach_followup_is_never_auto_cleaned(db_session, fake_redis, session_local):
    queue_job_id = "outreach-followup-job"
    _seed_queue(
        fake_redis,
        queue_type="outreach_followup",
        job_id=queue_job_id,
        payload={"requested_at": "2026-01-01T00:00:00+00:00"},
        state="ready",
    )

    result = queue_service.cleanup_orphaned_queue_entries()

    assert result["removed"] == 0
    assert fake_redis.lrange("pontis:queue:outreach_followup:ready", 0, -1) == [queue_job_id]


def test_cleanup_removes_orphaned_dead_letter_and_is_idempotent(db_session, fake_redis, session_local):
    queue_job_id = "orphan-dead-job"
    _seed_queue(
        fake_redis,
        queue_type="outreach_send",
        job_id=queue_job_id,
        payload={"job_id": "missing-job"},
        state="dead",
    )

    first = queue_service.cleanup_orphaned_queue_entries()
    second = queue_service.cleanup_orphaned_queue_entries()

    assert first["removed"] == 1
    assert first["dead_removed"] == 1
    assert second["removed"] == 0
    assert fake_redis.hgetall("pontis:queue:outreach_send:dead") == {}
    assert fake_redis.get("pontis:queue:outreach_send:job:orphan-dead-job") is None


def test_stale_processing_job_is_requeued_without_cleanup_deleting_it(db_session, fake_redis, session_local, monkeypatch):
    queue_job_id = "stale-processing-job"
    _seed_queue(
        fake_redis,
        queue_type="candidate_refresh",
        job_id=queue_job_id,
        payload={"batch_size": 5, "stale_days": 3, "claimed_at": datetime.now(timezone.utc).timestamp() - 3600},
        state="processing",
    )
    monkeypatch.setattr(queue_service, "JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS", 30)

    requeued = queue_service._requeue_stale_processing(fake_redis, "candidate_refresh")
    cleanup = queue_service.cleanup_orphaned_queue_entries()

    assert requeued == 1
    assert cleanup["removed"] == 0
    assert fake_redis.lrange("pontis:queue:candidate_refresh:processing", 0, -1) == []
    assert fake_redis.lrange("pontis:queue:candidate_refresh:ready", 0, -1) == [queue_job_id]
