from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import copy
import time
from dataclasses import dataclass
from typing import Any, Iterable

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:  # pragma: no cover - demo/runtime hardening fallback
    WebClient = None  # type: ignore[assignment]

    class SlackApiError(Exception):
        pass

from app.core.config import SLACK_BOT_TOKEN, SLACK_SKIP_SIGNATURE_VERIFICATION, SLACK_SIGNING_SECRET

logger = logging.getLogger(__name__)

SLACK_SIGNATURE_VERSION = "v0"
SLACK_TIMESTAMP_TOLERANCE_SECONDS = 60 * 5

slack_client = WebClient(token=SLACK_BOT_TOKEN) if (SLACK_BOT_TOKEN and WebClient is not None) else None


@dataclass(frozen=True)
class SlackCommandPayload:
    text: str
    user_id: str
    channel_id: str


def verify_slack_signature(*, raw_body: bytes, signature: str, timestamp: str) -> bool:
    if SLACK_SKIP_SIGNATURE_VERIFICATION:
        logger.warning("slack_verification_skipped debug_mode_enabled")
        return True
    if not SLACK_SIGNING_SECRET:
        logger.error("slack_verification_failed missing_signing_secret")
        return False
    if not signature or not timestamp:
        return False

    try:
        request_ts = int(timestamp)
    except ValueError:
        logger.warning("slack_verification_failed invalid_timestamp timestamp=%s", timestamp)
        return False

    now = int(time.time())
    if abs(now - request_ts) > SLACK_TIMESTAMP_TOLERANCE_SECONDS:
        logger.warning(
            "slack_verification_failed stale_request timestamp=%s now=%s tolerance_seconds=%s",
            request_ts,
            now,
            SLACK_TIMESTAMP_TOLERANCE_SECONDS,
        )
        return False

    base_string = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:{raw_body.decode('utf-8')}"
    expected = f"{SLACK_SIGNATURE_VERSION}=" + hmac.new(
        SLACK_SIGNING_SECRET.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _candidate_fit_text(candidate: Any) -> str:
    fit_score = float(getattr(candidate, "fitScore", 0.0) or 0.0)
    return f"{fit_score:.1f}/5"


def _top_skills_text(candidate: Any) -> str:
    skills = getattr(candidate, "skills", None) or []
    if isinstance(skills, str):
        skills = [skills]
    cleaned = [str(skill).strip() for skill in skills if str(skill).strip()]
    return ", ".join(cleaned[:5]) if cleaned else "Not specified"


def _experience_line(explanation: Any) -> str:
    experience_match = str(getattr(explanation, "experienceMatch", "") or "").strip()
    return experience_match or "Experience: Not specified"


def _matched_skills_line(explanation: Any) -> str:
    matched = getattr(explanation, "skillsMatched", None) or []
    if isinstance(matched, str):
        matched = [matched]
    cleaned = [str(skill).strip() for skill in matched if str(skill).strip()]
    return ", ".join(cleaned[:5]) if cleaned else "Not specified"


def _decision_label(decision: str) -> str:
    normalized = (decision or "").strip().lower()
    if normalized in {"accept", "shortlist", "select", "calibration_select"}:
        return "\u2705 Selected"
    if normalized == "save":
        return "\U0001F4BE Saved"
    if normalized == "reject":
        return "\u274c Rejected"
    return normalized.title() or "Processed"


def build_candidate_blocks(*, job_id: str, candidates: Iterable[Any]) -> list[dict[str, Any]]:
    candidate_rows = list(candidates)
    blocks: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidate_rows):
        candidate_id = str(getattr(candidate, "id", "") or "").strip()
        candidate_name = str(getattr(candidate, "name", "") or "Unnamed Candidate").strip()
        candidate_role = str(getattr(candidate, "role", "") or "Unknown role").strip()
        candidate_company = str(getattr(candidate, "company", "") or "Unknown company").strip()
        explanation = getattr(candidate, "explanation", None)
        matched_skills = _matched_skills_line(explanation) if explanation else "Not specified"
        experience_line = _experience_line(explanation) if explanation else "Experience: Not specified"
        candidate_text = (
            f"*{candidate_name}*\n"
            f"{candidate_role} at {candidate_company}\n"
            f"Fit: {_candidate_fit_text(candidate)}\n"
            f"Skills: {_top_skills_text(candidate)}\n"
            f"Matched: {matched_skills}\n"
            f"{experience_line}"
        )

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": candidate_text,
                },
            }
        )
        blocks.append(
            {
                "type": "actions",
                "block_id": f"hire:{job_id}:{candidate_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "select",
                        "text": {"type": "plain_text", "text": "Select"},
                        "style": "primary",
                        "value": f"select:{candidate_id}:{job_id}",
                    },
                    {
                        "type": "button",
                        "action_id": "save",
                        "text": {"type": "plain_text", "text": "Save"},
                        "value": f"save:{candidate_id}:{job_id}",
                    },
                    {
                        "type": "button",
                        "action_id": "reject",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "value": f"reject:{candidate_id}:{job_id}",
                    },
                ],
            }
        )
        if index < len(candidate_rows) - 1:
            blocks.append({"type": "divider"})
    return blocks


