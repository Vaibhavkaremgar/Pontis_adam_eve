from __future__ import annotations

import itertools
import math
import re
from typing import Any

from app.services.candidate_text import build_candidate_text
from app.services.embedding_service import embed
from app.services.skill_normalizer import normalize_skills, parse_experience

_STARTUP_TERMS = ("startup", "seed", "series a", "series b", "early-stage", "fast-paced", "scrappy")
_ENTERPRISE_TERMS = ("enterprise", "scale", "regulated", "large-scale", "global", "mature")
_BACKEND_TERMS = ("backend", "api", "service", "distributed", "microservice", "python", "java", "go", "node")
_INFRA_TERMS = ("infra", "platform", "aws", "gcp", "azure", "kubernetes", "terraform", "devops", "sre")
_DOMAIN_TERMS = ("fintech", "healthcare", "security", "payments", "search", "ads", "commerce", "ml", "data")
_SYSTEMS_TERMS = ("systems", "architecture", "scalable", "distributed", "performance", "reliability", "latency")
_LEADERSHIP_TERMS = ("lead", "leadership", "mentor", "architect", "own", "ownership", "drive")
_IC_TERMS = ("individual contributor", "ic", "hands-on", "build", "ship", "implement")


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _candidate_dict(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        return candidate
    return {
        "id": _normalize_text(getattr(candidate, "id", "")),
        "name": _normalize_text(getattr(candidate, "name", "")),
        "role": _normalize_text(getattr(candidate, "role", "")),
        "company": _normalize_text(getattr(candidate, "company", "")),
        "skills": list(getattr(candidate, "skills", []) or []),
        "summary": _normalize_text(getattr(candidate, "summary", "")),
        "fitScore": float(getattr(candidate, "fitScore", 0.0) or 0.0),
    }


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9\.\+#]+", text.lower()) if token}


def _feature_score(text: str, terms: tuple[str, ...]) -> float:
    lowered = text.lower()
    score = 0.0
    for term in terms:
        if term in lowered:
            score += 1.0
    return max(0.0, min(1.0, score / max(1, len(terms))))


def _candidate_features(candidate: Any) -> dict[str, Any]:
    row = _candidate_dict(candidate)
    summary = " ".join(
        [
            _normalize_text(row.get("role")),
            _normalize_text(row.get("company")),
            _normalize_text(row.get("summary")),
            " ".join(str(skill) for skill in (row.get("skills") or []) if str(skill).strip()),
        ]
    )
    skills = normalize_skills([str(skill) for skill in (row.get("skills") or []) if str(skill).strip()])
    experience = parse_experience(summary)

    return {
        "id": _normalize_text(row.get("id")),
        "name": _normalize_text(row.get("name")),
        "role": _normalize_text(row.get("role")),
        "company": _normalize_text(row.get("company")),
        "skills": sorted(skills),
        "skill_set": set(skills),
        "summary": _normalize_text(row.get("summary")),
        "fit_score": float(row.get("fitScore") or row.get("fit_score") or 0.0),
        "startup_score": _feature_score(summary, _STARTUP_TERMS),
        "enterprise_score": _feature_score(summary, _ENTERPRISE_TERMS),
        "backend_score": _feature_score(summary, _BACKEND_TERMS),
        "infra_score": _feature_score(summary, _INFRA_TERMS),
        "domain_score": _feature_score(summary, _DOMAIN_TERMS),
        "systems_score": _feature_score(summary, _SYSTEMS_TERMS),
        "leadership_score": _feature_score(summary, _LEADERSHIP_TERMS),
        "ic_score": _feature_score(summary, _IC_TERMS),
        "experience_years": experience,
        "embedding": embed(build_candidate_text(row)),
        "token_set": _tokens(summary),
    }


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate["id"],
        "name": candidate["name"],
        "role": candidate["role"],
        "company": candidate["company"],
        "skills": list(candidate["skills"]),
        "summary": candidate["summary"],
        "fitScore": round(float(candidate["fit_score"] or 0.0), 2),
    }


