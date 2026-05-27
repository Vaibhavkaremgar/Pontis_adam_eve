from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.config import PUBLIC_APP_URL
from app.db.repositories import OutreachEventRepository, UserRepository
from app.db.session import SessionLocal
from app.services.candidate_service import apply_feedback, fetch_ranked_candidates
from app.services.ats_lifecycle_service import transition_candidate_ats_state
from app.services.hiring_service import create_hiring_job
from app.services.interview_invite_service import send_interview_invite
from app.services.orchestration_service import (
    complete_voice_handoff,
    get_orchestration_session,
    handle_slack_action,
    prepare_voice_handoff,
    process_slack_answer,
    start_or_resume_slack_intake,
    start_voice_handoff,
)
from app.services.recruiter_preference_round_service import (
    bootstrap_preference_calibration_session,
    record_preference_calibration_choice,
)
from app.services.slack_integration import (
    build_calibration_blocks,
    build_candidate_blocks,
    extract_button_action,
    parse_slack_command_form,
    post_slack_message,
    post_slack_message_with_result,
    update_calibration_message_blocks,
    update_candidate_message_blocks,
    update_slack_message,
    verify_slack_signature,
)
from app.db.repositories import OrchestrationSessionRepository
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


def _send_slack_message_sync(*, channel_id: str, text: str, blocks: list[dict] | None = None, thread_ts: str | None = None) -> bool:
    try:
        return asyncio.run(post_slack_message(channel_id=channel_id, text=text, blocks=blocks, thread_ts=thread_ts))
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("slack_message_post_failed channel_id=%s error=%s", channel_id, str(exc), exc_info=exc)
        return False


def _send_orchestration_message_sync(*, channel_id: str, text: str, blocks: list[dict] | None = None, thread_ts: str | None = None) -> dict | None:
    try:
        return asyncio.run(post_slack_message_with_result(channel_id=channel_id, text=text, blocks=blocks, thread_ts=thread_ts))
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("slack_orchestration_message_failed channel_id=%s error=%s", channel_id, str(exc), exc_info=exc)
        return None


def _build_orchestration_blocks(
    *,
    session_id: str,
    question_key: str,
    question: str,
    voice_url: str = "",
    include_actions: bool = False,
) -> list[dict]:
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Adam*: {question}"},
        }
    ]
    if not include_actions:
        return blocks
    actions = [
        {
            "type": "button",
            "action_id": "continue_in_slack",
            "text": {"type": "plain_text", "text": "Continue in Slack"},
            "value": f"continue_in_slack:{session_id}:{question_key}",
        },
        {
            "type": "button",
            "action_id": "continue_with_voice",
            "text": {"type": "plain_text", "text": "Continue with Voice"},
            "value": f"continue_with_voice:{session_id}:{question_key}",
        },
    ]
    if voice_url:
        actions[1]["url"] = voice_url
    blocks.append({"type": "actions", "elements": actions})
    return blocks


