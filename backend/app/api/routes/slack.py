from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.db.repositories import CandidateFeedbackRepository, UserRepository
from app.db.session import SessionLocal
from app.services.candidate_service import apply_feedback, fetch_ranked_candidates
from app.services.hiring_service import create_hiring_job
from app.services.interview_invite_service import send_interview_invite
from app.services.outreach_service import trigger_candidate_outreach
from app.services.slack_integration import (
    build_candidate_blocks,
    parse_slack_command_form,
    post_slack_message,
    update_candidate_message_blocks,
    update_slack_message,
    verify_slack_signature,
)
from app.utils.responses import error_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])
SYSTEM_SLACK_USER_EMAIL = "slack-system@pontis.local"
SLACK_CHANNEL_RATE_LIMIT_SECONDS = 5.0
_channel_last_request: dict[str, float] = {}
_channel_lock = threading.Lock()


def _verify_request(request: Request, raw_body: bytes) -> None:
    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not verify_slack_signature(raw_body=raw_body, signature=signature, timestamp=timestamp):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")


def _ensure_system_user_id(db) -> str:
    user_repo = UserRepository(db)
    user = user_repo.get_by_email(SYSTEM_SLACK_USER_EMAIL)
    if user:
        return str(user.id)
    try:
        user = user_repo.create(email=SYSTEM_SLACK_USER_EMAIL)
        db.commit()
        return str(user.id)
    except IntegrityError:
        db.rollback()
        user = user_repo.get_by_email(SYSTEM_SLACK_USER_EMAIL)
        if user:
            return str(user.id)
        raise


def _derive_job_title(text: str) -> str:
    words = [word for word in re.split(r"\s+", text.strip()) if word]
    if not words:
        return "Hiring Role"
    return " ".join(words[:4]).strip().title() or "Hiring Role"


def _build_slack_job_payload(text: str) -> tuple[dict, dict]:
    company = {
        "name": "Slack Hiring",
        "website": "https://slack.com",
        "description": "Hiring requests sourced directly from Slack",
        "industry": "Recruiting",
    }
    job = {
        "title": _derive_job_title(text),
        "description": text,
        "location": "Remote",
        "compensation": "",
        "workAuthorization": "required",
        "remotePolicy": "remote",
        "experienceRequired": "",
        "vettingMode": "volume",
        "autoExportToAts": False,
    }
    return company, job


def _is_channel_rate_limited(channel_id: str) -> bool:
    target = (channel_id or "").strip()
    if not target:
        return False
    now = time.monotonic()
    with _channel_lock:
        last_request = _channel_last_request.get(target)
        if last_request is not None and now - last_request < SLACK_CHANNEL_RATE_LIMIT_SECONDS:
            return True
        _channel_last_request[target] = now
    return False


def _send_slack_message_sync(*, channel_id: str, text: str, blocks: list[dict] | None = None) -> bool:
    try:
        return asyncio.run(post_slack_message(channel_id=channel_id, text=text, blocks=blocks))
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("slack_message_post_failed channel_id=%s error=%s", channel_id, str(exc), exc_info=exc)
        return False


def _run_slack_hiring_pipeline(*, channel_id: str, text: str) -> None:
    logger.info("slack_request_received channel_id=%s text=%s", channel_id, text)
    try:
        _send_slack_message_sync(
            channel_id=channel_id,
            text="\u23f3 Fetching candidates from multiple sources...",
        )
        with SessionLocal() as db:
            system_user_id = _ensure_system_user_id(db)
            company_payload, job_payload = _build_slack_job_payload(text)
            job_id = create_hiring_job(db=db, user_id=system_user_id, company=company_payload, job=job_payload)
            logger.info("slack_job_created channel_id=%s job_id=%s", channel_id, job_id)
            candidates = fetch_ranked_candidates(db=db, job_id=job_id, mode="volume", refresh=True)
            logger.info("slack_candidates_fetched channel_id=%s job_id=%s count=%s", channel_id, job_id, len(candidates))
            top_candidates = candidates[:5]

            if not top_candidates:
                _send_slack_message_sync(
                    channel_id=channel_id,
                    text="\u26a0\ufe0f Failed to fetch candidates. Please try again.",
                )
                return

            blocks = build_candidate_blocks(job_id=job_id, candidates=top_candidates)
            posted = _send_slack_message_sync(channel_id=channel_id, text="Top candidates", blocks=blocks)
            if not posted:
                _send_slack_message_sync(
                    channel_id=channel_id,
                    text="\u26a0\ufe0f Failed to fetch candidates. Please try again.",
                )
    except Exception as exc:
        logger.error("slack_hiring_pipeline_failed channel_id=%s error=%s", channel_id, str(exc), exc_info=exc)
        _send_slack_message_sync(channel_id=channel_id, text="\u26a0\ufe0f Failed to fetch candidates. Please try again.")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/commands")
