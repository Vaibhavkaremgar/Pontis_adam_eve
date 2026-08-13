"""
TEMPORARY DIAGNOSTIC SCRIPT - debug_internal_candidate_matching.py
Read-only trace of the real internal candidate matching pipeline.

Run from backend/:
    python -m scripts.debug_internal_candidate_matching

Optional override:
    python -m scripts.debug_internal_candidate_matching <job_id>
"""
from __future__ import annotations

import math
import os
import re
import sys
from collections import Counter
from statistics import mean
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.core.config import (  # noqa: E402
    EMBEDDING_MODEL_NAME,
    EMBEDDING_VERSION,
    INTERNAL_CANDIDATE_COLLECTION_NAME,
    INTERNAL_CANDIDATE_MATCH_LIMIT,
    INTERNAL_CANDIDATE_MATCH_THRESHOLD,
    INTERNAL_CANDIDATE_MIN_MATCHES,
    INTERNAL_CANDIDATE_RETRIEVAL_TOP_K,
)
from app.db.session import SessionLocal  # noqa: E402
from app.db.repositories import JobRepository  # noqa: E402
from app.models.entities import CandidateProfileEntity  # noqa: E402
from app.services.embedding_service import get_embedding  # noqa: E402
from app.services.internal_candidate_semantic_service import (  # noqa: E402
    _candidate_result,
    _candidate_skill_tokens,
    _candidate_skills,
    _candidate_years,
    _experience_match,
    _job_experience,
    _job_role,
    _job_skills,
    _location_match,
    _role_match,
    _text,
    _tokens,
    INTERNAL_CANDIDATE_MATCH_WEIGHTS,
    match_internal_candidates_for_job,
)
from app.services.job_text_service import build_job_text  # noqa: E402
from app.services.qdrant_service import count_collection_points, search_internal_candidate_chunks  # noqa: E402
from app.utils.exceptions import APIError  # noqa: E402

DEFAULT_JOB_ID = "26ea1741-cb27-45b3-9f34-95aa25f443ee"
SEP = "=" * 80
EXAMPLE_LIMIT = 10


def _job_id_from_cli() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    return os.getenv("JOB_ID", DEFAULT_JOB_ID).strip()


def _has_invalid_vector(vector: list[float]) -> bool:
    return any(not isinstance(value, (int, float)) or math.isnan(float(value)) or math.isinf(float(value)) for value in vector)


def _print_section(title: str) -> None:
    print(f"\n{SEP}")
    print(title)
    print(SEP)


def _row_summary(row: Any) -> dict[str, Any]:
    return {
        "candidateRecordId": str(getattr(row, "id", "") or ""),
        "candidateId": str(getattr(row, "candidate_id", "") or ""),
        "agencyId": str(getattr(row, "agency_id", "") or ""),
        "embedding_status": str(getattr(row, "embedding_status", "") or ""),
        "embedding_version": str(getattr(row, "embedding_version", "") or ""),
    }


