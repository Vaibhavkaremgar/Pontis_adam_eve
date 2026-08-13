"""
Audit candidate experience persistence for top 10 semantic hits.
Read-only. No data changes.
"""
from __future__ import annotations
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from app.db.database_url import normalize_database_url
from app.models.entities import CandidateProfileEntity
from app.services.job_text_service import build_job_text
from app.services.embedding_service import get_embedding
from app.services.qdrant_service import search_internal_candidate_chunks, INTERNAL_CANDIDATE_COLLECTION_NAME
from app.models.entities import JobEntity
from app.core.config import EMBEDDING_VERSION, INTERNAL_CANDIDATE_RETRIEVAL_TOP_K

JOB_ID    = "26ea1741-cb27-45b3-9f34-95aa25f443ee"
AGENCY_ID = "0f71cf1f-1aca-4188-a76b-0b97af1483ad"

EXPERIENCE_KEYS = re.compile(
    r"experience|employment|work|career|history|years",
    re.IGNORECASE,
)

def _text(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _find_experience_fields(obj, path="") -> dict:
    """Recursively walk obj and collect every key whose name matches EXPERIENCE_KEYS."""
    found = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_path = f"{path}.{k}" if path else k
            if EXPERIENCE_KEYS.search(str(k)):
                found[full_path] = v
            found.update(_find_experience_fields(v, full_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.update(_find_experience_fields(item, f"{path}[{i}]"))
    return found


def _count_list(obj, key) -> int:
    if not isinstance(obj, dict):
        return 0
    val = obj.get(key)
    if isinstance(val, list):
        return len(val)
    return 0


engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]), pool_pre_ping=True)

with Session(engine) as db:
    job = db.get(JobEntity, JOB_ID)
    job_text = build_job_text(job)
    query_vector = get_embedding(job_text)

    hits = search_internal_candidate_chunks(
        query_vector=query_vector,
        limit=max(1, INTERNAL_CANDIDATE_RETRIEVAL_TOP_K),
        metadata_filters={"embeddingVersion": EMBEDDING_VERSION},
        raise_on_unavailable=True,
        allow_unfiltered_fallback=False,
    )

    # Extract top-10 candidateRecordIds in score order
    record_ids = []
    seen = set()
    for h in hits:
        rid = _text((h.get("payload") or {}).get("candidateRecordId"))
        if rid and rid not in seen:
            seen.add(rid)
            record_ids.append(rid)
        if len(record_ids) == 10:
            break

    rows = {
        str(r.id): r
        for r in db.scalars(
            select(CandidateProfileEntity).where(CandidateProfileEntity.id.in_(record_ids))
        ).all()
    }

    # Print in original score order
    for rank, rid in enumerate(record_ids, 1):
        row = rows.get(rid)
        if not row:
            print(f"\n[EXPERIENCE_STORAGE_AUDIT] rank={rank} record_id={rid} STATUS=NOT_FOUND_IN_POSTGRES\n")
            continue

        raw   = row.raw_data if isinstance(row.raw_data, dict) else {}
        parsed = row.parsed_resume_json if isinstance(row.parsed_resume_json, dict) else {}

        raw_keys    = list(raw.keys())
        parsed_keys = list(parsed.keys())

        exp_in_raw    = _find_experience_fields(raw)
        exp_in_parsed = _find_experience_fields(parsed)
        all_exp_fields = {**{f"raw_data.{k}": v for k, v in exp_in_raw.items()},
                          **{f"parsed_resume_json.{k}": v for k, v in exp_in_parsed.items()}}

        wh_raw    = _count_list(raw,    "work_history")
        wh_parsed = _count_list(parsed, "work_history")
        eh_raw    = _count_list(raw,    "employment_history")
        eh_parsed = _count_list(parsed, "employment_history")

        resume_preview = _text(row.resume_text)[:1000] if row.resume_text else "EMPTY"

        print(f"""
[EXPERIENCE_STORAGE_AUDIT]
candidate_id={_text(row.candidate_id or row.id)}
candidate_name={_text(row.name or row.candidate_id or row.id)}

total_experience_years={getattr(row, 'total_experience_years', 'COLUMN_ABSENT')}

raw_data_keys={raw_keys}

parsed_resume_json_keys={parsed_keys}

experience_related_fields_found={{""")
        if all_exp_fields:
            for field_path, val in all_exp_fields.items():
                if isinstance(val, (list, dict)):
                    print(f"  {field_path}: {type(val).__name__} len={len(val)}")
                    if isinstance(val, list) and val:
                        # show first entry summary
                        first = val[0]
                        if isinstance(first, dict):
                            print(f"    first_entry_keys={list(first.keys())}")
                            print(f"    first_entry={str(first)[:300]}")
                        else:
                            print(f"    first_entry={str(first)[:200]}")
                    elif isinstance(val, dict):
                        print(f"    keys={list(val.keys())}")
                        print(f"    value={str(val)[:300]}")
                else:
                    print(f"  {field_path}: {val!r}")
        else:
            print("  NONE FOUND")
        print("}")

        print(f"""
work_history_count={wh_raw} (raw_data) / {wh_parsed} (parsed_resume_json)
employment_history_count={eh_raw} (raw_data) / {eh_parsed} (parsed_resume_json)

resume_text_preview={resume_preview!r}
""")
        print("-" * 72)
