#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

export LOCAL_DEV_MODE="${LOCAL_DEV_MODE:-true}"
export SERPAPI_DEBUG="${SERPAPI_DEBUG:-true}"
export MOCK_XRAY_MODE="${MOCK_XRAY_MODE:-true}"
export SERPAPI_DEBUG_LOG_DIR="${SERPAPI_DEBUG_LOG_DIR:-$BACKEND_DIR/debug_logs/serpapi}"
export PYTHONPATH="$BACKEND_DIR${PYTHONPATH:+:$PYTHONPATH}"

python - <<'PY'
import json
import os
from pathlib import Path
from types import SimpleNamespace

from app.core.config import SERPAPI_DEBUG_LOG_DIR
from app.db.session import SessionLocal
from app.services.ranking.semantic_reranking_service import rerank_xray_candidates
from app.services.serpapi_sourcing_service import build_linkedin_xray_queries
from app.services.sourcing.xray_service import build_xray_candidate_results, discover_xray_candidates

job = SimpleNamespace(
    id="local-xray-job",
    title="Senior Backend Engineer",
    location="San Francisco",
    experience_level="Senior",
    description="Build recruiter-facing search and ranking systems.",
    skills_required=["Python", "FastAPI", "AWS", "Kubernetes"],
    structured_data={
        "role": "Senior Backend Engineer",
        "location": "San Francisco",
        "seniority": "Senior",
        "skills": ["Python", "FastAPI", "AWS", "Kubernetes"],
        "company_stage": "Series A",
        "hiring_preferences": "high ownership",
        "industry": "developer tools",
        "leadership_expectations": "technical leadership",
    },
)

intake = dict(job.structured_data)
queries = build_linkedin_xray_queries(
    role=intake["role"],
    seniority=intake["seniority"],
    skills=intake["skills"],
    location=intake["location"],
    company_stage=intake["company_stage"],
    hiring_preferences=intake["hiring_preferences"],
    industry=intake["industry"],
    leadership_expectations=intake["leadership_expectations"],
)

with SessionLocal() as db:
    candidates = discover_xray_candidates(
        job=job,
        intake=intake,
        limit=12,
        pages_per_query=2,
        recruiter_preferences={"preferred_companies": ["Acme", "Beacon"]},
        db=db,
    )
    review_deck = build_xray_candidate_results(job=job, candidates=candidates, limit=12)
    reranked_deck = review_deck
    rerank_error = ""
    try:
        reranked_deck = rerank_xray_candidates(
            db=db,
            job=job,
            candidates=review_deck,
            recruiter_id="local-debug",
            source_query=job.title,
        )
    except Exception as exc:
        rerank_error = str(exc)

debug_dir = Path(SERPAPI_DEBUG_LOG_DIR)
report_path = debug_dir / "dedupe_report.json"
report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}

print(f"query_count={len(queries)}")
print(f"api_hits={report.get('calls_executed', 0)}")
print(f"duplicate_rate={report.get('duplicate_rate', 0.0)}")
print(f"rerank_count={len(reranked_deck)}")
print(f"final_reviewable_count={len(review_deck)}")
print(f"quota_usage_estimate={report.get('pages_requested', 0)}")
if rerank_error:
    print(f"rerank_warning={rerank_error}")
PY
