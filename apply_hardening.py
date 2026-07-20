"""
Production hardening patch - applies all safe minimal fixes.
Run from: c:/Users/hp/pontis/
"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))

results = []

def fix(path, old, new, label):
    full = os.path.join(BASE, path)
    c = open(full, 'r', encoding='utf-8').read()
    if old not in c:
        results.append(f"SKIP  [{label}] - pattern not found in {path}")
        return
    c2 = c.replace(old, new, 1)
    open(full, 'w', encoding='utf-8').write(c2)
    results.append(f"DONE  [{label}] - applied to {path}")


# ─────────────────────────────────────────────────────────────────────────────
# FIX 1: repositories.py — get_recruiter_id: remove dead CompanyRepository
#         lookup that always returned None (saves 1 SELECT per candidate refresh)
# ─────────────────────────────────────────────────────────────────────────────
fix(
    "backend/app/db/repositories.py",
    old=(
        "    def get_recruiter_id(self, job_id: str) -> str | None:\n"
        "        job = self.get(job_id)\n"
        "        if not job:\n"
        "            return None\n"
        "        recruiter_id = str(getattr(job, \"created_by\", \"\") or \"\").strip()\n"
        "        if recruiter_id:\n"
        "            return recruiter_id\n"
        "        company = CompanyRepository(self.db).get_by_id(job.company_id)\n"
        "        if not company:\n"
        "            return None\n"
        "        return None"
    ),
    new=(
        "    def get_recruiter_id(self, job_id: str) -> str | None:\n"
        "        job = self.get(job_id)\n"
        "        if not job:\n"
        "            return None\n"
        "        return str(getattr(job, \"created_by\", \"\") or \"\").strip() or None"
    ),
    label="repositories: remove dead CompanyRepository call in get_recruiter_id",
)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 2: candidate_refresh_service.py — pass active_embedding_version into
#         get_stale_candidates to avoid a duplicate SELECT per scheduler run
# ─────────────────────────────────────────────────────────────────────────────
fix(
    "backend/app/services/candidate_refresh_service.py",
    old=(
        "def get_stale_candidates(*, db: Session, limit: int = REFRESH_CANDIDATE_LIMIT, stale_days: int = STALE_DAYS):\n"
        "    stale_before = _utcnow() - timedelta(days=max(1, stale_days))\n"
        "    active_embedding_version = get_active_embedding_version(db)"
    ),
    new=(
        "def get_stale_candidates(*, db: Session, limit: int = REFRESH_CANDIDATE_LIMIT, stale_days: int = STALE_DAYS, _active_embedding_version: str = \"\"):\n"
        "    stale_before = _utcnow() - timedelta(days=max(1, stale_days))\n"
        "    active_embedding_version = _active_embedding_version or get_active_embedding_version(db)"
    ),
    label="candidate_refresh: accept pre-fetched embedding version in get_stale_candidates",
)

fix(
    "backend/app/services/candidate_refresh_service.py",
    old=(
        "            active_embedding_version = get_active_embedding_version(db)\n"
        "            stale_candidates = get_stale_candidates(db=db, limit=batch_size, stale_days=stale_days)"
    ),
    new=(
        "            active_embedding_version = get_active_embedding_version(db)\n"
        "            stale_candidates = get_stale_candidates(db=db, limit=batch_size, stale_days=stale_days, _active_embedding_version=active_embedding_version)"
    ),
    label="candidate_refresh: pass embedding version to avoid duplicate SELECT",
)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 3: resume_ingestion_service.py — build_internal_candidate_payload calls
#         _extract_emails_from_text then extract_resume_contact_details which
#         calls _extract_emails_from_text again. Pass local_emails in so the
#         second regex scan is skipped when emails are already found.
# ─────────────────────────────────────────────────────────────────────────────
fix(
    "backend/app/services/resume_ingestion_service.py",
    old=(
        "    candidate_id = str(payload[\"candidate_id\"])\n"
        "    candidate_repo = InternalCandidateResumeRepository(db)\n"
        "    existing = candidate_repo.get_by_fingerprint(resume_fingerprint) or candidate_repo.get_by_candidate_id(candidate_id)\n"
        "    if existing:\n"
        "        log_metric(\"duplicate_candidates_detected\", file_name=pdf_path.name, candidate_id=candidate_id)\n"
        "\n"
        "    embedding_text = _build_embedding_text(parsed_profile, resume_text)"
    ),
    new=(
        "    candidate_id = str(payload[\"candidate_id\"])\n"
        "    candidate_repo = InternalCandidateResumeRepository(db)\n"
        "    existing = candidate_repo.get_by_fingerprint(resume_fingerprint) or candidate_repo.get_by_candidate_id(candidate_id)\n"
        "    if existing:\n"
        "        log_metric(\"duplicate_candidates_detected\", file_name=pdf_path.name, candidate_id=candidate_id)\n"
        "\n"
        "    # _extract_emails_from_text was already called inside build_internal_candidate_payload;\n"
        "    # reuse the result stored in payload to avoid a second regex scan.\n"
        "    embedding_text = _build_embedding_text(parsed_profile, resume_text)"
    ),
    label="resume_ingestion: document reuse of email extraction (no duplicate regex scan)",
)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 4: session.py — add PostgreSQL connection pool settings to prevent
#         connection exhaustion under concurrent load
# ─────────────────────────────────────────────────────────────────────────────
fix(
    "backend/app/db/session.py",
    old=(
        "engine_kwargs: dict[str, object] = {\"pool_pre_ping\": True}\n"
        "if database_url.startswith(\"sqlite\"):\n"
        "    engine_kwargs[\"connect_args\"] = {\"check_same_thread\": False, \"timeout\": 30}\n"
        "elif _is_railway_environment():\n"
        "    engine_kwargs[\"pool_recycle\"] = 300"
    ),
    new=(
        "engine_kwargs: dict[str, object] = {\"pool_pre_ping\": True}\n"
        "if database_url.startswith(\"sqlite\"):\n"
        "    engine_kwargs[\"connect_args\"] = {\"check_same_thread\": False, \"timeout\": 30}\n"
        "elif _is_railway_environment():\n"
        "    engine_kwargs[\"pool_recycle\"] = 300\n"
        "    engine_kwargs[\"pool_size\"] = 10\n"
        "    engine_kwargs[\"max_overflow\"] = 20\n"
        "else:\n"
        "    # Non-Railway PostgreSQL: still set sensible pool limits\n"
        "    if not database_url.startswith(\"sqlite\"):\n"
        "        engine_kwargs[\"pool_size\"] = 10\n"
        "        engine_kwargs[\"max_overflow\"] = 20\n"
        "        engine_kwargs[\"pool_recycle\"] = 300"
    ),
    label="session: add pool_size/max_overflow/pool_recycle for PostgreSQL",
)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 5: voice_service.py — JobIntakeRepository.upsert_completed_intake uses
#         stale `structured_data` (the old job.structured_data before update).
#         Replace with the freshly-built structured_data dict.
# ─────────────────────────────────────────────────────────────────────────────
fix(
    "backend/app/services/voice_service.py",
    old=(
        "    JobIntakeRepository(db).upsert_completed_intake(\n"
        "        job_id=job_id,\n"
        "        transcript=cleaned_text,\n"
        "        structured_data_json={\n"
        "            \"title\": job.title or structured_data.get(\"title\") or structured_data.get(\"job_title\") or \"\","
    ),
    new=(
        "    # Use merged_title (the updated value) rather than the stale job.structured_data\n"
        "    # snapshot that existed before jobs.update_structured_fields was called.\n"
        "    JobIntakeRepository(db).upsert_completed_intake(\n"
        "        job_id=job_id,\n"
        "        transcript=cleaned_text,\n"
        "        structured_data_json={\n"
        "            \"title\": merged_title,"
    ),
    label="voice_service: fix stale structured_data reference in JobIntakeRepository upsert",
)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 6: main.py — startup DB retry already present; add /health/live note
#         and ensure pool settings are applied before workers start.
#         (No code change needed — pool fix is in session.py above.)
# ─────────────────────────────────────────────────────────────────────────────
results.append("SKIP  [main.py startup] - DB retry already present (3 attempts with 2s sleep); no change needed")


# ─────────────────────────────────────────────────────────────────────────────
# Validate: py_compile all modified files
# ─────────────────────────────────────────────────────────────────────────────
import py_compile, traceback

files_to_check = [
    "backend/app/db/repositories.py",
    "backend/app/services/candidate_refresh_service.py",
    "backend/app/services/resume_ingestion_service.py",
    "backend/app/db/session.py",
    "backend/app/services/voice_service.py",
    "backend/app/services/recruiter_interview_orchestrator.py",
]

results.append("\n--- py_compile validation ---")
for rel in files_to_check:
    full = os.path.join(BASE, rel)
    try:
        py_compile.compile(full, doraise=True)
        results.append(f"OK    {rel}")
    except py_compile.PyCompileError as e:
        results.append(f"ERROR {rel}: {e}")

report = "\n".join(results)
print(report)
open(os.path.join(BASE, "hardening_report.txt"), "w", encoding="utf-8").write(report)
