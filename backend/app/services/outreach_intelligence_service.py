from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

_INTERESTED_KEYWORDS = (
    "interested",
    "sounds good",
    "open to discuss",
    "happy to proceed",
    "keen to explore",
    "let's talk",
    "lets talk",
    "yes",
)
_NOT_INTERESTED_KEYWORDS = (
    "not interested",
    "no thanks",
    "pass",
    "decline",
    "not looking",
)
_MORE_INFO_KEYWORDS = (
    "tell me more",
    "share details",
    "salary",
    "compensation",
    "job description",
    "benefits",
    "scope",
)
_FOLLOW_UP_LATER_KEYWORDS = (
    "follow up later",
    "follow-up later",
    "reconnect next month",
    "next month",
    "later this month",
    "reach out later",
    "circle back",
    "reach back out",
)
_OUT_OF_OFFICE_KEYWORDS = (
    "out of office",
    "ooo",
    "on vacation",
    "away from office",
    "back next week",
    "back next month",
)
_INVALID_CONTACT_KEYWORDS = (
    "unsubscribe",
    "remove me",
    "stop emailing",
    "do not contact",
    "do not email",
    "invalid",
    "undeliverable",
    "bounce",
    "mailbox full",
    "delivery failed",
    "user unknown",
)
_NEGATIVE_RESPONSE_KEYWORDS = (
    "not a fit",
    "hard no",
    "already accepted",
    "already have",
    "not moving forward",
    "not looking right now",
)


@dataclass(frozen=True)
class OutreachEngagementSnapshot:
    engagement_score: float
    reply_likelihood_score: float
    responsiveness_score: float
    open_count: int
    reply_count: int
    reply_state: str
    archive_reason: str
    follow_up_delay_days: int | None


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def classify_reply_state(*, text: str = "", subject: str = "", raw_event: dict[str, Any] | None = None) -> str:
    raw_event = raw_event or {}
    content = " ".join(
        _normalize_text(value)
        for value in (
            text,
            subject,
            raw_event.get("snippet"),
            raw_event.get("body"),
        )
        if str(value or "").strip()
    ).strip()
    if not content:
        return "NEED_MORE_INFO"
    if _contains_any(content, _INVALID_CONTACT_KEYWORDS):
        return "INVALID_CONTACT"
    if _contains_any(content, _OUT_OF_OFFICE_KEYWORDS):
        return "OUT_OF_OFFICE"
    if _contains_any(content, _FOLLOW_UP_LATER_KEYWORDS):
        return "ASKED_TO_FOLLOW_UP_LATER"
    if _contains_any(content, _MORE_INFO_KEYWORDS):
        return "NEED_MORE_INFO"
    if _contains_any(content, _INTERESTED_KEYWORDS):
        return "INTERESTED"
    if _contains_any(content, _NOT_INTERESTED_KEYWORDS):
        return "NOT_INTERESTED"
    if _contains_any(content, _NEGATIVE_RESPONSE_KEYWORDS):
        return "NEGATIVE_RESPONSE"
    return "NEED_MORE_INFO"


def outreach_reply_state_to_ats_state(reply_state: str) -> str:
    normalized = _normalize_text(reply_state)
    if normalized in {"interested", "need_more_info", "asked_to_follow_up_later", "out_of_office"}:
        return "replied_interested"
    if normalized in {"not_interested", "negative_response"}:
        return "replied_not_interested"
    if normalized == "invalid_contact":
        return "archived"
    return "replied_interested"


def outreach_reply_state_to_notification_title(reply_state: str, *, candidate_name: str) -> str:
    normalized = _normalize_text(reply_state)
    labels = {
        "interested": "Candidate replied positively",
        "need_more_info": "Candidate needs more information",
        "asked_to_follow_up_later": "Candidate asked to reconnect later",
        "out_of_office": "Candidate is out of office",
        "not_interested": "Candidate is not interested",
        "negative_response": "Candidate gave a negative response",
        "invalid_contact": "Candidate contact is invalid",
    }
    base = labels.get(normalized, "Candidate reply received")
    return f"{base}: {candidate_name}".strip()


def follow_up_delay_days(*, follow_up_count_after_send: int) -> int | None:
    mapping = {
        0: 5,
        1: 7,
        2: 2,
    }
    return mapping.get(max(0, int(follow_up_count_after_send or 0)))


