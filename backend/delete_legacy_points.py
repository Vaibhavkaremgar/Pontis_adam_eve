"""
Surgical deletion of 58 legacy internal_resume orphan points from Qdrant.

Safety gates (all must pass before any deletion occurs):
  1. Manifest exists and contains exactly 58 IDs with safe_to_delete=true
  2. Every manifest ID resolves in Qdrant with sourceType='internal_resume'
  3. Zero manifest IDs overlap the 221 protected valid point IDs
  4. Zero manifest IDs have sourceType='internal'
  5. Collection pre-count == 279

Deletion method: client.delete() by explicit point ID list only.
No filter-based deletion. No collection drop.

Post-deletion verification:
  - Total points == 221
  - All 221 candidateRecordIds still present
  - Zero sourceType='internal_resume' points remain
  - 3 NULL-agency candidates preserved
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

MANIFEST_PATH         = os.path.join(os.path.dirname(__file__), "legacy_deletion_manifest.json")
EXPECTED_BEFORE       = 279
EXPECTED_AFTER        = 221
EXPECTED_DELETE_COUNT = 58
LEGACY_SOURCE_TYPE    = "internal_resume"
PROTECTED_SOURCE_TYPE = "internal"

def stop(msg: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"STOP — {msg}")
    print(f"{'=' * 70}")
    sys.exit(1)

print("=" * 70)
print("SURGICAL DELETION — internal_candidate_chunks")
print("=" * 70)
print(f"Collection : {INTERNAL_CANDIDATE_COLLECTION_NAME}")
print(f"Manifest   : {MANIFEST_PATH}")
print()

# ── GATE 1: Read and validate manifest ───────────────────────────────────────
if not os.path.exists(MANIFEST_PATH):
    stop("Manifest file not found. Run verify_legacy_points.py first.")

with open(MANIFEST_PATH) as f:
    manifest = json.load(f)

if not manifest.get("safe_to_delete"):
    stop("Manifest safe_to_delete is not true. Aborting.")

if manifest.get("collection") != INTERNAL_CANDIDATE_COLLECTION_NAME:
    stop(f"Manifest collection mismatch: {manifest.get('collection')!r}")

manifest_ids: list[int] = [int(x) for x in manifest["legacy_point_ids"]]

if len(manifest_ids) != EXPECTED_DELETE_COUNT:
    stop(f"Manifest contains {len(manifest_ids)} IDs, expected {EXPECTED_DELETE_COUNT}.")

print(f"[GATE 1 PASS] Manifest valid: {len(manifest_ids)} IDs, safe_to_delete=true")

# ── Connect to Qdrant ─────────────────────────────────────────────────────────
try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointIdsList
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    client.get_collections()
    print("[CONNECT] Qdrant connection: OK")
except Exception as exc:
    stop(f"Qdrant connection failed: {exc}")

# ── GATE 2: Pre-deletion point count ─────────────────────────────────────────
try:
    info = client.get_collection(INTERNAL_CANDIDATE_COLLECTION_NAME)
    before_count = int(getattr(info, "points_count", 0) or 0)
except Exception as exc:
    stop(f"get_collection failed: {exc}")

print(f"[GATE 2] Pre-deletion points_count: {before_count}")
if before_count != EXPECTED_BEFORE:
    stop(f"Pre-deletion count is {before_count}, expected {EXPECTED_BEFORE}. State has changed — aborting.")
print(f"[GATE 2 PASS] Pre-count == {EXPECTED_BEFORE}")

# ── Full scroll: build live state ─────────────────────────────────────────────
print("\nScrolling all points to build live state...")
all_live: dict[str, dict] = {}   # str(id) -> payload
offset = None
while True:
    kw: dict = dict(
        collection_name=INTERNAL_CANDIDATE_COLLECTION_NAME,
        limit=100, with_payload=True, with_vectors=False,
    )
    if offset is not None:
        kw["offset"] = offset
    resp = client.scroll(**kw)
    batch, nxt = resp if isinstance(resp, tuple) else (resp, None)
    batch = list(batch or [])
    if not batch:
        break
    for pt in batch:
        all_live[str(getattr(pt, "id", ""))] = dict(getattr(pt, "payload", None) or {})
    if next is None or nxt is None:
        break
    offset = nxt

print(f"Live points scrolled: {len(all_live)}")
if len(all_live) != EXPECTED_BEFORE:
    stop(f"Scrolled {len(all_live)} points but expected {EXPECTED_BEFORE}.")

# Partition live state
live_legacy_ids  = {pid for pid, pl in all_live.items() if pl.get("sourceType") == LEGACY_SOURCE_TYPE}
live_valid_ids   = {pid for pid, pl in all_live.items() if pl.get("sourceType") != LEGACY_SOURCE_TYPE}
live_internal_ids = {pid for pid, pl in all_live.items() if pl.get("sourceType") == PROTECTED_SOURCE_TYPE}

print(f"Live legacy  (sourceType='{LEGACY_SOURCE_TYPE}') : {len(live_legacy_ids)}")
print(f"Live valid   (all other)                         : {len(live_valid_ids)}")
print(f"Live internal (sourceType='{PROTECTED_SOURCE_TYPE}')     : {len(live_internal_ids)}")

# ── GATE 3: Every manifest ID exists in Qdrant with correct sourceType ────────
manifest_id_strs = {str(i) for i in manifest_ids}
not_in_qdrant    = manifest_id_strs - set(all_live.keys())
wrong_sourcetype = {
    pid for pid in manifest_id_strs
    if pid in all_live and all_live[pid].get("sourceType") != LEGACY_SOURCE_TYPE
}

print(f"\n[GATE 3] Manifest IDs not found in Qdrant   : {len(not_in_qdrant)}")
print(f"[GATE 3] Manifest IDs with wrong sourceType : {len(wrong_sourcetype)}")

if not_in_qdrant:
    stop(f"These manifest IDs are missing from Qdrant: {not_in_qdrant}")
if wrong_sourcetype:
    details = {pid: all_live[pid].get("sourceType") for pid in wrong_sourcetype}
    stop(f"Manifest IDs have wrong sourceType: {details}")
print("[GATE 3 PASS] All 58 manifest IDs exist in Qdrant with sourceType='internal_resume'")

# ── GATE 4: Zero overlap with valid (protected) point IDs ────────────────────
overlap = manifest_id_strs & live_valid_ids
print(f"\n[GATE 4] Overlap with valid point IDs: {len(overlap)}")
if overlap:
    stop(f"Manifest IDs overlap valid points: {overlap}")
print("[GATE 4 PASS] Zero overlap with valid points")

# ── GATE 5: Zero manifest IDs have sourceType='internal' ─────────────────────
internal_in_manifest = manifest_id_strs & live_internal_ids
print(f"\n[GATE 5] Manifest IDs with sourceType='internal': {len(internal_in_manifest)}")
if internal_in_manifest:
    stop(f"Manifest contains protected 'internal' points: {internal_in_manifest}")
print("[GATE 5 PASS] No 'internal' sourceType points in manifest")

# ── GATE 6: Identify and protect the 3 NULL-agency candidates ────────────────
null_agency_valid = {
    pid for pid, pl in all_live.items()
    if pl.get("sourceType") == PROTECTED_SOURCE_TYPE and not pl.get("agencyId")
}
print(f"\n[GATE 6] NULL-agency 'internal' points (must be preserved): {len(null_agency_valid)}")
for pid in null_agency_valid:
    pl = all_live[pid]
    print(f"  id={pid}  candidateRecordId={pl.get('candidateRecordId')}  agencyId={pl.get('agencyId')!r}")

null_agency_in_manifest = manifest_id_strs & null_agency_valid
if null_agency_in_manifest:
    stop(f"Manifest contains NULL-agency protected points: {null_agency_in_manifest}")
print("[GATE 6 PASS] All 3 NULL-agency candidates are outside the manifest")

# ── ALL GATES PASSED — proceed to deletion ────────────────────────────────────
print()
print("=" * 70)
print("ALL 6 SAFETY GATES PASSED — PROCEEDING WITH DELETION")
print("=" * 70)
print(f"Deleting {len(manifest_ids)} points by explicit ID list...")

try:
    client.delete(
        collection_name=INTERNAL_CANDIDATE_COLLECTION_NAME,
        points_selector=PointIdsList(points=manifest_ids),
        wait=True,
    )
    print("client.delete() completed.")
except Exception as exc:
    stop(f"Deletion failed: {exc}")

# ── POST-DELETION VERIFICATION ────────────────────────────────────────────────
print()
print("=" * 70)
print("POST-DELETION VERIFICATION")
print("=" * 70)

# Re-scroll entire collection
post_all: dict[str, dict] = {}
offset = None
while True:
    kw = dict(
        collection_name=INTERNAL_CANDIDATE_COLLECTION_NAME,
        limit=100, with_payload=True, with_vectors=False,
    )
    if offset is not None:
        kw["offset"] = offset
    resp = client.scroll(**kw)
    batch, nxt = resp if isinstance(resp, tuple) else (resp, None)
    batch = list(batch or [])
    if not batch:
        break
    for pt in batch:
        post_all[str(getattr(pt, "id", ""))] = dict(getattr(pt, "payload", None) or {})
    if nxt is None:
        break
    offset = nxt

after_count = len(post_all)

# Classify post-deletion points
post_legacy   = {pid for pid, pl in post_all.items() if pl.get("sourceType") == LEGACY_SOURCE_TYPE}
post_valid    = {pid for pid, pl in post_all.items() if pl.get("sourceType") != LEGACY_SOURCE_TYPE}
post_null_agency = {
    pid for pid, pl in post_all.items()
    if pl.get("sourceType") == PROTECTED_SOURCE_TYPE and not pl.get("agencyId")
}
post_record_ids = {
    str(pl.get("candidateRecordId") or "")
    for pl in post_all.values()
    if pl.get("candidateRecordId")
}

# Check which manifest IDs still exist (should be zero)
manifest_still_present = manifest_id_strs & set(post_all.keys())

# Check which pre-deletion valid IDs are still present
valid_ids_lost = live_valid_ids - set(post_all.keys())

# DB cross-check: all 221 candidateRecordIds still in DB
db_match_count = 0
try:
    from sqlalchemy import create_engine, text
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        if post_record_ids:
            ph = ",".join(f"'{v}'" for v in list(post_record_ids)[:500])
            r = conn.execute(text(
                f"SELECT COUNT(*) FROM candidates WHERE id::text IN ({ph}) AND embedding_status = 'EMBEDDED'"
            )).fetchone()
            db_match_count = int(r[0])
except Exception as exc:
    print(f"[WARN] DB cross-check failed: {exc}")

# ── Results ───────────────────────────────────────────────────────────────────
print(f"After-count (scroll)          : {after_count}")
print(f"Legacy points remaining       : {len(post_legacy)}")
print(f"Valid points remaining        : {len(post_valid)}")
print(f"NULL-agency candidates present: {len(post_null_agency)}")
print(f"Manifest IDs still in Qdrant  : {len(manifest_still_present)}")
print(f"Valid IDs lost                : {len(valid_ids_lost)}")
print(f"DB EMBEDDED match             : {db_match_count} / {len(post_record_ids)}")

# ── Post-deletion assertions ──────────────────────────────────────────────────
print()
print("=" * 70)
print("POST-DELETION ASSERTIONS")
print("=" * 70)

post_ok = True

checks = [
    (after_count == EXPECTED_AFTER,
     f"After-count == {EXPECTED_AFTER}",
     f"After-count is {after_count}, expected {EXPECTED_AFTER}"),
    (len(post_legacy) == 0,
     "Zero legacy (internal_resume) points remain",
     f"{len(post_legacy)} legacy points still present"),
    (len(post_valid) == EXPECTED_AFTER,
     f"All {EXPECTED_AFTER} valid points preserved",
     f"Valid points: {len(post_valid)}, expected {EXPECTED_AFTER}"),
    (len(valid_ids_lost) == 0,
     "Zero valid point IDs lost",
     f"{len(valid_ids_lost)} valid point IDs were deleted"),
    (len(manifest_still_present) == 0,
     "All 58 manifest IDs removed from Qdrant",
     f"{len(manifest_still_present)} manifest IDs still present"),
    (len(post_null_agency) == 3,
     "All 3 NULL-agency candidates preserved",
     f"NULL-agency count is {len(post_null_agency)}, expected 3"),
    (db_match_count == len(post_record_ids),
     f"All {len(post_record_ids)} remaining candidateRecordIds match EMBEDDED DB rows",
     f"DB match: {db_match_count}/{len(post_record_ids)}"),
]

for passed, ok_msg, fail_msg in checks:
    if passed:
        print(f"[PASS] {ok_msg}")
    else:
        print(f"[FAIL] {fail_msg}")
        post_ok = False

# ── Final report ──────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("FINAL REPORT")
print("=" * 70)
print(f"BEFORE POINT COUNT              : {before_count}")
print(f"DELETED COUNT                   : {EXPECTED_DELETE_COUNT}")
print(f"AFTER POINT COUNT               : {after_count}")
print(f"CURRENT CANDIDATES PRESENT      : {len(post_valid)}")
print(f"LEGACY POINTS REMAINING         : {len(post_legacy)}")
print(f"PROTECTED CANDIDATES LOST       : {len(valid_ids_lost)}")
print(f"3 NULL-AGENCY CANDIDATES PRESERVED: {len(post_null_agency) == 3}")
print(f"DELETION SUCCESS                : {'YES' if post_ok else 'NO'}")
print("=" * 70)
