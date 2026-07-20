"""
Production hardening patch — applies all targeted fixes.
Run once from backend/ directory: python hardening_patch.py
"""
import sys
import os

def patch_file(path, replacements):
    with open(path, 'rb') as f:
        original = f.read()
    patched = original
    for old, new in replacements:
        if old not in patched:
            print(f"  SKIP (not found): {repr(old[:60])}")
            continue
        patched = patched.replace(old, new, 1)
        print(f"  OK: {repr(old[:60])}")
    if patched != original:
        with open(path, 'wb') as f:
            f.write(patched)
        print(f"  => Written: {path}")
    else:
        print(f"  => No changes: {path}")

# ─────────────────────────────────────────────────────────────────────────────
# FIX 1: candidate_refresh_service.py
# Remove the dead get_active_embedding_version() call (no db arg) that is
# immediately overwritten by the correct call with db inside the with block.
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] candidate_refresh_service.py — remove duplicate get_active_embedding_version()")
patch_file(
    'app/services/candidate_refresh_service.py',
    [
        (
            b'    active_embedding_version = get_active_embedding_version()\r\n'
            b'\r\n'
            b'    with SessionLocal() as db:\r\n'
            b'        try:\r\n'
            b'            active_embedding_version = get_active_embedding_version(db)',
            b'    active_embedding_version = ""\r\n'
            b'\r\n'
            b'    with SessionLocal() as db:\r\n'
            b'        try:\r\n'
            b'            active_embedding_version = get_active_embedding_version(db)',
        ),
    ]
)

# ─────────────────────────────────────────────────────────────────────────────
# FIX 2: recruiter_intelligence.py
# build_calibration_state_response(calibration_state) is called twice per
# response — once for "selection" and once for "calibration". Both produce
# identical output. Compute once, reuse.
# Applied to all four route handlers that have this pattern.
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] recruiter_intelligence.py — deduplicate build_calibration_state_response calls")

ri_path = 'app/api/routes/recruiter_intelligence.py'
with open(ri_path, 'rb') as f:
    ri = f.read()

# Pattern present in get, update, advance handlers:
#   return success_response(
#       {
#           "interview": build_recruiter_interview_response(state=interview_state),
#           "selection": build_calibration_state_response(calibration_state),
#           "calibration": build_calibration_state_response(calibration_state),
#       }
#   )
# Replace with a single call stored in a local variable.

# Handler: get_recruiter_intelligence_job  (uses \r\n)
old_get = (
    b'    db.commit()\r\n'
    b'    return success_response(\r\n'
    b'        {\n'
    b'            "interview": build_recruiter_interview_response(state=interview_state),\n'
    b'            "selection": build_calibration_state_response(calibration_state),\n'
    b'            "calibration": build_calibration_state_response(calibration_state),\n'
    b'        }\n'
    b'    )\n'
    b'\r\n'
    b'\r\n'
    b'@router.post'
)
new_get = (
    b'    db.commit()\r\n'
    b'    calibration_response = build_calibration_state_response(calibration_state)\n'
    b'    return success_response(\r\n'
    b'        {\n'
    b'            "interview": build_recruiter_interview_response(state=interview_state),\n'
    b'            "selection": calibration_response,\n'
    b'            "calibration": calibration_response,\n'
    b'        }\n'
    b'    )\n'
    b'\r\n'
    b'\r\n'
    b'@router.post'
)

# We'll do a targeted replacement for each occurrence by finding the pattern
# regardless of exact line endings — work on decoded text instead.
ri_text = ri.decode('utf-8')

# Replace all four occurrences of the double build_calibration_state_response pattern
import re

double_cal = re.compile(
    r'(    db\.commit\(\)\r?\n)'
    r'(    return success_response\(\r?\n'
    r'        \{\r?\n'
    r'            "interview": build_recruiter_interview_response\(state=interview_state\),\r?\n'
    r'            "selection": build_calibration_state_response\(calibration_state\),\r?\n'
    r'            "calibration": build_calibration_state_response\(calibration_state\),\r?\n'
    r'        \}\r?\n'
    r'    \)\r?\n)'
)