def build_calibration_blocks(*, job_id: str, calibration_set: dict[str, Any], current_index: int, total_sets: int) -> list[dict[str, Any]]:
    set_title = str(calibration_set.get("set_title") or f"Calibration set {current_index}").strip()
    set_theme = str(calibration_set.get("set_theme") or "").strip()
    archetypes = list(calibration_set.get("archetypes") or [])
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Preference calibration {current_index}/{total_sets}*\n*{set_title}*\n{set_theme}" if set_theme else f"*Preference calibration {current_index}/{total_sets}*\n*{set_title}*",
            },
        }
    ]

    for index, archetype in enumerate(archetypes):
        archetype_id = str(archetype.get("id") or "").strip()
        archetype_title = str(archetype.get("name") or archetype.get("role") or "Archetype").strip()
        summary = str(archetype.get("summary") or "").strip()
        profile = archetype.get("profileData") or {}
        strengths = profile.get("strengths") or []
        communication_style = str(profile.get("communicationStyle") or "").strip()
        work_style = str(profile.get("workStyle") or "").strip()
        ownership_level = str(profile.get("ownershipLevel") or "").strip()
        ideal_environment = str(profile.get("idealEnvironment") or "").strip()
        execution_style = str(profile.get("executionStyle") or "").strip()
        risk_tolerance = str(profile.get("riskTolerance") or "").strip()
        leadership_signals = profile.get("leadershipSignals") or []

        description_lines = [f"*{archetype_title}*"]
        if summary:
            description_lines.append(summary)
        if strengths:
            description_lines.append(f"*Strengths:* {', '.join(str(item) for item in strengths[:4] if str(item).strip())}")
        if work_style:
            description_lines.append(f"*Work style:* {work_style}")
        if ownership_level:
            description_lines.append(f"*Ownership:* {ownership_level}")
        if ideal_environment:
            description_lines.append(f"*Ideal environment:* {ideal_environment}")
        if communication_style:
            description_lines.append(f"*Communication:* {communication_style}")
        if execution_style:
            description_lines.append(f"*Execution:* {execution_style}")
        if risk_tolerance:
            description_lines.append(f"*Risk tolerance:* {risk_tolerance}")
        if leadership_signals:
            description_lines.append(f"*Leadership signals:* {', '.join(str(item) for item in leadership_signals[:4] if str(item).strip())}")

        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(description_lines)},
            }
        )
        blocks.append(
            {
                "type": "actions",
                "block_id": f"calibration:{job_id}:{current_index}:{archetype_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "calibration_select",
                        "text": {"type": "plain_text", "text": "Select"},
                        "style": "primary",
                        "value": f"calibration_select:{archetype_id}:{job_id}",
                    }
                ],
            }
        )
        if index < len(archetypes) - 1:
            blocks.append({"type": "divider"})
    return blocks


