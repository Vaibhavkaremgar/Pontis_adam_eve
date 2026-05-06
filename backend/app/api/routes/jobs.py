from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.repositories import JobRepository
from app.db.session import get_db
from app.schemas.job import JobModeData, JobModeRequest, JobParseData, JobParseRequest
from app.utils.exceptions import APIError
from app.utils.responses import success_response
from app.services.job_parser_service import parse_job_posting_url

router = APIRouter(tags=["jobs"])
logger = logging.getLogger(__name__)


def _strategy_for_mode(mode: str) -> str:
    return "high_precision" if mode == "elite" else "high_volume"


@router.post("/jobs/parse")
def parse_job_posting(
    payload: JobParseRequest,
    _: dict = Depends(get_current_user),
):
    raw_url = (payload.url or "").strip()
    if not raw_url:
        raise APIError("url is required", status_code=400)

    try:
        parsed_job = parse_job_posting_url(url=raw_url)
        data = JobParseData(
            title=parsed_job.get("title", ""),
            description=parsed_job.get("description", ""),
            location=parsed_job.get("location", ""),
            compensation=parsed_job.get("compensation", ""),
            workAuthorization=parsed_job.get("workAuthorization", "required"),
            remotePolicy=parsed_job.get("remotePolicy", "hybrid"),
            experienceRequired=parsed_job.get("experienceRequired", ""),
        )
        logger.info(
            "job_parse_success url=%s title=%s location=%s",
            raw_url,
            data.title,
            data.location,
        )
        return success_response(data.model_dump())
    except APIError:
        raise
    except Exception as exc:
        logger.error("job_parse_failed url=%s error=%s", raw_url, str(exc))
        raise APIError("Failed to parse the URL", status_code=400)


@router.post("/jobs/{job_id}/mode")
def set_job_mode(
    job_id: str,
    payload: JobModeRequest,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mode = (payload.mode or "volume").strip().lower()
    if mode not in {"volume", "elite"}:
        raise APIError("mode must be either 'volume' or 'elite'", status_code=400)

    job_repo = JobRepository(db)
    job = job_repo.set_vetting_mode(job_id=job_id, vetting_mode=mode)
    if not job:
        raise APIError("Job not found", status_code=404)

    data = JobModeData(jobId=job_id, mode=job.vetting_mode or "volume", strategy=_strategy_for_mode(job.vetting_mode or "volume"))
    return success_response(data.model_dump())
