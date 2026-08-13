"""
TEMPORARY DIAGNOSTIC SCRIPT — debug_internal_candidate_index.py
Safe to delete after investigation. READ-ONLY. No data is modified.

Run from backend/:
    python -m scripts.debug_internal_candidate_index
"""
from __future__ import annotations

import sys
import os

# Allow running from backend/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import Counter, defaultdict
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from sqlalchemy import select, func

from app.core.config import (
    EMBEDDING_VERSION,
    INTERNAL_CANDIDATE_COLLECTION_NAME,
    INTERNAL_CANDIDATE_MATCH_LIMIT,
    INTERNAL_CANDIDATE_MATCH_THRESHOLD,
    INTERNAL_CANDIDATE_MIN_MATCHES,
    INTERNAL_CANDIDATE_RETRIEVAL_TOP_K,
    QDRANT_API_KEY,
    QDRANT_URL,
)
from app.db.session import SessionLocal
from app.models.entities import CandidateProfileEntity

SEP = "=" * 60
EXAMPLE_LIMIT = 10


# ── Qdrant helpers ────────────────────────────────────────────────────────────

def _qdrant_client() -> QdrantClient:
    if not QDRANT_URL:
        raise RuntimeError("QDRANT_URL is not configured")
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)


def _scroll_all(client: QdrantClient, collection: str) -> list[Any]:
    """Scroll through all points in a collection and return them."""
    points: list[Any] = []
    offset = None
    while True:
        response = client.scroll(
            collection_name=collection,
            limit=250,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        batch, next_offset = response if isinstance(response, tuple) else (response, None)
        points.extend(batch or [])
        if not next_offset:
            break
        offset = next_offset
    return points


# ── CHECK 1 ───────────────────────────────────────────────────────────────────

def check1_collection_health(client: QdrantClient) -> dict[str, Any]:
    print(f"\n{SEP}")
    print("CHECK 1 — QDRANT COLLECTION HEALTH")
    print(SEP)
    info = client.get_collection(INTERNAL_CANDIDATE_COLLECTION_NAME)
    status = str(getattr(info, "status", "unknown"))
    points_count = int(getattr(info, "points_count", 0) or 0)
    config = getattr(info, "config", None)
    params = getattr(config, "params", None) if config else None
    vectors_config = getattr(params, "vectors", None) if params else None
    if vectors_config and hasattr(vectors_config, "size"):
        vector_size = int(vectors_config.size)
        distance = str(getattr(vectors_config, "distance", "unknown"))
    elif isinstance(vectors_config, dict):
        first = next(iter(vectors_config.values()), None)
        vector_size = int(getattr(first, "size", 0)) if first else 0
        distance = str(getattr(first, "distance", "unknown")) if first else "unknown"
    else:
        vector_size = 0
        distance = "unknown"

    print(f"  Collection:      {INTERNAL_CANDIDATE_COLLECTION_NAME}")
    print(f"  Status:          {status}")
    print(f"  Points count:    {points_count}")
    print(f"  Vector size:     {vector_size}")
    print(f"  Distance:        {distance}")
    return {"collection": INTERNAL_CANDIDATE_COLLECTION_NAME, "status": status,
            "points_count": points_count, "vector_size": vector_size, "distance": distance}


# ── CHECK 2 ───────────────────────────────────────────────────────────────────

def check2_payload_health(points: list[Any]) -> dict[str, Any]:
    print(f"\n{SEP}")
    print("CHECK 2 — QDRANT PAYLOAD HEALTH")
    print(SEP)
    total = len(points)
    missing_record_id = missing_candidate_id = missing_version = missing_agency = 0
    wrong_source = wrong_source_type = wrong_content_type = 0
    version_dist: Counter = Counter()
    source_dist: Counter = Counter()
    source_type_dist: Counter = Counter()

    for p in points:
        pl = getattr(p, "payload", None) or {}
        if not pl.get("candidateRecordId"):
            missing_record_id += 1
        if not pl.get("candidateId"):
            missing_candidate_id += 1
        if not pl.get("embeddingVersion"):
            missing_version += 1
        if not pl.get("agencyId"):
            missing_agency += 1
        if pl.get("source") != "internal":
            wrong_source += 1
        if pl.get("sourceType") not in ("internal", "internal_resume"):
            wrong_source_type += 1
        if pl.get("contentType") != "resume":
            wrong_content_type += 1
        version_dist[pl.get("embeddingVersion") or "<missing>"] += 1
        source_dist[pl.get("source") or "<missing>"] += 1
        source_type_dist[pl.get("sourceType") or "<missing>"] += 1

    print(f"  Total points:              {total}")
    print(f"  Missing candidateRecordId: {missing_record_id}")
    print(f"  Missing candidateId:       {missing_candidate_id}")
    print(f"  Missing embeddingVersion:  {missing_version}")
    print(f"  Missing agencyId:          {missing_agency}")
    print(f"  Wrong source (!= internal):{wrong_source}")
    print(f"  Wrong sourceType:          {wrong_source_type}")
    print(f"  Wrong contentType:         {wrong_content_type}")
    print(f"\n  embeddingVersion distribution:")
    for v, c in version_dist.most_common():
        print(f"    {v}: {c}")
    print(f"\n  source distribution:")
    for v, c in source_dist.most_common():
        print(f"    {v}: {c}")
    print(f"\n  sourceType distribution:")
    for v, c in source_type_dist.most_common():
        print(f"    {v}: {c}")

    return {
        "total": total, "missing_record_id": missing_record_id,
        "missing_candidate_id": missing_candidate_id, "missing_version": missing_version,
        "missing_agency": missing_agency, "wrong_source": wrong_source,
        "wrong_source_type": wrong_source_type, "wrong_content_type": wrong_content_type,
        "version_dist": dict(version_dist), "source_dist": dict(source_dist),
    }


# ── CHECK 3 ───────────────────────────────────────────────────────────────────

def check3_id_matching(points: list[Any], pg_ids: set[str]) -> dict[str, Any]:
    print(f"\n{SEP}")
    print("CHECK 3 — POSTGRESQL ↔ QDRANT ID MATCHING")
    print(SEP)
    qdrant_record_ids: set[str] = set()
    for p in points:
        pl = getattr(p, "payload", None) or {}
        rid = str(pl.get("candidateRecordId") or "").strip()
        if rid:
            qdrant_record_ids.add(rid)

    in_both = qdrant_record_ids & pg_ids
    qdrant_only = qdrant_record_ids - pg_ids
    pg_only = pg_ids - qdrant_record_ids

    print(f"  PostgreSQL candidates:          {len(pg_ids)}")
    print(f"  Qdrant unique candidateRecordIds:{len(qdrant_record_ids)}")
    print(f"  Present in both:                {len(in_both)}")
    print(f"  Qdrant-only (no PG row):        {len(qdrant_only)}")
    print(f"  PostgreSQL-only (no Qdrant):    {len(pg_only)}")

    if qdrant_only:
        print(f"\n  Sample Qdrant-only IDs (up to {EXAMPLE_LIMIT}):")
        for rid in list(qdrant_only)[:EXAMPLE_LIMIT]:
            print(f"    {rid}")
    if pg_only:
        print(f"\n  Sample PostgreSQL-only IDs (up to {EXAMPLE_LIMIT}):")
        for rid in list(pg_only)[:EXAMPLE_LIMIT]:
            print(f"    {rid}")

    return {"pg_count": len(pg_ids), "qdrant_unique": len(qdrant_record_ids),
            "in_both": len(in_both), "qdrant_only": len(qdrant_only), "pg_only": len(pg_only),
            "qdrant_only_ids": list(qdrant_only), "pg_only_ids": list(pg_only)}


# ── CHECK 4 ───────────────────────────────────────────────────────────────────

def check4_embedding_consistency(points: list[Any], pg_rows: dict[str, Any]) -> dict[str, Any]:
    print(f"\n{SEP}")
    print("CHECK 4 — EMBEDDING STATUS CONSISTENCY")
    print(SEP)
    embedded = not_embedded = version_match = version_mismatch = 0
    mismatch_examples: list[dict] = []

    for p in points:
        pl = getattr(p, "payload", None) or {}
        rid = str(pl.get("candidateRecordId") or "").strip()
        row = pg_rows.get(rid)
        if not row:
            continue
        if row.embedding_status == "EMBEDDED":
            embedded += 1
        else:
            not_embedded += 1
        qdrant_ver = str(pl.get("embeddingVersion") or "").strip()
        pg_ver = str(row.embedding_version or "").strip()
        if qdrant_ver == pg_ver:
            version_match += 1
        else:
            version_mismatch += 1
            if len(mismatch_examples) < EXAMPLE_LIMIT:
                mismatch_examples.append({
                    "candidateRecordId": rid,
                    "pg_embedding_status": row.embedding_status,
                    "pg_embedding_version": pg_ver,
                    "qdrant_embeddingVersion": qdrant_ver,
                })

    print(f"  Qdrant candidates with PG embedding_status=EMBEDDED: {embedded}")
    print(f"  Qdrant candidates with PG embedding_status!=EMBEDDED: {not_embedded}")
    print(f"  Matching embedding versions:   {version_match}")
    print(f"  Mismatched embedding versions: {version_mismatch}")
    if mismatch_examples:
        print(f"\n  Version mismatch examples (up to {EXAMPLE_LIMIT}):")
        for ex in mismatch_examples:
            print(f"    {ex}")

    return {"embedded": embedded, "not_embedded": not_embedded,
            "version_match": version_match, "version_mismatch": version_mismatch,
            "mismatch_examples": mismatch_examples}


# ── CHECK 5 ───────────────────────────────────────────────────────────────────

def check5_agency_consistency(points: list[Any], pg_rows: dict[str, Any]) -> dict[str, Any]:
    print(f"\n{SEP}")
    print("CHECK 5 — AGENCY CONSISTENCY")
    print(SEP)
    match = mismatch = missing = 0
    mismatch_examples: list[dict] = []

    for p in points:
        pl = getattr(p, "payload", None) or {}
        rid = str(pl.get("candidateRecordId") or "").strip()
        row = pg_rows.get(rid)
        if not row:
            continue
        qdrant_agency = str(pl.get("agencyId") or "").strip()
        pg_agency = str(row.agency_id or "").strip()
        if not qdrant_agency or not pg_agency:
            missing += 1
        elif qdrant_agency == pg_agency:
            match += 1
        else:
            mismatch += 1
            if len(mismatch_examples) < EXAMPLE_LIMIT:
                mismatch_examples.append({
                    "candidateRecordId": rid,
                    "qdrant_agencyId": qdrant_agency,
                    "pg_agency_id": pg_agency,
                })

    print(f"  Matching agency IDs:   {match}")
    print(f"  Mismatched agency IDs: {mismatch}")
    print(f"  Missing agency IDs:    {missing}")
    if mismatch_examples:
        print(f"\n  Agency mismatch examples (up to {EXAMPLE_LIMIT}):")
        for ex in mismatch_examples:
            print(f"    {ex}")

    return {"match": match, "mismatch": mismatch, "missing": missing,
            "mismatch_examples": mismatch_examples}


# ── CHECK 6 ───────────────────────────────────────────────────────────────────

def check6_duplicates(points: list[Any]) -> dict[str, Any]:
    print(f"\n{SEP}")
    print("CHECK 6 — DUPLICATES / STALE POINTS")
    print(SEP)
    counts: Counter = Counter()
    for p in points:
        pl = getattr(p, "payload", None) or {}
        rid = str(pl.get("candidateRecordId") or "").strip()
        if rid:
            counts[rid] += 1

    total = len(points)
    unique = len(counts)
    duplicates = {rid: c for rid, c in counts.items() if c > 1}
    max_per_candidate = max(counts.values(), default=0)

    print(f"  Total Qdrant points:          {total}")
    print(f"  Unique candidateRecordIds:    {unique}")
    print(f"  Duplicate candidateRecordIds: {len(duplicates)}")
    print(f"  Max points per candidate:     {max_per_candidate}")
    if duplicates:
        print(f"\n  Duplicate examples (up to {EXAMPLE_LIMIT}):")
        for rid, c in list(sorted(duplicates.items(), key=lambda x: -x[1]))[:EXAMPLE_LIMIT]:
            print(f"    {rid}: {c} points")

    return {"total": total, "unique": unique, "duplicate_count": len(duplicates),
            "max_per_candidate": max_per_candidate, "duplicates": duplicates}


# ── CHECK 7 ───────────────────────────────────────────────────────────────────

def check7_matching_config() -> None:
    print(f"\n{SEP}")
    print("CHECK 7 — CURRENT MATCHING CONFIGURATION")
    print(SEP)
    print(f"  INTERNAL_CANDIDATE_COLLECTION_NAME:  {INTERNAL_CANDIDATE_COLLECTION_NAME}")
    print(f"  EMBEDDING_VERSION:                   {EMBEDDING_VERSION}")
    print(f"  INTERNAL_CANDIDATE_RETRIEVAL_TOP_K:  {INTERNAL_CANDIDATE_RETRIEVAL_TOP_K}")
    print(f"  INTERNAL_CANDIDATE_MATCH_THRESHOLD:  {INTERNAL_CANDIDATE_MATCH_THRESHOLD}")
    print(f"  INTERNAL_CANDIDATE_MATCH_LIMIT:      {INTERNAL_CANDIDATE_MATCH_LIMIT}")
    print(f"  INTERNAL_CANDIDATE_MIN_MATCHES:      {INTERNAL_CANDIDATE_MIN_MATCHES}")
    print()
    print("  Qdrant search uses metadata_filters={'embeddingVersion': EMBEDDING_VERSION}: YES")
    print("  allow_unfiltered_fallback in match_internal_candidates_for_job:             FALSE")
    print("  (allow_unfiltered_fallback=False is hardcoded in internal_candidate_semantic_service.py)")


# ── FINAL REPORT ──────────────────────────────────────────────────────────────

def final_report(c1: dict, c2: dict, c3: dict, c4: dict, c5: dict, c6: dict) -> None:
    print(f"\n{SEP}")
    print("CHECK 9 — FINAL DIAGNOSTIC REPORT")
    print(SEP)

    print("\nA. QDRANT HEALTH")
    print(f"   Collection:      {c1['collection']}")
    print(f"   Status:          {c1['status']}")
    print(f"   Points:          {c1['points_count']}")
    print(f"   Vector dimension:{c1['vector_size']}")
    print(f"   Distance:        {c1['distance']}")

    print("\nB. QDRANT PAYLOAD HEALTH")
    print(f"   Unique candidateRecordIds: {c6['unique']}")
    print(f"   Missing candidateRecordId: {c2['missing_record_id']}")
    print(f"   Missing embeddingVersion:  {c2['missing_version']}")
    v2_count = c2['version_dist'].get(EMBEDDING_VERSION, 0)
    print(f"   {EMBEDDING_VERSION} count:  {v2_count}")
    internal_count = c2['source_dist'].get('internal', 0)
    print(f"   Internal source count:     {internal_count}")
    resume_count = c1['points_count'] - c2['wrong_content_type']
    print(f"   Resume content count:      {resume_count}")

    print("\nC. DATABASE ↔ QDRANT MATCH")
    print(f"   PostgreSQL candidates:    {c3['pg_count']}")
    print(f"   Qdrant unique candidates: {c3['qdrant_unique']}")
    print(f"   Present in both:          {c3['in_both']}")
    print(f"   Qdrant-only:              {c3['qdrant_only']}")
    print(f"   PostgreSQL-only:          {c3['pg_only']}")

    print("\nD. EMBEDDING CONSISTENCY")
    print(f"   PostgreSQL EMBEDDED:       {c4['embedded']}")
    print(f"   Correct embedding version: {c4['version_match']}")
    print(f"   Version mismatches:        {c4['version_mismatch']}")
    print(f"   Wrong embedding status:    {c4['not_embedded']}")

    print("\nE. AGENCY CONSISTENCY")
    print(f"   Matching agency IDs:   {c5['match']}")
    print(f"   Mismatched agency IDs: {c5['mismatch']}")

    print("\nF. DUPLICATES")
    print(f"   Duplicate candidateRecordIds: {c6['duplicate_count']}")
    print(f"   Maximum points per candidate: {c6['max_per_candidate']}")

    print("\nG. CURRENT MATCHING CONFIG")
    print(f"   INTERNAL_CANDIDATE_COLLECTION_NAME: {INTERNAL_CANDIDATE_COLLECTION_NAME}")
    print(f"   EMBEDDING_VERSION:                  {EMBEDDING_VERSION}")
    print(f"   INTERNAL_CANDIDATE_RETRIEVAL_TOP_K: {INTERNAL_CANDIDATE_RETRIEVAL_TOP_K}")
    print(f"   INTERNAL_CANDIDATE_MATCH_THRESHOLD: {INTERNAL_CANDIDATE_MATCH_THRESHOLD}")
    print(f"   INTERNAL_CANDIDATE_MATCH_LIMIT:     {INTERNAL_CANDIDATE_MATCH_LIMIT}")
    print(f"   INTERNAL_CANDIDATE_MIN_MATCHES:     {INTERNAL_CANDIDATE_MIN_MATCHES}")
    print(f"   metadata_filters embeddingVersion:  YES (hardcoded in semantic service)")
    print(f"   allow_unfiltered_fallback:          FALSE (hardcoded in semantic service)")

    # ── VERDICT ──────────────────────────────────────────────────────────────
    print("\nH. VERDICT")
    issues: list[str] = []
    if c3['pg_only'] > 0:
        issues.append(f"QDRANT INDEX HAS MISSING CANDIDATES ({c3['pg_only']} PG candidates have no Qdrant point)")
    if c3['qdrant_only'] > 0:
        issues.append(f"QDRANT ↔ POSTGRES ID MISMATCH ({c3['qdrant_only']} Qdrant points have no PG row)")
    if c4['version_mismatch'] > 0:
        issues.append(f"EMBEDDING VERSION MISMATCH ({c4['version_mismatch']} points)")
    if c4['not_embedded'] > 0:
        issues.append(f"CANDIDATE EMBEDDING STATUS PROBLEM ({c4['not_embedded']} not EMBEDDED in PG)")
    if c5['mismatch'] > 0:
        issues.append(f"AGENCY MAPPING PROBLEM ({c5['mismatch']} mismatches)")
    if c6['duplicate_count'] > 0:
        issues.append(f"DUPLICATE/STALE QDRANT DATA ({c6['duplicate_count']} duplicate candidateRecordIds)")

    if not issues:
        verdict = "CANDIDATE INDEX IS HEALTHY"
    elif len(issues) == 1:
        verdict = issues[0]
    else:
        verdict = "MULTIPLE ISSUES FOUND"

    print(f"   {verdict}")
    if len(issues) > 1:
        for issue in issues:
            print(f"     - {issue}")

    # ── NEXT STEP ─────────────────────────────────────────────────────────────
    print("\nI. NEXT DEBUGGING STEP")
    if not issues:
        print("   The index is healthy. Investigate the matching pipeline itself:")
        print("   - Check if job text is being generated correctly (build_job_text).")
        print("   - Check if the embedding service is returning valid vectors.")
        print("   - Check if agency_id filtering is restricting results unexpectedly.")
        print("   - Check if INTERNAL_CANDIDATE_MATCH_THRESHOLD is too high for your data.")
    else:
        if c3['pg_only'] > 0:
            print(f"   {c3['pg_only']} PostgreSQL candidates are NOT indexed in Qdrant.")
            print("   Run: python -m scripts.backfill_internal_candidate_embeddings")
            print("   to re-index missing candidates.")
        if c3['qdrant_only'] > 0:
            print(f"   {c3['qdrant_only']} Qdrant points reference IDs that don't exist in PostgreSQL.")
            print("   These are stale/orphan vectors. Investigate whether candidates were deleted from PG.")
        if c4['version_mismatch'] > 0:
            print(f"   {c4['version_mismatch']} Qdrant points have a different embeddingVersion than PG.")
            print(f"   Expected: {EMBEDDING_VERSION}. Re-index affected candidates.")
        if c5['mismatch'] > 0:
            print("   Agency ID mismatches will cause agency-scoped queries to miss candidates.")
            print("   Re-index affected candidates to fix agencyId in Qdrant payload.")
        if c6['duplicate_count'] > 0:
            print("   Duplicate Qdrant points exist. The upsert uses a stable hash-based point ID,")
            print("   so duplicates should not occur unless the hash seed changed. Investigate.")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(SEP)
    print("INTERNAL CANDIDATE INDEX DIAGNOSTIC")
    print(f"Collection: {INTERNAL_CANDIDATE_COLLECTION_NAME}")
    print(f"Embedding version: {EMBEDDING_VERSION}")
    print(SEP)

    # Connect to Qdrant
    print("\nConnecting to Qdrant...")
    client = _qdrant_client()
    print("  OK")

    # CHECK 1
    c1 = check1_collection_health(client)

    # Scroll all points
    print(f"\nScrolling all points from {INTERNAL_CANDIDATE_COLLECTION_NAME}...")
    points = _scroll_all(client, INTERNAL_CANDIDATE_COLLECTION_NAME)
    print(f"  Scrolled {len(points)} points total")

    # CHECK 2
    c2 = check2_payload_health(points)

    # Load PostgreSQL data
    print(f"\nLoading PostgreSQL candidates...")
    db = SessionLocal()
    try:
        rows = db.scalars(select(CandidateProfileEntity)).all()
        pg_ids: set[str] = {str(row.id) for row in rows}
        pg_rows: dict[str, CandidateProfileEntity] = {str(row.id): row for row in rows}
        print(f"  Loaded {len(pg_ids)} candidates from PostgreSQL")
    finally:
        db.close()

    # CHECK 3
    c3 = check3_id_matching(points, pg_ids)

    # CHECK 4
    c4 = check4_embedding_consistency(points, pg_rows)

    # CHECK 5
    c5 = check5_agency_consistency(points, pg_rows)

    # CHECK 6
    c6 = check6_duplicates(points)

    # CHECK 7
    check7_matching_config()

    # FINAL REPORT
    final_report(c1, c2, c3, c4, c5, c6)

    print(f"\n{SEP}")
    print("DIAGNOSTIC COMPLETE — this file is safe to delete after investigation.")
    print(SEP)


if __name__ == "__main__":
    main()