def update_calibration_message_blocks(
    *,
    blocks: list[dict[str, Any]],
    job_id: str,
    archetype_id: str,
    decision: str,
) -> list[dict[str, Any]]:
    updated_blocks = copy.deepcopy(blocks)
    target_block_id = f"calibration:{job_id}"
    label = _decision_label(decision)

    for index, block in enumerate(updated_blocks):
        if block.get("type") != "actions":
            continue
        if not str(block.get("block_id") or "").strip().startswith(target_block_id):
            continue
        if str(block.get("block_id") or "").strip().endswith(f":{archetype_id}"):
            if index > 0 and updated_blocks[index - 1].get("type") == "section":
                section = updated_blocks[index - 1]
                text_obj = section.get("text") or {}
                text_value = str(text_obj.get("text") or "").rstrip()
                if label not in text_value:
                    text_obj["text"] = f"{text_value}\n{label}"
                    section["text"] = text_obj

            updated_blocks[index] = {
                "type": "section",
                "text": {"type": "mrkdwn", "text": label},
            }
            if index + 1 < len(updated_blocks) and updated_blocks[index + 1].get("type") == "divider":
                del updated_blocks[index + 1]
            break

    return updated_blocks


def update_candidate_message_blocks(
    *,
    blocks: list[dict[str, Any]],
    job_id: str,
    candidate_id: str,
    decision: str,
) -> list[dict[str, Any]]:
    updated_blocks = copy.deepcopy(blocks)
    target_block_id = f"hire:{job_id}:{candidate_id}"
    label = _decision_label(decision)

    for index, block in enumerate(updated_blocks):
        if block.get("type") != "actions":
            continue
        if str(block.get("block_id") or "").strip() != target_block_id:
            continue

        if index > 0 and updated_blocks[index - 1].get("type") == "section":
            section = updated_blocks[index - 1]
            text_obj = section.get("text") or {}
            text_value = str(text_obj.get("text") or "").rstrip()
            if label not in text_value:
                text_obj["text"] = f"{text_value}\n{label}"
                section["text"] = text_obj

        updated_blocks[index] = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": label},
        }
        if index + 1 < len(updated_blocks) and updated_blocks[index + 1].get("type") == "divider":
            del updated_blocks[index + 1]
        break

    return updated_blocks


async def post_slack_message(
    *,
    channel_id: str | None,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
    thread_ts: str | None = None,
) -> bool:
    result = await post_slack_message_with_result(
        channel_id=channel_id,
        text=text,
        blocks=blocks,
        thread_ts=thread_ts,
    )
    return bool(result)


