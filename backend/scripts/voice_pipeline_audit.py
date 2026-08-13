"""
Find most recent job with a real voice transcript, then run full pipeline audit.
Read-only. No code changes.
"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import create_engine, text
from app.db.database_url import normalize_database_url
from app.services.job_text_service import build_job_text, _extract_voice_transcript

SEP = "=" * 80
def section(t): print(f"\n{SEP}\n  {t}\n{SEP}")

engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]), pool_pre_ping=True)

with engine.connect() as db:

    # ── STEP 0: Find the right job ────────────────────────────────────────────
    section("STEP 0: FINDING MOST RECENT JOB WITH REAL VOICE TRANSCRIPT")

    # Strategy 1: job_intakes with transcript length > 500
    candidates = db.execute(text("""
        SELECT ji.job_id, ji.transcript, jd.title, jd.agency_id, jd.updated_at
        FROM job_intakes ji
        JOIN job_descriptions jd ON jd.id = ji.job_id
        WHERE length(ji.transcript) > 500
        ORDER BY jd.updated_at DESC
        LIMIT 10
    """)).mappings().all()

    print(f"\n  Strategy 1 — job_intakes.transcript > 500 chars: {len(candidates)} found")
    for r in candidates:
        print(f"    job_id={r['job_id']}  title={r['title']!r}  transcript_len={len(r['transcript'] or '')}")

    # Strategy 2: structured_data voiceTranscript at top level
    sd_candidates = db.execute(text("""
        SELECT id, title, agency_id, updated_at,
               structured_data->>'voiceTranscript' AS vt
        FROM job_descriptions
        WHERE structured_data->>'voiceTranscript' IS NOT NULL
          AND length(structured_data->>'voiceTranscript') > 500
        ORDER BY updated_at DESC
        LIMIT 10
    """)).mappings().all()

    print(f"\n  Strategy 2 — structured_data.voiceTranscript > 500 chars: {len(sd_candidates)} found")
    for r in sd_candidates:
        print(f"    job_id={r['id']}  title={r['title']!r}  vt_len={len(r['vt'] or '')}")

    # Strategy 3: recruiterIntelligence.voiceTranscript
    ri_candidates = db.execute(text("""
        SELECT id, title, agency_id, updated_at,
               structured_data->'recruiterIntelligence'->>'voiceTranscript' AS ri_vt
        FROM job_descriptions
        WHERE structured_data->'recruiterIntelligence'->>'voiceTranscript' IS NOT NULL
          AND length(structured_data->'recruiterIntelligence'->>'voiceTranscript') > 500
        ORDER BY updated_at DESC
        LIMIT 10
    """)).mappings().all()

    print(f"\n  Strategy 3 — recruiterIntelligence.voiceTranscript > 500 chars: {len(ri_candidates)} found")
    for r in ri_candidates:
        print(f"    job_id={r['id']}  title={r['title']!r}  ri_vt_len={len(r['ri_vt'] or '')}")

    # Strategy 4: recruiterCalibration.state.voice_transcript
    rc_candidates = db.execute(text("""
        SELECT id, title, agency_id, updated_at,
               structured_data->'recruiterCalibration'->'state'->>'voice_transcript' AS rc_vt
        FROM job_descriptions
        WHERE structured_data->'recruiterCalibration'->'state'->>'voice_transcript' IS NOT NULL
          AND length(structured_data->'recruiterCalibration'->'state'->>'voice_transcript') > 500
        ORDER BY updated_at DESC
        LIMIT 10
    """)).mappings().all()

    print(f"\n  Strategy 4 — recruiterCalibration.state.voice_transcript > 500 chars: {len(rc_candidates)} found")
    for r in rc_candidates:
        print(f"    job_id={r['id']}  title={r['title']!r}  rc_vt_len={len(r['rc_vt'] or '')}")

    # Strategy 5: voiceExtraction.transcript inside structured_data
    ve_candidates = db.execute(text("""
        SELECT id, title, agency_id, updated_at,
               structured_data->'voiceExtraction'->>'transcript' AS ve_t
        FROM job_descriptions
        WHERE structured_data->'voiceExtraction'->>'transcript' IS NOT NULL
          AND length(structured_data->'voiceExtraction'->>'transcript') > 500
        ORDER BY updated_at DESC
        LIMIT 10
    """)).mappings().all()

    print(f"\n  Strategy 5 — structured_data.voiceExtraction.transcript > 500 chars: {len(ve_candidates)} found")
    for r in ve_candidates:
        print(f"    job_id={r['id']}  title={r['title']!r}  ve_t_len={len(r['ve_t'] or '')}")

    # Pick best candidate — prefer job_intakes first, then others
    JOB_ID = None
    TRANSCRIPT = ""
    TRANSCRIPT_SOURCE = ""

    if candidates:
        best = candidates[0]
        JOB_ID = str(best["job_id"])
        TRANSCRIPT = best["transcript"] or ""
        TRANSCRIPT_SOURCE = "job_intakes.transcript"
    elif sd_candidates:
        best = sd_candidates[0]
        JOB_ID = str(best["id"])
        TRANSCRIPT = best["vt"] or ""
        TRANSCRIPT_SOURCE = "structured_data.voiceTranscript"
    elif ri_candidates:
        best = ri_candidates[0]
        JOB_ID = str(best["id"])
        TRANSCRIPT = best["ri_vt"] or ""
        TRANSCRIPT_SOURCE = "structured_data.recruiterIntelligence.voiceTranscript"
    elif rc_candidates:
        best = rc_candidates[0]
        JOB_ID = str(best["id"])
        TRANSCRIPT = best["rc_vt"] or ""
        TRANSCRIPT_SOURCE = "recruiterCalibration.state.voice_transcript"
    elif ve_candidates:
        best = ve_candidates[0]
        JOB_ID = str(best["id"])
        TRANSCRIPT = best["ve_t"] or ""
        TRANSCRIPT_SOURCE = "structured_data.voiceExtraction.transcript"

    if not JOB_ID:
        print("\n  NO JOB FOUND WITH ANY VOICE TRANSCRIPT > 500 CHARS")
        print("  Falling back: finding most recent job with ANY non-empty voice data...")
        any_voice = db.execute(text("""
            SELECT id, title, agency_id, updated_at,
                   structured_data->>'voiceTranscript' AS vt,
                   structured_data->'recruiterIntelligence'->>'voiceTranscript' AS ri_vt,
                   structured_data->'recruiterIntelligence'->>'voiceSummary' AS ri_vs,
                   structured_data->'recruiterCalibration'->'state'->>'voice_transcript' AS rc_vt
            FROM job_descriptions
            WHERE (
                (structured_data->>'voiceTranscript' IS NOT NULL AND structured_data->>'voiceTranscript' != '')
                OR (structured_data->'recruiterIntelligence'->>'voiceTranscript' IS NOT NULL AND structured_data->'recruiterIntelligence'->>'voiceTranscript' != '')
                OR (structured_data->'recruiterCalibration'->'state'->>'voice_transcript' IS NOT NULL AND structured_data->'recruiterCalibration'->'state'->>'voice_transcript' != '')
            )
            ORDER BY updated_at DESC
            LIMIT 20
        """)).mappings().all()

        print(f"  Found {len(any_voice)} jobs with any voice data:")
        for r in any_voice:
            vt_len  = len(r['vt'] or '')
            ri_len  = len(r['ri_vt'] or '')
            rc_len  = len(r['rc_vt'] or '')
            print(f"    job_id={r['id']}  title={r['title']!r}  vt={vt_len}  ri_vt={ri_len}  rc_vt={rc_len}")

        if any_voice:
            # pick the one with the longest combined transcript
            best = max(any_voice, key=lambda r: len(r['vt'] or '') + len(r['ri_vt'] or '') + len(r['rc_vt'] or ''))
            JOB_ID = str(best["id"])
            TRANSCRIPT = best["vt"] or best["ri_vt"] or best["rc_vt"] or ""
            TRANSCRIPT_SOURCE = "best available"

    if not JOB_ID:
        print("\n  FATAL: No job with any voice transcript found in the database.")
        sys.exit(0)

    print(f"\n  SELECTED JOB_ID: {JOB_ID}")
    print(f"  TRANSCRIPT SOURCE: {TRANSCRIPT_SOURCE}")
    print(f"  TRANSCRIPT LENGTH: {len(TRANSCRIPT)}")

    # ── A) Job identity ───────────────────────────────────────────────────────
    section("A) JOB IDENTITY")
    job_row = db.execute(text("""
        SELECT id, title, agency_id, description, skills_required, responsibilities,
               experience_level, experience_required, location, salary_range,
               remote_policy, job_status, created_at, updated_at, structured_data
        FROM job_descriptions WHERE id = :j
    """), {"j": JOB_ID}).mappings().first()

    if not job_row:
        print(f"  FATAL: job {JOB_ID} not found"); sys.exit(1)

    job_row = dict(job_row)
    raw_sd = job_row.pop("structured_data") or {}
    if isinstance(raw_sd, str):
        try: raw_sd = json.loads(raw_sd)
        except: raw_sd = {}
    sd = dict(raw_sd)

    print(f"  job_id:    {job_row['id']}")
    print(f"  title:     {job_row['title']!r}")
    print(f"  agency_id: {job_row['agency_id']}")
    print(f"  status:    {job_row['job_status']}")
    print(f"  updated:   {job_row['updated_at']}")

    # ── B) Transcript ─────────────────────────────────────────────────────────
    section("B) TRANSCRIPT — length and first 1000 chars")
    print(f"  Source:  {TRANSCRIPT_SOURCE}")
    print(f"  Length:  {len(TRANSCRIPT)} chars")
    print(f"\n--- TRANSCRIPT FIRST 1000 CHARS ---")
    print(TRANSCRIPT[:1000])
    print("--- END ---")

    # ── C) Structured job requirements ───────────────────────────────────────
    section("C) STRUCTURED JOB REQUIREMENTS")
    print(f"  skills_required:    {job_row['skills_required']!r}")
    print(f"  responsibilities:   {job_row['responsibilities']!r}")
    print(f"  experience_level:   {job_row['experience_level']!r}")
    print(f"  experience_required:{job_row['experience_required']!r}")

    # ── D) All transcript-related fields ─────────────────────────────────────
    section("D) ALL TRANSCRIPT-RELATED FIELDS")

    # D1: job_intakes
    print("\n  -- D1: job_intakes row --")
    intake = db.execute(text("""
        SELECT id, job_id, transcript, structured_data_json, intake_status, completed_at, updated_at
        FROM job_intakes WHERE job_id = :j
    """), {"j": JOB_ID}).mappings().first()

    if intake:
        intake = dict(intake)
        t = intake.get("transcript") or ""
        sdj = intake.get("structured_data_json") or {}
        print(f"  id:             {intake['id']}")
        print(f"  intake_status:  {intake['intake_status']}")
        print(f"  completed_at:   {intake['completed_at']}")
        print(f"  transcript len: {len(t)}")
        print(f"  transcript[:200]: {t[:200]!r}")
        print(f"  structured_data_json keys: {list(sdj.keys())[:10] if isinstance(sdj, dict) else type(sdj)}")
    else:
        print("  NO ROW in job_intakes for this job_id")

    # D2: structured_data top-level voice keys
    print("\n  -- D2: structured_data top-level voice keys --")
    voice_top_keys = [
        "voiceTranscript","voice_transcript","voiceTranscriptRaw","voiceTranscriptClean",
        "voiceExtraction","voice_extraction","voiceSummary","voice_summary",
        "transcript","asyncQuestions","async_questions",
    ]
    for k in voice_top_keys:
        v = sd.get(k)
        if v is None:
            print(f"  {k}: ABSENT")
        elif isinstance(v, str):
            print(f"  {k}: str len={len(v)}  first 200: {v[:200]!r}")
        elif isinstance(v, dict):
            print(f"  {k}: dict keys={list(v.keys())[:8]}")
            # print inner transcript fields
            for ik in ("transcript","rawTranscript","cleanedTranscript","notes","summary"):
                iv = v.get(ik)
                if iv:
                    print(f"    .{ik}: len={len(iv)}  first 200: {iv[:200]!r}")
        elif isinstance(v, list):
            print(f"  {k}: list len={len(v)}")

    # D3: recruiterIntelligence voice fields
    print("\n  -- D3: recruiterIntelligence voice fields --")
    ri = sd.get("recruiterIntelligence") or {}
    ri_voice_keys = ["transcript","voiceTranscript","voiceSummary","voice_summary",
                     "voiceTranscriptRaw","voiceTranscriptClean"]
    for k in ri_voice_keys:
        v = ri.get(k)
        if v is None:
            print(f"  recruiterIntelligence.{k}: ABSENT")
        elif isinstance(v, str):
            print(f"  recruiterIntelligence.{k}: len={len(v)}  first 200: {v[:200]!r}")

    # D4: recruiterCalibration.state.voice_transcript
    print("\n  -- D4: recruiterCalibration.state.voice_transcript --")
    rc = sd.get("recruiterCalibration") or {}
    rc_state = rc.get("state") or {}
    rc_vt = rc_state.get("voice_transcript") or ""
    print(f"  recruiterCalibration.state.voice_transcript: len={len(rc_vt)}  first 200: {rc_vt[:200]!r}")

    # ── E) Exact build_job_text() output ──────────────────────────────────────
    section("E) EXACT build_job_text(job) OUTPUT — MATCHER CODE PATH")

    class FakeJob:
        pass
    fj = FakeJob()
    fj.id                 = str(job_row["id"])
    fj.title              = str(job_row.get("title") or "")
    fj.description        = str(job_row.get("description") or "")
    fj.skills_required    = job_row.get("skills_required") or []
    fj.responsibilities   = job_row.get("responsibilities") or []
    fj.experience_level   = str(job_row.get("experience_level") or "")
    fj.location           = str(job_row.get("location") or "")
    fj.compensation       = str(job_row.get("salary_range") or "")
    fj.work_authorization = ""
    fj.remote_policy      = str(job_row.get("remote_policy") or "")
    fj.experience_required= str(job_row.get("experience_required") or "")
    fj.structured_data    = sd
    fj.company            = None

    # This is the EXACT call in match_internal_candidates_for_job()
    matcher_text = build_job_text(fj)

    print(f"\n  TOTAL LENGTH: {len(matcher_text)} chars")
    print(f"\n--- FULL EMBEDDING TEXT START ---")
    print(matcher_text)
    print("--- FULL EMBEDDING TEXT END ---")

    # ── F) Evidence checks ────────────────────────────────────────────────────
    section("F) EVIDENCE CHECKS — what is and is not in the embedding text")

    mt_lower = matcher_text.lower()

    def check(label, candidates_list):
        """Check if any of the candidate strings appear in matcher_text."""
        for c in candidates_list:
            if not c or not str(c).strip():
                continue
            snippet = str(c).strip()[:60]
            if snippet.lower() in mt_lower:
                return True, snippet
        return False, None

    # F1: transcript text
    transcript_samples = [TRANSCRIPT[:50], TRANSCRIPT[50:100], TRANSCRIPT[100:150]]
    f1, f1_snip = check("transcript", transcript_samples)
    print(f"\n  F1. Transcript text in embedding:              {f1}")
    if f1: print(f"      matched snippet: {f1_snip!r}")
    else:  print(f"      transcript first 50: {TRANSCRIPT[:50]!r}")

    # F2: recruiterCalibration data (any archetype name/skill)
    arch_skills_all = []
    arch_roles_all  = []
    selected_archs  = rc.get("selectedArchetypes") or []
    for arch in selected_archs:
        if isinstance(arch, dict):
            arch_roles_all.append(arch.get("role") or arch.get("current_role") or arch.get("name") or "")
            skills = arch.get("skills") or arch.get("top_skills") or []
            arch_skills_all.extend(skills)

    f2, f2_snip = check("calibration archetype skills/roles", arch_skills_all + arch_roles_all)
    print(f"\n  F2. recruiterCalibration data in embedding:    {f2}")
    if f2: print(f"      matched snippet: {f2_snip!r}")
    else:  print(f"      archetype roles: {arch_roles_all[:3]}")
    print(f"      archetype skills (all): {arch_skills_all[:6]}")

    # F3: selected archetype skills specifically
    f3, f3_snip = check("archetype skills", arch_skills_all)
    print(f"\n  F3. Selected archetype skills in embedding:    {f3}")
    if f3: print(f"      matched snippet: {f3_snip!r}")
    for s in arch_skills_all[:5]:
        found = str(s).lower() in mt_lower if s else False
        print(f"      skill '{s}': {found}")

    # F4: selected archetype roles
    f4, f4_snip = check("archetype roles", arch_roles_all)
    print(f"\n  F4. Selected archetype roles in embedding:     {f4}")
    if f4: print(f"      matched snippet: {f4_snip!r}")
    for r_val in arch_roles_all[:3]:
        found = str(r_val).lower() in mt_lower if r_val else False
        print(f"      role '{r_val}': {found}")

    # F5: intentProfile
    intent = ri.get("intentProfile") or {}
    pref_text = intent.get("preference_text") or ""
    culture = intent.get("culture_preferences") or []
    f5_candidates = [pref_text[:50]] + [str(c) for c in culture]
    f5, f5_snip = check("intentProfile", f5_candidates)
    print(f"\n  F5. recruiterIntelligence.intentProfile in embedding: {f5}")
    if f5: print(f"      matched snippet: {f5_snip!r}")
    else:  print(f"      preference_text first 80: {pref_text[:80]!r}")
    print(f"      culture_preferences: {culture}")

    # F6: skills_required
    skills_req = job_row.get("skills_required") or []
    sd_skills   = sd.get("skills") or sd.get("skills_required") or []
    all_skills  = list(skills_req) + list(sd_skills)
    f6, f6_snip = check("skills_required", all_skills)
    skills_line = next((l for l in matcher_text.splitlines() if l.startswith("Skills:")), "Skills: (line not found)")
    print(f"\n  F6. skills_required in embedding:              {f6}")
    if f6: print(f"      matched snippet: {f6_snip!r}")
    print(f"      Skills line in embedding text: {skills_line!r}")
    print(f"      job.skills_required value: {skills_req!r}")
    print(f"      structured_data skills value: {sd_skills!r}")

    # F7: responsibilities
    resp = job_row.get("responsibilities") or []
    sd_resp = sd.get("responsibilities") or []
    all_resp = (list(resp) if isinstance(resp, list) else [str(resp)]) + (list(sd_resp) if isinstance(sd_resp, list) else [])
    f7, f7_snip = check("responsibilities", [r for r in all_resp if r and str(r).strip() and str(r).strip() != "[]"])
    resp_lines = [l for l in matcher_text.splitlines() if l.startswith("- ") and l.strip() != "- []"]
    print(f"\n  F7. responsibilities in embedding:             {f7}")
    if f7: print(f"      matched snippet: {f7_snip!r}")
    print(f"      Responsibility lines in embedding: {resp_lines[:3]}")
    print(f"      job.responsibilities value: {str(resp)[:120]!r}")

    # ── G) Embedding text length ──────────────────────────────────────────────
    section("G) FINAL EMBEDDING TEXT LENGTH")
    print(f"  Total chars: {len(matcher_text)}")
    print(f"  Total lines: {len(matcher_text.splitlines())}")
    print(f"  'Voice Input:' section: {repr(matcher_text.split('Voice Input:')[-1].strip()[:300]) if 'Voice Input:' in matcher_text else 'SECTION ABSENT'}")

    # ── H) Stored in DB but never used by build_job_text() ───────────────────
    section("H) FIELDS STORED IN DB BUT NEVER READ BY build_job_text()")

    # Keys that build_job_text() actually reads from structured_data
    READ_BY_BUILD = {
        "role","title","skills","skills_required","experience","experience_level",
        "experienceRequired","experience_required","location","compensation","salary_range",
        "workAuthorization","work_authorization","remotePolicy","remote_policy",
        "responsibilities","companyName","company","industry","companyDescription",
        "voice_extraction","transcript","description",
    }

    print("\n  -- structured_data keys NEVER read by build_job_text() --")
    for k in sorted(sd.keys()):
        if k not in READ_BY_BUILD:
            v = sd[k]
            if isinstance(v, str):
                desc = f"str len={len(v)}"
                if len(v) > 0:
                    desc += f"  first 80: {v[:80]!r}"
            elif isinstance(v, dict):
                desc = f"dict  keys={list(v.keys())[:8]}"
            elif isinstance(v, list):
                desc = f"list  len={len(v)}"
            else:
                desc = f"{type(v).__name__} = {v!r}"
            print(f"  UNUSED [{k}]: {desc}")

    print("\n  -- Specific high-value unused fields --")
    # recruiterIntelligence sub-fields
    if ri:
        print(f"\n  recruiterIntelligence (entire object — NEVER read by build_job_text):")
        for k, v in ri.items():
            if k == "recruiter_preference_embedding":
                print(f"    .{k}: list len={len(v) if isinstance(v,list) else 'N/A'}")
            elif isinstance(v, str):
                print(f"    .{k}: str len={len(v)}  ->  {v[:100]!r}")
            elif isinstance(v, dict):
                print(f"    .{k}: dict keys={list(v.keys())[:8]}")
            elif isinstance(v, list):
                print(f"    .{k}: list len={len(v)}")
            else:
                print(f"    .{k}: {type(v).__name__} = {v!r}")

    if rc:
        print(f"\n  recruiterCalibration (entire object — NEVER read by build_job_text):")
        for k, v in rc.items():
            if k in ("selectedArchetypes","archetype_pool","history","rounds","archetype_sets"):
                print(f"    .{k}: list len={len(v) if isinstance(v,list) else 'N/A'}")
            elif isinstance(v, str):
                print(f"    .{k}: str len={len(v)}  ->  {v[:80]!r}")
            elif isinstance(v, dict):
                print(f"    .{k}: dict keys={list(v.keys())[:8]}")
            elif isinstance(v, list):
                print(f"    .{k}: list len={len(v)}")
            else:
                print(f"    .{k}: {type(v).__name__} = {v!r}")

    # Summary
    section("FINAL SUMMARY")
    print(f"  Job ID:                          {JOB_ID}")
    print(f"  Transcript source:               {TRANSCRIPT_SOURCE}")
    print(f"  Transcript length:               {len(TRANSCRIPT)}")
    print(f"  Embedding text length:           {len(matcher_text)} chars")
    print(f"  F1 transcript in embedding:      {f1}")
    print(f"  F2 calibration in embedding:     {f2}")
    print(f"  F3 archetype skills in embedding:{f3}")
    print(f"  F4 archetype roles in embedding: {f4}")
    print(f"  F5 intentProfile in embedding:   {f5}")
    print(f"  F6 skills_required in embedding: {f6}")
    print(f"  F7 responsibilities in embedding:{f7}")
    print(f"  Voice Input section content:     {repr(matcher_text.split('Voice Input:')[-1].strip()[:100]) if 'Voice Input:' in matcher_text else 'ABSENT'}")