def follow_up_due_at(*, sent_at: datetime, follow_up_count_after_send: int) -> datetime | None:
    delay_days = follow_up_delay_days(follow_up_count_after_send=follow_up_count_after_send)
    if delay_days is None:
        return None
    base = sent_at if sent_at.tzinfo else sent_at.replace(tzinfo=timezone.utc)
    return base + timedelta(days=delay_days)


def scheduled_reengagement_delay(*, reply_state: str) -> int | None:
    normalized = _normalize_text(reply_state)
    if normalized == "asked_to_follow_up_later":
        return 30
    if normalized == "out_of_office":
        return 7
    return None


def compute_outreach_engagement_snapshot(
    *,
    status: str,
    reply_state: str,
    open_count: int,
    reply_count: int,
    follow_up_count: int,
    sent_at: datetime | None,
    responded_at: datetime | None,
    last_opened_at: datetime | None = None,
    last_replied_at: datetime | None = None,
) -> OutreachEngagementSnapshot:
    normalized_status = _normalize_text(status)
    normalized_reply_state = _normalize_text(reply_state)
    open_count = max(0, int(open_count or 0))
    reply_count = max(0, int(reply_count or 0))
    follow_up_count = max(0, int(follow_up_count or 0))

    engagement_score = 0.08
    if normalized_status in {"sent", "delivered", "opened", "follow_up_sent"}:
        engagement_score += 0.15
    if normalized_status in {"replied", "archived"}:
        engagement_score += 0.22
    engagement_score += min(0.18, open_count * 0.06)
    engagement_score += min(0.2, reply_count * 0.12)

    state_bonus = {
        "interested": 0.55,
        "need_more_info": 0.4,
        "asked_to_follow_up_later": 0.34,
        "out_of_office": 0.18,
        "not_interested": 0.1,
        "negative_response": 0.05,
        "invalid_contact": 0.0,
    }.get(normalized_reply_state, 0.1 if responded_at else 0.0)
    engagement_score += state_bonus
    if follow_up_count:
        engagement_score -= min(0.12, follow_up_count * 0.03)
    engagement_score = max(0.0, min(1.0, engagement_score))

    reply_likelihood_score = 0.1
    reply_likelihood_score += min(0.2, open_count * 0.08)
    reply_likelihood_score += min(0.2, follow_up_count * 0.06)
    if normalized_reply_state in {"interested", "need_more_info", "asked_to_follow_up_later"}:
        reply_likelihood_score += 0.35
    elif normalized_reply_state in {"out_of_office"}:
        reply_likelihood_score += 0.22
    elif normalized_reply_state in {"not_interested", "negative_response"}:
        reply_likelihood_score += 0.08
    if responded_at:
        reply_likelihood_score = max(reply_likelihood_score, 0.85)
    reply_likelihood_score = max(0.0, min(1.0, reply_likelihood_score))

    responsiveness_score = 0.0
    if sent_at and responded_at:
        delta_days = max(0.0, (responded_at - sent_at).total_seconds() / 86400.0)
        if delta_days <= 1:
            responsiveness_score = 1.0
        elif delta_days <= 3:
            responsiveness_score = 0.8
        elif delta_days <= 7:
            responsiveness_score = 0.55
        elif delta_days <= 14:
            responsiveness_score = 0.35
        else:
            responsiveness_score = 0.2
    elif last_opened_at:
        responsiveness_score = 0.25

    archive_reason = ""
    if normalized_reply_state == "invalid_contact":
        archive_reason = "invalid_contact"
    elif normalized_reply_state in {"not_interested", "negative_response"}:
        archive_reason = "negative_response"
    elif normalized_status == "archived":
        archive_reason = "no_response_archive"

    follow_up_delay_days_value = scheduled_reengagement_delay(reply_state=normalized_reply_state)
    return OutreachEngagementSnapshot(
        engagement_score=round(engagement_score, 4),
        reply_likelihood_score=round(reply_likelihood_score, 4),
        responsiveness_score=round(responsiveness_score, 4),
        open_count=open_count,
        reply_count=reply_count,
        reply_state=normalized_reply_state,
        archive_reason=archive_reason,
        follow_up_delay_days=follow_up_delay_days_value,
    )
