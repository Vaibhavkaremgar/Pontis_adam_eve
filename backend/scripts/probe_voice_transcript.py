"""
Runtime probe: load JobDescriptionEntity for job_id and execute
_extract_voice_transcript + build_job_text. Print exact results.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.database_url import normalize_database_url
from app.models.entities import JobEntity as JobDescriptionEntity
from app.services.job_text_service import _extract_voice_transcript, build_job_text

JOB_ID = "26ea1741-cb27-45b3-9f34-95aa25f443ee"

engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]), pool_pre_ping=True)

with Session(engine) as db:
    job = db.get(JobDescriptionEntity, JOB_ID)
    if not job:
        print("FATAL: job not found"); sys.exit(1)

    sd = job.structured_data
    print(f"[1] type(job.structured_data) = {type(sd)}")

    # ── Step 2: voiceTranscript key ───────────────────────────────────────────
    print("\n=== STEP 2: job.structured_data['voiceTranscript'] ===")
    vt = sd.get("voiceTranscript") if isinstance(sd, dict) else None
    if vt is None:
        print("  KEY ABSENT — voiceTranscript not in structured_data")
        print(f"  Top-level keys: {list(sd.keys()) if isinstance(sd, dict) else 'NOT A DICT'}")
    else:
        print(f"  len = {len(vt)}")
        print(f"  first 100 chars: {vt[:100]!r}")

    # ── Step 3+4: _extract_voice_transcript(job.structured_data) ─────────────
    print("\n=== STEP 3+4: _extract_voice_transcript(job.structured_data) ===")
    extracted = _extract_voice_transcript(sd)
    print(f"  returned length = {len(extracted)}")
    if extracted:
        print(f"  first 100 chars: {extracted[:100]!r}")
    else:
        print("  returned EMPTY STRING")
        # Diagnose which key was checked and why it failed
        print("\n  --- Diagnosis: checking each key _extract_voice_transcript reads ---")
        if isinstance(sd, dict):
            for key in ("voiceTranscript", "voiceTranscriptClean", "voiceTranscriptRaw"):
                val = sd.get(key)
                print(f"  sd[{key!r}]: type={type(val).__name__}  truthy={bool(val)}  len={len(val) if isinstance(val, str) else 'N/A'}")
            ve = sd.get("voiceExtraction") or sd.get("voice_extraction")
            print(f"  sd['voiceExtraction']: type={type(ve).__name__}  keys={list(ve.keys()) if isinstance(ve, dict) else 'N/A'}")
            if isinstance(ve, dict):
                for key in ("transcript", "cleanedTranscript", "rawTranscript", "notes", "summary"):
                    val = ve.get(key)
                    print(f"    voiceExtraction[{key!r}]: type={type(val).__name__}  truthy={bool(val)}  len={len(val) if isinstance(val, str) else 'N/A'}")

    # ── Step 5+6: build_job_text(job) ─────────────────────────────────────────
    print("\n=== STEP 5+6: build_job_text(job) ===")
    job_text = build_job_text(job)
    print(f"  total length = {len(job_text)}")

    if "Voice Input:" in job_text:
        voice_section = job_text.split("Voice Input:", 1)[1]
        print(f"  length after 'Voice Input:' = {len(voice_section)}")
        print(f"  first 300 chars of Voice Input section: {voice_section[:300]!r}")
    else:
        print("  'Voice Input:' section: ABSENT from job_text")
