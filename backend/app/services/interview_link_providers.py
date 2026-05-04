from __future__ import annotations

import logging
from typing import Any, Callable
from urllib.parse import urlencode

from app.core.config import (
    BOOKING_PROVIDER,
    BOOKING_PROVIDER_URL,
    INTERVIEW_PROVIDER,
    INTERVIEW_PROVIDER_URL,
    PUBLIC_APP_URL,
)

logger = logging.getLogger(__name__)


def _string_field(item: Any, *names: str) -> str:
    for name in names:
        if isinstance(item, dict):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _base_url() -> str:
    return (PUBLIC_APP_URL or "").rstrip("/")


def _build_relative_link(path: str, params: dict[str, str]) -> str:
    query = urlencode({key: value for key, value in params.items() if value})
    return f"{path}?{query}" if query else path


def _build_absolute_link(path: str, params: dict[str, str]) -> str:
    base_url = _base_url()
    relative = _build_relative_link(path, params)
    return f"{base_url}{relative}" if base_url else relative


def _placeholder_booking_link(candidate: Any, job: Any) -> str:
    return _build_absolute_link(
        "/booking/placeholder",
        {
            "candidateId": _string_field(candidate, "id", "candidate_id"),
            "candidateName": _string_field(candidate, "name"),
            "jobId": _string_field(job, "id", "job_id"),
            "jobTitle": _string_field(job, "title"),
        },
    )


def _placeholder_interview_link(candidate: Any, job: Any, scheduled_time: Any) -> str:
    return _build_absolute_link(
        "/interview/placeholder",
        {
            "candidateId": _string_field(candidate, "id", "candidate_id"),
            "candidateName": _string_field(candidate, "name"),
            "jobId": _string_field(job, "id", "job_id"),
            "jobTitle": _string_field(job, "title"),
            "scheduledAt": str(scheduled_time or "").strip(),
        },
    )


def _configured_booking_link(candidate: Any, job: Any) -> str:
    if BOOKING_PROVIDER_URL:
        return BOOKING_PROVIDER_URL
    logger.debug("booking_provider_url_missing provider=%s using_placeholder=true", BOOKING_PROVIDER)
    return _placeholder_booking_link(candidate, job)


def _configured_interview_link(candidate: Any, job: Any, scheduled_time: Any) -> str:
    if INTERVIEW_PROVIDER_URL:
        return INTERVIEW_PROVIDER_URL
    logger.debug("interview_provider_url_missing provider=%s using_placeholder=true", INTERVIEW_PROVIDER)
    return _placeholder_interview_link(candidate, job, scheduled_time)


_BOOKING_LINK_PROVIDERS: dict[str, Callable[[Any, Any], str]] = {
    "placeholder": _placeholder_booking_link,
    "calendly": _configured_booking_link,
}

_INTERVIEW_LINK_PROVIDERS: dict[str, Callable[[Any, Any, Any], str]] = {
    "placeholder": _placeholder_interview_link,
    "zoom": _configured_interview_link,
}


def get_booking_link(candidate: Any, job: Any) -> str:
    provider = (BOOKING_PROVIDER or "placeholder").strip().lower()
    handler = _BOOKING_LINK_PROVIDERS.get(provider, _placeholder_booking_link)
    return handler(candidate, job)


def get_interview_link(candidate: Any, job: Any, scheduled_time: Any) -> str:
    provider = (INTERVIEW_PROVIDER or "placeholder").strip().lower()
    handler = _INTERVIEW_LINK_PROVIDERS.get(provider, _placeholder_interview_link)
    return handler(candidate, job, scheduled_time)
