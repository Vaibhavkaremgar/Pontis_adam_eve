from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.candidate import CandidateExplanation


@dataclass(slots=True)
class RankedCandidate:
    candidate_id: str
    finalScore: float
    semanticScore: float = 0.0
    xrayScore: float = 0.0
    recruiterScore: float = 0.0
    historicalScore: float = 0.0
    sourceProvider: str = ""
    explanation: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def coerce_candidate_explanation(value: Any) -> CandidateExplanation:
    if isinstance(value, CandidateExplanation):
        return value
    payload = value if isinstance(value, dict) else {}
    return CandidateExplanation(
        semanticScore=_as_float(payload.get("semanticScore")),
        skillOverlap=_as_float(payload.get("skillOverlap")),
        finalScore=_as_float(payload.get("finalScore")),
        pdlRelevance=_as_float(payload.get("pdlRelevance")),
        recencyScore=_as_float(payload.get("recencyScore")),
        engineeringScore=_as_float(payload.get("engineeringScore")),
        penalties=dict(payload.get("penalties") or {}),
        skillsMatched=list(payload.get("skillsMatched") or []),
        experienceMatch=str(payload.get("experienceMatch") or ""),
        candidateExperience=str(payload.get("candidateExperience") or ""),
        jobExperience=str(payload.get("jobExperience") or ""),
        aiReasoning=str(payload.get("aiReasoning") or ""),
        retrievalAttribution=dict(payload.get("retrievalAttribution") or {}),
        sourceBreakdown=dict(payload.get("sourceBreakdown") or {}),
        recruiterPreferenceInfluence=_as_float(payload.get("recruiterPreferenceInfluence")),
        voiceInterviewInfluence=_as_float(payload.get("voiceInterviewInfluence")),
        lexicalRetrievalInfluence=_as_float(payload.get("lexicalRetrievalInfluence")),
        vectorRetrievalInfluence=_as_float(payload.get("vectorRetrievalInfluence")),
        freshnessInfluence=_as_float(payload.get("freshnessInfluence")),
        selectionRoundInfluence=_as_float(payload.get("selectionRoundInfluence")),
    )


def ranked_candidate_final_score(candidate: Any) -> float:
    explanation = getattr(candidate, "explanation", None)
    if isinstance(candidate, dict):
        explanation = candidate.get("explanation")
        fit_score = _as_float(candidate.get("fitScore") or candidate.get("fit_score"))
    else:
        fit_score = _as_float(getattr(candidate, "fitScore", 0.0))

    if isinstance(explanation, dict):
        value = explanation.get("finalScore")
        if value is not None:
            return _as_float(value)
    elif explanation is not None:
        value = getattr(explanation, "finalScore", None)
        if value is not None:
            return _as_float(value)

    return max(0.0, fit_score / 5.0)


def ranked_candidate_sort_key(candidate: Any) -> tuple[float, float, str]:
    if isinstance(candidate, dict):
        name = str(candidate.get("name") or candidate.get("id") or "")
        fit_score = _as_float(candidate.get("fitScore") or candidate.get("fit_score"))
    else:
        name = str(getattr(candidate, "name", "") or getattr(candidate, "id", "") or "")
        fit_score = _as_float(getattr(candidate, "fitScore", 0.0))
    return (-ranked_candidate_final_score(candidate), -fit_score, name)