async def post_slack_message_with_result(
    *,
    channel_id: str | None,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
    thread_ts: str | None = None,
) -> dict[str, Any] | None:
    if not slack_client:
        logger.error("slack_message_skipped missing_bot_token channel_id=%s", channel_id)
        return None

    target_channel = (channel_id or "").strip()
    if not target_channel:
        logger.error("slack_message_skipped missing_channel_id")
        return None

    try:
        kwargs: dict[str, Any] = {"channel": target_channel, "text": text}
        if blocks is not None:
            kwargs["blocks"] = blocks
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        response = await asyncio.to_thread(slack_client.chat_postMessage, **kwargs)
        return response if isinstance(response, dict) else {"ok": True}
    except SlackApiError as exc:
        error = exc.response.get("error")
        logger.error("slack_message_failed channel_id=%s error=%s", target_channel, error, exc_info=exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("slack_message_failed channel_id=%s error=%s", target_channel, str(exc), exc_info=exc)
        return None


async def update_slack_message(
    *,
    channel_id: str | None,
    ts: str | None,
    blocks: list[dict[str, Any]],
    text: str = "Updated candidate state",
) -> bool:
    if not slack_client:
        logger.error("slack_message_update_skipped missing_bot_token channel_id=%s", channel_id)
        return False

    target_channel = (channel_id or "").strip()
    target_ts = (ts or "").strip()
    if not target_channel or not target_ts:
        logger.error("slack_message_update_skipped missing_channel_or_ts channel_id=%s ts=%s", target_channel, target_ts)
        return False

    try:
        kwargs: dict[str, Any] = {
            "channel": target_channel,
            "ts": target_ts,
            "text": text,
            "blocks": blocks,
        }
        await asyncio.to_thread(slack_client.chat_update, **kwargs)
        return True
    except SlackApiError as exc:
        logger.error(
            "slack_message_update_failed channel_id=%s ts=%s error=%s",
            target_channel,
            target_ts,
            exc.response.get("error"),
            exc_info=exc,
        )
        return False
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("slack_message_update_failed channel_id=%s ts=%s error=%s", target_channel, target_ts, str(exc), exc_info=exc)
        return False


async def send_slack_dm_message(
    *,
    user_id: str | None,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
    thread_ts: str | None = None,
) -> bool:
    if not slack_client:
        logger.error("slack_dm_message_skipped missing_bot_token user_id=%s", user_id)
        return False

    target_user = (user_id or "").strip()
    if not target_user:
        logger.error("slack_dm_message_skipped missing_user_id")
        return False

    try:
        response = await asyncio.to_thread(slack_client.conversations_open, users=target_user)
        channel = response.get("channel") or {}
        dm_channel_id = str(channel.get("id") or "").strip()
        if not dm_channel_id:
            logger.error("slack_dm_message_failed missing_dm_channel_id user_id=%s response=%s", target_user, response)
            return False

        return await post_slack_message(
            channel_id=dm_channel_id,
            text=text,
            blocks=blocks,
            thread_ts=thread_ts,
        )
    except SlackApiError as exc:
        logger.error(
            "slack_dm_message_failed user_id=%s error=%s",
            target_user,
            exc.response.get("error"),
            exc_info=exc,
        )
        return False
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("slack_dm_message_failed user_id=%s error=%s", target_user, str(exc), exc_info=exc)
        return False


async def open_slack_dm(user_id: str) -> str | None:
    if not slack_client:
        logger.error("slack_dm_open_failed missing_bot_token user_id=%s", user_id)
        return None

    user = (user_id or "").strip()
    if not user:
        logger.error("slack_dm_open_failed missing_user_id")
        return None

    try:
        response = await asyncio.to_thread(slack_client.conversations_open, users=user)
        channel = response.get("channel") or {}
        dm_channel_id = channel.get("id")
        if not dm_channel_id:
            logger.error("slack_dm_open_failed missing_channel_id user_id=%s response=%s", user, response)
            return None
        return str(dm_channel_id)
    except SlackApiError as exc:
        logger.error("slack_dm_open_failed user_id=%s error=%s", user, exc.response.get("error"), exc_info=exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("slack_dm_open_failed user_id=%s error=%s", user, str(exc), exc_info=exc)
        return None


def parse_slack_command_form(form_data: Any) -> SlackCommandPayload:
    return SlackCommandPayload(
        text=(form_data.get("text") or "").strip(),
        user_id=(form_data.get("user_id") or "").strip(),
        channel_id=(form_data.get("channel_id") or "").strip(),
    )


def build_processing_text(text: str) -> str:
    return f"Processing your request: {text}"


def parse_interaction_payload(payload_text: str) -> dict[str, Any]:
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Invalid Slack interaction payload")
    return payload


def extract_button_action(payload: dict[str, Any]) -> tuple[str, str, str]:
    actions = payload.get("actions") or []
    if not actions:
        raise ValueError("Missing Slack action")

    action = actions[0] or {}
    value = str(action.get("value") or "").strip()
    action_id = str(action.get("action_id") or "").strip().lower()
    if not value:
        raise ValueError("Missing Slack action metadata")

    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("Invalid Slack action value")

    action_value, candidate_id, job_id = (part.strip() for part in parts)
    if not action_value:
        action_value = action_id
    return action_value.lower(), candidate_id, job_id
