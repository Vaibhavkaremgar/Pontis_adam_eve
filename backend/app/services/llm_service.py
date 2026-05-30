from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from typing import Any

from openai import OpenAI

from app.core.config import (
    GEMINI_API_KEY,
    GEMINI_BASE_URL,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    OPEN_ROUTER_API,
    OPEN_ROUTER_BASE_URL,
    OPEN_ROUTER_MODEL,
)
from app.services.metrics_service import log_metric

logger = logging.getLogger(__name__)
_llm_disabled_until: datetime | None = None
_llm_disable_reason = ""
_llm_last_error = ""
LLM_DISABLE_COOLDOWN_SECONDS = 300
LLM_RATE_LIMIT_COOLDOWN_SECONDS = 60 * 60


@lru_cache(maxsize=1)
def _gemini_client() -> OpenAI:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    return OpenAI(base_url=GEMINI_BASE_URL, api_key=GEMINI_API_KEY)


@lru_cache(maxsize=1)
def _groq_client() -> OpenAI:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    return OpenAI(base_url=GROQ_BASE_URL, api_key=GROQ_API_KEY)


@lru_cache(maxsize=1)
def _openrouter_client() -> OpenAI:
    if not OPEN_ROUTER_API:
        raise RuntimeError("OPEN_ROUTER_API missing")
    return OpenAI(base_url=OPEN_ROUTER_BASE_URL, api_key=OPEN_ROUTER_API)


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
    providers: list[tuple[str, str, str, callable]] = [
        ("gemini", GEMINI_MODEL, "GEMINI_API_KEY", _gemini_client),
    ]
    if GROQ_API_KEY:
        providers.append(("groq", GROQ_MODEL, "GROQ_API_KEY", _groq_client))
    if OPEN_ROUTER_API:
        providers.append(("openrouter", OPEN_ROUTER_MODEL, "OPEN_ROUTER_API", _openrouter_client))

    if _llm_is_disabled() and not any(name in {"groq", "openrouter"} for name, _, _, _ in providers):
        log_metric("llm_usage", model=GEMINI_MODEL, disabled=True, fallback=True, expect_json=expect_json, reason=_llm_disable_reason or "disabled")
        return _local_fallback(prompt, expect_json=expect_json)

    last_error: Exception | None = None
    for provider_name, model_name, key_label, client_factory in providers:
        if provider_name == "gemini" and not GEMINI_API_KEY:
            continue
        if provider_name == "groq" and not GROQ_API_KEY:
            continue
        if provider_name == "openrouter" and not OPEN_ROUTER_API:
            continue
        try:
            client = client_factory()
        except Exception as exc:
            last_error = exc
            logger.warning("llm_client_unavailable provider=%s reason=%s", provider_name, str(exc))
            continue

        try:
            output = _run_chat(client, model=model_name, prompt=prompt)
            log_metric("llm_usage", model=model_name, provider=provider_name, disabled=False, fallback=(provider_name != "gemini"), expect_json=expect_json)
            if not expect_json:
                return output

            parsed = _extract_json_payload(output)
            if parsed is not None:
                return parsed

            retry_output = _run_chat(client, model=model_name, prompt="Return ONLY valid JSON:\n" + prompt)
            log_metric("llm_usage", model=model_name, provider=provider_name, disabled=False, fallback=(provider_name != "gemini"), expect_json=expect_json, retry=True)
            parsed = _extract_json_payload(retry_output)
            if parsed is not None:
                return parsed

            if provider_name == "gemini" and (GROQ_API_KEY or OPEN_ROUTER_API):
                logger.info("llm_provider_fallback provider=%s fallback_provider=groq reason=invalid_json", provider_name)
                continue
            if provider_name == "groq" and OPEN_ROUTER_API:
                logger.info("llm_provider_fallback provider=%s fallback_provider=openrouter reason=invalid_json", provider_name)
                continue
            return _local_fallback(prompt, expect_json=True)
        except Exception as exc:
            last_error = exc
            logger.warning("llm_generation_failed provider=%s model=%s reason=%s", provider_name, model_name, str(exc))
            if provider_name == "gemini" and (GROQ_API_KEY or OPEN_ROUTER_API):
                if _is_rate_limit_error(exc):
                    _disable_llm(str(exc), cooldown_seconds=LLM_RATE_LIMIT_COOLDOWN_SECONDS)
                    logger.info(
                        "llm_provider_cooldown provider=%s fallback_provider=%s reason=rate_limit cooldown_seconds=%s",
                        provider_name,
                        "openrouter" if OPEN_ROUTER_API and not GROQ_API_KEY else "groq",
                        LLM_RATE_LIMIT_COOLDOWN_SECONDS,
                    )
                continue
            if provider_name == "groq" and OPEN_ROUTER_API:
                logger.info("llm_provider_fallback provider=%s fallback_provider=openrouter reason=request_failed", provider_name)
                continue
            if provider_name == "openrouter":
                _disable_llm(str(exc))
                log_metric("llm_usage", model=model_name, provider=provider_name, disabled=True, fallback=True, expect_json=expect_json)
                return _local_fallback(prompt, expect_json=expect_json)
            _disable_llm(str(exc))
            log_metric("llm_usage", model=model_name, provider=provider_name, disabled=True, fallback=True, expect_json=expect_json)
            return _local_fallback(prompt, expect_json=expect_json)

    if last_error is not None:
        _disable_llm(str(last_error))
        log_metric("llm_usage", model=GEMINI_MODEL, disabled=True, fallback=True, expect_json=expect_json, reason=type(last_error).__name__)
    return _local_fallback(prompt, expect_json=expect_json)


def llm_health() -> dict:
    try:
        status = "disabled" if _llm_is_disabled() or (not GEMINI_API_KEY and not GROQ_API_KEY and not OPEN_ROUTER_API) else "ok"
        if status == "ok":
            generate("ping")
        return {
            "status": status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "model": GEMINI_MODEL if GEMINI_API_KEY else GROQ_MODEL if GROQ_API_KEY else OPEN_ROUTER_MODEL,
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
