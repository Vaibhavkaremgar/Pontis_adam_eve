"""Sprint 6 wiring patches."""
import sys

# ── Patch 1: apply_feedback in candidate_service.py ──────────────────────────
# Replace the inline schedule_automation_job block with request_enrichment call.
path1 = "app/services/candidate_service.py"
with open(path1, "rb") as f:
    data1 = f.read()

old1 = (
    b"    enrichment_result: dict[str, Any] | None = None\r\n"
    b"    if action == \"accept\":\r\n"
    b"        try:\r\n"
    b"            from app.services.automation_service import schedule_automation_job\r\n"
    b"\r\n"
    b"            enrichment_result = schedule_automation_job(\r\n"
    b"                db=db,\r\n"
    b"                automation_type=\"candidate_enrichment\",\r\n"
    b"                job_id=job_id,\r\n"
    b"                candidate_id=candidate_id,\r\n"
    b"                run_at=datetime.now(timezone.utc),\r\n"
    b"                payload={\r\n"
    b"                    \"feedbackAction\": action,\r\n"
    b"                    \"sourceType\": \"ui\",\r\n"
    b"                },\r\n"
    b"                automation_key=f\"candidate-enrichment:{job_id}:{candidate_id}\",\r\n"
    b"            )\r\n"
    b"        except Exception as exc:\r\n"
    b"            logger.error(\"automation_failed step=%s error=%s\", \"auto_enrichment\", str(exc))\r\n"
)

new1 = (
    b"    enrichment_result: dict[str, Any] | None = None\r\n"
    b"    if action in {\"accept\", \"maybe\", \"not_now\"}:\r\n"
    b"        try:\r\n"
    b"            from app.services.enrichment_orchestration_service import request_enrichment\r\n"
    b"\r\n"
    b"            _enrich_result = request_enrichment(\r\n"
    b"                db=db,\r\n"
    b"                job_id=job_id,\r\n"
    b"                candidate_id=candidate_id,\r\n"
    b"                action=action,\r\n"
    b"                source_type=str(\"slack\" if slack_team_id else \"ui\"),\r\n"
    b"                selection_session_id=\"\",\r\n"
    b"            )\r\n"
    b"            enrichment_result = {\r\n"
    b"                \"triggered\": _enrich_result.triggered,\r\n"
    b"                \"skipped\": _enrich_result.skipped,\r\n"
    b"                \"skip_reason\": _enrich_result.skip_reason,\r\n"
    b"                \"enrichment_state\": _enrich_result.enrichment_state,\r\n"
    b"            }\r\n"
    b"        except Exception as exc:\r\n"
    b"            logger.warning(\"enrichment_orchestration_failed step=apply_feedback error=%s\", str(exc))\r\n"
)

if old1 in data1:
    data1 = data1.replace(old1, new1, 1)
    print("Patch1 applied: apply_feedback now uses request_enrichment")
else:
    print("Patch1 NOT FOUND - searching for fragment...")
    idx = data1.find(b"automation_type=\"candidate_enrichment\"")
    print(f"  automation_type fragment at: {idx}")
    if idx > 0:
        print("  context:", repr(data1[idx-200:idx+100]))
    sys.exit(1)

with open(path1, "wb") as f:
    f.write(data1)
print("candidate_service.py saved, size:", len(data1))

# ── Patch 2: candidate_refresh_service.py — dedup guard before Apify call ────
path2 = "app/services/candidate_refresh_service.py"
with open(path2, "rb") as f:
    data2 = f.read()

old2 = (
    b"            if decision == \"selected\":\r\n"
    b"                if APIFY_TOKEN:\r\n"
    b"                    _refresh_candidate_with_apify_timeout(db, candidate, timeout_seconds=30.0)\r\n"
    b"                else:\r\n"
    b"                    logger.info(\r\n"
    b"                        \"candidate_refresh_enrichment_skipped job_id=%s candidate_id=%s reason=no_apify_token\",\r\n"
    b"                        getattr(candidate, \"job_id\", \"\"),\r\n"
    b"                        getattr(candidate, \"candidate_id\", \"\"),\r\n"
    b"                    )"
)

new2 = (
    b"            if decision == \"selected\":\r\n"
    b"                # Sprint 6: skip enrichment if already recently completed\r\n"
    b"                try:\r\n"
    b"                    from app.services.enrichment_orchestration_service import is_enrichment_needed\r\n"
    b"                    _enrich_needed = is_enrichment_needed(candidate)\r\n"
    b"                except Exception:\r\n"
    b"                    _enrich_needed = True\r\n"
    b"                if _enrich_needed and APIFY_TOKEN:\r\n"
    b"                    _refresh_candidate_with_apify_timeout(db, candidate, timeout_seconds=30.0)\r\n"
    b"                elif not _enrich_needed:\r\n"
    b"                    logger.info(\r\n"
    b"                        \"candidate_refresh_enrichment_skipped job_id=%s candidate_id=%s reason=already_enriched_recently\",\r\n"
    b"                        getattr(candidate, \"job_id\", \"\"),\r\n"
    b"                        getattr(candidate, \"candidate_id\", \"\"),\r\n"
    b"                    )\r\n"
    b"                else:\r\n"
    b"                    logger.info(\r\n"
    b"                        \"candidate_refresh_enrichment_skipped job_id=%s candidate_id=%s reason=no_apify_token\",\r\n"
    b"                        getattr(candidate, \"job_id\", \"\"),\r\n"
    b"                        getattr(candidate, \"candidate_id\", \"\"),\r\n"
    b"                    )"
)

if old2 in data2:
    data2 = data2.replace(old2, new2, 1)
    print("Patch2 applied: candidate_refresh_service now uses is_enrichment_needed dedup guard")
else:
    # Try LF variant
    old2lf = old2.replace(b"\r\n", b"\n")
    new2lf = new2.replace(b"\r\n", b"\n")
    if old2lf in data2:
        data2 = data2.replace(old2lf, new2lf, 1)
        print("Patch2 applied (LF variant)")
    else:
        print("Patch2 NOT FOUND")
        idx = data2.find(b"decision == \"selected\"")
        print("  'decision=selected' at:", idx)
        if idx > 0:
            print("  context:", repr(data2[idx:idx+200]))
        sys.exit(1)

with open(path2, "wb") as f:
    f.write(data2)
print("candidate_refresh_service.py saved, size:", len(data2))

# verify
with open(path1, "rb") as f:
    v1 = f.read()
with open(path2, "rb") as f:
    v2 = f.read()
print("verify patch1:", b"request_enrichment" in v1)
print("verify patch2:", b"is_enrichment_needed" in v2)
