from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.repositories import JobRepository
from app.db.session import get_db
from app.schemas.job import JobModeData, JobModeRequest, JobParseData, JobParseRequest
from app.utils.exceptions import APIError
from app.utils.responses import success_response

router = APIRouter(tags=["jobs"])


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

    parsed = urlparse(raw_url)
    host = parsed.netloc or "job posting"
    slug = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    title = slug.replace("-", " ").replace("_", " ").strip().title() if slug else f"{host} role"
    description = f"Imported from {host}. Replace this stub with real job parsing once the source parser is connected."
    location = "Remote"
    remote_policy = "remote"
    if "hybrid" in raw_url.lower():
        remote_policy = "hybrid"
        location = "Hybrid"
    elif "onsite" in raw_url.lower() or "on-site" in raw_url.lower():
        remote_policy = "onsite"
        location = "On-site"
    compensation = ""
    if any(token in raw_url.lower() for token in ("salary", "comp", "pay")):
        compensation = "$120k - $180k"

    data = JobParseData(
        title=title,
        description=description,
        location=location,
        compensation=compensation,
        workAuthorization="required",
        remotePolicy=remote_policy,
        experienceRequired="3+ years",
    )
    return success_response(data.model_dump())


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
