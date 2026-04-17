from __future__ import annotations

import logging
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.schemas.candidate import Candidate
from app.services.db_service import get_job, update_job
from app.services.embedding_service import get_embedding
from app.services.pdl_service import fetch_candidates_with_filters
from app.services.qdrant_service import create_collection, insert_vector, search_vector

router = APIRouter(tags=["candidates"])
logger = logging.getLogger(__name__)

MAX_FILTER_SKILLS = 2
MAX_RETURNED_CANDIDATES = 5


def _build_job_search_text(job: dict) -> str:
    company = job.get("company", {})
    job_skills = job.get("skills") or job.get("required_skills") or job.get("preferred_skills") or []
    if isinstance(job_skills, str):
        job_skills = [job_skills]

    return (
        "Job Description:\n"
        f"Title: {job.get('title', '')}\n"
        f"Description: {job.get('description', '')}\n"
        f"Skills: {', '.join(str(skill) for skill in job_skills)}\n"
        f"Location: {job.get('location', '')}\n"
        f"Compensation: {job.get('compensation', '')}\n"
        f"Work Authorization: {job.get('workAuthorization', '')}\n"
        f"Company: {company.get('name', '')}\n"
        f"Company Description: {company.get('description', '')}"
    )


def _candidate_status(score: float) -> str:
    if score >= 0.8:
        return "Highly Relevant"
    if score >= 0.6:
        return "Relevant"
    return "Less Relevant"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    for value in values:
        normalized = value.strip(" ,.;:").lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)

    return ordered


def _extract_skills(job_text: str) -> list[str]:
    collected: list[str] = []
    lines = [line.strip() for line in job_text.splitlines() if line.strip()]

    for line in lines:
        if not any(separator in line for separator in [",", "/", "|"]):
            continue

        for chunk in re.split(r"[,/|]", line):
            cleaned = re.sub(r"\s+", " ", chunk).strip(" -:;,.")
            if not cleaned:
                continue
            if len(cleaned.split()) > 3:
                continue
            collected.append(cleaned.lower())

    normalized_skills = _unique_strings(collected)[:MAX_FILTER_SKILLS]
    logger.info("Dynamically extracted skills for PDL filters", extra={"skills": normalized_skills})
    return normalized_skills


def extract_filters(job: dict) -> dict:
    title = str(job.get("title") or "").strip().lower()
    description = str(job.get("description") or "").strip()
    location = str(job.get("location") or "").strip().lower()
    combined_text = "\n".join(part for part in [title, description] if part)

    role = title
    skills = _extract_skills(combined_text)

    filters = {
        "role": role,
        "skills": skills,
        "location": location,
    }

    logger.info("Generated candidate filters", extra={"filters": filters})
    return filters


def _build_candidate_text(candidate: dict) -> str:
    name = candidate.get("full_name") or candidate.get("name") or ""
    role = candidate.get("job_title") or candidate.get("title") or ""
    company = candidate.get("job_company_name") or candidate.get("company") or ""
    skills = candidate.get("skills") or []

    return (
        f"Name: {name}\n"
        f"Role: {role}\n"
        f"Company: {company}\n"
        f"Skills: {', '.join(str(skill) for skill in skills)}"
    )


def _candidate_id(candidate: dict) -> str:
    candidate_name = str(candidate.get("full_name") or candidate.get("name") or "").strip()
    candidate_role = str(candidate.get("job_title") or candidate.get("title") or "").strip()
    candidate_company = str(candidate.get("job_company_name") or candidate.get("company") or "").strip()

    stable_parts = [part.lower() for part in [candidate_name, candidate_role, candidate_company] if part]
    if not stable_parts:
        stable_parts.append(_build_candidate_text(candidate).strip().lower())

    deterministic_key = "|".join(stable_parts)
    candidate_uuid = str(uuid5(NAMESPACE_URL, deterministic_key))

    try:
        UUID(candidate_uuid)
    except ValueError as exc:
        raise ValueError(f"Generated invalid Qdrant point ID: {candidate_uuid}") from exc

    logger.info("Generated candidate point ID", extra={"candidate_id": candidate_uuid, "key": deterministic_key})
    return candidate_uuid


def _normalize_candidate(result: dict) -> Candidate:
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    if not payload and isinstance(result.get("data"), dict):
        payload = result["data"]

    raw_score = result.get("score", 0.0)
    print(f"Score before normalization: {raw_score}")
    logger.info("Normalizing Qdrant candidate score", extra={"raw_score": raw_score, "result": result})

    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        logger.warning("Invalid Qdrant score encountered; defaulting to 0", extra={"raw_score": raw_score})
        score = 0.0

    score = max(0.0, min(1.0, score))

    return Candidate(
        name=str(payload.get("name", "")),
        job_title=str(payload.get("job_title", "")),
        company=str(payload.get("company", "")),
        skills=[str(skill) for skill in (payload.get("skills") or [])],
        score=score,
        status=_candidate_status(score),
    )


def _candidate_from_payload(payload: dict, score: float = 0.0) -> Candidate:
    normalized_score = max(0.0, min(1.0, float(score)))
    return Candidate(
        name=str(payload.get("name", "")),
        job_title=str(payload.get("job_title", "")),
        company=str(payload.get("company", "")),
        skills=[str(skill) for skill in (payload.get("skills") or [])],
        score=normalized_score,
        status=_candidate_status(normalized_score),
    )


