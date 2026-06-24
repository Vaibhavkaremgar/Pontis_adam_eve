"""Wire Sprint 5 feedback memory into candidate_service.py."""
import sys
path = "app/services/candidate_service.py"

with open(path, "rb") as f:
    data = f.read()

# ── Insertion point: after _post_rerank_count = len(xray_results) ─────────────
# We insert the Sprint 5 feedback memory block right after the sorted+counted line
# and before the Sprint 2 Qdrant persistence block.

old_block = b"            _post_rerank_count = len(xray_results)\r\n\r\n"

new_block = (
    b"            _post_rerank_count = len(xray_results)\r\n"
    b"\r\n"
    b"            # \xe2\x94\x80\xe2\x94\x80 Sprint 5: recruiter feedback memory \xe2\x94\x80\xe2\x94\x80\r\n"
    b"            # Best-effort only \xe2\x80\x94 never blocks delivery.\r\n"
    b"            _feedback_diag = None\r\n"
    b"            try:\r\n"
    b"                from app.services.feedback_memory_service import apply_feedback_memory\r\n"
    b"                _job_company_id = str(getattr(job, 'company_id', '') or '').strip()\r\n"
    b"                xray_results, _feedback_diag = apply_feedback_memory(\r\n"
    b"                    xray_results,\r\n"
    b"                    db=db,\r\n"
    b"                    job_id=job.id,\r\n"
    b"                    company_id=_job_company_id,\r\n"
    b"                )\r\n"
    b"            except Exception as _fb_exc:\r\n"
    b"                logger.warning(\r\n"
    b"                    'feedback_memory_step_failed job_id=%s error=%s',\r\n"
    b"                    job.id, str(_fb_exc),\r\n"
    b"                )\r\n"
    b"\r\n"
)

if old_block in data:
    data = data.replace(old_block, new_block, 1)
    print("Sprint5 insertion: applied")
else:
    print("Sprint5 insertion: NOT FOUND")
    sys.exit(1)

# ── Patch SourcingDiagnostics constructor to pass Sprint 5 fields ─────────────
# Find the existing Sprint 4 recall_latency_ms line and add Sprint 5 fields after it

old_diag = b"                recall_latency_ms=_recall_diag.get(\"recall_latency_ms\", 0.0),\r\n"
new_diag = (
    b"                recall_latency_ms=_recall_diag.get(\"recall_latency_ms\", 0.0),\r\n"
    b"                # Sprint 5: feedback memory stats\r\n"
    b"                feedback_lookup_attempted=bool((_feedback_diag.feedback_lookup_attempted if _feedback_diag else False)),\r\n"
    b"                feedback_lookup_skipped=bool((_feedback_diag.feedback_lookup_skipped if _feedback_diag else False)),\r\n"
    b"                feedback_lookup_skip_reason=str((_feedback_diag.feedback_lookup_skip_reason if _feedback_diag else '')),\r\n"
    b"                candidates_new=int((_feedback_diag.candidates_new if _feedback_diag else 0)),\r\n"
    b"                candidates_seen_before=int((_feedback_diag.candidates_seen_before if _feedback_diag else 0)),\r\n"
    b"                candidates_passed_before=int((_feedback_diag.candidates_passed_before if _feedback_diag else 0)),\r\n"
    b"                candidates_approved_before=int((_feedback_diag.candidates_approved_before if _feedback_diag else 0)),\r\n"
    b"                candidates_shortlisted_before=int((_feedback_diag.candidates_shortlisted_before if _feedback_diag else 0)),\r\n"
    b"                candidates_held_before=int((_feedback_diag.candidates_held_before if _feedback_diag else 0)),\r\n"
    b"                candidates_suppressed_by_feedback=int((_feedback_diag.candidates_suppressed if _feedback_diag else 0)),\r\n"
    b"                candidates_boosted_by_feedback=int((_feedback_diag.candidates_boosted if _feedback_diag else 0)),\r\n"
    b"                feedback_lookup_latency_ms=float((_feedback_diag.feedback_lookup_latency_ms if _feedback_diag else 0.0)),\r\n"
)

if old_diag in data:
    data = data.replace(old_diag, new_diag, 1)
    print("Sprint5 diag fields: applied")
else:
    print("Sprint5 diag fields: NOT FOUND")
    sys.exit(1)

with open(path, "wb") as f:
    f.write(data)
print("Saved. Size:", len(data))

# Verify
with open(path, "rb") as f:
    verify = f.read()
print("verify insert:", b"apply_feedback_memory" in verify)
print("verify diag:", b"feedback_lookup_attempted" in verify)
