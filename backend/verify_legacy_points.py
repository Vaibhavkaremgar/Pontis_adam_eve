"""
READ-ONLY verification of legacy internal_resume points in Qdrant.
Produces a deletion manifest. Does NOT delete, upsert, or modify anything.
"""
from __future__ import annotations
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app.core.config import (
    QDRANT_URL, QDRANT_API_KEY,
    INTERNAL_CANDIDATE_COLLECTION_NAME,
    EMBEDDING_VERSION,
    DATABASE_URL,
)

EXPECTED_LEGACY_COUNT = 58
LEGACY_SOURCE_TYPE    = "internal_resume"

print("=" * 70)
print("LEGACY POINT VERIFICATION  —  READ-ONLY")
print("=" * 70)
print(f"Collection      : {INTERNAL_CANDIDATE_COLLECTION_NAME}")
print(f"Qdrant URL      : {QDRANT_URL}")
print(f"Embedding ver   : {EMBEDDING_VERSION}")
print(f"API key set     : {bool(QDRANT_API_KEY)}")
print()

# ── 1. Connect ────────────────────────────────────────────────────────────────
try:
    from qdrant_client import QdrantClient
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    client.get_collections()
    print("Qdrant connection: OK")
except Exception as exc:
    print(f"QDRANT CONNECTION FAILED: {exc}")
    sys.exit(1)

# ── 2. Collection info ────────────────────────────────────────────────────────
try:
    info = client.get_collection(INTERNAL_CANDIDATE_COLLECTION_NAME)
    reported_points = int(getattr(info, "points_count", 0) or 0)
    print(f"Reported points_count: {reported_points}")
except Exception as exc:
    print(f"get_collection failed: {exc}")
    sys.exit(1)

# ── 3. Full scroll — collect every point ─────────────────────────────────────
print("\nScrolling all points (payload only, no vectors)...")

