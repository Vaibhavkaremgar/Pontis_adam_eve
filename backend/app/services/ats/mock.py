from __future__ import annotations

import logging
from uuid import uuid4

from app.services.ats.base import ATSProvider

logger = logging.getLogger(__name__)


class MockATSProvider(ATSProvider):
    def create_candidate(self, candidate_data: dict) -> str:
        candidate_id = str(uuid4())
        message = f"mock_ats_candidate_created candidate_id={candidate_id} candidate_data={candidate_data}"
        logger.info(message)
        return candidate_id

    def attach_candidate_to_job(self, candidate_id: str, job_id: str) -> None:
        message = f"mock_ats_candidate_attached candidate_id={candidate_id} job_id={job_id}"
        logger.info(message)