def _search_candidates(job_vector: list[float], stage: str) -> list[dict]:
    results = search_vector(job_vector)
    if results and all(float(result.get("score", 0.0) or 0.0) == 0.0 for result in results):
        logger.warning(
            "Qdrant returned only zero scores",
            extra={"stage": stage, "result_count": len(results), "results": results},
        )
    logger.info(
        "Qdrant search completed",
        extra={"stage": stage, "result_count": len(results), "results": results},
    )
    return results


def _validate_pdl_response(response: Any) -> list[dict]:
    if not isinstance(response, dict):
        logger.error("PDL response is not a dictionary", extra={"response_type": type(response).__name__})
        return []

    candidates = response.get("data")
    if not isinstance(candidates, list):
        logger.error("PDL response missing list data", extra={"response_keys": list(response.keys())})
        return []

    logger.info(
        "PDL response validated",
        extra={"candidate_count": len(candidates), "response_keys": list(response.keys())},
    )
    return [candidate for candidate in candidates if isinstance(candidate, dict)]


def _ingest_candidates(candidates: list[dict]) -> list[dict]:
    seen: set[str] = set()
    inserted_payloads: list[dict] = []

    logger.info("Starting candidate ingestion", extra={"candidate_count": len(candidates)})

    for candidate in candidates:
        logger.info("Processing candidate", extra={"candidate": candidate})

        candidate_name = str(candidate.get("full_name") or candidate.get("name") or "").strip().lower()
        dedupe_key = candidate_name or _candidate_id(candidate)
        if dedupe_key in seen:
            logger.info("Skipping duplicate candidate during ingestion", extra={"candidate": candidate_name})
            continue
        seen.add(dedupe_key)

        payload = {
            "name": str(candidate.get("full_name") or candidate.get("name") or ""),
            "job_title": str(candidate.get("job_title") or candidate.get("title") or ""),
            "company": str(candidate.get("job_company_name") or candidate.get("company") or ""),
            "skills": [str(skill) for skill in (candidate.get("skills") or [])],
        }
        if not any(payload.values()):
            logger.warning("Skipping candidate with empty payload", extra={"candidate": candidate})
            continue

        candidate_text = _build_candidate_text(candidate)
        vector = get_embedding(candidate_text)
        logger.info(
            "Candidate embedding generated",
            extra={"candidate_name": payload["name"], "embedding_size": len(vector)},
        )

        candidate_id = _candidate_id(candidate)
        insert_vector(id=candidate_id, vector=vector, payload=payload)
        logger.info(
            "Candidate inserted into Qdrant",
            extra={"candidate_name": payload["name"], "candidate_id": candidate_id, "payload": payload},
        )
        inserted_payloads.append(payload)

    logger.info("Candidate ingestion completed", extra={"inserted_count": len(inserted_payloads)})
    return inserted_payloads


@router.get("/candidates", response_model=list[Candidate])
def get_candidates(jobId: str = Query(...)) -> list[Candidate] | JSONResponse:
    job = get_job(jobId)
    if not job:
        logger.warning("Job not found during candidate lookup", extra={"job_id": jobId})
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    logger.info("Loaded job for candidate search", extra={"job_id": jobId, "job": job})

    job_search_text = _build_job_search_text(job)

    try:
        create_collection()
        job_vector = get_embedding(job_search_text)
        logger.info("Job embedding generated", extra={"job_id": jobId, "embedding_size": len(job_vector)})
    except Exception:
        logger.exception("Failed to prepare job embedding or collection", extra={"job_id": jobId})
        return []

    try:
        results = _search_candidates(job_vector, stage="initial")
    except Exception:
        logger.exception("Initial Qdrant search failed", extra={"job_id": jobId})
        return []

    if not results and not job.get("pdl_fetched"):
        filters = extract_filters(job)
        logger.info("Falling back to PDL search", extra={"job_id": jobId, "filters": filters})

        try:
            pdl_response = fetch_candidates_with_filters(filters)
            logger.info("Received raw PDL response", extra={"job_id": jobId, "pdl_response": pdl_response})
        except Exception:
            logger.exception("PDL request failed", extra={"job_id": jobId, "filters": filters})
            return []

        fetched_candidates = _validate_pdl_response(pdl_response)

        inserted_payloads: list[dict] = []
        if fetched_candidates:
            try:
                inserted_payloads = _ingest_candidates(fetched_candidates)
                update_job(jobId, {"pdl_fetched": True})
            except Exception:
                logger.exception("Candidate ingestion failed", extra={"job_id": jobId})
                return []

            try:
                results = _search_candidates(job_vector, stage="post_ingestion")
            except Exception:
                logger.exception("Post-ingestion Qdrant search failed", extra={"job_id": jobId})
                results = []

            if not results and inserted_payloads:
                logger.warning(
                    "Qdrant search still empty after ingestion; returning inserted candidates directly",
                    extra={"job_id": jobId, "inserted_count": len(inserted_payloads)},
                )
                final_candidates = [_candidate_from_payload(payload) for payload in inserted_payloads[:MAX_RETURNED_CANDIDATES]]
                logger.info("Final candidate output", extra={"job_id": jobId, "output": [c.model_dump() for c in final_candidates]})
                return final_candidates
        else:
            logger.warning("PDL returned no valid candidates", extra={"job_id": jobId, "filters": filters})
            update_job(jobId, {"pdl_fetched": True})

    final_candidates = [_normalize_candidate(result) for result in results[:MAX_RETURNED_CANDIDATES]]
    logger.info("Final candidate output", extra={"job_id": jobId, "output": [c.model_dump() for c in final_candidates]})
    return final_candidates