async def slack_commands(
    request: Request,
    background_tasks: BackgroundTasks,
    text: str = Form(default=""),
    user_id: str = Form(default=""),
    channel_id: str = Form(default=""),
):
    try:
        command = parse_slack_command_form(
            {
                "text": text,
                "user_id": user_id,
                "channel_id": channel_id,
            }
        )
        logger.info(
            "slack_command_received channel_id=%s user_id=%s text=%s",
            command.channel_id,
            command.user_id,
            command.text,
        )
        print("Slack Hire Request:", command.text, "Channel:", command.channel_id)

        if _is_channel_rate_limited(command.channel_id):
            logger.warning("slack_command_rate_limited channel_id=%s user_id=%s", command.channel_id, command.user_id)
            return JSONResponse(
                status_code=200,
                content={
                    "response_type": "ephemeral",
                    "text": "\u26a0\ufe0f Please wait a few seconds before sending another request.",
                },
            )

        background_tasks.add_task(
            _run_slack_hiring_pipeline,
            channel_id=command.channel_id,
            text=command.text,
        )

        return JSONResponse(
            status_code=200,
            content={
                "response_type": "in_channel",
                "text": "\U0001f50d Sourcing candidates for your requirement...",
            },
        )
    except Exception as exc:
        logger.error("slack_command_failed error=%s", str(exc), exc_info=exc)
        return JSONResponse(
            status_code=200,
            content={
                "response_type": "ephemeral",
                "text": "Something went wrong. Please try again.",
            },
        )


@router.post("/interactions")
async def slack_interactions(request: Request, background_tasks: BackgroundTasks):
    payload: dict = {}
    try:
        raw_body = await request.body()
        _verify_request(request, raw_body)

        form_data = await request.form()
        if "payload" not in form_data:
            raise HTTPException(status_code=400, detail="Missing payload")

        payload = json.loads(form_data["payload"])
        logger.info("slack_interaction_received payload=%s", json.dumps(payload, ensure_ascii=False))

        value = payload["actions"][0]["value"]
        channel_id = payload["channel"]["id"]
        action, candidate_id, job_id = value.split(":")
        message = payload.get("message") or {}
        message_ts = str(message.get("ts") or "").strip()
        action = action.strip().lower()
        if action not in {"shortlist", "reject", "schedule"}:
            raise HTTPException(status_code=400, detail="Unsupported action")

        logger.info(
            "slack_button_action action=%s candidate_id=%s job_id=%s channel_id=%s",
            action,
            candidate_id,
            job_id,
            channel_id,
        )

        if action == "schedule":
            background_tasks.add_task(
                send_interview_invite,
                candidate_id,
                job_id,
                channel_id=channel_id,
            )
            logger.info(
                "slack_schedule_requested job_id=%s candidate_id=%s channel_id=%s",
                job_id,
                candidate_id,
                channel_id,
            )
            return {"ok": True}

        if not message_ts:
            raise HTTPException(status_code=400, detail="Missing message timestamp")

        with SessionLocal() as db:
            existing_feedback = CandidateFeedbackRepository(db).get(job_id=job_id, candidate_id=candidate_id)
            if existing_feedback:
                logger.info(
                    "slack_button_duplicate_ignored job_id=%s candidate_id=%s existing_feedback=%s",
                    job_id,
                    candidate_id,
                    existing_feedback.feedback,
                )
                await post_slack_message(
                    channel_id=channel_id,
                    text="\u26a0\ufe0f This candidate has already been processed.",
                )
                return {"ok": True, "duplicate": True}

            result = apply_feedback(
                db=db,
                job_id=job_id,
                candidate_id=candidate_id,
                action="accept" if action == "shortlist" else "reject",
            )
            db.commit()

        if action == "shortlist":
            background_tasks.add_task(
                trigger_candidate_outreach,
                candidate_id,
                job_id,
                channel_id=channel_id,
            )

        updated_blocks = update_candidate_message_blocks(
            blocks=list(message.get("blocks") or []),
            job_id=job_id,
            candidate_id=candidate_id,
            decision=action,
        )
        update_ok = await update_slack_message(
            channel_id=channel_id,
            ts=message_ts,
            blocks=updated_blocks,
            text=f"Candidate {action}ed",
        )
        if not update_ok:
            logger.warning(
                "slack_message_update_failed_nonfatal channel_id=%s message_ts=%s job_id=%s candidate_id=%s",
                channel_id,
                message_ts,
                job_id,
                candidate_id,
            )

        response_text = "\u2705 Candidate shortlisted" if action == "shortlist" else "\u274c Candidate rejected"
        await post_slack_message(channel_id=channel_id, text=response_text)
        logger.info(
            "slack_interaction_processed job_id=%s candidate_id=%s action=%s result=%s",
            job_id,
            candidate_id,
            action,
            json.dumps(result, ensure_ascii=False),
        )
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("slack_interaction_failed error=%s", str(exc), exc_info=exc)
        try:
            channel_id = (payload.get("channel") or {}).get("id") or (payload.get("channel") or {}).get("channel_id")
            if channel_id:
                await post_slack_message(channel_id=channel_id, text="\u26a0\ufe0f Failed to fetch candidates. Please try again.")
        except Exception:  # pragma: no cover - defensive fallback
            logger.exception("slack_interaction_fallback_failed")
        return JSONResponse(status_code=500, content=error_response("Failed to process Slack interaction"))
