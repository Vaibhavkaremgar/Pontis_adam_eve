from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from typing import Any

from openai import OpenAI

from app.core.config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
)
from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)
_llm_disabled_until: datetime | None = None
_llm_disable_reason = ""
_llm_last_error = ""
LLM_DISABLE_COOLDOWN_SECONDS = 300
LLM_RATE_LIMIT_COOLDOWN_SECONDS = 60 * 60


@lru_cache(maxsize=1)
def _groq_client() -> OpenAI:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    return OpenAI(base_url=GROQ_BASE_URL, api_key=GROQ_API_KEY)


def _llm_is_disabled() -> bool:
    global _llm_disabled_until, _llm_disable_reason

    if _llm_disabled_until is None:
        return False
    if datetime.now(timezone.utc) >= _llm_disabled_until:
        _llm_disabled_until = None
        _llm_disable_reason = ""
        logger.info("llm_reenabled_after_cooldown")
        return False
    return True


def _disable_llm(reason: str, *, cooldown_seconds: int = LLM_DISABLE_COOLDOWN_SECONDS) -> None:
    global _llm_disabled_until, _llm_disable_reason, _llm_last_error

    _llm_disabled_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, cooldown_seconds))
    _llm_disable_reason = reason
    _llm_last_error = reason
    logger.warning("llm_disabled reason=%s retry_at=%s", reason, _llm_disabled_until.isoformat())


def _local_fallback(prompt: str, *, expect_json: bool) -> Any:
    if expect_json:
        return {}

    prompt_text = (prompt or "").strip()
    if not prompt_text:
        return ""
    lines = [line.strip("- ").strip() for line in prompt_text.splitlines() if line.strip()]
    if not lines:
        return ""
    snippet = " ".join(lines[:2])[:220].strip()
    return snippet or "Local fallback response."


def _run_chat(client: OpenAI, *, model: str, prompt: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return (response.choices[0].message.content or "").strip()


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "quota" in text or "resource_exhausted" in text


def _extract_json_payload(text: str) -> dict | list | None:
    raw = (text or "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, (dict, list)):
            return parsed
        return None
    except json.JSONDecodeError:
        pass

    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(1))
        if isinstance(parsed, (dict, list)):
            return parsed
    except json.JSONDecodeError:
        return None
    return None


def generate(prompt: str, expect_json: bool = False):
    if _llm_is_disabled() and not GROQ_API_KEY:
        log_metric("llm_usage", model=GROQ_MODEL, disabled=True, fallback=True, expect_json=expect_json, reason=_llm_disable_reason or "disabled")
        return _local_fallback(prompt, expect_json=expect_json)

    if not GROQ_API_KEY:
        log_metric("llm_usage", model=GROQ_MODEL, disabled=True, fallback=True, expect_json=expect_json, reason="GROQ_API_KEY missing")
        return _local_fallback(prompt, expect_json=expect_json)

    try:
        client = _groq_client()
    except Exception as exc:
        _disable_llm(str(exc))
        logger.warning("llm_client_unavailable provider=groq reason=%s", str(exc))
        log_metric("llm_usage", model=GROQ_MODEL, disabled=True, fallback=True, expect_json=expect_json)
        return _local_fallback(prompt, expect_json=expect_json)

    try:
        output = _run_chat(client, model=GROQ_MODEL, prompt=prompt)
        log_metric("llm_usage", model=GROQ_MODEL, provider="groq", disabled=False, fallback=False, expect_json=expect_json)
        if not expect_json:
            return output

        parsed = _extract_json_payload(output)
        if parsed is not None:
            return parsed

        retry_output = _run_chat(client, model=GROQ_MODEL, prompt="Return ONLY valid JSON:\n" + prompt)
        log_metric("llm_usage", model=GROQ_MODEL, provider="groq", disabled=False, fallback=False, expect_json=expect_json, retry=True)
        parsed = _extract_json_payload(retry_output)
        if parsed is not None:
            return parsed
        return _local_fallback(prompt, expect_json=True)
    except Exception as exc:
        logger.warning("llm_generation_failed provider=groq model=%s reason=%s", GROQ_MODEL, str(exc))
        if _is_rate_limit_error(exc):
            _disable_llm(str(exc), cooldown_seconds=LLM_RATE_LIMIT_COOLDOWN_SECONDS)
        else:
            _disable_llm(str(exc))
        log_metric("llm_usage", model=GROQ_MODEL, provider="groq", disabled=True, fallback=True, expect_json=expect_json)
        return _local_fallback(prompt, expect_json=expect_json)


def llm_health() -> dict:
    try:
        status = "disabled" if _llm_is_disabled() or not GROQ_API_KEY else "ok"
        if status == "ok":
            generate("ping")
        return {
            "status": status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "model": GROQ_MODEL,
            "retry_at": _llm_disabled_until.isoformat() if _llm_disabled_until else None,
            "last_error": _llm_last_error,
        }
    except Exception as exc:
        logger.warning("llm_health_check_failed error=%s", str(exc))
        return {
            "status": "error",
            "error": str(exc),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
