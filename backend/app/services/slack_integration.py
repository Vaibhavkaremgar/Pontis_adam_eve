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


def _client_for_token(bot_token: str | None = None):
    token = (bot_token or "").strip()
    if token and WebClient is not None:
        return WebClient(token=token)
    return slack_client


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


def _no_results_blocks(reason: str) -> list[dict[str, Any]]:
    """
    Return Slack blocks for the three no-results / failure scenarios.
    reason: "quota_exhausted" | "provider_disabled" | "zero_found" | "all_filtered" | ""
    """
    if reason == "quota_exhausted":
        icon = "\u23F3"
        title = f"{icon} Live sourcing temporarily unavailable"
        body = (
            "Adam has reached the daily search quota for live LinkedIn sourcing. "
            "Sourcing will resume automatically when the quota resets. "
            "No action needed — candidates will be delivered once sourcing is available again."
        )
    elif reason == "provider_disabled":
        icon = "\u26A0\uFE0F"
        title = f"{icon} Sourcing provider temporarily offline"
        body = (
            "The live sourcing provider is currently unavailable. "
            "Adam will retry automatically. If this persists, contact your admin."
        )
    elif reason == "all_filtered":
        icon = "\U0001F50D"
        title = f"{icon} Candidates found but none passed the ranking filter"
        body = (
            "Adam found profiles but none met the minimum match threshold for this role. "
            "Consider broadening the job criteria — location, experience range, or required skills — "
            "then re-run sourcing."
        )
    else:
        # zero_found or unknown
        icon = "\U0001F9D0"
        title = f"{icon} No strong candidates found yet"
        body = (
            "Adam searched LinkedIn but could not find enough strong profile matches for the current criteria. "
            "Try broadening the location, adjusting required skills, or updating the job description "
            "to improve future sourcing runs."
        )

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{title}*\n{body}",
            },
        }
    ]


def _decision_label(decision: str) -> str:
    normalized = (decision or "").strip().lower()
    if normalized in {"accept", "shortlist", "select", "calibration_select"}:
        return "\u2705 Selected"
    if normalized == "save":
        return "\U0001F4BE Saved"
    if normalized == "reject":
        return "\u274c Rejected"
    return normalized.title() or "Processed"


def _text_value(source: Any, *keys: str) -> str:
    if isinstance(source, dict):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split()).strip()
            if isinstance(value, (int, float, bool)) and str(value).strip():
                return str(value).strip()
    else:
        for key in keys:
            value = getattr(source, key, None)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split()).strip()
            if isinstance(value, (int, float, bool)) and str(value).strip():
                return str(value).strip()
    return ""


def _list_value(source: Any, *keys: str) -> list[str]:
    def visit(item: Any, bucket: list[str]) -> None:
        if item is None:
            return
        if isinstance(item, str):
            cleaned = " ".join(item.split()).strip()
            if not cleaned:
                return
            if any(sep in cleaned for sep in [",", ";", "|"]):
                for piece in cleaned.replace(";", ",").replace("|", ",").split(","):
                    piece = " ".join(piece.split()).strip()
                    if piece:
                        bucket.append(piece)
                return
            bucket.append(cleaned)
            return
        if isinstance(item, dict):
            for key in ("text", "label", "title", "name", "role", "value", "skill", "strength", "signal", "tradeoff"):
                nested = item.get(key)
                if isinstance(nested, str) and nested.strip():
                    visit(nested, bucket)
                    return
            for nested in item.values():
                visit(nested, bucket)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested, bucket)
            return
        cleaned = " ".join(str(item).split()).strip()
        if cleaned:
            bucket.append(cleaned)

    collected: list[str] = []
    if isinstance(source, dict):
        for key in keys:
            visit(source.get(key), collected)
    else:
        for key in keys:
            visit(getattr(source, key, None), collected)

    seen: set[str] = set()
    ordered: list[str] = []
    for item in collected:
        normalized = " ".join(item.split()).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def build_candidate_blocks(*, job_id: str, candidates, no_results_reason: str = ""):
    from typing import Any
    from app.services.candidate_presentation_service import build_candidate_view_model

    candidate_rows = list(candidates)

    if not candidate_rows:
        return _no_results_blocks(no_results_reason or "zero_found")

    blocks = []
    for index, candidate in enumerate(candidate_rows):
        vm = build_candidate_view_model(candidate)
        candidate_id = vm["candidate_id"]
        candidate_name = vm["name"]
        role = vm["role"] or "Unknown role"
        company = vm["company"] or "Unknown company"
        fit_display = vm["fit_score_display"]
        matched_skills = vm["matched_skills"]
        all_skills = vm["all_skills"]
        summary_lines = vm["summary_lines"]
        linkedin_url = vm["linkedin_url"]

        skills_display = (
            ", ".join(matched_skills[:5]) if matched_skills
            else ", ".join(all_skills[:5]) or "Not specified"
        )
        matched_label = "Matched skills" if matched_skills else "Skills"
        summary_text = "\n".join(f"\u2022 {ln}" for ln in summary_lines) if summary_lines else ""
        linkedin_line = (
            f"<{linkedin_url}|View LinkedIn profile>" if linkedin_url
            else "_LinkedIn profile not available_"
        )

        candidate_text = (
            f"*{candidate_name}*\n"
            f"{role} at {company}\n"
            f"Fit score: *{fit_display}*\n"
            f"{matched_label}: {skills_display}\n"
        )
        if summary_text:
            candidate_text += summary_text + "\n"
        candidate_text += linkedin_line

        approve_btn = {
            "type": "button",
            "action_id": "like",
            "text": {"type": "plain_text", "text": "\U0001F44D Approve"},
            "style": "primary",
            "value": f"like:{candidate_id}:{job_id}",
        }
        pass_btn = {
            "type": "button",
            "action_id": "pass",
            "text": {"type": "plain_text", "text": "Pass"},
            "style": "danger",
            "value": f"pass:{candidate_id}:{job_id}",
        }
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": candidate_text}})
        blocks.append({"type": "actions", "block_id": f"hire:{job_id}:{candidate_id}", "elements": [approve_btn, pass_btn]})
        if index < len(candidate_rows) - 1:
            blocks.append({"type": "divider"})
    return blocks


