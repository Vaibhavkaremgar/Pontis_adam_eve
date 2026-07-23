from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.repositories import JobRepository
from app.schemas.candidate import CandidateExplanation, CandidateResult, InternalCandidateMatchItem
from app.services.candidate_service import fetch_ranked_candidates
from app.services.internal_candidate_matcher_service import InternalCandidateMatchFilters, InternalCandidateMatcher
from app.services.ranking.models import ranked_candidate_sort_key


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _unique_strings(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _as_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


@dataclass(frozen=True, slots=True)
class CandidateDiscoveryConfig:
    minimum_internal_candidates: int = int(os.getenv("CANDIDATE_DISCOVERY_MIN_INTERNAL_CANDIDATES", "5"))
    minimum_match_score: float = float(os.getenv("CANDIDATE_DISCOVERY_MIN_MATCH_SCORE", "0.65"))
    internal_match_limit: int = int(os.getenv("CANDIDATE_DISCOVERY_INTERNAL_MATCH_LIMIT", "25"))
    merged_result_limit: int = int(os.getenv("CANDIDATE_DISCOVERY_MERGED_RESULT_LIMIT", "50"))


class CandidateDiscoveryItem(BaseModel):
    candidate: CandidateResult
    source: str = ""
    internalMatch: InternalCandidateMatchItem | None = None


class CandidateDiscoveryResponse(BaseModel):
    agencyId: str
    jobId: str
    usedInternalOnly: bool = False
    enoughInternalCandidates: bool = False
    internalCandidateCount: int = 0
    serpCandidateCount: int = 0
    totalCandidateCount: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
    candidates: list[CandidateDiscoveryItem] = Field(default_factory=list)
    internalCandidates: list[CandidateDiscoveryItem] = Field(default_factory=list)
    serpCandidates: list[CandidateDiscoveryItem] = Field(default_factory=list)


class CandidateDiscoveryService:
    """
    Orchestrates candidate discovery after Adam calibration.

    This service only composes existing components:
    - InternalCandidateMatcher for internal DB discovery
    - fetch_ranked_candidates for existing external SERP / ranking sourcing
    - existing DTOs for the unified response payload
    """

    def __init__(self, db: Session, config: CandidateDiscoveryConfig | None = None) -> None:
        self.db = db
        self.config = config or CandidateDiscoveryConfig()

    def discover_candidates(
        self,
        *,
        agency_id: str,
        job_id: str,
        calibrated_recruiter_preferences: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> CandidateDiscoveryResponse:
        job = JobRepository(self.db).get(job_id)
        if not job:
            raise ValueError("Job not found")
        resolved_agency_id = _as_text(agency_id) or _as_text(getattr(job, "company_id", "") or getattr(job, "agency_id", ""))
        if not resolved_agency_id:
            raise ValueError("agency_id is required")

        internal_matches = self._discover_internal_candidates(
            job_id=job_id,
            calibrated_recruiter_preferences=calibrated_recruiter_preferences,
            limit=limit,
        )
        qualified_internal_matches = [item for item in internal_matches if item.candidate.fitScore >= self.config.minimum_match_score]
        enough_internal_candidates = len(qualified_internal_matches) >= self.config.minimum_internal_candidates

        if enough_internal_candidates:
            internal_items = [self._with_source(item, source="internal") for item in qualified_internal_matches]
            return self._build_response(
                agency_id=resolved_agency_id,
                job_id=job_id,
                internal_items=internal_items,
                serp_items=[],
                enough_internal_candidates=True,
                used_internal_only=True,
                limit=limit,
            )

        serp_items = self._discover_serp_candidates(job_id=job_id, limit=limit)
        merged_items = self._merge_internal_and_serp_candidates(
            internal_items=[self._with_source(item, source="internal") for item in qualified_internal_matches],
            serp_items=serp_items,
            limit=limit,
        )
        return self._build_response(
            agency_id=resolved_agency_id,
            job_id=job_id,
            internal_items=[self._with_source(item, source="internal") for item in qualified_internal_matches],
            serp_items=serp_items,
            enough_internal_candidates=False,
            used_internal_only=False,
            limit=limit,
            candidates_override=merged_items,
        )

    def discover_internal_candidates(
        self,
        *,
        job_id: str,
        calibrated_recruiter_preferences: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[CandidateDiscoveryItem]:
        matches = self._discover_internal_candidates(
            job_id=job_id,
            calibrated_recruiter_preferences=calibrated_recruiter_preferences,
            limit=limit,
        )
        qualified = [item for item in matches if item.candidate.fitScore >= self.config.minimum_match_score]
        return [self._with_source(item, source="internal") for item in qualified]

    def discover_serp_candidates(
        self,
        *,
        job_id: str,
        limit: int | None = None,
    ) -> list[CandidateDiscoveryItem]:
        return self._discover_serp_candidates(job_id=job_id, limit=limit)

    def _discover_internal_candidates(
        self,
        *,
        job_id: str,
        calibrated_recruiter_preferences: dict[str, Any] | None,
        limit: int | None,
    ) -> list[CandidateDiscoveryItem]:
        filters = self._build_internal_filters(calibrated_recruiter_preferences)
        raw_result = InternalCandidateMatcher(self.db).match(
            job_id=job_id,
            filters=filters,
            limit=limit or self.config.internal_match_limit,
        )
        items = []
        for raw_item in raw_result.get("items", []):
            discovery_item = self._to_internal_discovery_item(job_id=job_id, item=raw_item)
            if discovery_item is not None:
                items.append(discovery_item)
        items.sort(key=lambda item: ranked_candidate_sort_key(item.candidate))
        return items

    def _discover_serp_candidates(self, *, job_id: str, limit: int | None) -> list[CandidateDiscoveryItem]:
        candidates = fetch_ranked_candidates(
            db=self.db,
            job_id=job_id,
            refresh=True,
            debug=False,
            request_source="api",
        )
        items: list[CandidateDiscoveryItem] = []
        max_limit = max(1, int(limit or self.config.merged_result_limit))
        for candidate in candidates[:max_limit]:
            items.append(
                CandidateDiscoveryItem(
                    candidate=self._coerce_candidate_result(candidate, source="serp"),
                    source="serp",
                )
            )
        return items

    def _build_internal_filters(self, calibrated_recruiter_preferences: dict[str, Any] | None) -> InternalCandidateMatchFilters:
        prefs = dict(calibrated_recruiter_preferences or {})
        skills = _unique_strings(
            [
                *self._pref_list(prefs, "preferred_skills", "preferredSkills"),
                *self._pref_list(prefs, "top_skills", "topSkills"),
                *self._pref_list(prefs, "skills", "skill_tokens", "skillTokens"),
            ]
        )
        return InternalCandidateMatchFilters(
            skills=skills,
            experience=_as_text(
                prefs.get("experience")
                or prefs.get("experience_bucket")
                or prefs.get("experienceBucket")
                or prefs.get("preferred_experience")
                or prefs.get("preferredExperience")
            ),
            current_role=self._pref_first_text(prefs, "current_role", "currentRole", "role", "role_tokens"),
            current_company=_as_text(prefs.get("current_company") or prefs.get("currentCompany") or ""),
            location=_as_text(prefs.get("location") or prefs.get("preferred_location") or prefs.get("preferredLocation") or ""),
            min_resume_score=self._pref_float(prefs, "min_resume_score", "minResumeScore"),
            min_interview_score=self._pref_float(prefs, "min_interview_score", "minInterviewScore"),
            recommendation=_as_text(prefs.get("recommendation") or prefs.get("preferred_recommendation") or ""),
            interview_date_from=_as_text(prefs.get("interview_date_from") or prefs.get("interviewDateFrom") or ""),
            interview_date_to=_as_text(prefs.get("interview_date_to") or prefs.get("interviewDateTo") or ""),
            talent_pool_ready_only=bool(prefs.get("talent_pool_ready_only", prefs.get("talentPoolReadyOnly", True))),
        )

    def _to_internal_discovery_item(self, *, job_id: str, item: dict[str, Any]) -> CandidateDiscoveryItem | None:
        candidate_id = _as_text(item.get("candidateId"))
        if not candidate_id:
            return None
        explanation = CandidateExplanation(
            semanticScore=max(0.0, min(1.0, _as_float(item.get("overallMatch")) / 100.0)),
            skillOverlap=max(0.0, min(1.0, _as_float(item.get("resumeMatch")))),
            finalScore=max(0.0, min(1.0, _as_float(item.get("overallMatch")) / 100.0)),
            pdlRelevance=0.0,
            recencyScore=max(0.0, min(1.0, _as_float(item.get("freshness")))),
            penalties={},
            skillsMatched=_unique_strings(item.get("skills") or [])[:5],
            experienceMatch=_as_text(item.get("matchingExplanation") or ""),
            candidateExperience=_as_text(item.get("currentRole") or ""),
            jobExperience="",
            aiReasoning=_as_text(item.get("matchingExplanation") or ""),
        )
        candidate = CandidateResult(
            id=candidate_id,
            name=_as_text(item.get("name") or candidate_id),
            role=_as_text(item.get("currentRole") or ""),
            company=_as_text(item.get("currentCompany") or ""),
            email="",
            isMockEmail=False,
            headline=_as_text(item.get("currentRole") or ""),
            location="",
            yearsExperience=_as_float(item.get("experience"), 0.0),
            skills=list(item.get("skills") or []),
            summary=_as_text(item.get("matchingExplanation") or ""),
            education=[],
            projects=[],
            certifications=[],
            companiesHistory=[],
            domainExperience=[],
            resumeText="",
            profileData={
                "source": "internal_candidate_matcher",
                "job_id": job_id,
                "candidate_id": candidate_id,
            },
            fitScore=max(0.0, min(5.0, _as_float(item.get("overallMatch")) / 20.0)),
            decision=_as_text(item.get("recommendation") or "review"),
            explanation=explanation,
            strategy="internal_match",
            status="reviewed",
            debug=None,
            outreachStatus="pending",
            enrichmentStatus="pending",
            sourceProvider="internal_db",
            sourceQuery="",
            sourceTimestamp="",
            sourceType="internal_db",
            source="internal_db",
            source_url="",
            linkedinUrl="",
            githubUrl=None,
            portfolioUrl=None,
            currentCompany=_as_text(item.get("currentCompany") or ""),
            inferredExperience=_as_text(item.get("matchingExplanation") or ""),
            snippetQuality="partial",
            rawDiscovery={
                "source": "internal_candidate_matcher",
                "matchingExplanation": item.get("matchingExplanation"),
            },
        )
        internal_match_item = InternalCandidateMatchItem(
            candidateId=candidate_id,
            name=candidate.name,
            currentRole=candidate.role or "",
            currentCompany=candidate.company or "",
            experience=_as_float(item.get("experience"), 0.0),
            resumeMatch=_as_float(item.get("resumeMatch")),
            interviewScore=_as_float(item.get("interviewScore")),
            overallMatch=_as_float(item.get("overallMatch")),
            recommendation=_as_text(item.get("recommendation") or ""),
            interviewDate=_as_text(item.get("interviewDate") or "") or None,
            freshness=_as_float(item.get("freshness")),
            matchingExplanation=_as_text(item.get("matchingExplanation") or ""),
            skills=list(item.get("skills") or []),
        )
        return CandidateDiscoveryItem(candidate=candidate, source="internal", internalMatch=internal_match_item)

    def _coerce_candidate_result(self, candidate: Any, *, source: str) -> CandidateResult:
        if isinstance(candidate, CandidateResult):
            result = candidate.model_copy(deep=True)
        else:
            result = CandidateResult.model_validate(candidate)
        result.profileData = dict(result.profileData or {})
        result.profileData.setdefault("discovery_source", source)
        result.source = source
        if source == "serp":
            result.sourceProvider = result.sourceProvider or "xray_apollo"
            result.sourceType = result.sourceType or "linkedin_xray"
        return result

    def _with_source(self, item: CandidateDiscoveryItem, *, source: str) -> CandidateDiscoveryItem:
        candidate = item.candidate.model_copy(deep=True)
        candidate.source = source
        candidate.sourceType = candidate.sourceType or source
        candidate.sourceProvider = candidate.sourceProvider or ("internal_db" if source == "internal" else candidate.sourceProvider)
        return CandidateDiscoveryItem(candidate=candidate, source=source, internalMatch=item.internalMatch)

    def _merge_internal_and_serp_candidates(
        self,
        *,
        internal_items: list[CandidateDiscoveryItem],
        serp_items: list[CandidateDiscoveryItem],
        limit: int | None,
    ) -> list[CandidateDiscoveryItem]:
        max_limit = max(1, int(limit or self.config.merged_result_limit))
        merged: list[CandidateDiscoveryItem] = []
        seen: set[str] = set()

        def append_group(items: list[CandidateDiscoveryItem]) -> None:
            nonlocal merged
            for item in sorted(items, key=lambda entry: ranked_candidate_sort_key(entry.candidate)):
                candidate_id = _as_text(item.candidate.id)
                if not candidate_id or candidate_id in seen:
                    continue
                seen.add(candidate_id)
                merged.append(item)
                if len(merged) >= max_limit:
                    return

        append_group(internal_items)
        if len(merged) < max_limit:
            append_group(serp_items)
        return merged[:max_limit]

    def _build_response(
        self,
        *,
        agency_id: str,
        job_id: str,
        internal_items: list[CandidateDiscoveryItem],
        serp_items: list[CandidateDiscoveryItem],
        enough_internal_candidates: bool,
        used_internal_only: bool,
        limit: int | None,
        candidates_override: list[CandidateDiscoveryItem] | None = None,
    ) -> CandidateDiscoveryResponse:
        candidates = candidates_override
        if candidates is None:
            candidates = self._merge_internal_and_serp_candidates(
                internal_items=internal_items,
                serp_items=serp_items,
                limit=limit,
            )
        return CandidateDiscoveryResponse(
            agencyId=agency_id,
            jobId=job_id,
            usedInternalOnly=used_internal_only,
            enoughInternalCandidates=enough_internal_candidates,
            internalCandidateCount=len(internal_items),
            serpCandidateCount=len(serp_items),
            totalCandidateCount=len(candidates),
            config={
                "minimum_internal_candidates": self.config.minimum_internal_candidates,
                "minimum_match_score": self.config.minimum_match_score,
                "internal_match_limit": self.config.internal_match_limit,
                "merged_result_limit": self.config.merged_result_limit,
            },
            candidates=candidates,
            internalCandidates=internal_items,
            serpCandidates=serp_items,
        )

    @staticmethod
    def _pref_list(prefs: dict[str, Any], *keys: str) -> list[str]:
        collected: list[str] = []
        for key in keys:
            value = prefs.get(key)
            if isinstance(value, list):
                collected.extend([_as_text(item) for item in value if _as_text(item)])
            elif isinstance(value, str) and value.strip():
                collected.extend([part.strip() for part in value.split(",") if part.strip()])
        return _unique_strings(collected)

    @staticmethod
    def _pref_float(prefs: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = prefs.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _pref_first_text(prefs: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = prefs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                for item in value:
                    text = _as_text(item)
                    if text:
                        return text
        return ""
