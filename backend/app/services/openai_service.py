from __future__ import annotations

import logging
from datetime import datetime, timezone

from openai import OpenAI

from app.core.config import OPENAI_API_KEY, OPENAI_MODEL
from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)


def _fallback_refinement(description: str, voice_notes: list[str]) -> str:
    cleaned_notes = [note.strip() for note in voice_notes if note and note.strip()]
    if not cleaned_notes:
        return description

    notes_block = "\n".join(f"- {note}" for note in cleaned_notes)
    base = description.strip() or "Role description provided by recruiter."
    return f"{base}\n\nAdditional recruiter notes:\n{notes_block}"


def refine_description(*, description: str, voice_notes: list[str]) -> str:
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY is not configured; using local refinement fallback")
        log_metric("fallback", source="openai", reason="unconfigured")
        return _fallback_refinement(description, voice_notes)

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        notes_blob = "\n".join(f"- {note}" for note in voice_notes if note.strip())

        prompt = (
            "You are refining a hiring job description for candidate search.\n"
            "Return only the refined description text.\n\n"
            f"Current Description:\n{description}\n\n"
            f"Voice Notes:\n{notes_blob}\n"
        )

        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
            temperature=0.2,
        )
        refined = (response.output_text or "").strip()
        if refined:
            return refined
    except Exception as exc:
        log_metric("error", source="openai", kind="refine_failure")
        logger.warning("OpenAI refinement failed; using local refinement fallback", exc_info=exc)
        log_metric("fallback", source="openai", reason="request_failed")

    return _fallback_refinement(description, voice_notes)


def openai_health_snapshot() -> dict:
    if not OPENAI_API_KEY:
        return {
            "status": "unconfigured",
            "error": "OPENAI_API_KEY missing",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.responses.create(
            model=OPENAI_MODEL,
            input="healthcheck",
            max_output_tokens=1,
        )
        _ = response.output_text
        return {"status": "ok", "error": "", "checked_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return {"status": "degraded", "error": str(exc), "checked_at": datetime.now(timezone.utc).isoformat()}