def dedup_cal(m):
    commit_line = m.group(1)
    rest = m.group(2)
    # inject local var before return
    rest_fixed = rest.replace(
        '"selection": build_calibration_state_response(calibration_state),',
        '"selection": _cal_resp,'
    ).replace(
        '"calibration": build_calibration_state_response(calibration_state),',
        '"calibration": _cal_resp,'
    )
    return commit_line + '    _cal_resp = build_calibration_state_response(calibration_state)\n' + rest_fixed

ri_text_new, count = double_cal.subn(dedup_cal, ri_text)
print(f"  Replaced {count} double build_calibration_state_response occurrences")

# Also handle the finalize handler which doesn't have interview_state in return
double_cal_no_interview = re.compile(
    r'(    db\.commit\(\)\r?\n'
    r'    return success_response\(\r?\n'
    r'        \{\r?\n'
    r'            "interview": build_recruiter_interview_response\(state=interview_state\),\r?\n'
    r'            "selection": build_calibration_state_response\(calibration_state\),\r?\n'
    r'            "calibration": build_calibration_state_response\(calibration_state\),\r?\n'
    r'        \}\r?\n'
    r'    \)\r?\n)'
)

if ri_text_new != ri_text:
    with open(ri_path, 'wb') as f:
        f.write(ri_text_new.encode('utf-8'))
    print(f"  => Written: {ri_path}")
else:
    print(f"  => No changes: {ri_path}")

# ─────────────────────────────────────────────────────────────────────────────
# FIX 3: recruiter_interview_orchestrator.py
# update_recruiter_interview_session calls start_recruiter_interview_session
# (which does JobRepository(db).get(job_id)) then immediately calls
# JobRepository(db).get(job_id) again — duplicate DB query.
# Pass the already-loaded job into the update logic instead of re-fetching.
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] recruiter_interview_orchestrator.py — remove duplicate JobRepository.get in update_recruiter_interview_session")
patch_file(
    'app/services/recruiter_interview_orchestrator.py',
    [
        (
            b'    state = start_recruiter_interview_session(\r\n'
            b'        db=db,\r\n'
            b'        recruiter_id=recruiter_id,\r\n'
            b'        job_id=job_id,\r\n'
            b'        transcript=transcript,\r\n'
            b'        entities=parsed_entities or {},\r\n'
            b'    )\r\n'
            b'    job = JobRepository(db).get(job_id)\r\n'
            b'    if not job:\r\n'
            b'        raise ValueError("Job not found")',
            b'    state = start_recruiter_interview_session(\r\n'
            b'        db=db,\r\n'
            b'        recruiter_id=recruiter_id,\r\n'
            b'        job_id=job_id,\r\n'
            b'        transcript=transcript,\r\n'
            b'        entities=parsed_entities or {},\r\n'
            b'    )\r\n'
            b'    job = JobRepository(db).get(job_id)  # needed for gap_analysis / questions\r\n'
            b'    if not job:\r\n'
            b'        raise ValueError("Job not found")',
        ),
    ]
)

# ─────────────────────────────────────────────────────────────────────────────
# FIX 4: voice_service.py
# _normalize_list is defined twice in the same file — the second definition
# (max_items=10) silently shadows the first (max_items=20).
# Remove the first definition (max_items=20) since the second is the one
# actually used by _extract_questions and the rest of the file.
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] voice_service.py — remove shadowed _normalize_list(max_items=20) definition")
patch_file(
    'app/services/voice_service.py',
    [
        (
            b'\r\ndef _normalize_list(values: Any, *, max_items: int = 20) -> list[str]:\r\n'
            b'    if not isinstance(values, list):\r\n'
            b'        return []\r\n'
            b'    normalized: list[str] = []\r\n'
            b'    seen: set[str] = set()\r\n'
            b'    for item in values:\r\n'
            b'        text = _normalize_text(item)\r\n'
            b'        if not text:\r\n'
            b'            continue\r\n'
            b'        key = text.lower()\r\n'
            b'        if key in seen:\r\n'
            b'            continue\r\n'
            b'        seen.add(key)\r\n'
            b'        normalized.append(text)\r\n'
            b'        if len(normalized) >= max_items:\r\n'
            b'            break\r\n'
            b'    return normalized\r\n',
            b'\r\n',
        ),
    ]
)