def _run_orchestration_intake_start(*, team_id: str, channel_id: str, user_id: str) -> None:
    try:
        with SessionLocal() as db:
            result = start_or_resume_slack_intake(
                db=db,
                slack_team_id=team_id,
                slack_channel_id=channel_id,
                slack_user_id=user_id,
                reuse_existing_session=False,
            )
            session = result.get("session") or {}
            session_id = str(session.get("id") or "").strip()
            question = str(result.get("question") or "").strip()
            question_key = str(result.get("questionKey") or "path_selection").strip()
            path_selection_needed = bool(result.get("pathSelectionNeeded"))
            thread_ts = str(session.get("slackThreadTs") or "").strip() or None

            if not session_id:
                logger.error("orchestration_start_missing_session_id channel_id=%s user_id=%s", channel_id, user_id)
                return

            if path_selection_needed:
                try:
                    voice_data = prepare_voice_handoff(db=db, session_id=session_id)
                    voice_url = str(voice_data.get("voiceUrl") or "").strip()
                    if voice_url.startswith("/"):
                        voice_url = f"{PUBLIC_APP_URL}{voice_url}"
                except Exception as exc:
                    logger.warning("orchestration_voice_prep_failed session_id=%s error=%s", session_id, str(exc), exc_info=exc)
                    voice_url = ""
                response = _send_orchestration_message_sync(
                    channel_id=channel_id,
                    text=question,
                    blocks=_build_orchestration_blocks(
                        session_id=session_id,
                        question_key=question_key,
                        question=question,
                        voice_url=voice_url,
                        include_actions=True,
                    ),
                    thread_ts=thread_ts,
                )
            else:
                response = _send_orchestration_message_sync(
                    channel_id=channel_id,
                    text=question,
                    blocks=_build_orchestration_blocks(
                        session_id=session_id,
                        question_key=question_key,
                        question=question,
                        include_actions=False,
                    ),
                    thread_ts=thread_ts,
                )
            posted_ts = ""
            if isinstance(response, dict):
                posted_ts = str(response.get("ts") or "").strip()
                if not posted_ts and isinstance(response.get("message"), dict):
                    posted_ts = str(response["message"].get("ts") or "").strip()
            if posted_ts:
                session = OrchestrationSessionRepository(db).get(session_id)
                if session and not session.slack_thread_ts:
                    session.slack_thread_ts = posted_ts
                    session.updated_at = datetime.now(timezone.utc)
                    db.commit()
    except Exception as exc:
        logger.error("orchestration_start_failed channel_id=%s error=%s", channel_id, str(exc), exc_info=exc)


def _run_orchestration_message_event(*, team_id: str, channel_id: str, user_id: str, thread_ts: str, text: str, ts: str) -> None:
    try:
        with SessionLocal() as db:
            result = process_slack_answer(
                db=db,
                slack_team_id=team_id,
                slack_channel_id=channel_id,
                slack_user_id=user_id,
                thread_ts=thread_ts,
                answer=text,
                timestamp=ts,
            )
            question = str(result.get("nextQuestion") or "").strip()
            question_key = str(result.get("nextQuestionKey") or "").strip()
            if result.get("completed"):
                session = result.get("session") or {}
                if session.get("jobId"):
                    _send_orchestration_message_sync(
                        channel_id=channel_id,
                        text=" Intake complete. Calibration is now starting.",
                        thread_ts=thread_ts,
                    )
                else:
                    _send_orchestration_message_sync(
                        channel_id=channel_id,
                        text=" Intake captured.",
                        thread_ts=thread_ts,
                    )
                return

            if result.get("pathSelectionNeeded"):
                try:
                    voice_data = prepare_voice_handoff(db=db, session_id=str((result.get("session") or {}).get("id") or "").strip())
                    voice_url = str(voice_data.get("voiceUrl") or "").strip()
                    if voice_url.startswith("/"):
                        voice_url = f"{PUBLIC_APP_URL}{voice_url}"
                except Exception as exc:
                    logger.warning("orchestration_voice_prep_failed event_session_id=%s error=%s", str((result.get("session") or {}).get("id") or "").strip(), str(exc), exc_info=exc)
                    voice_url = ""
                _send_orchestration_message_sync(
                    channel_id=channel_id,
                    text=question or "Core intake looks good. Choose how you'd like to continue.",
                    blocks=_build_orchestration_blocks(
                        session_id=str((result.get("session") or {}).get("id") or "").strip(),
                        question_key=question_key or "path_selection",
                        question=question or "Core intake looks good. Choose how you'd like to continue.",
                        voice_url=voice_url,
                        include_actions=True,
                    ),
                    thread_ts=thread_ts,
                )
            elif question:
                _send_orchestration_message_sync(
                    channel_id=channel_id,
                    text=question,
                    blocks=_build_orchestration_blocks(
                        session_id=str((result.get("session") or {}).get("id") or "").strip(),
                        question_key=question_key or "adaptive_followup",
                        question=question,
                        include_actions=False,
                    ),
                    thread_ts=thread_ts,
                )
    except Exception as exc:
        logger.error("orchestration_message_event_failed channel_id=%s error=%s", channel_id, str(exc), exc_info=exc)