all_points: list[dict] = []   # {id, payload}
offset = None
while True:
    kwargs: dict = dict(
        collection_name=INTERNAL_CANDIDATE_COLLECTION_NAME,
        limit=100,
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
    for pt in batch:
        all_points.append({
            "id":      str(getattr(pt, "id", "")),
            "payload": dict(getattr(pt, "payload", None) or {}),
        })
    if next_offset is None:
        break
    offset = next_offset

total_scrolled = len(all_points)
print(f"Total points scrolled: {total_scrolled}")

# ── 4. Partition: legacy vs valid ────────────────────────────────────────────
legacy_points: list[dict] = []
valid_points:  list[dict] = []

for pt in all_points:
    st = str(pt["payload"].get("sourceType") or "")
    if st == LEGACY_SOURCE_TYPE:
        legacy_points.append(pt)
    else:
        valid_points.append(pt)

print(f"\nLegacy (sourceType='{LEGACY_SOURCE_TYPE}'): {len(legacy_points)}")
print(f"Valid  (all other sourceType values)     : {len(valid_points)}")

# ── 5. Count-gate ─────────────────────────────────────────────────────────────
print()
if len(legacy_points) != EXPECTED_LEGACY_COUNT:
    print("=" * 70)
    print(f"STOP — UNEXPECTED LEGACY COUNT")
    print(f"  Expected : {EXPECTED_LEGACY_COUNT}")
    print(f"  Actual   : {len(legacy_points)}")
    print("  Do NOT proceed with deletion until this discrepancy is resolved.")
    print("=" * 70)
    sys.exit(2)

print(f"Count check PASSED: exactly {EXPECTED_LEGACY_COUNT} legacy points found.")

# ── 6. Detail every legacy point ─────────────────────────────────────────────
FIELDS = ["candidateId", "candidateRecordId", "agencyId",
          "sourceType", "source", "contentType",
          "indexedAt", "embeddingVersion"]

print()
print("=" * 70)
print("LEGACY POINT DETAILS")
print("=" * 70)

legacy_candidate_ids:        list[str] = []
legacy_candidate_record_ids: list[str] = []
legacy_point_ids:            list[str] = []

for i, pt in enumerate(legacy_points, 1):
    pid = pt["id"]
    pl  = pt["payload"]
    legacy_point_ids.append(pid)
    cid  = str(pl.get("candidateId")       or "")
    crid = str(pl.get("candidateRecordId") or "")
    if cid:
        legacy_candidate_ids.append(cid)
    if crid:
        legacy_candidate_record_ids.append(crid)
    print(f"\n  [{i:02d}] Point ID: {pid}")
    for f in FIELDS:
        print(f"        {f:<22}: {pl.get(f, '<missing>')}")

# ── 7. Collect valid-point identifiers (what must NOT be deleted) ─────────────
valid_record_ids: set[str] = set()
valid_point_ids:  set[str] = set()
for pt in valid_points:
    valid_point_ids.add(pt["id"])
    crid = str(pt["payload"].get("candidateRecordId") or "")
    if crid:
        valid_record_ids.add(crid)

print()
print("=" * 70)
print("VALID POINT PROTECTION CHECK")
print("=" * 70)
print(f"Valid point IDs collected  : {len(valid_point_ids)}")
print(f"Valid candidateRecordIds   : {len(valid_record_ids)}")

# Confirm zero overlap between legacy IDs and valid IDs
overlap_ids = set(legacy_point_ids) & valid_point_ids
print(f"ID overlap (legacy & valid): {len(overlap_ids)}")
if overlap_ids:
    print(f"  OVERLAP DETECTED — STOP: {overlap_ids}")
    sys.exit(3)
else:
    print("  No overlap — legacy point IDs are disjoint from valid point IDs. SAFE.")

# ── 8. DB cross-check ─────────────────────────────────────────────────────────
print()
print("=" * 70)
print("DATABASE CROSS-CHECK")
print("=" * 70)

db_total = db_embedded = 0
legacy_cids_in_db:  list[str] = []
legacy_crids_in_db: list[str] = []

try:
    from sqlalchemy import create_engine, text
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        db_total = int(conn.execute(text("SELECT COUNT(*) FROM candidates")).fetchone()[0])
        db_embedded = int(conn.execute(text(
            "SELECT COUNT(*) FROM candidates WHERE embedding_status = 'EMBEDDED'"
        )).fetchone()[0])
        print(f"Total candidates in DB          : {db_total}")
        print(f"EMBEDDED candidates in DB       : {db_embedded}")

        # Check whether any legacy candidateId matches a DB candidate_id
        if legacy_candidate_ids:
            ph = ",".join(f"'{v}'" for v in legacy_candidate_ids[:500])
            r = conn.execute(text(
                f"SELECT candidate_id FROM candidates WHERE candidate_id IN ({ph})"
            )).fetchall()
            legacy_cids_in_db = [row[0] for row in r]

        # Check whether any legacy candidateRecordId matches a DB id (primary key)
        if legacy_candidate_record_ids:
            ph2 = ",".join(f"'{v}'" for v in legacy_candidate_record_ids[:500])
            r2 = conn.execute(text(
                f"SELECT id::text FROM candidates WHERE id::text IN ({ph2})"
            )).fetchall()
            legacy_crids_in_db = [row[0] for row in r2]

        print(f"\nLegacy candidateIds found in DB : {len(legacy_cids_in_db)}")
        if legacy_cids_in_db:
            for v in legacy_cids_in_db[:10]:
                print(f"  {v}")

        print(f"Legacy candidateRecordIds in DB : {len(legacy_crids_in_db)}")
        if legacy_crids_in_db:
            for v in legacy_crids_in_db[:10]:
                print(f"  {v}")

        # Confirm valid DB candidates are all covered by valid Qdrant points
        if valid_record_ids:
            ph3 = ",".join(f"'{v}'" for v in list(valid_record_ids)[:500])
            r3 = conn.execute(text(
                f"SELECT COUNT(*) FROM candidates WHERE id::text IN ({ph3}) AND embedding_status = 'EMBEDDED'"
            )).fetchone()
            valid_db_match = int(r3[0])
            print(f"\nValid Qdrant points matched to EMBEDDED DB rows: {valid_db_match} / {len(valid_record_ids)}")

except Exception as exc:
    print(f"DB query failed: {exc}")
    sys.exit(4)

# ── 9. Safety assertions ──────────────────────────────────────────────────────
print()
print("=" * 70)
print("SAFETY ASSERTIONS")
print("=" * 70)

safe = True

# A. Legacy count exact
if len(legacy_points) == EXPECTED_LEGACY_COUNT:
    print(f"[PASS] Legacy count == {EXPECTED_LEGACY_COUNT}")
else:
    print(f"[FAIL] Legacy count mismatch: {len(legacy_points)} != {EXPECTED_LEGACY_COUNT}")
    safe = False

# B. No legacy point ID overlaps a valid point ID
if not overlap_ids:
    print("[PASS] No ID overlap between legacy and valid points")
else:
    print(f"[FAIL] ID overlap detected: {overlap_ids}")
    safe = False

# C. Legacy candidateRecordIds do NOT appear in DB as EMBEDDED candidates
if not legacy_crids_in_db:
    print("[PASS] No legacy candidateRecordId maps to a DB candidate row")
else:
    print(f"[WARN] {len(legacy_crids_in_db)} legacy candidateRecordId(s) found in DB — inspect before deleting")
    safe = False

# D. All valid points have candidateRecordIds that ARE in DB as EMBEDDED
if valid_db_match == len(valid_record_ids):
    print(f"[PASS] All {len(valid_record_ids)} valid Qdrant points map to EMBEDDED DB rows")
else:
    print(f"[WARN] Only {valid_db_match}/{len(valid_record_ids)} valid points map to EMBEDDED DB rows")

# E. All legacy points have sourceType == internal_resume (no other type leaked in)
wrong_type = [pt for pt in legacy_points if pt["payload"].get("sourceType") != LEGACY_SOURCE_TYPE]
if not wrong_type:
    print(f"[PASS] All {len(legacy_points)} legacy points have sourceType='{LEGACY_SOURCE_TYPE}'")
else:
    print(f"[FAIL] {len(wrong_type)} legacy points have unexpected sourceType")
    safe = False

# ── 10. Deletion manifest ─────────────────────────────────────────────────────
print()
print("=" * 70)
print("DELETION MANIFEST  (DO NOT EXECUTE YET)")
print("=" * 70)
print(f"Points to delete: {len(legacy_point_ids)}")
print()
print("LEGACY_POINT_IDS = [")
for pid in legacy_point_ids:
    print(f"    {pid},")
print("]")

# Also write manifest to file for safe-keeping
manifest = {
    "collection": INTERNAL_CANDIDATE_COLLECTION_NAME,
    "total_qdrant_points": total_scrolled,
    "valid_points": len(valid_points),
    "legacy_points": len(legacy_points),
    "legacy_source_type": LEGACY_SOURCE_TYPE,
    "legacy_point_ids": legacy_point_ids,
    "safe_to_delete": safe,
}
manifest_path = os.path.join(os.path.dirname(__file__), "legacy_deletion_manifest.json")
with open(manifest_path, "w") as f:
    json.dump(manifest, f, indent=2)
print(f"\nManifest written to: {manifest_path}")

# ── 11. Final verdict ─────────────────────────────────────────────────────────
print()
print("=" * 70)
print("FINAL VERIFICATION REPORT")
print("=" * 70)
print(f"TOTAL QDRANT POINTS     : {total_scrolled}")
print(f"VALID CURRENT POINTS    : {len(valid_points)}")
print(f"LEGACY POINTS           : {len(legacy_points)}")
print(f"LEGACY SOURCE TYPES     : {LEGACY_SOURCE_TYPE}")
print(f"LEGACY POINT IDS        : {len(legacy_point_ids)} (see manifest above)")
print(f"CURRENT CANDIDATE IDS PROTECTED : {len(valid_record_ids)} candidateRecordIds")
print(f"CONFIRMED SAFE TO DELETE: {'YES' if safe else 'NO — see FAIL/WARN above'}")
print("=" * 70)
