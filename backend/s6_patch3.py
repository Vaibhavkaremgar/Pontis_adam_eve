"""Sprint 6 patch 3: submit_selection_choice uses request_enrichment."""
import sys

path = "app/services/candidate_selection_service.py"
with open(path, "rb") as f:
    data = f.read()

# The current code does enqueue_job("candidate_enrichment", ...) with idempotency key
# We replace that block with request_enrichment which does proper dedup via automation_key
old = (
    b"            queue_result = enqueue_job(\r\n"
    b"                \"candidate_enrichment\",\r\n"
    b"                {\r\n"
    b"                    \"job_id\": job_id,\r\n"
    b"                    \"candidate_id\": candidate_id,\r\n"
    b"                    \"selection_session_id\": session.id,\r\n"
    b"                    \"source_type\": \"review_selection\",\r\n"
    b"                    \"sourceType\": \"review_selection\",\r\n"
    b"                },\r\n"
    b"                idempotency_key=f\"candidate-enrichment:{job_id}:{candidate_id}\",\r\n"
    b"            )"
)

new = (
    b"            from app.services.enrichment_orchestration_service import request_enrichment\r\n"
    b"            _sel_enrich = request_enrichment(\r\n"
    b"                db=db,\r\n"
    b"                job_id=job_id,\r\n"
    b"                candidate_id=candidate_id,\r\n"
    b"                action=\"selected\",\r\n"
    b"                source_type=\"review_selection\",\r\n"
    b"                selection_session_id=session.id,\r\n"
    b"            )\r\n"
    b"            queue_result = {\r\n"
    b"                \"job_id\": _sel_enrich.queue_job_id,\r\n"
    b"                \"queue_type\": \"candidate_enrichment\",\r\n"
    b"                \"triggered\": _sel_enrich.triggered,\r\n"
    b"                \"skipped\": _sel_enrich.skipped,\r\n"
    b"                \"skip_reason\": _sel_enrich.skip_reason,\r\n"
    b"            }"
)

if old in data:
    data = data.replace(old, new, 1)
    print("Patch3 applied: submit_selection_choice uses request_enrichment")
else:
    # try LF
    old_lf = old.replace(b"\r\n", b"\n")
    new_lf = new.replace(b"\r\n", b"\n")
    if old_lf in data:
        data = data.replace(old_lf, new_lf, 1)
        print("Patch3 applied (LF variant)")
    else:
        print("Patch3 NOT FOUND")
        idx = data.find(b"candidate_enrichment")
        print("  candidate_enrichment at:", idx)
        if idx > 0:
            print("  context:", repr(data[max(0,idx-100):idx+200]))
        sys.exit(1)

with open(path, "wb") as f:
    f.write(data)
print("Saved. Size:", len(data))
with open(path, "rb") as f:
    v = f.read()
print("verify:", b"request_enrichment" in v)