def build_calibration_blocks(*, job_id: str, calibration_set: dict[str, Any], current_index: int, total_sets: int) -> list[dict[str, Any]]:
    set_title = str(calibration_set.get("set_title") or f"Calibration set {current_index}").strip()
    set_theme = str(calibration_set.get("set_theme") or "").strip()
    calibration_set_id = str(calibration_set.get("calibration_set_id") or calibration_set.get("calibrationSetId") or "").strip()
    if not calibration_set_id:
        calibration_set_id = f"calibration-set-{max(1, int(current_index or 1))}"
    archetypes = list(calibration_set.get("archetypes") or [])
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Ideal candidate profile set {current_index}/{total_sets}*\n*{set_title}*\n{set_theme}" if set_theme else f"*Ideal candidate profile set {current_index}/{total_sets}*\n*{set_title}*",
            },
        }
    ]

    for index, archetype in enumerate(archetypes):
        archetype_id = str(archetype.get("id") or "").strip()
        if not archetype_id:
            archetype_id = f"{calibration_set_id}-archetype-{index + 1}"
        profile = archetype.get("profileData") or {}
        archetype_title = _text_value(
            profile,
            "profileTitle",
            "profile_title",
            "candidateHeadline",
            "candidate_headline",
            "title",
        ) or str(archetype.get("name") or archetype.get("role") or "Ideal candidate profile").strip()
        resume_summary = _text_value(profile, "resumeSummary", "resume_summary", "experienceSnapshot", "experience_snapshot") or str(archetype.get("headline") or "").strip()
        typical_background = _text_value(profile, "typicalBackground", "typical_background", "careerPattern", "career_pattern")
        profile = archetype.get("profileData") or {}
        strengths = _list_value(profile, "strongestSkills", "strongest_skills", "technicalStrengths", "technical_strengths", "strengths", "skills")
        typical_companies = _list_value(profile, "typicalCompanies", "typical_companies", "currentCompany", "current_company")
        engineering_style = _text_value(profile, "engineeringStyle", "engineering_style", "workStyle", "work_style", "executionStyle", "execution_style")
        ownership_pattern = _text_value(profile, "ownershipPattern", "ownership_pattern", "ownershipStyle", "ownership_style", "ownershipLevel", "ownership_level")
        tradeoff = _text_value(profile, "tradeoff", "tradeOff")
        why_recruiter_would_prefer_them = _text_value(
            profile,
            "whyRecruiterWouldPreferThem",
            "why_recruiter_would_prefer_them",
            "fitNote",
            "fit_note",
        )

        description_lines = [f"*{archetype_title}*"]
        if resume_summary:
            description_lines.append(f"*Resume summary:* {resume_summary}")
        if typical_background:
            description_lines.append(f"*Typical background:* {typical_background}")
        if strengths:
            description_lines.append(f"*Strongest skills:* {', '.join(str(item) for item in strengths[:5] if str(item).strip())}")
        if typical_companies:
            description_lines.append(f"*Typical companies:* {', '.join(str(item) for item in typical_companies[:5] if str(item).strip())}")
        if engineering_style:
            description_lines.append(f"*Engineering style:* {engineering_style}")
        if ownership_pattern:
            description_lines.append(f"*Ownership pattern:* {ownership_pattern}")
        if tradeoff:
            description_lines.append(f"*Tradeoff:* {tradeoff}")
        if why_recruiter_would_prefer_them:
            description_lines.append(f"*Why recruiter would prefer them:* {why_recruiter_would_prefer_them}")

        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(description_lines)},
            }
        )
        blocks.append(
            {
                "type": "actions",
                "block_id": f"calibration:{job_id}:{calibration_set_id}:{archetype_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "calibration_select",
                        "text": {"type": "plain_text", "text": "Select"},
                        "style": "primary",
                        "value": f"calibration_select:{calibration_set_id}:{archetype_id}:{job_id}",
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
    calibration_set_id: str,
    archetype_id: str,
    decision: str,
) -> list[dict[str, Any]]:
    updated_blocks = copy.deepcopy(blocks)
    target_block_id = f"calibration:{job_id}:{calibration_set_id}"
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
    bot_token: str | None = None,
) -> bool:
    result = await post_slack_message_with_result(
        channel_id=channel_id,
        text=text,
        blocks=blocks,
        thread_ts=thread_ts,
        bot_token=bot_token,
    )
    return bool(result)


