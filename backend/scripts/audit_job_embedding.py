"""
Audit script: trace exact embedding input for job 6ac18079-c93d-4951-9d24-7b5fb1c9066e
NO code changes. Evidence only.
Tables confirmed: job_intakes, job_descriptions
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.database_url import normalize_database_url
from app.services.job_text_service import build_job_text, _extract_voice_transcript

JOB_ID = "6ac18079-c93d-4951-9d24-7b5fb1c9066e"

DATABASE_URL = normalize_database_url(os.environ["DATABASE_URL"])
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SEP = "=" * 80

def dump(label, value):
    print(f"\n{SEP}")
    print(f"  {label}")
    print(SEP)
    if isinstance(value, dict):
        print(json.dumps(value, indent=2, default=str))
    else:
        print(value)

with Session(engine) as db:

    # ── A) job_intakes row ───────────────────────────────────────────────────
    # Confirmed columns: id, job_id, agency_id, transcript, structured_data_json,
    #                    intake_status, completed_at, updated_at, created_at, company_id
    intake_row = db.execute(
        text("""
            SELECT id, job_id, agency_id, transcript, structured_data_json,
                   intake_status, completed_at, updated_at, created_at, company_id
            FROM job_intakes
            WHERE job_id = :jid
        """),
        {"jid": JOB_ID},
    ).mappings().first()

    dump("A) job_intakes ROW", dict(intake_row) if intake_row else "NO ROW FOUND FOR THIS JOB_ID")

    transcript_in_intake = ""
    if intake_row:
        transcript_in_intake = intake_row["transcript"] or ""
        dump("C) EXACT TRANSCRIPT stored in job_intakes.transcript",
             transcript_in_intake if transcript_in_intake else "(COLUMN EXISTS BUT IS EMPTY)")
    else:
        dump("C) EXACT TRANSCRIPT stored in job_intakes.transcript",
             "(NO ROW IN job_intakes FOR THIS JOB_ID)")

    # ── B) job_descriptions row ──────────────────────────────────────────────
    # Confirmed columns include: id, title, description, skills_required,
    # responsibilities, experience_level, location, salary_range,
    # work_authorization, remote_policy, experience_required, structured_data
    job_row = db.execute(
        text("""
            SELECT id, title, description, skills_required, responsibilities,
                   experience_level, location, salary_range,
                   remote_policy, experience_required, structured_data,
                   job_status, agency_id, company_registry_id, created_at, updated_at
            FROM job_descriptions
            WHERE id = :jid
        """),
        {"jid": JOB_ID},
    ).mappings().first()

    if not job_row:
        print(f"\nFATAL: job {JOB_ID} not found in job_descriptions table")
        sys.exit(1)

    # Scalar fields (no structured_data)
    job_scalar = {k: v for k, v in dict(job_row).items() if k != "structured_data"}
    dump("JOB SCALAR FIELDS from job_descriptions", job_scalar)

    # ── B) structured_data ────────────────────────────────────────────────────
    raw_sd = job_row.get("structured_data") or {}
    if isinstance(raw_sd, str):
        try:
            structured_data = json.loads(raw_sd)
        except Exception:
            structured_data = {}
    else:
        structured_data = dict(raw_sd) if raw_sd else {}

    dump("B) job_descriptions.structured_data (full)", structured_data)

    # ── B1) Key inventory ─────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  B1) KEY INVENTORY inside job_descriptions.structured_data")
    print(SEP)
    if structured_data:
        for key in sorted(structured_data.keys()):
            val = structured_data[key]
            if isinstance(val, str):
                preview = val[:120].replace("\n", " ")
                print(f"  [{key}]  (str, len={len(val)})  ->  {preview!r}")
            elif isinstance(val, list):
                print(f"  [{key}]  (list, len={len(val)})  ->  {val[:3]}")
            elif isinstance(val, dict):
                inner_keys = list(val.keys())[:8]
                print(f"  [{key}]  (dict, keys={inner_keys})")
            else:
                print(f"  [{key}]  ({type(val).__name__})  ->  {val!r}")
    else:
        print("  structured_data is EMPTY or NULL")

    # ── B2) voiceTranscript key ───────────────────────────────────────────────
    voice_transcript_val = structured_data.get("voiceTranscript") or structured_data.get("voice_transcript") or ""
    dump("B2) structured_data['voiceTranscript']",
         voice_transcript_val if voice_transcript_val else "(KEY MISSING OR EMPTY)")

    # ── B3/B4) voiceExtraction ────────────────────────────────────────────────
    voice_extraction = structured_data.get("voiceExtraction") or structured_data.get("voice_extraction") or {}
    dump("B3) structured_data['voiceExtraction'] — top-level keys",
         list(voice_extraction.keys()) if voice_extraction else "(KEY MISSING OR EMPTY)")

    if voice_extraction and isinstance(voice_extraction, dict):
        inner_t = voice_extraction.get("transcript") or voice_extraction.get("rawTranscript") or ""
        dump("B4) voiceExtraction['transcript'] value",
             inner_t[:2000] if inner_t else "(EMPTY)")

    # ── Build FakeJob replicating exactly what ORM loads ─────────────────────
    class FakeJob:
        pass

    fake = FakeJob()
    fake.id                 = str(job_row["id"] or "")
    fake.title              = str(job_row["title"] or "")
    fake.description        = str(job_row["description"] or "")
    fake.skills_required    = job_row["skills_required"] or []
    fake.responsibilities   = job_row["responsibilities"] or []
    fake.experience_level   = str(job_row["experience_level"] or "")
    fake.location           = str(job_row["location"] or "")
    fake.compensation       = str(job_row["salary_range"] or "")
    fake.work_authorization = ""   # column not in job_descriptions — confirmed absent
    fake.remote_policy      = str(job_row["remote_policy"] or "")
    fake.experience_required= str(job_row["experience_required"] or "")
    fake.structured_data    = structured_data
    fake.company            = None  # matcher does not eager-load company

    # ── D) EXACT matcher call: build_job_text(job) — no extra args ────────────
    job_text_matcher = build_job_text(fake)

    dump("D) build_job_text(job)  [EXACT MATCHER CALL — no structured_data arg, no transcript arg]",
         f"TOTAL LENGTH: {len(job_text_matcher)} chars\n\nFIRST 3000 CHARS:\n{job_text_matcher[:3000]}")

    # ── E) Voice-refine call: build_job_text(job, structured_data=..., transcript=...) ──
    job_text_voice = build_job_text(fake, structured_data=structured_data, transcript=voice_transcript_val)

    dump("E) build_job_text(job, structured_data=..., transcript=voiceTranscript)  [VOICE REFINE CALL]",
         f"TOTAL LENGTH: {len(job_text_voice)} chars\n\nFIRST 3000 CHARS:\n{job_text_voice[:3000]}")

    # ── F) _extract_voice_transcript result ───────────────────────────────────
    extracted = _extract_voice_transcript(structured_data)
    dump("F) _extract_voice_transcript(structured_data) return value",
         extracted[:2000] if extracted else "(RETURNS EMPTY STRING — key lookup failed)")

    print(f"\n{SEP}")
    print("  F1) KEY MISMATCH EVIDENCE")
    print(SEP)
    print(f"  _extract_voice_transcript() code path:")
    print(f"    voice_extraction = structured_data.get('voice_extraction') or structured_data.get('transcript') or {{}}")
    print(f"")
    print(f"  Key 'voice_extraction' in structured_data:  {'voice_extraction' in structured_data}")
    print(f"  Key 'voiceExtraction'  in structured_data:  {'voiceExtraction' in structured_data}")
    print(f"  Key 'voiceTranscript'  in structured_data:  {'voiceTranscript' in structured_data}")
    print(f"  Key 'transcript'       in structured_data:  {'transcript' in structured_data}")
    print(f"")
    print(f"  Result: _extract_voice_transcript returns: {repr(extracted[:80]) if extracted else repr('')}")

    # ── G) voiceTranscript in DB but NOT in matcher output ────────────────────
    print(f"\n{SEP}")
    print("  G) voiceTranscript: EXISTS IN DB vs PRESENT IN MATCHER OUTPUT")
    print(SEP)
    vt = structured_data.get("voiceTranscript", "")
    if vt:
        first_50 = vt[:50]
        in_matcher = first_50.lower() in job_text_matcher.lower()
        print(f"  voiceTranscript exists in structured_data:          YES")
        print(f"  voiceTranscript length:                             {len(vt)} chars")
        print(f"  voiceTranscript first 50 chars:                     {first_50!r}")
        print(f"  Those chars appear in matcher job_text output:      {in_matcher}")
        print(f"  CONCLUSION: voiceTranscript is {'INCLUDED' if in_matcher else 'NOT INCLUDED'} in matcher embedding input")
        print(f"")
        print(f"  CODE PATH PROOF:")
        print(f"  match_internal_candidates_for_job() calls:")
        print(f"    job_text = build_job_text(job)")
        print(f"  build_job_text(job) with no args calls:")
        print(f"    transcript_text = _normalize_text('') or _extract_voice_transcript(structured_data)")
        print(f"  _extract_voice_transcript looks for key 'voice_extraction' -> NOT FOUND")
        print(f"  _extract_voice_transcript falls back to key 'transcript'   -> NOT FOUND")
        print(f"  _extract_voice_transcript returns ''")
        print(f"  transcript_text = ''")
        print(f"  'Voice Input:' line in output = empty")
    else:
        print("  voiceTranscript key is ABSENT or EMPTY in structured_data")

    # ── H) Full embedding source text ─────────────────────────────────────────
    dump("H) FINAL EMBEDDING SOURCE TEXT (matcher path) — full length + first 3000 chars",
         f"TOTAL LENGTH: {len(job_text_matcher)} chars\n\n{job_text_matcher[:3000]}")

    # ── SUMMARY TABLE ─────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  FINAL SUMMARY")
    print(SEP)
    print(f"  job_intakes row exists:                              {'YES' if intake_row else 'NO'}")
    print(f"  job_intakes.transcript non-empty:                    {'YES' if transcript_in_intake else 'NO'} (len={len(transcript_in_intake)})")
    print(f"  structured_data['voiceTranscript'] non-empty:        {'YES' if voice_transcript_val else 'NO'} (len={len(voice_transcript_val)})")
    print(f"  structured_data['voiceExtraction'] non-empty:        {'YES' if voice_extraction else 'NO'}")
    print(f"  _extract_voice_transcript() returns non-empty:       {'YES' if extracted else 'NO'}")
    print(f"  voiceTranscript included in MATCHER job_text:        {'YES' if vt and vt[:50].lower() in job_text_matcher.lower() else 'NO'}")
    print(f"  voiceTranscript included in VOICE-REFINE job_text:   {'YES' if voice_transcript_val and voice_transcript_val[:50].lower() in job_text_voice.lower() else 'NO'}")
    print(f"  Matcher job_text total length:                       {len(job_text_matcher)} chars")
    print(f"  Voice-refine job_text total length:                  {len(job_text_voice)} chars")
    print(f"  Delta (voice adds N chars to embedding):             {len(job_text_voice) - len(job_text_matcher)} chars")
    print(SEP)
