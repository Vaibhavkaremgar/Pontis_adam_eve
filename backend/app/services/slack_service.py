from __future__ import annotations

import logging

import requests

from app.core.config import HTTP_TIMEOUT_SECONDS, SLACK_WEBHOOK_URL

logger = logging.getLogger(__name__)


def notify_slack(*, title: str, lines: list[str] | None = None) -> None:
    if not SLACK_WEBHOOK_URL:
        return

    content_lines = [line.strip() for line in (lines or []) if line and line.strip()]
    message = f"*{title}*"
    if content_lines:
        message = f"{message}\n" + "\n".join(f"• {line}" for line in content_lines)

    try:
        requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": message},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Slack notification failed", exc_info=exc)