async def post_slack_message_with_result(
    *,
    channel_id: str | None,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
    thread_ts: str | None = None,
    bot_token: str | None = None,
) -> dict[str, Any] | None:
    client = _client_for_token(bot_token)
    if not client:
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
            voice_button_url = ""
            voice_button_value = ""
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "actions":
                    continue
                for element in block.get("elements") or []:
                    if not isinstance(element, dict):
                        continue
                    if element.get("action_id") == "continue_with_voice":
                        voice_button_url = str(element.get("url") or "").strip()
                        voice_button_value = str(element.get("value") or "").strip()
                        break
                if voice_button_url or voice_button_value:
                    break
            logger.info(
                "slack_chat_postMessage_payload channel_id=%s voice_button_url=%s voice_button_value=%s blocks=%s",
                target_channel,
                voice_button_url,
                voice_button_value,
                blocks,
            )
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        response = await asyncio.to_thread(client.chat_postMessage, **kwargs)
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
    bot_token: str | None = None,
) -> bool:
    client = _client_for_token(bot_token)
    if not client:
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
        await asyncio.to_thread(client.chat_update, **kwargs)
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
    bot_token: str | None = None,
) -> bool:
    client = _client_for_token(bot_token)
    if not client:
        logger.error("slack_dm_message_skipped missing_bot_token user_id=%s", user_id)
        return False

    target_user = (user_id or "").strip()
    if not target_user:
        logger.error("slack_dm_message_skipped missing_user_id")
        return False
    if not str(target_user).startswith("U"):
        logger.info("slack_dm_skipped not_a_slack_user user_id=%s", target_user)
        return False

    try:
        response = await asyncio.to_thread(client.conversations_open, users=target_user)
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


async def open_slack_dm(user_id: str, bot_token: str | None = None) -> str | None:
    return await open_slack_dm_for_token(user_id=user_id, bot_token=bot_token)


async def open_slack_dm_for_token(user_id: str, bot_token: str | None = None) -> str | None:
    client = _client_for_token(bot_token)
    if not client:
        logger.error("slack_dm_open_failed missing_bot_token user_id=%s", user_id)
        return None

    user = (user_id or "").strip()
    if not user:
        logger.error("slack_dm_open_failed missing_user_id")
        return None

    try:
        response = await asyncio.to_thread(client.conversations_open, users=user)
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


def extract_button_action(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    actions = payload.get("actions") or []
    if not actions:
        raise ValueError("Missing Slack action")

    action = actions[0] or {}
    value = str(action.get("value") or "").strip()
    action_id = str(action.get("action_id") or "").strip().lower()
    if not value:
        raise ValueError("Missing Slack action metadata")

    parts = value.split(":")
    if len(parts) not in {3, 4}:
        raise ValueError("Invalid Slack action value")

    action_value = parts[0].strip()
    calibration_set_id = ""
    if len(parts) == 3:
        candidate_id = parts[1].strip()
        job_id = parts[2].strip()
    else:
        calibration_set_id = parts[1].strip()
        candidate_id = parts[2].strip()
        job_id = parts[3].strip()
    if not action_value:
        action_value = action_id
    return action_value.lower(), candidate_id, job_id, calibration_set_id