def _run_orchestration_action_event(
    *,
    action: str,
    session_id: str,
    channel_id: str,
    user_id: str,
    thread_ts: str,
    message_ts: str,
    question_key: str = "",
) -> None:
    try:
        with SessionLocal() as db:
            result = handle_slack_action(
                db=db,
                action=action,
                session_id=session_id,
                slack_channel_id=channel_id,
                slack_user_id=user_id,
                thread_ts=thread_ts,
                question_key=question_key,
            )

            if result.get("duplicate"):
                logger.info("orchestration_action_duplicate_acked session_id=%s action=%s", session_id, action)
                return

            if action in {"continue_in_slack", "resume_intake"}:
                question = str(result.get("question") or "").strip()
                if question:
                    _send_orchestration_message_sync(
                        channel_id=channel_id,
                        text=question,
                        blocks=_build_orchestration_blocks(
                            session_id=session_id,
                            question_key=str(result.get("questionKey") or question_key or "adaptive_followup"),
                            question=question,
                            voice_url=str(result.get("voiceUrl") or ""),
                            include_actions=False,
                        ),
                        thread_ts=thread_ts,
                    )
                return

            if action in {"confirm_intake", "start_sourcing"}:
                status = str(result.get("status") or "").strip()
                calibration = result.get("calibration") or {}
                if status == "needs_clarification":
                    _send_orchestration_message_sync(
                        channel_id=channel_id,
                        text=str(result.get("question") or "We need one more detail before sourcing."),
                        blocks=_build_orchestration_blocks(
                            session_id=session_id,
                            question_key=str(result.get("questionKey") or question_key or "adaptive_followup"),
                            question=str(result.get("question") or "We need one more detail before sourcing."),
                            include_actions=False,
                        ),
                        thread_ts=thread_ts,
                    )
                elif isinstance(calibration, dict) and calibration.get("current_pair"):
                    current_pair = calibration.get("current_pair") or {}
                    blocks = build_calibration_blocks(
                        job_id=str(result.get("jobId") or ""),
                        calibration_set=current_pair if isinstance(current_pair, dict) else {},
                        current_index=int(calibration.get("current_round_index") or 1),
                        total_sets=len(calibration.get("archetype_sets") or []),
                    )
                    _send_orchestration_message_sync(
                        channel_id=channel_id,
                        text="Calibration is ready. Pick your preferred archetypes before sourcing starts.",
                        blocks=blocks,
                        thread_ts=thread_ts,
                    )
                else:
                    _send_orchestration_message_sync(
                        channel_id=channel_id,
                        text="✅ Intake confirmed. Calibration is now starting.",
                        thread_ts=thread_ts,
                    )
                return

            if action == "cancel_search":
                _send_orchestration_message_sync(
                    channel_id=channel_id,
                    text="🛑 Search cancelled.",
                    thread_ts=thread_ts,
                )
                return

            if action == "continue_with_voice":
                voice_url = str(result.get("voiceUrl") or "").strip()
                if voice_url.startswith("/"):
                    voice_url = f"{PUBLIC_APP_URL}{voice_url}"
                _send_orchestration_message_sync(
                    channel_id=channel_id,
                    text=f"Open the voice intake here: {voice_url}",
                    thread_ts=thread_ts,
                )
    except Exception as exc:
        logger.error("orchestration_action_event_failed session_id=%s action=%s error=%s", session_id, action, str(exc), exc_info=exc)


