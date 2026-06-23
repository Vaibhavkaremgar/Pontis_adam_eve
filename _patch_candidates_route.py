"""
Patch candidates.py to:
1. Enrich each candidate's serialized dict with recruiterSummary, fitScoreDisplay,
   matchedSkills from build_candidate_view_model — same builder Slack uses.
2. Add noResultsReason + sourcingState to the response envelope when debug=True
   or when the candidate list is empty.
"""
content = open("backend/app/api/routes/candidates.py", encoding="utf-8").read()

old_imports = "from app.utils.responses import success_response"
new_imports = (
    "from app.utils.responses import success_response\n"
    "from app.services.candidate_presentation_service import build_candidate_view_model\n"
    "from app.services.sourcing_diagnostics import resolve_no_results_reason\n"
    "from app.services.serpapi_sourcing_service import serpapi_health_snapshot"
)
assert old_imports in content, "import anchor not found"
content = content.replace(old_imports, new_imports, 1)

old_get = '''\
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    candidates = fetch_ranked_candidates(db=db, job_id=jobId, mode=mode, refresh=refresh, debug=debug)
    payload = [candidate.model_dump(exclude_none=True) for candidate in candidates]
    debug_payload = build_candidate_fetch_debug(
        db=db,
        job_id=jobId,
        mode=mode,
        refresh=refresh,
        request_source="api",
        returned_count=len(payload),
    )
    if debug or not payload:
        return {"success": True, "data": payload, "error": None, "debug": debug_payload}
    return success_response(payload)'''

new_get = '''\
    assert_job_ownership(db=db, job_id=jobId, user_id=_.get("id", ""))
    candidates = fetch_ranked_candidates(db=db, job_id=jobId, mode=mode, refresh=refresh, debug=debug)

    # Build each candidate dict enriched with the shared presentation view model
    # so both UI and Slack derive recruiterSummary / fitScoreDisplay from the same builder.
    payload: list[dict] = []
    for candidate in candidates:
        base = candidate.model_dump(exclude_none=True)
        vm = build_candidate_view_model(candidate)
        base["recruiterSummary"] = vm["recruiter_summary"]
        base["recruiterSummaryLines"] = vm["summary_lines"]
        base["fitScoreDisplay"] = vm["fit_score_display"]
        base["matchedSkills"] = vm["matched_skills"]
        base["linkedinUrl"] = vm["linkedin_url"] or base.get("linkedinUrl", "")
        payload.append(base)

    debug_payload = build_candidate_fetch_debug(
        db=db,
        job_id=jobId,
        mode=mode,
        refresh=refresh,
        request_source="api",
        returned_count=len(payload),
    )

    # Determine no-results reason for UI empty-state messaging
    no_results_reason = ""
    if not payload:
        snap = serpapi_health_snapshot()
        quota_exhausted = snap.get("status") == "down" and "quota" in snap.get("reason", "").lower()
        provider_disabled = snap.get("status") == "down"
        raw_count = int((debug_payload or {}).get("candidateProfileCount") or 0)
        no_results_reason = resolve_no_results_reason(
            quota_exhausted=quota_exhausted,
            serpapi_disabled=provider_disabled,
            raw_count=raw_count,
            deduped_count=raw_count,
            ranked_count=0,
            delivered_count=0,
        )

    sourcing_state = "delivered" if payload else (no_results_reason or "zero_found")

    if debug or not payload:
        return {
            "success": True,
            "data": payload,
            "error": None,
            "debug": debug_payload,
            "sourcingState": sourcing_state,
            "noResultsReason": no_results_reason,
        }
    return success_response(payload)'''

assert old_get in content, f"get_candidates body anchor not found. First 200 chars around candidates:\n{content[content.find('def get_candidates'):content.find('def get_candidates')+600]}"
content = content.replace(old_get, new_get, 1)

open("backend/app/api/routes/candidates.py", "w", encoding="utf-8").write(content)
print("patched ok")
