"""
READ-ONLY Qdrant + DB inspection for internal candidate embeddings.
Does NOT write to Qdrant. Does NOT modify the database.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.core.config import (
    QDRANT_URL, QDRANT_API_KEY,
    INTERNAL_CANDIDATE_COLLECTION_NAME,
    EMBEDDING_VERSION, VECTOR_SIZE,
    DATABASE_URL,
)

print("=" * 60)
print("STEP 1 — CONFIGURATION")
print("=" * 60)
print(f"QDRANT_URL              : {QDRANT_URL}")
print(f"INTERNAL_COLLECTION     : {INTERNAL_CANDIDATE_COLLECTION_NAME}")
print(f"EMBEDDING_VERSION       : {EMBEDDING_VERSION}")
print(f"VECTOR_SIZE             : {VECTOR_SIZE}")
print(f"QDRANT_API_KEY set      : {bool(QDRANT_API_KEY)}")
print()

# ── 1. Connect to Qdrant (read-only operations only) ─────────────────────────
print("=" * 60)
print("STEP 2 — QDRANT CONNECTION + COLLECTION INFO")
print("=" * 60)

try:
    from qdrant_client import QdrantClient
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    collections = client.get_collections()
    coll_names = [c.name for c in collections.collections]
    print(f"Connected OK. Collections visible: {coll_names}")
except Exception as e:
    print(f"QDRANT CONNECTION FAILED: {e}")
    sys.exit(1)

# ── 2. Collection info ────────────────────────────────────────────────────────
collection_exists = INTERNAL_CANDIDATE_COLLECTION_NAME in coll_names
print(f"\nCollection '{INTERNAL_CANDIDATE_COLLECTION_NAME}' exists: {collection_exists}")

points_count = 0
vectors_count = 0
collection_status = "NOT_FOUND"

if collection_exists:
    try:
        info = client.get_collection(INTERNAL_CANDIDATE_COLLECTION_NAME)
        points_count  = getattr(info, "points_count",  0) or 0
        vectors_count = getattr(info, "vectors_count", 0) or 0
        collection_status = str(getattr(info, "status", "unknown"))
        print(f"  status        : {collection_status}")
        print(f"  points_count  : {points_count}")
        print(f"  vectors_count : {vectors_count}")
        # config section
        cfg = getattr(info, "config", None)
        if cfg:
            params = getattr(cfg, "params", None)
            if params:
                vp = getattr(params, "vectors", None)
                if vp:
                    print(f"  vector_size   : {getattr(vp, 'size', 'unknown')}")
                    print(f"  distance      : {getattr(vp, 'distance', 'unknown')}")
    except Exception as e:
        print(f"  get_collection failed: {e}")
else:
    print("  Collection does not exist — no points to inspect.")

# ── 3. Scroll up to 10 points ─────────────────────────────────────────────────
print()
print("=" * 60)
print("STEP 3 — SCROLL SAMPLE POINTS (max 10, payload only, no vectors)")
print("=" * 60)

sample_points = []
if collection_exists and points_count > 0:
    try:
        response = client.scroll(
            collection_name=INTERNAL_CANDIDATE_COLLECTION_NAME,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        raw_points = response[0] if isinstance(response, tuple) else response
        sample_points = list(raw_points or [])
        print(f"Returned {len(sample_points)} point(s).")
    except Exception as e:
        print(f"scroll() failed: {e}")
else:
    print("Skipped (collection empty or missing).")

# ── 4. Report each point's payload fields ─────────────────────────────────────
print()
print("=" * 60)
print("STEP 4 — POINT DETAILS")
print("=" * 60)

PAYLOAD_FIELDS = [
    "candidateId", "candidateRecordId", "agencyId",
    "source", "sourceType", "contentType",
    "embeddingVersion", "indexedAt", "resumeFingerprint",
]

candidate_record_ids_in_qdrant = []
agency_ids_in_qdrant = []
missing_agency = []
version_mismatches = []
duplicate_check: dict[str, int] = {}

for pt in sample_points:
    pid = str(getattr(pt, "id", ""))
    payload = getattr(pt, "payload", None) or {}
    print(f"\n  Point ID: {pid}")
    for field in PAYLOAD_FIELDS:
        val = payload.get(field, "<missing>")
        print(f"    {field:<22}: {val}")

    crid = str(payload.get("candidateRecordId") or "")
    aid  = str(payload.get("agencyId") or "")
    ev   = str(payload.get("embeddingVersion") or "")

    if crid:
        candidate_record_ids_in_qdrant.append(crid)
        duplicate_check[crid] = duplicate_check.get(crid, 0) + 1
    if aid:
        agency_ids_in_qdrant.append(aid)
    else:
        missing_agency.append(pid)
    if ev and ev != EMBEDDING_VERSION:
        version_mismatches.append((pid, ev))

# ── 5. Full scroll to get ALL candidateRecordIds (for DB cross-check) ─────────
print()
print("=" * 60)
print("STEP 5 — FULL SCROLL (all points, payload only, no vectors)")
print("=" * 60)

all_record_ids: list[str] = []
all_agency_ids: list[str] = []
all_missing_agency: list[str] = []
all_version_mismatches: list[tuple[str, str]] = []
all_duplicate_check: dict[str, int] = {}

if collection_exists and points_count > 0:
    try:
        offset = None
        batch_size = 100
        total_scrolled = 0
        while True:
            kwargs: dict = dict(
                collection_name=INTERNAL_CANDIDATE_COLLECTION_NAME,
                limit=batch_size,
                with_payload=True,
                with_vectors=False,
            )
            if offset is not None:
                kwargs["offset"] = offset
            resp = client.scroll(**kwargs)
            batch, next_offset = resp if isinstance(resp, tuple) else (resp, None)
            batch = list(batch or [])
            if not batch:
                break
            total_scrolled += len(batch)
            for pt in batch:
                pid  = str(getattr(pt, "id", ""))
                pl   = getattr(pt, "payload", None) or {}
                crid = str(pl.get("candidateRecordId") or "")
                aid  = str(pl.get("agencyId") or "")
                ev   = str(pl.get("embeddingVersion") or "")
                if crid:
                    all_record_ids.append(crid)
                    all_duplicate_check[crid] = all_duplicate_check.get(crid, 0) + 1
                if aid:
                    all_agency_ids.append(aid)
                else:
                    all_missing_agency.append(pid)
                if ev and ev != EMBEDDING_VERSION:
                    all_version_mismatches.append((pid, ev))
            if next_offset is None:
                break
            offset = next_offset
        print(f"Total points scrolled: {total_scrolled}")
    except Exception as e:
        print(f"Full scroll failed: {e}")
        # fall back to sample
        all_record_ids = candidate_record_ids_in_qdrant
        all_agency_ids = agency_ids_in_qdrant
        all_missing_agency = missing_agency
        all_version_mismatches = version_mismatches
        all_duplicate_check = duplicate_check
else:
    print("Skipped.")

unique_record_ids = list(dict.fromkeys(all_record_ids))
unique_agency_ids = list(dict.fromkeys(all_agency_ids))
duplicates = {k: v for k, v in all_duplicate_check.items() if v > 1}

print(f"Unique candidateRecordIds in Qdrant : {len(unique_record_ids)}")
print(f"Unique agencyIds in Qdrant          : {len(unique_agency_ids)}")
print(f"Points missing agencyId             : {len(all_missing_agency)}")
print(f"Points with version mismatch        : {len(all_version_mismatches)}")
print(f"Duplicate candidateRecordIds        : {len(duplicates)}")
if duplicates:
    for k, v in list(duplicates.items())[:5]:
        print(f"  {k}: {v} points")

# ── 6. DB cross-check ─────────────────────────────────────────────────────────
print()
print("=" * 60)
print("STEP 6 — DATABASE CROSS-CHECK")
print("=" * 60)

db_total = 0
db_matched = 0
db_not_indexed = 0
db_agency_ids: list[str] = []

try:
    from sqlalchemy import create_engine, text
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        # total candidates
        row = conn.execute(text("SELECT COUNT(*) FROM candidates")).fetchone()
        db_total = int(row[0]) if row else 0
        print(f"Total candidates rows in DB: {db_total}")

        # candidates with embedding_status = EMBEDDED
        row2 = conn.execute(text(
            "SELECT COUNT(*) FROM candidates WHERE embedding_status = 'EMBEDDED'"
        )).fetchone()
        db_embedded = int(row2[0]) if row2 else 0
        print(f"Candidates with embedding_status=EMBEDDED: {db_embedded}")

        # candidates with current embedding version
        row3 = conn.execute(text(
            "SELECT COUNT(*) FROM candidates WHERE embedding_status = 'EMBEDDED' AND embedding_version = :ver"
        ), {"ver": EMBEDDING_VERSION}).fetchone()
        db_current_version = int(row3[0]) if row3 else 0
        print(f"Candidates with current EMBEDDING_VERSION ({EMBEDDING_VERSION}): {db_current_version}")

        # cross-check: how many Qdrant record IDs exist in DB
        if unique_record_ids:
            placeholders = ",".join(f"'{rid}'" for rid in unique_record_ids[:500])
            row4 = conn.execute(text(
                f"SELECT COUNT(*) FROM candidates WHERE id::text IN ({placeholders})"
            )).fetchone()
            db_matched = int(row4[0]) if row4 else 0
        print(f"Qdrant points matched to DB candidates: {db_matched} / {len(unique_record_ids)}")

        # candidates NOT in Qdrant (no embedding or stale)
        row5 = conn.execute(text(
            "SELECT COUNT(*) FROM candidates WHERE embedding_status IS NULL OR embedding_status != 'EMBEDDED'"
        )).fetchone()
        db_not_indexed = int(row5[0]) if row5 else 0
        print(f"Candidates not yet indexed (no EMBEDDED status): {db_not_indexed}")

        # agency breakdown in DB
        rows6 = conn.execute(text(
            "SELECT agency_id, COUNT(*) as cnt FROM candidates GROUP BY agency_id ORDER BY cnt DESC LIMIT 10"
        )).fetchall()
        print(f"\nDB agency breakdown (top 10):")
        for r in rows6:
            print(f"  agency_id={r[0]}  count={r[1]}")

        # sample of candidates with EMBEDDED status
        rows7 = conn.execute(text(
            "SELECT id, candidate_id, agency_id, embedding_version, embedding_status, embedding_indexed_at "
            "FROM candidates WHERE embedding_status = 'EMBEDDED' LIMIT 5"
        )).fetchall()
        print(f"\nSample EMBEDDED candidates from DB:")
        for r in rows7:
            print(f"  id={r[0]}  cand_id={r[1]}  agency={r[2]}  ver={r[3]}  status={r[4]}  indexed_at={r[5]}")

except Exception as e:
    print(f"DB query failed: {e}")

# ── 7. Final report ───────────────────────────────────────────────────────────
print()
print("=" * 60)
print("PRODUCTION READINESS REPORT")
print("=" * 60)
print(f"QDRANT COLLECTION       : {INTERNAL_CANDIDATE_COLLECTION_NAME}")
print(f"STATUS                  : {collection_status}")
print(f"POINTS                  : {points_count}")
print(f"VECTORS                 : {vectors_count}")
print(f"DB CANDIDATES (total)   : {db_total}")
print(f"DB EMBEDDED             : {db_embedded if 'db_embedded' in dir() else 'N/A'}")
print(f"MATCHED (Qdrant->DB)    : {db_matched} / {len(unique_record_ids)}")
print(f"NOT INDEXED (DB)        : {db_not_indexed}")
print(f"CURRENT EMBEDDING VER   : {EMBEDDING_VERSION}")
print(f"AGENCIES IN QDRANT      : {unique_agency_ids if unique_agency_ids else 'none'}")
print(f"MISSING AGENCY PAYLOAD  : {len(all_missing_agency)} point(s)")
print(f"VERSION MISMATCHES      : {len(all_version_mismatches)} point(s)")
print(f"DUPLICATE RECORD IDs    : {len(duplicates)}")
print()
print("SAMPLE POINTS (first 10 from Qdrant):")
for pt in sample_points:
    pid = str(getattr(pt, "id", ""))
    pl  = getattr(pt, "payload", None) or {}
    print(f"  id={pid}  candidateId={pl.get('candidateId','')}  "
          f"candidateRecordId={pl.get('candidateRecordId','')}  "
          f"agencyId={pl.get('agencyId','')}  "
          f"embeddingVersion={pl.get('embeddingVersion','')}  "
          f"indexedAt={pl.get('indexedAt','')}")

print()
# ── Verdict ───────────────────────────────────────────────────────────────────
ready = (
    collection_exists
    and points_count > 0
    and len(all_missing_agency) == 0
    and len(all_version_mismatches) == 0
    and len(duplicates) == 0
    and db_matched > 0
)
print("=" * 60)
if ready:
    print("Candidate embeddings are ready for E2E testing: YES")
else:
    print("Candidate embeddings are ready for E2E testing: NO")
    reasons = []
    if not collection_exists:
        reasons.append("Collection does not exist")
    if points_count == 0:
        reasons.append("Collection is empty (0 points)")
    if len(all_missing_agency) > 0:
        reasons.append(f"{len(all_missing_agency)} point(s) missing agencyId payload")
    if len(all_version_mismatches) > 0:
        reasons.append(f"{len(all_version_mismatches)} point(s) have stale embeddingVersion")
    if len(duplicates) > 0:
        reasons.append(f"{len(duplicates)} duplicate candidateRecordId(s)")
    if db_matched == 0 and len(unique_record_ids) > 0:
        reasons.append("No Qdrant points map to existing DB candidates")
    for r in reasons:
        print(f"  REASON: {r}")
print("=" * 60)