def _run_calibration_candidate_delivery_event(*, channel_id: str, job_id: str, recruiter_id: str) -> None:
    try:
        with SessionLocal() as db:
            candidates = fetch_ranked_candidates(
                db=db,
                job_id=job_id,
                mode="volume",
                refresh=True,
                recruiter_id=recruiter_id,
                request_source="slack",
            )
            reachable_candidates = []
            for candidate in candidates:
                email = str(getattr(candidate, "email", "") or "").strip()
                is_mock = bool(getattr(candidate, "isMockEmail", False))
                if not email or is_mock:
                    continue
                reachable_candidates.append(candidate)
                if len(reachable_candidates) >= 6:
                    break

            logger.info(
                "slack_calibrated_candidates_ready channel_id=%s job_id=%s recruiter_id=%s count=%s",
                channel_id,
                job_id,
                recruiter_id,
                len(reachable_candidates),
            )

            if not reachable_candidates:
                _send_slack_message_sync(
                    channel_id=channel_id,
                    text="?? Calibration is complete, but no reachable candidates were found yet. Adam will keep sourcing.",
                )
                return

            blocks = build_candidate_blocks(job_id=job_id, candidates=reachable_candidates)
            posted = _send_slack_message_sync(
                channel_id=channel_id,
                text="Top candidates",
                blocks=blocks,
            )
            if not posted:
                _send_slack_message_sync(channel_id=channel_id, text="?? Failed to deliver shortlist. Please try again.")
    except Exception as exc:
        logger.error("slack_calibration_delivery_failed channel_id=%s job_id=%s error=%s", channel_id, job_id, str(exc), exc_info=exc)
        _send_slack_message_sync(channel_id=channel_id, text="?? Failed to deliver shortlist. Please try again.")