def _intent_keywords(intent_profile: dict[str, Any]) -> set[str]:
    keywords: set[str] = set()
    keywords.update(str(item).lower() for item in (intent_profile.get("required_skills") or []) if str(item).strip())
    keywords.update(str(item).lower() for item in (intent_profile.get("preferred_skills") or []) if str(item).strip())
    keywords.update(str(item).lower() for item in (intent_profile.get("culture_preferences") or []) if str(item).strip())
    for value in (intent_profile.get("preference_text") or "", intent_profile.get("voice_summary") or ""):
        keywords.update(_tokens(str(value)))
    return keywords


def _intent_alignment(candidate: dict[str, Any], intent_profile: dict[str, Any]) -> float:
    keywords = _intent_keywords(intent_profile)
    if not keywords:
        return candidate["fit_score"]
    intersection = len(candidate["token_set"].intersection(keywords))
    structural = len(candidate["skill_set"].intersection(keywords))
    skill_weight = 0.65 if candidate["skill_set"] else 0.0
    return max(
        0.0,
        min(
            1.0,
            (intersection / max(1, len(keywords)) * 0.45)
            + (structural / max(1, len(candidate["skill_set"]) or 1) * skill_weight)
            + max(0.0, min(1.0, candidate["fit_score"] / 5.0)) * 0.3,
        ),
    )


def _contrast_by_axis(left: dict[str, Any], right: dict[str, Any], axis: str) -> float:
    if axis == "startup":
        return abs(left["startup_score"] - right["startup_score"]) + abs(left["enterprise_score"] - right["enterprise_score"])
    if axis == "backend_infra":
        return abs((left["backend_score"] + left["infra_score"]) - (right["backend_score"] + right["infra_score"]))
    if axis == "domain_systems":
        return abs(left["domain_score"] - right["systems_score"]) + abs(right["domain_score"] - left["systems_score"])
    if axis == "leadership_ic":
        return abs(left["leadership_score"] - right["ic_score"]) + abs(right["leadership_score"] - left["ic_score"])
    if axis == "exact_adjacent":
        left_exp = left["fit_score"]
        right_exp = right["fit_score"]
        return 1.0 - min(1.0, abs(left_exp - right_exp) / 5.0)
    return 0.0


def _pair_axes(round_index: int) -> list[str]:
    if round_index <= 1:
        return ["startup", "backend_infra", "domain_systems", "leadership_ic", "exact_adjacent"]
    if round_index == 2:
        return ["backend_infra", "leadership_ic", "domain_systems", "exact_adjacent"]
    return ["domain_systems", "leadership_ic", "exact_adjacent"]


def _pair_score(left: dict[str, Any], right: dict[str, Any], intent_profile: dict[str, Any], round_index: int) -> tuple[float, dict[str, float], list[str]]:
    axes = _pair_axes(round_index)
    align_left = _intent_alignment(left, intent_profile)
    align_right = _intent_alignment(right, intent_profile)
    avg_alignment = (align_left + align_right) / 2.0
    fit_gap = abs(left["fit_score"] - right["fit_score"]) / 5.0
    uncertainty = max(0.0, 1.0 - fit_gap)
    contrast_scores = {axis: _contrast_by_axis(left, right, axis) for axis in axes}
    contrast = max(contrast_scores.values()) if contrast_scores else 0.0
    diversity = max(
        0.0,
        min(
            1.0,
            len(left["token_set"].symmetric_difference(right["token_set"])) / max(1, len(left["token_set"].union(right["token_set"]))),
        ),
    )
    exact_match_bonus = 1.0 if len(left["skill_set"].intersection(right["skill_set"])) >= 2 else 0.0
    information_gain = max(0.0, min(1.0, (contrast * 0.45) + (uncertainty * 0.25) + (diversity * 0.20) + (exact_match_bonus * 0.10)))
    score = (avg_alignment * 0.30) + (information_gain * 0.40) + (contrast * 0.15) + (uncertainty * 0.10) + (diversity * 0.05)
    return score, {
        "alignment": avg_alignment,
        "contrast": contrast,
        "uncertainty": uncertainty,
        "diversity": diversity,
        "exactMatchBonus": exact_match_bonus,
        "informationGain": information_gain,
    }, [axis for axis, value in contrast_scores.items() if value >= max(contrast_scores.values(), default=0.0) * 0.8]


