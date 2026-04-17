from fastapi import APIRouter, HTTPException

from app.schemas.job import Job, VoiceRefineRequest
from app.services.db_service import get_job, update_job

router = APIRouter(tags=["voice"])


@router.post("/voice/refine", response_model=Job)
def refine_voice_notes(payload: VoiceRefineRequest) -> Job:
    existing_job = get_job(payload.jobId)
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")

    notes_text = " ".join(payload.voiceNotes).strip()
    refined_description = existing_job.get("description", "")
    if notes_text:
        refined_description = f"{refined_description}\n\nHiring Notes:\n{notes_text}".strip()

    updated_job = update_job(payload.jobId, {"description": refined_description})
    if not updated_job:
        raise HTTPException(status_code=404, detail="Job not found")

    return Job(
        id=updated_job["id"],
        title=updated_job["title"],
        description=updated_job["description"],
        location=updated_job["location"],
        compensation=updated_job["compensation"],
        workAuthorization=updated_job["workAuthorization"],
    )
