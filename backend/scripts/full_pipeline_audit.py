"""
Full pipeline audit: voice intake -> matching for a given job_id.
Read-only. No code changes.
"""
from __future__ import annotations
import json, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import create_engine, text
from app.db.database_url import normalize_database_url
from app.services.job_text_service import build_job_text, _extract_voice_transcript

JOB_ID = "6ac18079-c93d-4951-9d24-7b5fb1c9066e"
SEP = "=" * 80

def section(title):
    print(f"\n{SEP}\n  {title}\n{SEP}")

def pjson(obj):
    print(json.dumps(obj, indent=2, default=str))

engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]), pool_pre_ping=True)

with engine.connect() as db:

    # ── 1. Full job_intakes row ───────────────────────────────────────────────
    section("1. FULL job_intakes ROW")
    intake = db.execute(
        text("SELECT * FROM job_intakes WHERE job_id = :j"), {"j": JOB_ID}
    ).mappings().first()

    if intake:
        intake = dict(intake)
        # print everything except the big json blob first
        for k, v in intake.items():
            if k != "structured_data_json":
                print(f"  {k}: {v!r}")
        print(f"\n  structured_data_json keys: {list((intake.get('structured_data_json') or {}).keys())}")
    else:
        print("  NO ROW IN job_intakes FOR THIS JOB_ID")

    # ── 2. Transcript length + first 3000 chars ───────────────────────────────
    section("2. TRANSCRIPT — length and first 3000 chars")
    transcript_text = ""
    if intake:
        transcript_text = intake.get("transcript") or ""
        print(f"  job_intakes.transcript length: {len(transcript_text)}")
        if transcript_text:
            print(f"\n--- TRANSCRIPT START ---\n{transcript_text[:3000]}\n--- TRANSCRIPT END ---")
        else:
            print("  EMPTY")
    else:
        print("  NO ROW — transcript = ''")

    # ── 3+4. Full job_descriptions row ───────────────────────────────────────
    section("3+4. FULL job_descriptions ROW (scalar fields)")
    job = db.execute(
        text("""
            SELECT id, title, description, skills_required, responsibilities,
                   experience_level, location, salary_range, remote_policy,
                   experience_required, structured_data, job_status,
                   agency_id, created_at, updated_at
            FROM job_descriptions WHERE id = :j
        """), {"j": JOB_ID}
    ).mappings().first()

    if not job:
        print(f"  FATAL: job {JOB_ID} not found"); sys.exit(1)

    job = dict(job)
    raw_sd = job.pop("structured_data") or {}
    if isinstance(raw_sd, str):
        try: raw_sd = json.loads(raw_sd)
        except: raw_sd = {}
    sd = dict(raw_sd)

    for k, v in job.items():
        print(f"  {k}: {v!r}")

    # ── 5. Full structured_data ───────────────────────────────────────────────
    section("5. FULL job_descriptions.structured_data — KEY INVENTORY")
    def show_sd(d, indent=0):
        pad = "  " * indent
        for k in sorted(d.keys()):
            v = d[k]
            if isinstance(v, str):
                print(f"{pad}[{k}]  str len={len(v)}  ->  {v[:120]!r}")
            elif isinstance(v, list):
                print(f"{pad}[{k}]  list len={len(v)}  ->  {[str(x)[:40] for x in v[:3]]}")
            elif isinstance(v, dict):
                print(f"{pad}[{k}]  dict  keys={list(v.keys())[:10]}")
                if indent < 2:
                    show_sd(v, indent+1)
            elif isinstance(v, (int, float, bool)) or v is None:
                print(f"{pad}[{k}]  {type(v).__name__}  ->  {v!r}")
            else:
                print(f"{pad}[{k}]  {type(v).__name__}  ->  {str(v)[:80]!r}")
    show_sd(sd)

    # ── Specific voice fields ─────────────────────────────────────────────────
    section("5a. VOICE FIELDS inside structured_data")
    voice_keys = [
        "voiceTranscript","voice_transcript","voiceTranscriptRaw","voiceTranscriptClean",
        "voiceExtraction","voice_extraction","voiceSummary","voice_summary",
        "transcript","asyncQuestions","async_questions",
    ]
    for k in voice_keys:
        v = sd.get(k)
        if v is None:
            print(f"  {k}: ABSENT")
        elif isinstance(v, str):
            print(f"  {k}: str len={len(v)}  ->  {v[:120]!r}")
        elif isinstance(v, dict):
            print(f"  {k}: dict keys={list(v.keys())[:8]}")
        elif isinstance(v, list):
            print(f"  {k}: list len={len(v)}")

    # ── recruiterIntelligence sub-object ──────────────────────────────────────
    section("5b. recruiterIntelligence sub-object (full)")
    ri = sd.get("recruiterIntelligence") or {}
    if ri:
        for k, v in ri.items():
            if k in ("recruiter_preference_embedding",):
                print(f"  {k}: [vector, len={len(v) if isinstance(v,list) else 'N/A'}]")
            elif isinstance(v, str):
                print(f"  {k}: str len={len(v)}  ->  {v[:200]!r}")
            elif isinstance(v, dict):
                print(f"  {k}: dict keys={list(v.keys())[:10]}")
            elif isinstance(v, list):
                print(f"  {k}: list len={len(v)}")
            else:
                print(f"  {k}: {type(v).__name__} = {v!r}")
    else:
        print("  recruiterIntelligence: ABSENT")

    # ── recruiterCalibration sub-object ───────────────────────────────────────
    section("5c. recruiterCalibration sub-object (summary)")
    rc = sd.get("recruiterCalibration") or {}
    if rc:
        for k, v in rc.items():
            if k in ("selectedArchetypes","rejectedCandidateIds","selectedCandidateIds","calibrationRounds"):
                if isinstance(v, list):
                    print(f"  {k}: list len={len(v)}")
                elif isinstance(v, dict):
                    print(f"  {k}: dict keys={list(v.keys())[:6]}")
                else:
                    print(f"  {k}: {v!r}")
            elif isinstance(v, str):
                print(f"  {k}: {v!r}")
            elif isinstance(v, list):
                print(f"  {k}: list len={len(v)}")
            elif isinstance(v, dict):
                print(f"  {k}: dict keys={list(v.keys())[:6]}")
            else:
                print(f"  {k}: {v!r}")
    else:
        print("  recruiterCalibration: ABSENT")

    # ── 6. build_job_text(job) — exact matcher call ───────────────────────────
    section("6. build_job_text(job) — EXACT MATCHER CALL (no extra args)")

    class FakeJob:
        pass
    fj = FakeJob()
    fj.id                 = str(job.get("id",""))
    fj.title              = str(job.get("title","") or "")
    fj.description        = str(job.get("description","") or "")
    fj.skills_required    = job.get("skills_required") or []
    fj.responsibilities   = job.get("responsibilities") or []
    fj.experience_level   = str(job.get("experience_level","") or "")
    fj.location           = str(job.get("location","") or "")
    fj.compensation       = str(job.get("salary_range","") or "")
    fj.work_authorization = ""
    fj.remote_policy      = str(job.get("remote_policy","") or "")
    fj.experience_required= str(job.get("experience_required","") or "")
    fj.structured_data    = sd
    fj.company            = None

    matcher_text = build_job_text(fj)
    print(f"  TOTAL LENGTH: {len(matcher_text)} chars")
    print(f"\n--- MATCHER EMBEDDING TEXT START ---\n{matcher_text}\n--- MATCHER EMBEDDING TEXT END ---")

    # ── 7. Exact text into get_embedding() ───────────────────────────────────
    section("7. TEXT SENT INTO get_embedding() — same as above (matcher path)")
    print(f"  Identical to section 6. Length = {len(matcher_text)} chars")
    print(f"  First 3000 chars:\n{matcher_text[:3000]}")

    # ── 8. Is transcript content in embedding text? ───────────────────────────
    section("8. IS TRANSCRIPT CONTENT IN EMBEDDING TEXT?")
    ri_transcript = ri.get("transcript","") or ri.get("voiceTranscript","") or ""
    top_transcript = sd.get("voiceTranscript","") or sd.get("voice_transcript","") or ""
    intake_transcript = transcript_text

    for label, t in [
        ("job_intakes.transcript", intake_transcript),
        ("structured_data.voiceTranscript", top_transcript),
        ("structured_data.recruiterIntelligence.transcript", ri_transcript),
    ]:
        if t:
            sample = t[:40].lower()
            found = sample in matcher_text.lower()
            print(f"  {label}:")
            print(f"    stored len={len(t)}, first 40 chars: {t[:40]!r}")
            print(f"    appears in matcher embedding text: {found}")
        else:
            print(f"  {label}: EMPTY/ABSENT — nothing to check")

    # ── 9. Is calibration data in embedding text? ─────────────────────────────
    section("9. IS RECRUITER CALIBRATION DATA IN EMBEDDING TEXT?")
    # Check selected archetype skills/roles
    selected = rc.get("selectedArchetypes") or []
    if isinstance(selected, list) and selected:
        first_arch = selected[0] if isinstance(selected[0], dict) else {}
        arch_skills = first_arch.get("skills") or first_arch.get("top_skills") or []
        arch_role   = first_arch.get("role") or first_arch.get("current_role") or ""
        print(f"  Calibration archetypes selected: {len(selected)}")
        print(f"  First archetype role: {arch_role!r}")
        print(f"  First archetype skills: {arch_skills}")
        if arch_role:
            in_text = arch_role.lower() in matcher_text.lower()
            print(f"  Archetype role in embedding text: {in_text}")
        if arch_skills:
            for s in arch_skills[:3]:
                in_text = str(s).lower() in matcher_text.lower()
                print(f"  Skill '{s}' in embedding text: {in_text}")
    else:
        print("  No selectedArchetypes found in recruiterCalibration")

    intent = sd.get("intentProfile") or ri.get("intentProfile") or {}
    if intent:
        pref_text = intent.get("preference_text","")
        print(f"\n  intentProfile.preference_text: {pref_text!r}")
        print(f"  preference_text in embedding: {pref_text[:40].lower() in matcher_text.lower() if pref_text else False}")
    else:
        print("\n  intentProfile: ABSENT from structured_data top level and recruiterIntelligence")

    # ── 10. Every field contributing to semantic matching ─────────────────────
    section("10. EVERY FIELD CONTRIBUTING TO SEMANTIC MATCHING")
    fields = {
        "job.title":              fj.title,
        "job.description":        fj.description,
        "job.skills_required":    fj.skills_required,
        "job.responsibilities":   fj.responsibilities,
        "job.experience_level":   fj.experience_level,
        "job.location":           fj.location,
        "job.compensation":       fj.compensation,
        "job.remote_policy":      fj.remote_policy,
        "job.experience_required":fj.experience_required,
        "structured_data.skills": sd.get("skills") or sd.get("skills_required"),
        "structured_data.role":   sd.get("role") or sd.get("title"),
        "structured_data.experience": sd.get("experience") or sd.get("experience_level") or sd.get("experienceRequired"),
        "structured_data.location":   sd.get("location"),
        "structured_data.compensation": sd.get("compensation") or sd.get("salary_range"),
        "structured_data.remotePolicy": sd.get("remotePolicy") or sd.get("remote_policy"),
        "structured_data.responsibilities": sd.get("responsibilities"),
        "structured_data.companyName": sd.get("companyName") or sd.get("company"),
        "structured_data.industry": sd.get("industry"),
        "structured_data.companyDescription": sd.get("companyDescription"),
        "transcript (via _extract_voice_transcript)": _extract_voice_transcript(sd),
    }
    for name, val in fields.items():
        if val is None or val == "" or val == [] or val == {}:
            status = "EMPTY/ABSENT — NOT contributing"
        else:
            v_str = str(val)[:60]
            status = f"PRESENT -> {v_str!r}"
        print(f"  {name}: {status}")

    # ── 11. Stored but never read by build_job_text ───────────────────────────
    section("11. FIELDS STORED IN structured_data BUT NEVER READ BY build_job_text()")
    # build_job_text reads these keys from structured_data:
    read_by_build = {
        "role","title","skills","skills_required","experience","experience_level",
        "experienceRequired","experience_required","location","compensation","salary_range",
        "workAuthorization","work_authorization","remotePolicy","remote_policy",
        "responsibilities","companyName","company","industry","companyDescription",
        "voice_extraction","transcript",  # via _extract_voice_transcript
        "description",
    }
    never_read = []
    for k in sorted(sd.keys()):
        if k not in read_by_build:
            v = sd[k]
            if isinstance(v, str):
                never_read.append((k, f"str len={len(v)}"))
            elif isinstance(v, dict):
                never_read.append((k, f"dict keys={list(v.keys())[:6]}"))
            elif isinstance(v, list):
                never_read.append((k, f"list len={len(v)}"))
            else:
                never_read.append((k, f"{type(v).__name__}={v!r}"))

    for k, desc in never_read:
        print(f"  UNUSED: [{k}]  {desc}")

    # ── 12. Gap report ────────────────────────────────────────────────────────
    section("12. GAP REPORT")

    print("\n  --- Stored but unused fields ---")
    for k, desc in never_read:
        print(f"    {k}: {desc}")

    print("\n  --- Missing extracted requirements ---")
    missing = []
    if not fj.skills_required:
        missing.append("skills_required: EMPTY LIST in job_descriptions")
    if not sd.get("skills") and not sd.get("skills_required"):
        missing.append("structured_data.skills: ABSENT")
    if not fj.responsibilities or fj.responsibilities == []:
        missing.append("responsibilities: EMPTY LIST in job_descriptions")
    if not sd.get("responsibilities"):
        missing.append("structured_data.responsibilities: ABSENT")
    if not fj.experience_level and not fj.experience_required:
        missing.append("experience_level + experience_required: BOTH EMPTY")
    if not _extract_voice_transcript(sd):
        missing.append("voice transcript: NOT EXTRACTABLE by build_job_text (key mismatch)")
    for m in missing:
        print(f"    MISSING: {m}")

    print("\n  --- Missing skills ---")
    print(f"    job.skills_required = {fj.skills_required!r}")
    print(f"    structured_data skills keys present: {[k for k in ('skills','skills_required') if sd.get(k)]}")

    print("\n  --- Missing responsibilities ---")
    print(f"    job.responsibilities = {fj.responsibilities!r}")
    print(f"    structured_data.responsibilities = {sd.get('responsibilities')!r}")

    print("\n  --- Missing experience data ---")
    print(f"    job.experience_level   = {fj.experience_level!r}")
    print(f"    job.experience_required= {fj.experience_required!r}")
    print(f"    structured_data.experienceRequired = {sd.get('experienceRequired')!r}")
    print(f"    structured_data.experience_level   = {sd.get('experience_level')!r}")

    # ── 13. What is the matcher actually using? ───────────────────────────────
    section("13. WHAT IS THE MATCHER ACTUALLY USING?")

    has_form_data    = bool(fj.title or fj.description or fj.location)
    has_transcript   = bool(_extract_voice_transcript(sd) or transcript_text)
    has_calibration  = bool(rc.get("selectedArchetypes"))

    print(f"  (a) Job form data present and used:          {has_form_data}")
    print(f"  (b) Transcript present and used in text:     {has_transcript}")
    print(f"  (c) Calibration data present and used:       {has_calibration}")
    print()
    if has_form_data and not has_transcript and not has_calibration:
        verdict = "ONLY JOB FORM DATA"
    elif has_form_data and has_transcript and not has_calibration:
        verdict = "JOB FORM + TRANSCRIPT"
    elif has_form_data and has_transcript and has_calibration:
        verdict = "JOB FORM + TRANSCRIPT + CALIBRATION"
    elif has_form_data and not has_transcript and has_calibration:
        verdict = "JOB FORM + CALIBRATION (no transcript)"
    else:
        verdict = "UNKNOWN"
    print(f"  VERDICT: Matcher is using -> {verdict}")
    print()
    print(f"  Embedding text total length: {len(matcher_text)} chars")
    print(f"  'Voice Input:' section content: {repr(matcher_text.split('Voice Input:')[-1].strip()[:200]) if 'Voice Input:' in matcher_text else 'SECTION ABSENT'}")
    print(f"  Skills line in embedding: {repr([l for l in matcher_text.splitlines() if l.startswith('Skills:')][0] if any(l.startswith('Skills:') for l in matcher_text.splitlines()) else 'NOT FOUND')}")
    print(f"  Responsibilities in embedding: {'- Not specified' in matcher_text or any(l.startswith('- ') and len(l)>4 for l in matcher_text.splitlines())}")
