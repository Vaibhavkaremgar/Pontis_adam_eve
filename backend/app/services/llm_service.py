from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from datetime import datetime, timezone

from openai import OpenAI

from app.core.config import GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    return OpenAI(base_url=GROQ_BASE_URL, api_key=GROQ_API_KEY)


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
    client = _client()

    def _run(instruction: str) -> str:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": instruction}],
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip()

    output = _run(prompt)
    if not expect_json:
        return output

    parsed = _extract_json_payload(output)
    if parsed is not None:
        return parsed

    retry_output = _run("Return ONLY valid JSON:\n" + prompt)
    parsed = _extract_json_payload(retry_output)
    if parsed is not None:
        return parsed
    return retry_output


def llm_health() -> dict:
    try:
        generate("ping")
        return {"status": "ok", "checked_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        logger.warning("llm_health_check_failed error=%s", str(exc), exc_info=exc)
        return {
            "status": "error",
            "error": str(exc),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
