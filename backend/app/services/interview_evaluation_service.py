from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories import (
    InterviewEvaluationRepository,
    JobRepository,
    RecruiterNoteRepository,
)
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.notification_intelligence_service import route_recruiter_notification


def record_interview_evaluation(
    *,
    db: Session,
    job_id: str,
    candidate_id: str,
    stage_name: str,
    interviewer_id: str | None = None,
    summary: str = "",
    recommendation: str = "",
    competency_scores: dict[str, Any] | None = None,
    notes: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = InterviewEvaluationRepository(db).upsert(
        job_id=job_id,
        candidate_id=candidate_id,
        stage_name=stage_name,
        interviewer_id=interviewer_id,
        summary=summary,
        recommendation=recommendation,
        competency_scores=competency_scores or {},
        notes=notes,
        metadata=metadata or {},
        status="submitted",
    )

    recruiter_id = JobRepository(db).get_recruiter_id(job_id)
    if notes.strip():
        RecruiterNoteRepository(db).create(
            job_id=job_id,
            candidate_id=candidate_id,
            recruiter_id=recruiter_id,
            note_type="interview",
            body=notes,
            metadata={"stageName": stage_name, "evaluationId": row.id},
        )

    normalized_recommendation = (recommendation or "").strip().lower()
    if normalized_recommendation in {"advance", "strong_yes", "yes"}:
        transition_candidate_ats_state(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            to_status="advanced",
            source="interview_evaluation",
            reason="interview_advanced",
            metadata={"evaluationId": row.id, "stageName": stage_name},
        )
    elif normalized_recommendation in {"reject", "no"}:
        transition_candidate_ats_state(
            db=db,
            job_id=job_id,
            candidate_id=candidate_id,
            to_status="rejected",
            source="interview_evaluation",
            reason="interview_rejected",
            metadata={"evaluationId": row.id, "stageName": stage_name},
        )

    route_recruiter_notification(
        db=db,
        job_id=job_id,
        candidate_id=candidate_id,
        notification_key=f"interview-evaluation:{job_id}:{candidate_id}:{stage_name}",
        notification_type="interview_evaluation",
        title="Interview evaluation submitted",
        body=summary or notes or recommendation or "Interview evaluation recorded",
        metadata={"evaluationId": row.id, "stageName": stage_name, "recommendation": normalized_recommendation},
    )
    db.commit()
    return {
        "id": row.id,
        "jobId": row.job_id,
        "candidateId": row.candidate_id,
        "stageName": row.stage_name,
        "status": row.status,
        "recommendation": row.recommendation,
        "summary": row.summary,
        "competencyScores": row.competency_scores,
        "notes": row.notes,
        "metadata": row.metadata_json,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


def list_interview_evaluations(*, db: Session, job_id: str, candidate_id: str) -> list[dict[str, Any]]:
    rows = InterviewEvaluationRepository(db).list_for_candidate(job_id=job_id, candidate_id=candidate_id, limit=50)
    return [
        {
            "id": row.id,
            "jobId": row.job_id,
            "candidateId": row.candidate_id,
            "interviewerId": row.interviewer_id,
            "stageName": row.stage_name,
            "status": row.status,
            "summary": row.summary,
            "recommendation": row.recommendation,
            "competencyScores": row.competency_scores,
            "notes": row.notes,
            "metadata": row.metadata_json,
            "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(),
        }
        for row in rows
    ]
