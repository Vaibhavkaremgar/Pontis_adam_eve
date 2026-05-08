from __future__ import annotations

import re

_CONTROL_PATTERNS = [
    r"(?is)\bignore\s+previous\s+instructions\b",
    r"(?is)\bdisregard\s+all\s+prior\b",
    r"(?is)\bsystem\s*:\s*",
    r"(?is)\bassistant\s*:\s*",
    r"(?is)\bdeveloper\s*:\s*",
    r"(?is)\bact\s+as\s+",
    r"(?is)\byou\s+are\s+now\s+",
    r"(?is)```[\s\S]*?```",
    r"(?is)<\s*\/?\s*(system|assistant|developer)\s*>",
]


def sanitize_prompt_text(value: str | None, *, max_length: int = 4000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""

    sanitized = text
    for pattern in _CONTROL_PATTERNS:
        sanitized = re.sub(pattern, "", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized[:max_length]


def sanitize_prompt_block(label: str, value: str | None, *, max_length: int = 4000) -> str:
    sanitized = sanitize_prompt_text(value, max_length=max_length)
    return f"{label}:\n{sanitized or 'Not provided'}"
