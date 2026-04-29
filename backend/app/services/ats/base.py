from __future__ import annotations


class ATSProvider:
    def create_candidate(self, candidate_data: dict) -> str:
        raise NotImplementedError

    def attach_candidate_to_job(self, candidate_id: str, job_id: str) -> None:
        raise NotImplementedError