def main() -> None:
    job_id = _job_id_from_cli()
    print(SEP)
    print("INTERNAL CANDIDATE MATCH DIAGNOSTIC")
    print(f"job_id: {job_id}")
    print(f"collection: {INTERNAL_CANDIDATE_COLLECTION_NAME}")
    print(f"embedding_version: {EMBEDDING_VERSION}")
    print(SEP)

    db = SessionLocal()
    try:
        _print_section("CHECK 1 - JOB")
        job = JobRepository(db).get(job_id)
        if not job:
            print("job_exists: false")
            print("failure_point: job could not be loaded")
            return

        structured_data = getattr(job, "structured_data", {}) if isinstance(getattr(job, "structured_data", {}), dict) else {}
        transcript = structured_data.get("voiceTranscript") or structured_data.get("voice_transcript") or structured_data.get("transcript") or ""
        description = _text(getattr(job, "description", ""))
        skills = _job_skills(job)
        responsibilities = getattr(job, "responsibilities", []) if isinstance(getattr(job, "responsibilities", []), list) else []

        print("job_exists: true")
        print(f"job_id: {job_id}")
        print(f"agency_id: {_text(getattr(job, 'agency_id', ''))}")
        print(f"title: {_text(getattr(job, 'title', ''))}")
        print(f"description_length: {len(description)}")
        print(f"skills_count: {len(skills)}")
        print(f"responsibilities_count: {len(responsibilities)}")
        print(f"structured_data_present: {bool(structured_data)}")
        print(f"voice_transcript_present: {bool(_text(transcript))}")

        _print_section("CHECK 2 - JOB EMBEDDING")
        job_text = build_job_text(job)
        print(f"embedding_model: {EMBEDDING_MODEL_NAME}")
        try:
            query_vector = get_embedding(job_text)
            print(f"embedding_generation_succeeded: true")
            print(f"vector_dimension: {len(query_vector)}")
            print(f"vector_length: {len(query_vector)}")
            print(f"vector_has_invalid_values: { _has_invalid_vector(query_vector) }")
        except Exception as exc:  # noqa: BLE001
            print("embedding_generation_succeeded: false")
            print(f"embedding_error: {type(exc).__name__}: {exc}")
            return

        _print_section("CHECK 3 - RAW QDRANT SEARCH")
        limit = max(1, INTERNAL_CANDIDATE_RETRIEVAL_TOP_K)
        filters = {"embeddingVersion": EMBEDDING_VERSION}
        print(f"qdrant_collection: {INTERNAL_CANDIDATE_COLLECTION_NAME}")
        print(f"requested_limit: {limit}")
        print(f"filters: {filters}")
        raw_hits = search_internal_candidate_chunks(
            query_vector=query_vector,
            limit=limit,
            metadata_filters=filters,
            raise_on_unavailable=True,
            allow_unfiltered_fallback=False,
        )
        total_points = count_collection_points(INTERNAL_CANDIDATE_COLLECTION_NAME)
        print(f"collection_points: {total_points}")
        print(f"raw_qdrant_hits: {len(raw_hits)}")
        top_scores = [round(float(hit.get("score") or 0.0), 6) for hit in raw_hits[:10]]
        top_candidate_ids = [str((hit.get("payload") or {}).get("candidateRecordId") or "") for hit in raw_hits[:10]]
        top_versions = [str((hit.get("payload") or {}).get("embeddingVersion") or "") for hit in raw_hits[:10]]
        top_agencies = [str((hit.get("payload") or {}).get("agencyId") or "") for hit in raw_hits[:10]]
        print(f"top_10_scores: {top_scores}")
        print(f"top_10_candidateRecordIds: {top_candidate_ids}")
        print(f"top_10_embeddingVersions: {top_versions}")
        print(f"top_10_agencyIds: {top_agencies}")

        _print_section("CHECK 4 - QDRANT -> POSTGRES LOOKUP")
        record_ids = list(dict.fromkeys(_text((hit.get("payload") or {}).get("candidateRecordId")) for hit in raw_hits if _text((hit.get("payload") or {}).get("candidateRecordId"))))
        rows = db.scalars(select(CandidateProfileEntity).where(CandidateProfileEntity.id.in_(record_ids))).all() if record_ids else []
        row_by_id = {str(row.id): row for row in rows}
        missing_from_pg = [rid for rid in record_ids if rid not in row_by_id]
        dropped_status_rows = [row for row in rows if getattr(row, "embedding_status", None) != "EMBEDDED"]
        dropped_version_rows = [row for row in rows if getattr(row, "embedding_version", None) != EMBEDDING_VERSION]
        print(f"qdrant_candidate_ids_returned: {record_ids[:20]}")
        print(f"postgres_rows_found: {len(rows)}")
        print(f"qdrant_ids_missing_from_postgres: {missing_from_pg[:EXAMPLE_LIMIT]}")
        print(f"rows_with_embedding_status_not_embedded: {len(dropped_status_rows)}")
        print(f"rows_with_embedding_version_mismatch: {len(dropped_version_rows)}")

        _print_section("CHECK 5 - ACTUAL SCORE CALCULATION")
        job_skills = _job_skills(job)
        job_tokens = _tokens(job_skills)
        job_experience = _job_experience(job)
        scored: list[dict[str, Any]] = []
        invalid_education_candidates: list[dict[str, Any]] = []
        for hit in raw_hits:
            payload = hit.get("payload") or {}
            row = row_by_id.get(_text(payload.get("candidateRecordId")))
            if not row:
                continue
            if getattr(row, "embedding_status", None) != "EMBEDDED":
                continue
            if getattr(row, "embedding_version", None) != EMBEDDING_VERSION:
                continue

            candidate_skills = _candidate_skills(row)
            candidate_skill_tokens = _candidate_skill_tokens(row, job_tokens)
            matched = sorted(job_tokens.intersection(candidate_skill_tokens))
            skill_match = len(matched) / len(job_tokens) if job_tokens else 0.5
            candidate_years, experience_source = _candidate_years(row)
            experience_match = _experience_match(candidate_years, job_experience)
            location_match = _location_match(job, row)
            role_match = _role_match(job, row)
            semantic = max(0.0, min(1.0, float(hit.get("score") or 0.0)))
            weights = INTERNAL_CANDIDATE_MATCH_WEIGHTS
            weight_sum = sum(max(0.0, float(weights.get(key, 0.0))) for key in ("semantic_similarity", "skill_match", "experience_match")) or 1.0
            base_score = (
                max(0.0, float(weights.get("semantic_similarity", 0.7))) * semantic
                + max(0.0, float(weights.get("skill_match", 0.2))) * skill_match
                + max(0.0, float(weights.get("experience_match", 0.1))) * experience_match
            ) / weight_sum
            final_score = max(0.0, min(1.0, base_score * (0.85 + (0.15 * location_match)) * (0.90 + (0.10 * role_match))))
            education = getattr(row, "education", [])
            if isinstance(education, list) and any(not isinstance(item, str) for item in education):
                invalid_education_candidates.append(
                    {
                        "candidateRecordId": str(row.id),
                        "candidateId": str(row.candidate_id or row.id),
                        "education_type": type(education[0]).__name__ if education else "list",
                    }
                )
            scored.append(
                {
                    "_row": row,
                    "candidateRecordId": str(row.id),
                    "candidateId": str(row.candidate_id or row.id),
                    "name": _text(row.name or row.candidate_id or row.id),
                    "semantic_score": round(semantic, 6),
                    "skill_score": round(skill_match, 6),
                    "experience_score": round(experience_match, 6),
                    "location_score": round(location_match, 6),
                    "role_score": round(role_match, 6),
                    "final_match_score": round(final_score, 6),
                    "experience_source": experience_source,
                }
            )

        scores = [row["final_match_score"] for row in scored]
        if scores:
            print(f"candidates_scored: {len(scored)}")
            print(f"highest_score: {max(scores):.6f}")
            print(f"lowest_score: {min(scores):.6f}")
            print(f"average_score: {mean(scores):.6f}")
            print(f"score_ge_threshold_count: {sum(1 for score in scores if score >= INTERNAL_CANDIDATE_MATCH_THRESHOLD)}")
            print(f"score_lt_threshold_count: {sum(1 for score in scores if score < INTERNAL_CANDIDATE_MATCH_THRESHOLD)}")
            print(f"score_eq_zero_count: {sum(1 for score in scores if score == 0)}")
            print(f"score_between_zero_and_threshold_count: {sum(1 for score in scores if 0 < score < INTERNAL_CANDIDATE_MATCH_THRESHOLD)}")
            print(f"candidates_with_non_string_education_entries: {len(invalid_education_candidates)}")
            if invalid_education_candidates:
                print(f"invalid_education_examples: {invalid_education_candidates[:EXAMPLE_LIMIT]}")
            print("top_20_scored_candidates:")
            for idx, row in enumerate(sorted(scored, key=lambda item: item["final_match_score"], reverse=True)[:20], start=1):
                print(
                    f"  {idx:02d} candidateRecordId={row['candidateRecordId']} "
                    f"candidateId={row['candidateId']} name={row['name']} "
                    f"semantic={row['semantic_score']:.6f} skill={row['skill_score']:.6f} "
                    f"experience={row['experience_score']:.6f} location={row['location_score']:.6f} "
                    f"role={row['role_score']:.6f} final_match_score={row['final_match_score']:.6f}"
                )
        else:
            print("candidates_scored: 0")
            print("highest_score: 0.0")
            print("lowest_score: 0.0")
            print("average_score: 0.0")
            print("score_ge_threshold_count: 0")
            print("score_lt_threshold_count: 0")
            print("score_eq_zero_count: 0")
            print("score_between_zero_and_threshold_count: 0")

        _print_section("CHECK 6 - THRESHOLD FILTER")
        qualified = [row for row in scored if row["final_match_score"] >= INTERNAL_CANDIDATE_MATCH_THRESHOLD]
        removed = [row for row in scored if row["final_match_score"] < INTERNAL_CANDIDATE_MATCH_THRESHOLD]
        print(f"threshold: {INTERNAL_CANDIDATE_MATCH_THRESHOLD}")
        print(f"candidates_before_threshold: {len(scored)}")
        print(f"candidates_after_threshold: {len(qualified)}")
        print(f"candidates_removed_below_threshold: {len(removed)}")
        if removed:
            print(f"removed_candidate_ids: {[row['candidateRecordId'] for row in removed[:EXAMPLE_LIMIT]]}")

        _print_section("CHECK 7 - FINAL RESULT")
        print(f"qualified_candidates: {len(qualified)}")
        print(f"final_limit: {INTERNAL_CANDIDATE_MATCH_LIMIT}")
        try:
            final_result = match_internal_candidates_for_job(db=db, job_id=job_id, agency_id=_text(getattr(job, "agency_id", "")), limit=None)
            final_candidates = final_result.get("candidates") or []
            final_ids = [str(getattr(candidate, "id", "")) for candidate in final_candidates]
            print(f"returned_candidates: {len(final_candidates)}")
            print(f"returned_candidate_ids: {final_ids}")
            print(f"deduplication_removed_anything: {len(raw_hits) != len(record_ids)}")
        except Exception as exc:  # noqa: BLE001
            print("returned_candidates: 0")
            print(f"final_result_error: {type(exc).__name__}: {exc}")
            print(f"deduplication_removed_anything: {len(raw_hits) != len(record_ids)}")

        _print_section("CHECK 8 - FRONTEND/API ISOLATION")
        if len(raw_hits) == 0:
            print("A. Qdrant returned zero")
        elif len(rows) == 0:
            print("B. Qdrant returned candidates but PostgreSQL lookup removed them")
        elif len(scored) == 0:
            print("C. scoring removed them")
        elif len(qualified) == 0:
            print("D. threshold removed them")
        elif 'final_candidates' in locals() and len(final_candidates) > 0:
            print("BACKEND MATCHING WORKS - INVESTIGATE API/FRONTEND")
        else:
            print("E. backend returned candidates but frontend later hid them")

        _print_section("CHECK 9 - 403 / 500 DIAGNOSTIC")
        print("route: GET /candidates")
        print("possible_403_sources:")
        print("- authentication via get_current_user before route body")
        print("- assert_job_ownership(db, job_id=jobId, user_id=_.get('id', ''))")
        print("- _resolve_agency_scope() if resolve_company_id_for_user returns no agency")
        print("- match_internal_candidates_for_job() returns 403 if job.agency_id != resolved agency_id")
        print("possible_500_sources:")
        print("- database failures in job lookup, request-state lookup, or candidate profile loading")
        print("- embedding/Qdrant failures that raise unexpected exceptions")
        print("- build_candidate_view_model or response shaping failures")
        print("csrf: not applicable to GET /candidates in this code path")

    except APIError as exc:
        print(f"api_error: status_code={exc.status_code} code={getattr(exc, 'code', '')} message={exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