def _pair_rationale(left: dict[str, Any], right: dict[str, Any], axes: list[str]) -> str:
    names = [left.get("name") or left.get("id")[:8], right.get("name") or right.get("id")[:8]]
    if not axes:
        axes = ["fit clarity"]
    axis_text = ", ".join(axes[:3]).replace("_", " ")
    return f"Contrast {names[0]} vs {names[1]} to reveal {axis_text} preference signals."


def generate_preference_pair(
    *,
    candidates: list[Any],
    intent_profile: dict[str, Any],
    round_index: int,
    previous_choice: dict[str, Any] | None = None,
    excluded_ids: set[str] | None = None,
) -> dict[str, Any]:
    excluded_ids = set(excluded_ids or set())
    pool = [_candidate_features(candidate) for candidate in candidates if _normalize_text(_candidate_dict(candidate).get("id"))]
    pool = [candidate for candidate in pool if candidate["id"] not in excluded_ids]
    if len(pool) < 2:
        return {
            "round_index": round_index,
            "candidate_ids": [],
            "candidates": [],
            "signal_quality": 0.0,
            "contrast_axes": [],
            "rationale": "Not enough candidates available to form a comparison pair.",
        }

    anchor_id = _normalize_text((previous_choice or {}).get("selected_candidate_id"))
    anchor = next((candidate for candidate in pool if candidate["id"] == anchor_id), None)
    if anchor is not None:
        ranked_pool = sorted(pool, key=lambda candidate: (-candidate["fit_score"], candidate["id"]))
        opposite = sorted(
            [candidate for candidate in ranked_pool if candidate["id"] != anchor_id],
            key=lambda candidate: (
                -_pair_score(anchor, candidate, intent_profile, round_index)[0],
                candidate["id"],
            ),
        )
        if opposite:
            candidate_a, candidate_b = anchor, opposite[0]
        else:
            candidate_a, candidate_b = ranked_pool[:2]
    else:
        best_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
        best_score = -1.0
        best_meta: dict[str, float] = {}
        best_axes: list[str] = []
        for left, right in itertools.combinations(pool, 2):
            score, meta, axes = _pair_score(left, right, intent_profile, round_index)
            if score > best_score:
                best_pair = (left, right)
                best_score = score
                best_meta = meta
                best_axes = axes
        if best_pair is None:
            best_pair = (pool[0], pool[1])
            best_score, best_meta, best_axes = _pair_score(best_pair[0], best_pair[1], intent_profile, round_index)
        candidate_a, candidate_b = best_pair

    score, meta, axes = _pair_score(candidate_a, candidate_b, intent_profile, round_index)
    rationale = _pair_rationale(candidate_a, candidate_b, axes or _pair_axes(round_index))
    return {
        "round_index": round_index,
        "candidate_ids": [candidate_a["id"], candidate_b["id"]],
        "candidates": [_public_candidate(candidate_a), _public_candidate(candidate_b)],
        "signal_quality": round(score, 4),
        "contrast_axes": axes or _pair_axes(round_index),
        "signal_breakdown": {key: round(value, 4) for key, value in meta.items()},
        "rationale": rationale,
        "pair_explanation": {
            "why_selected": rationale,
            "contrast_axes": axes or _pair_axes(round_index),
            "signal_quality": round(score, 4),
        },
    }


def generate_three_round_plan(
    *,
    candidates: list[Any],
    intent_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    remaining_ids: set[str] = set()
    previous_choice: dict[str, Any] | None = None
    plan: list[dict[str, Any]] = []
    for round_index in range(1, 4):
        pair = generate_preference_pair(
            candidates=candidates,
            intent_profile=intent_profile,
            round_index=round_index,
            previous_choice=previous_choice,
            excluded_ids=remaining_ids,
        )
        plan.append(pair)
        if pair["candidate_ids"]:
            remaining_ids.update(pair["candidate_ids"])
            previous_choice = {"selected_candidate_id": pair["candidate_ids"][0]}
    return plan