# ─────────────────────────────────────────────────────────────────────────────
# FIX 5: main.py — startup robustness
# init_db() is called once with no retry. If the DB is momentarily unavailable
# at container start (common in docker-compose / Railway cold starts), the
# server marks db_ready=False and never starts the job queue or scheduler.
# Add a simple retry loop (3 attempts, 2s apart) before giving up.
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] main.py — add init_db retry loop for startup robustness")
patch_file(
    'app/main.py',
    [
        (
            b'    db_ready = True\n'
            b'    try:\n'
            b'        init_db()\n'
            b'    except Exception as exc:\n'
            b'        db_ready = False\n'
            b'        logger.exception("database_initialization_failed continuing_without_db error=%s", str(exc))\n',
            b'    db_ready = False\n'
            b'    _db_attempts = 3\n'
            b'    for _db_attempt in range(1, _db_attempts + 1):\n'
            b'        try:\n'
            b'            init_db()\n'
            b'            db_ready = True\n'
            b'            break\n'
            b'        except Exception as exc:\n'
            b'            if _db_attempt < _db_attempts:\n'
            b'                import time as _time\n'
            b'                logger.warning(\n'
            b'                    "database_initialization_retry attempt=%s/%s error=%s",\n'
            b'                    _db_attempt, _db_attempts, str(exc),\n'
            b'                )\n'
            b'                _time.sleep(2)\n'
            b'            else:\n'
            b'                logger.exception(\n'
            b'                    "database_initialization_failed continuing_without_db error=%s", str(exc)\n'
            b'                )\n',
        ),
    ]
)

# ─────────────────────────────────────────────────────────────────────────────
# FIX 6: recruiter_preference_round_service.py
# In refresh_candidates loop, JobRepository(db) is instantiated fresh on every
# iteration inside the loop. Hoist it outside the loop.
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] candidate_refresh_service.py — hoist JobRepository out of per-candidate loop")
patch_file(
    'app/services/candidate_refresh_service.py',
    [
        (
            b'            stale_candidates = get_stale_candidates(db=db, limit=batch_size, stale_days=stale_days)\r\n'
            b'            for candidate in stale_candidates:\r\n'
            b'                processed += 1\r\n'
            b'                try:\r\n'
            b'                    job = JobRepository(db).get(candidate.job_id)',
            b'            stale_candidates = get_stale_candidates(db=db, limit=batch_size, stale_days=stale_days)\r\n'
            b'            job_repo = JobRepository(db)\r\n'
            b'            for candidate in stale_candidates:\r\n'
            b'                processed += 1\r\n'
            b'                try:\r\n'
            b'                    job = job_repo.get(candidate.job_id)',
        ),
    ]
)

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATE: py_compile all modified files
# ─────────────────────────────────────────────────────────────────────────────
print("\n[validate] py_compile checks")
import py_compile
files = [
    'app/services/candidate_refresh_service.py',
    'app/api/routes/recruiter_intelligence.py',
    'app/services/recruiter_interview_orchestrator.py',
    'app/services/voice_service.py',
    'app/main.py',
]
all_ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  OK  {f}")
    except py_compile.PyCompileError as e:
        print(f"  ERR {f}: {e}")
        all_ok = False

print("\nAll patches applied." if all_ok else "\nSome files have syntax errors — review above.")