def _run_slack_hiring_pipeline(*, team_id: str, channel_id: str, text: str, user_id: str) -> None:
    logger.info("slack_request_received channel_id=%s text=%s", channel_id, text)
    try:
        with SessionLocal() as db:
            result = start_or_resume_slack_intake(
                db=db,
                slack_team_id=team_id,
                slack_channel_id=channel_id,
                slack_user_id=user_id,
                initial_brief=text,
                reuse_existing_session=False,
            )
            session = result.get("session") or {}
            session_id = str(session.get("id") or "").strip()
            question = str(result.get("question") or "").strip()
            question_key = str(result.get("questionKey") or "company_name").strip()
            path_selection_needed = bool(result.get("pathSelectionNeeded"))
            thread_ts = str(session.get("slackThreadTs") or "").strip() or None
            voice_url = ""
            if path_selection_needed and session_id:
                try:
                    voice_data = prepare_voice_handoff(db=db, session_id=session_id)
                    voice_url = str(voice_data.get("voiceUrl") or "").strip()
                    if voice_url.startswith("/"):
                        voice_url = f"{PUBLIC_APP_URL}{voice_url}"
                except Exception as exc:
                    logger.warning("slack_voice_prep_failed session_id=%s error=%s", session_id, str(exc), exc_info=exc)
                    voice_url = ""
            posted = _send_slack_message_sync(
                channel_id=channel_id,
                text=question or "Adam is gathering the hiring brief in Slack.",
                blocks=_build_orchestration_blocks(
                    session_id=session_id,
                    question_key=question_key,
                    question=question or "Adam is gathering the hiring brief in Slack.",
                    voice_url=voice_url,
                    include_actions=path_selection_needed,
                ),
                thread_ts=thread_ts,
            )
            if not posted:
                _send_slack_message_sync(channel_id=channel_id, text="?? Failed to start the intake. Please try again.")
    except Exception as exc:
        logger.error("slack_hiring_pipeline_failed channel_id=%s error=%s", channel_id, str(exc), exc_info=exc)
        _send_slack_message_sync(channel_id=channel_id, text="?? Failed to start the intake. Please try again.")


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
    team_id: str = Form(default=""),
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

        if not command.text:
            background_tasks.add_task(
                _run_orchestration_intake_start,
                team_id=team_id,
                channel_id=command.channel_id,
                user_id=command.user_id,
            )
            return JSONResponse(
                status_code=200,
                content={
                    "response_type": "ephemeral",
                    "text": "",
                },
            )

        background_tasks.add_task(
            _run_slack_hiring_pipeline,
            team_id=team_id,
            channel_id=command.channel_id,
            text=command.text,
            user_id=command.user_id,
        )

        return JSONResponse(
            status_code=200,
            content={
                "response_type": "ephemeral",
                "text": "",
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


@router.post("/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    _verify_request(request, raw_body)
    payload = json.loads(raw_body.decode("utf-8") or "{}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Slack event payload")

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if payload.get("type") != "event_callback":
        return {"ok": True}

    event = payload.get("event") or {}
    if not isinstance(event, dict):
        return {"ok": True}
    if event.get("type") != "message":
        return {"ok": True}
    if event.get("bot_id") or event.get("subtype"):
        return {"ok": True}

    text = str(event.get("text") or "").strip()
    if not text:
        return {"ok": True}

    thread_ts = str(event.get("thread_ts") or event.get("ts") or "").strip()
    channel_id = str(event.get("channel") or "").strip()
    user_id = str(event.get("user") or "").strip()
    team = payload.get("team")
    team_id = str(payload.get("team_id") or (team.get("id") if isinstance(team, dict) else team or "")).strip()
    if not thread_ts or not channel_id or not user_id:
        return {"ok": True}

    background_tasks.add_task(
        _run_orchestration_message_event,
        team_id=team_id,
        channel_id=channel_id,
        user_id=user_id,
        thread_ts=thread_ts,
        text=text,
        ts=str(event.get("ts") or "").strip(),
    )
    return {"ok": True}


@router.get("/orchestration/sessions/{token}")
def get_orchestration_session_route(token: str, request: Request):
    with SessionLocal() as db:
        payload = get_orchestration_session(db=db, token=token)
        return JSONResponse(status_code=200, content={"success": True, "data": payload})


@router.post("/orchestration/voice/start/{token}")
def start_orchestration_voice_route(token: str, request: Request):
    with SessionLocal() as db:
        payload = start_voice_handoff(db=db, token=token)
        return JSONResponse(status_code=200, content={"success": True, "data": payload})


@router.post("/orchestration/voice/complete/{token}")
async def complete_orchestration_voice_route(token: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid request payload")
    transcript = str(body.get("transcript") or "").strip()
    voice_notes = body.get("voiceNotes")
    if not isinstance(voice_notes, list):
        voice_notes = []
    with SessionLocal() as db:
        payload = complete_voice_handoff(db=db, token=token, transcript=transcript, voice_notes=[str(item) for item in voice_notes])
        session = payload.get("session") or {}
        calibration = payload.get("calibration") or (payload.get("finalization") or {}).get("calibration") or {}
        if payload.get("completed") and isinstance(calibration, dict) and calibration.get("current_pair"):
            channel_id = str(session.get("slackChannelId") or session.get("slack_channel_id") or "").strip()
            thread_ts = str(session.get("slackThreadTs") or session.get("slack_thread_ts") or "").strip() or None
            job_id = str((payload.get("finalization") or {}).get("jobId") or session.get("jobId") or session.get("job_id") or "").strip()
            if channel_id and job_id:
                blocks = build_calibration_blocks(
                    job_id=job_id,
                    calibration_set=calibration.get("current_pair") if isinstance(calibration.get("current_pair"), dict) else {},
                    current_index=int(calibration.get("current_round_index") or 1),
                    total_sets=len(calibration.get("archetype_sets") or []),
                )
                await post_slack_message(
                    channel_id=channel_id,
                    text="Calibration is ready. Pick the archetype that best matches your hiring style.",
                    blocks=blocks,
                    thread_ts=thread_ts,
                )
        return JSONResponse(status_code=200, content={"success": True, "data": payload})


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
        action = ""
        candidate_id = ""
        job_id = ""
        calibration_set_id = ""
        session_id = ""
        question_key = ""
        message = payload.get("message") or {}
        message_ts = str(message.get("ts") or "").strip()
        channel_id = str((payload.get("channel") or {}).get("id") or "").strip()

        try:
            action, candidate_id, job_id, calibration_set_id = extract_button_action(payload)
            if action in {"continue_in_slack", "resume_intake", "cancel_search", "confirm_intake", "start_sourcing", "continue_with_voice"}:
                parts = str((payload.get("actions") or [{}])[0].get("value") or "").split(":")
                session_id = parts[1].strip() if len(parts) > 1 else ""
                question_key = parts[2].strip() if len(parts) > 2 else ""
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Slack interaction payload")

        action = action.strip().lower()
        if action == "calibration_select":
            logger.info(
                "slack_calibration_action action=%s calibration_set_id=%s archetype_id=%s job_id=%s channel_id=%s",
                action,
                calibration_set_id,
                candidate_id,
                job_id,
                channel_id,
            )

            if not message_ts:
                raise HTTPException(status_code=400, detail="Missing message timestamp")
            if not job_id:
                raise HTTPException(status_code=400, detail="Missing calibration job")

            with SessionLocal() as db:
                calibration_result = record_preference_calibration_choice(
                    db=db,
                    recruiter_id=str((payload.get("user") or {}).get("id") or "").strip(),
                    job_id=job_id,
                    selected_candidate_id=candidate_id,
                    calibration_set_id=calibration_set_id,
                )
                db.commit()

            updated_blocks = update_calibration_message_blocks(
                blocks=list(message.get("blocks") or []),
                job_id=job_id,
                calibration_set_id=calibration_set_id,
                archetype_id=candidate_id,
                decision=action,
            )
            update_ok = await update_slack_message(
                channel_id=channel_id,
                ts=message_ts,
                blocks=updated_blocks,
                text="Calibration choice saved",
            )
            if not update_ok:
                logger.warning(
                    "slack_calibration_message_update_failed channel_id=%s message_ts=%s job_id=%s calibration_set_id=%s archetype_id=%s",
                    channel_id,
                    message_ts,
                    job_id,
                    calibration_set_id,
                    candidate_id,
                )

            calibration_stage = str(calibration_result.get("stage") or "").strip()
            if calibration_stage == "real_sourcing_ready":
                background_tasks.add_task(
                    _run_calibration_candidate_delivery_event,
                    channel_id=channel_id,
                    job_id=job_id,
                    recruiter_id=str((payload.get("user") or {}).get("id") or "").strip(),
                )
                await post_slack_message(
                    channel_id=channel_id,
                    text="✅ Calibration complete. Adam is now sourcing the strongest reachable candidates.",
                    thread_ts=message_ts or None,
                )
            else:
                current_pair = calibration_result.get("current_pair") or {}
                next_blocks = build_calibration_blocks(
                    job_id=job_id,
                    calibration_set=current_pair if isinstance(current_pair, dict) else {},
                    current_index=int(calibration_result.get("current_round_index") or 1),
                    total_sets=len(calibration_result.get("archetype_sets") or []),
                )
                await post_slack_message(
                    channel_id=channel_id,
                    text="🧭 Preference calibrated. Pick the next archetype that best matches your hiring style.",
                    blocks=next_blocks,
                    thread_ts=message_ts or None,
                )
            logger.info(
                "slack_calibration_processed job_id=%s archetype_id=%s stage=%s",
                job_id,
                candidate_id,
                calibration_stage,
            )
            return {"ok": True}

        if action in {"select", "save", "reject", "advance", "archive"}:
            logger.info(
                "slack_button_action action=%s candidate_id=%s job_id=%s channel_id=%s",
                action,
                candidate_id,
                job_id,
                channel_id,
            )

            if not message_ts:
                raise HTTPException(status_code=400, detail="Missing message timestamp")

            with SessionLocal() as db:
                outreach_repo = OutreachEventRepository(db)
                existing_outreach = outreach_repo.get(job_id=job_id, candidate_id=candidate_id)
                if action == "select" and existing_outreach and (existing_outreach.status or "").strip().lower() in {"queued", "sending", "sent", "delivered"}:
                    await post_slack_message(
                        channel_id=channel_id,
                        text="\u26a0\ufe0f This candidate has already been processed.",
                    )
                    return {"ok": True, "duplicate": True}

                if action in {"select", "save"}:
                    result = apply_feedback(
                        db=db,
                        job_id=job_id,
                        candidate_id=candidate_id,
                        action="accept",
                    )
                elif action == "advance":
                    result = transition_candidate_ats_state(
                        db=db,
                        job_id=job_id,
                        candidate_id=candidate_id,
                        to_status="advanced",
                        source="slack",
                        reason="slack_advance",
                        metadata={"action": action, "channelId": channel_id, "messageTs": message_ts},
                    )
                elif action == "archive":
                    result = transition_candidate_ats_state(
                        db=db,
                        job_id=job_id,
                        candidate_id=candidate_id,
                        to_status="archived",
                        source="slack",
                        reason="slack_archive",
                        metadata={"action": action, "channelId": channel_id, "messageTs": message_ts},
                    )
                else:
                    result = apply_feedback(
                        db=db,
                        job_id=job_id,
                        candidate_id=candidate_id,
                        action="reject",
                    )
                db.commit()

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

            if action == "select":
                response_text = "\u2705 Candidate selected"
            elif action == "save":
                response_text = "\U0001f4be Candidate saved"
            elif action == "advance":
                response_text = "\u27a1\ufe0f Candidate advanced"
            elif action == "archive":
                response_text = "\U0001f5d1\ufe0f Candidate archived"
            else:
                response_text = "\u274c Candidate rejected"
            await post_slack_message(channel_id=channel_id, text=response_text)
            logger.info(
                "slack_interaction_processed job_id=%s candidate_id=%s action=%s result=%s",
                job_id,
                candidate_id,
                action,
                json.dumps(result, ensure_ascii=False),
            )
            if action == "save":
                await post_slack_message(channel_id=channel_id, text="💾 Candidate saved for future ranking", thread_ts=message_ts or None)
                return {"ok": True}
            return {"ok": True}

        if action in {"continue_in_slack", "resume_intake", "cancel_search", "confirm_intake", "start_sourcing", "continue_with_voice"}:
            if not session_id:
                raise HTTPException(status_code=400, detail="Missing orchestration session")
            background_tasks.add_task(
                _run_orchestration_action_event,
                action=action,
                session_id=session_id,
                channel_id=channel_id,
                user_id=str((payload.get("user") or {}).get("id") or "").strip(),
                thread_ts=str((message or {}).get("thread_ts") or message_ts or "").strip(),
                message_ts=message_ts,
                question_key=question_key,
            )
            return {"ok": True}

        if action in {"continue_in_slack", "resume_intake", "cancel_search", "confirm_intake", "start_sourcing", "continue_with_voice"}:
            if not session_id:
                raise HTTPException(status_code=400, detail="Missing orchestration session")
            with SessionLocal() as db:
                result = handle_slack_action(
                    db=db,
                    action=action,
                    session_id=session_id,
                    slack_channel_id=channel_id,
                    slack_user_id=str((payload.get("user") or {}).get("id") or "").strip(),
                    thread_ts=str((message or {}).get("thread_ts") or message_ts or "").strip(),
                )

                if action in {"continue_in_slack", "resume_intake"}:
                    question = str(result.get("question") or "").strip()
                    if question:
                        await post_slack_message(
                            channel_id=channel_id,
                            text=question,
                            thread_ts=message_ts or None,
                            blocks=_build_orchestration_blocks(
                                session_id=session_id,
                                question_key=str(result.get("questionKey") or question_key or "adaptive_followup"),
                                question=question,
                                voice_url=str(result.get("voiceUrl") or ""),
                                include_actions=False,
                            ),
                        )
                elif action in {"confirm_intake", "start_sourcing"}:
                    await post_slack_message(
                        channel_id=channel_id,
                        text="✅ Intake confirmed. Calibration is now starting.",
                        thread_ts=message_ts or None,
                    )
                elif action == "cancel_search":
                    await post_slack_message(
                        channel_id=channel_id,
                        text="🛑 Search cancelled.",
                        thread_ts=message_ts or None,
                    )
                elif action == "continue_with_voice":
                    voice_url = str(result.get("voiceUrl") or "").strip()
                    if voice_url.startswith("/"):
                        voice_url = f"{PUBLIC_APP_URL}{voice_url}"
                    await post_slack_message(
                        channel_id=channel_id,
                        text=f"Open the voice intake here: {voice_url}",
                        thread_ts=message_ts or None,
                    )
            return {"ok": True}

        raise HTTPException(status_code=400, detail="Unsupported action")
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



