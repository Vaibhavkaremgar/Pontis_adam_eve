"""
Trigger match_internal_candidates_for_job() to emit [EXPERIENCE_DEBUG] logs.
Read-only. No changes to data.
"""
from __future__ import annotations
import logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# Route all logger.error() calls to stdout so we capture [EXPERIENCE_DEBUG]
logging.basicConfig(level=logging.ERROR, format="%(message)s", stream=sys.stdout)

from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from app.db.database_url import normalize_database_url
from app.models.entities import JobEntity
from app.services.internal_candidate_semantic_service import match_internal_candidates_for_job

JOB_ID   = "26ea1741-cb27-45b3-9f34-95aa25f443ee"
AGENCY_ID = "0f71cf1f-1aca-4188-a76b-0b97af1483ad"

engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]), pool_pre_ping=True)
with Session(engine) as db:
    result = match_internal_candidates_for_job(db=db, job_id=JOB_ID, agency_id=AGENCY_ID)
    print(f"\n--- RESULT SUMMARY ---")
    print(f"qualified_count : {result['qualified_count']}")
    print(f"retrieval_count : {result['retrieval_count']}")
    print(f"threshold       : {result['threshold']}")
    print(f"fallback_eligible: {result['fallback_eligible']}")
