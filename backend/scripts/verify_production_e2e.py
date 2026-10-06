"""
verify_production_e2e.py
========================
Read-only verification helper for the Adam + Eve + Maya E2E flow.

Usage:
    python backend/scripts/verify_production_e2e.py \\
        --job-id <job_id> \\
        --candidate-id <candidate_id> \\
        --candidate-record-id <candidate_record_id> \\
        --agency-id <agency_id> \\
        --recruiter-user-id <recruiter_user_id>

All checks are READ-ONLY. This script never writes to the database or Qdrant.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# Ensure the backend package is importable when run from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

_PASS = "\033[92m[PASS]\033[0m"
_FAIL = "\033[91m[FAIL]\033[0m"
_WARN = "\033[93m[WARN]\033[0m"
_INFO = "\033[94m[INFO]\033[0m"


def _p(status: str, msg: str) -> None:
    print(f"{status} {msg}")


def _get_db_session():
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    engine = create_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    return Session()


def check_job(db, job_id: str) -> bool:
    row = db.execute(
        text("SELECT id, title, job_status, is_active, agency_id, LENGTH(COALESCE(description,'') || COALESCE(requirements,'')) AS text_len FROM job_descriptions WHERE id = :id"),
        {"id": job_id},
    ).mappings().first()
    if not row:
        _p(_FAIL, f"Job not found: {job_id}")
        return False
    _p(_PASS, f"Job exists: '{row['title']}' status={row['job_status']} is_active={row['is_active']}")
    if row["job_status"] != "active":
        _p(_WARN, f"Job job_status is '{row['job_status']}', expected 'active'")
    if (row["text_len"] or 0) < 20:
        _p(_FAIL, f"Job text too short ({row['text_len']} chars) — embedding/matching will fail")
        return False
    _p(_PASS, f"Job text length: {row['text_len']} chars")
    return True


def check_linkedin_fallback(db, job_id: str) -> None:
    row = db.execute(
        text("SELECT structured_data FROM job_descriptions WHERE id = :id"),
        {"id": job_id},
    ).mappings().first()
    if not row:
        return
    sd = row["structured_data"] or {}
    posting = sd.get("linkedinPosting") if isinstance(sd, dict) else {}
    if not posting:
        _p(_INFO, "No LinkedIn posting recorded for this job (fallback not triggered yet)")
        return
    app_url = (posting or {}).get("applicationUrl", "")
    if app_url and "job_id" not in app_url and app_url.endswith("/application"):
        _p(_PASS, f"LinkedIn fallback application URL correct: {app_url}")
    elif app_url:
        _p(_WARN, f"LinkedIn fallback application URL: {app_url} — verify no job_id appended")
    status = (posting or {}).get("status", "")
    _p(_INFO, f"LinkedIn posting status: {status}")


def check_candidate(db, candidate_id: str, candidate_record_id: str) -> bool:
    row = db.execute(
        text("""
            SELECT id, candidate_id, name, email, agency_id, embedding_status,
                   embedding_version, embedding_text_hash, embedding_indexed_at,
                   last_refreshed_at, created_at,
                   (resume_text IS NOT NULL) AS has_resume,
                   (parsed_resume_text IS NOT NULL) AS has_parsed,
                   (summary IS NOT NULL) AS has_summary,
                   (skills IS NOT NULL) AS has_skills,
                   (parsed_resume_json IS NOT NULL) AS has_json
            FROM candidates
            WHERE id = :record_id
        """),
        {"record_id": candidate_record_id},
    ).mappings().first()
    if not row:
        _p(_FAIL, f"Candidate record not found: record_id={candidate_record_id}")
        return False
    _p(_PASS, f"Candidate exists: name='{row['name']}' email='{row['email']}' agency_id={row['agency_id']}")
    if not row["email"]:
        _p(_FAIL, "Candidate email is NULL — interest request will fail with 422")
        return False
    _p(_PASS, f"Candidate email present: {row['email']}")
    profile_ok = any([row["has_resume"], row["has_parsed"], row["has_summary"], row["has_skills"], row["has_json"]])
    if profile_ok:
        _p(_PASS, "Candidate has sufficient profile data for embedding")
    else:
        _p(_FAIL, "Candidate has no profile data — embedding will be skipped")
    return True


def check_embedding(db, candidate_record_id: str) -> bool:
    row = db.execute(
        text("""
            SELECT embedding_status, embedding_version, embedding_text_hash,
                   embedding_indexed_at, updated_at
            FROM candidates WHERE id = :id
        """),
        {"id": candidate_record_id},
    ).mappings().first()
    if not row:
        _p(_FAIL, f"Candidate record not found for embedding check: {candidate_record_id}")
        return False
    status = row["embedding_status"]
    version = row["embedding_version"]
    text_hash = row["embedding_text_hash"]
    indexed_at = row["embedding_indexed_at"]
    updated_at = row["updated_at"]

    expected_version = os.getenv("EMBEDDING_VERSION", "v2_structured")

    if status == "EMBEDDED":
        _p(_PASS, f"Candidate embedding_status = EMBEDDED")
    else:
        _p(_FAIL, f"Candidate embedding_status = {status!r} (expected EMBEDDED)")
        return False

    if version == expected_version:
        _p(_PASS, f"Embedding version matches: {version}")
    else:
        _p(_FAIL, f"Embedding version mismatch: row={version!r} expected={expected_version!r}")
        return False

    if text_hash:
        _p(_PASS, f"Embedding text_hash present: {text_hash[:16]}...")
    else:
        _p(_FAIL, "Embedding text_hash is NULL")

    if indexed_at:
        _p(_PASS, f"Embedding indexed_at: {indexed_at}")
        if updated_at and indexed_at < updated_at:
            _p(_WARN, f"Embedding may be stale: indexed_at={indexed_at} < updated_at={updated_at}")
    else:
        _p(_FAIL, "Embedding indexed_at is NULL")
        return False

    return True


def check_qdrant(candidate_record_id: str) -> bool:
    qdrant_url = os.getenv("QDRANT_URL", "")
    qdrant_key = os.getenv("QDRANT_API_KEY", "")
    collection = os.getenv("INTERNAL_CANDIDATE_COLLECTION_NAME", "internal_candidate_chunks")

    if not qdrant_url:
        _p(_FAIL, "QDRANT_URL is not set — cannot check Qdrant")
        return False

    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue
    except ImportError:
        _p(_WARN, "qdrant_client not installed — skipping Qdrant check")
        return False

    try:
        client = QdrantClient(url=qdrant_url, api_key=qdrant_key or None)
        info = client.get_collection(collection)
        points_count = getattr(info, "points_count", 0)
        _p(_PASS, f"Qdrant collection '{collection}' accessible, total points: {points_count}")
    except Exception as exc:
        _p(_FAIL, f"Qdrant connection failed: {exc}")
        if "403" in str(exc) or "Forbidden" in str(exc):
            _p(_FAIL, "403 Forbidden — check QDRANT_URL and QDRANT_API_KEY match the cluster")
        return False

    try:
        results, _ = client.scroll(
            collection_name=collection,
            scroll_filter=Filter(must=[
                FieldCondition(key="candidateRecordId", match=MatchValue(value=candidate_record_id))
            ]),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if results:
            payload = results[0].payload or {}
            _p(_PASS, f"Qdrant point found for candidateRecordId={candidate_record_id}")
            _p(_INFO, f"  agencyId={payload.get('agencyId')} embeddingVersion={payload.get('embeddingVersion')} indexedAt={payload.get('indexedAt')}")
            expected_version = os.getenv("EMBEDDING_VERSION", "v2_structured")
            if payload.get("embeddingVersion") != expected_version:
                _p(_WARN, f"  Qdrant embeddingVersion={payload.get('embeddingVersion')!r} != expected={expected_version!r}")
        else:
            _p(_FAIL, f"No Qdrant point found for candidateRecordId={candidate_record_id}")
            return False
    except Exception as exc:
        _p(_FAIL, f"Qdrant scroll failed: {exc}")
        return False

    return True


def check_interest_request(db, job_id: str, candidate_id: str, agency_id: str) -> dict[str, Any]:
    row = db.execute(
        text("""
            SELECT id, status, created_by, created_at, responded_at, eve_event_id
            FROM candidate_requests
            WHERE agency_id = :agency_id AND job_id = :job_id AND candidate_id = :candidate_id
            ORDER BY created_at DESC LIMIT 1
        """),
        {"agency_id": agency_id, "job_id": job_id, "candidate_id": candidate_id},
    ).mappings().first()
    if not row:
        _p(_INFO, "No candidate_requests row found (interest not yet clicked)")
        return {}
    _p(_PASS, f"candidate_requests row: id={row['id']} status={row['status']}")
    return dict(row)


def check_outbound_event(db, request_id: str) -> dict[str, Any]:
    row = db.execute(
        text("""
            SELECT id, adam_event_id, notification_type, status, attempt_count, last_error, delivered_at
            FROM adam_eve_outbound_events
            WHERE adam_event_id = :adam_event_id
            ORDER BY created_at DESC LIMIT 1
        """),
        {"adam_event_id": request_id},
    ).mappings().first()
    if not row:
        _p(_FAIL, f"No adam_eve_outbound_events row for adam_event_id={request_id}")
        return {}
    if row["status"] == "delivered":
        _p(_PASS, f"Adam→Eve event delivered: attempt_count={row['attempt_count']} delivered_at={row['delivered_at']}")
    elif row["status"] == "pending":
        _p(_WARN, f"Adam→Eve event still pending: attempt_count={row['attempt_count']} last_error={row['last_error']}")
    else:
        _p(_FAIL, f"Adam→Eve event status={row['status']} last_error={row['last_error']}")
    return dict(row)


def check_booking_token(db, job_id: str, candidate_id: str) -> dict[str, Any]:
    row = db.execute(
        text("""
            SELECT id, token, token_type, workflow_name, is_active, status, expires_at, created_at
            FROM notification_workflow_tokens
            WHERE job_id = :job_id AND candidate_id = :candidate_id AND token_type = 'slot_selection'
            ORDER BY created_at DESC LIMIT 1
        """),
        {"job_id": job_id, "candidate_id": candidate_id},
    ).mappings().first()
    if not row:
        _p(_INFO, "No notification_workflow_tokens row (acceptance not yet processed)")
        return {}
    booking_url = f"https://interview.pontis.one/booking.html?token={row['token']}"
    _p(_PASS, f"Booking token exists: token_type={row['token_type']} is_active={row['is_active']}")
    _p(_PASS, f"Booking URL: {booking_url}")
    return dict(row)


def check_booking_url_consistency(db, job_id: str, candidate_id: str, candidate_record_id: str, request_id: str) -> None:
    email_row = db.execute(
        text("""
            SELECT body AS email_booking_url, delivery_reference AS token
            FROM notification_events
            WHERE notification_key LIKE :key_prefix
              AND candidate_id = :candidate_record_id
            ORDER BY created_at DESC LIMIT 1
        """),
        {"key_prefix": f"slot-selection:{job_id}%", "candidate_record_id": candidate_record_id},
    ).mappings().first()

    eve_row = db.execute(
        text("""
            SELECT payload->>'booking_url' AS eve_booking_url, status
            FROM adam_eve_outbound_events
            WHERE adam_event_id LIKE :key_prefix
              AND notification_type = 'interview_slot_booking'
            ORDER BY created_at DESC LIMIT 1
        """),
        {"key_prefix": f"slot-booking:{request_id}%"},
    ).mappings().first()

    if not email_row and not eve_row:
        _p(_INFO, "No booking URL consistency data yet (acceptance not processed)")
        return

    email_url = (email_row or {}).get("email_booking_url", "")
    eve_url = (eve_row or {}).get("eve_booking_url", "")

    if email_url:
        _p(_PASS, f"Email booking URL: {email_url}")
    else:
        _p(_WARN, "Email booking URL not found in notification_events")

    if eve_url:
        _p(_PASS, f"Eve notification booking URL: {eve_url}")
    else:
        _p(_WARN, "Eve notification booking URL not found in adam_eve_outbound_events")

    if email_url and eve_url:
        if email_url == eve_url:
            _p(_PASS, "Booking URLs are identical in email and Eve notification")
        else:
            _p(_FAIL, f"Booking URL MISMATCH:\n  email: {email_url}\n  eve:   {eve_url}")


def check_interview_results(db, job_id: str, candidate_id: str) -> None:
    row = db.execute(
        text("""
            SELECT id, status, interview_score, technical_score, communication_score,
                   culture_fit_score, ai_summary, transcript, video_url, created_at
            FROM interviews
            WHERE job_id = :job_id AND candidate_id = :candidate_id
            ORDER BY created_at DESC LIMIT 1
        """),
        {"job_id": job_id, "candidate_id": candidate_id},
    ).mappings().first()
    if not row:
        _p(_INFO, "No interviews row yet (interview not completed)")
        return
    status = row["status"] or ""
    if status in ("completed", "interview_completed", "results_ready"):
        _p(_PASS, f"Interview completed: status={status} score={row['interview_score']}")
    else:
        _p(_WARN, f"Interview status={status!r} (not yet completed)")
    if row["transcript"]:
        _p(_PASS, f"Transcript present ({len(row['transcript'])} chars)")
    else:
        _p(_WARN, "Transcript is NULL")
    if row["ai_summary"]:
        _p(_PASS, "AI summary present")
    else:
        _p(_WARN, "AI summary is NULL")
    if row["video_url"]:
        _p(_PASS, f"Video URL present: {row['video_url']}")
    else:
        _p(_WARN, "Video URL is NULL (recording not yet available)")


def check_idempotency_counts(db, job_id: str, candidate_id: str, agency_id: str, candidate_record_id: str, request_id: str) -> None:
    counts = db.execute(
        text("""
            SELECT
                (SELECT COUNT(*) FROM candidate_requests
                 WHERE agency_id = :agency_id AND job_id = :job_id AND candidate_id = :candidate_id) AS request_count,
                (SELECT COUNT(*) FROM notification_workflow_tokens
                 WHERE job_id = :job_id AND candidate_id = :candidate_id AND token_type = 'slot_selection') AS token_count,
                (SELECT COUNT(*) FROM notification_events
                 WHERE notification_key LIKE :notif_key AND candidate_id = :candidate_record_id) AS notif_count,
                (SELECT COUNT(*) FROM adam_eve_outbound_events
                 WHERE adam_event_id LIKE :slot_key AND notification_type = 'interview_slot_booking') AS slot_event_count
        """),
        {
            "agency_id": agency_id,
            "job_id": job_id,
            "candidate_id": candidate_id,
            "notif_key": f"slot-selection:{job_id}%",
            "candidate_record_id": candidate_record_id,
            "slot_key": f"slot-booking:{request_id}%",
        },
    ).mappings().first()
    if not counts:
        return
    for field, label in [
        ("request_count", "candidate_requests rows"),
        ("token_count", "notification_workflow_tokens rows"),
        ("notif_count", "notification_events rows"),
        ("slot_event_count", "slot_booking outbound events"),
    ]:
        val = counts[field]
        if val == 1:
            _p(_PASS, f"Idempotency: {label} = {val} (correct)")
        elif val == 0:
            _p(_INFO, f"Idempotency: {label} = 0 (not yet created)")
        else:
            _p(_FAIL, f"Idempotency: {label} = {val} (expected 1 — DUPLICATE DETECTED)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only E2E verification for Adam+Eve+Maya flow")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--candidate-id", required=True, help="External candidate_id string")
    parser.add_argument("--candidate-record-id", required=True, help="candidates.id UUID")
    parser.add_argument("--agency-id", required=True)
    parser.add_argument("--recruiter-user-id", required=False, default="")
    args = parser.parse_args()

    job_id = args.job_id
    candidate_id = args.candidate_id
    candidate_record_id = args.candidate_record_id
    agency_id = args.agency_id

    print("\n" + "=" * 60)
    print("Adam + Eve + Maya — Production E2E Verification")
    print("=" * 60)
    print(f"job_id              = {job_id}")
    print(f"candidate_id        = {candidate_id}")
    print(f"candidate_record_id = {candidate_record_id}")
    print(f"agency_id           = {agency_id}")
    print("=" * 60 + "\n")

    try:
        db = _get_db_session()
    except Exception as exc:
        _p(_FAIL, f"Cannot connect to database: {exc}")
        sys.exit(1)

    print("--- STAGE 1: Job ---")
    check_job(db, job_id)
    check_linkedin_fallback(db, job_id)

    print("\n--- STAGE 2: Candidate ---")
    check_candidate(db, candidate_id, candidate_record_id)

    print("\n--- STAGE 3: Embedding (DB) ---")
    check_embedding(db, candidate_record_id)

    print("\n--- STAGE 4: Qdrant ---")
    check_qdrant(candidate_record_id)

    print("\n--- STAGE 7: Interest Request ---")
    request_row = check_interest_request(db, job_id, candidate_id, agency_id)
    request_id = request_row.get("id", "")

    if request_id:
        print("\n--- STAGE 8: Adam→Eve Delivery ---")
        check_outbound_event(db, request_id)

        print("\n--- STAGE 12: Booking Token ---")
        check_booking_token(db, job_id, candidate_id)

        print("\n--- STAGE 13: Booking URL Consistency ---")
        check_booking_url_consistency(db, job_id, candidate_id, candidate_record_id, request_id)

        print("\n--- STAGE 11: Idempotency Counts ---")
        check_idempotency_counts(db, job_id, candidate_id, agency_id, candidate_record_id, request_id)

    print("\n--- STAGE 17: Interview Results ---")
    check_interview_results(db, job_id, candidate_id)

    print("\n" + "=" * 60)
    print("Verification complete. Review [FAIL] and [WARN] lines above.")
    print("=" * 60 + "\n")

    db.close()


if __name__ == "__main__":
    main()
