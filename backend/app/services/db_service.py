from __future__ import annotations

from threading import Lock
from typing import Dict
from uuid import uuid4


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, dict] = {}
        self._lock = Lock()

    def create_job(self, company: dict, job: dict) -> str:
        job_id = str(uuid4())
        record = {
            "id": job_id,
            "company": company,
            **job,
        }

        with self._lock:
            self._jobs[job_id] = record

        return job_id

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def update_job(self, job_id: str, updates: dict) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None

            job.update(updates)
            return dict(job)


job_store = InMemoryJobStore()


def create_job(company: dict, job: dict) -> str:
    return job_store.create_job(company=company, job=job)


def get_job(job_id: str) -> dict | None:
    return job_store.get_job(job_id=job_id)


def update_job(job_id: str, updates: dict) -> dict | None:
    return job_store.update_job(job_id=job_id, updates=updates)
