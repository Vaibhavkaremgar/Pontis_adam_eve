"""
role_classifier.py
------------------
Deterministic, keyword-based role family classifier.
No LLM. No external calls. Pure string matching.

Returns:
    {"family": "<family>", "confidence": 1.0}  on match
    {"family": "generic",  "confidence": 0.0}  on no match
"""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "role_taxonomy.json")


@lru_cache(maxsize=1)
def _load_taxonomy() -> dict[str, Any]:
    path = os.path.normpath(_TAXONOMY_PATH)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.error("role_classifier_taxonomy_load_failed path=%s error=%s", path, exc)
        return {}


def _tokenise(text: str) -> str:
    """Lowercase and collapse whitespace — no stemming needed."""
    return " " + re.sub(r"\s+", " ", text.strip().lower()) + " "


def classify_role(role_title: str) -> dict[str, Any]:
    """
    Classify a role title into a family using keyword matching.

    Priority: families are checked in definition order; the first family
    whose keyword list produces any match wins.  The 'generic' family
    is the fallback and is never matched by keyword.

    Args:
        role_title: Free-text role title from the recruiter.

    Returns:
        dict with keys 'family' (str) and 'confidence' (float).
    """
    if not role_title or not role_title.strip():
        logger.info("role_classifier role_title='' classified_family=generic confidence=0.0")
        return {"family": "generic", "confidence": 0.0}

    haystack = _tokenise(role_title)
    taxonomy = _load_taxonomy()

    for family, spec in taxonomy.items():
        if family == "generic":
            continue
        keywords: list[str] = spec.get("keywords", [])
        for kw in keywords:
            needle = _tokenise(kw).strip()
            # Match as a whole word / phrase boundary inside the haystack
            if f" {needle} " in haystack or haystack.startswith(f"{needle} ") or haystack.endswith(f" {needle}"):
                logger.info(
                    "role_classifier role_title=%r classified_family=%s matched_keyword=%r confidence=1.0",
                    role_title,
                    family,
                    kw,
                )
                return {"family": family, "confidence": 1.0}

    logger.info(
        "role_classifier role_title=%r classified_family=generic confidence=0.0",
        role_title,
    )
    return {"family": "generic", "confidence": 0.0}


def get_role_questions(family: str) -> list[dict[str, Any]]:
    """
    Return the role-specific question list for the given family.
    Falls back to 'generic' questions if the family is unknown.

    Each item: {"key": str, "field": str, "prompt": str}
    """
    taxonomy = _load_taxonomy()
    spec = taxonomy.get(family) or taxonomy.get("generic", {})
    return spec.get("questions", [])


def get_role_followup_bank(family: str) -> list[tuple[str, str]]:
    """
    Return a list of (question_key, prompt) tuples suitable for
    direct injection into the orchestration service's followup bank.
    """
    questions = get_role_questions(family)
    return [(q["key"], q["prompt"]) for q in questions]
