from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.repositories import RankingRunRepository
from app.db.session import get_db
from app.services.recruiter_preference_service import (
    get_recruiter_experience_preferences,
    get_recruiter_learning_metrics,
)
from app.utils.exceptions import APIError
from app.utils.responses import success_response

router = APIRouter(prefix="/recruiters", tags=["recruiters"])


@router.get("/{recruiter_id}/learning-metrics")
def get_learning_metrics(
    recruiter_id: str,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = get_recruiter_learning_metrics(db, recruiter_id)
    return success_response(data)


@router.get("/{recruiter_id}/ranking-runs")
def list_ranking_runs(
    recruiter_id: str,
    request: Request,
    job_id: str | None = Query(None, alias="job_id"),
    limit: int = Query(20, ge=1, le=100),
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logged_in_recruiter_id = str((request.state.user or {}).get("id") or "").strip()
    if not logged_in_recruiter_id or logged_in_recruiter_id != recruiter_id.strip():
        raise APIError("Forbidden", status_code=403)

    rows = RankingRunRepository(db).list_for_recruiter(
        recruiter_id=recruiter_id,
        job_id=job_id,
        limit=limit,
    )
    data = [
        {
            "job_id": row.job_id,
            "avg_existing_score": float(row.avg_existing_score or 0.0),
            "avg_final_score": float(row.avg_final_score or 0.0),
            "avg_recruiter_score": float(row.avg_recruiter_score or 0.0),
            "percent_recruiter_capped": float(row.percent_recruiter_capped or 0.0),
            "candidate_count": int(row.candidate_count or 0),
            "drift_delta": float(row.drift_delta or 0.0),
            "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
            "run_type": (row.run_type or "initial"),
        }
        for row in rows
    ]
    return success_response(data)


@router.get("/{recruiter_id}/experience-preferences")
def get_experience_preferences(
    recruiter_id: str,
    request: Request,
    _: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logged_in_recruiter_id = str((request.state.user or {}).get("id") or "").strip()
    if not logged_in_recruiter_id or logged_in_recruiter_id != recruiter_id.strip():
        raise APIError("Forbidden", status_code=403)

    data = get_recruiter_experience_preferences(db, recruiter_id)
    return success_response(data)
