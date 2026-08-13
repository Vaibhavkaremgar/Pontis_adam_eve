"""
Runtime audit of match_internal_candidates_for_job() for a specific job_id.
Executes the EXACT same code path. Read-only. No changes.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from app.db.database_url import normalize_database_url
from app.models.entities import JobEntity, CandidateProfileEntity
from app.services.job_text_service import build_job_text
from app.services.embedding_service import get_embedding
from app.services.qdrant_service import (
    search_internal_candidate_chunks,
    INTERNAL_CANDIDATE_COLLECTION_NAME,
    count_collection_points,
)
from app.services.skill_normalizer import normalize_skills, parse_experience
from app.core.config import (
    EMBEDDING_VERSION,
    INTERNAL_CANDIDATE_RETRIEVAL_TOP_K,
    INTERNAL_CANDIDATE_MATCH_THRESHOLD,
    INTERNAL_CANDIDATE_MATCH_WEIGHTS,
)
import re

JOB_ID = "26ea1741-cb27-45b3-9f34-95aa25f443ee"
SEP = "=" * 72

def section(t): print(f"\n{SEP}\n  {t}\n{SEP}")
def _text(v): return re.sub(r"\s+", " ", str(v or "")).strip()
def _tokens(values):
    if isinstance(values, dict): values = list(values.keys()) + list(values.values())
    if isinstance(values, str): values = re.split(r"[,;/|]", values)
    if not isinstance(values, (list, tuple, set)): values = []
    return normalize_skills([_text(v) for v in values if _text(v)])

engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]), pool_pre_ping=True)

with Session(engine) as db:

    # ── Load job ──────────────────────────────────────────────────────────────
    job = db.get(JobEntity, JOB_ID)
    if not job:
        print("FATAL: job not found"); sys.exit(1)

    section("STEP 1: JOB EMBEDDING VECTOR")
    job_text = build_job_text(job)
    print(f"  job_text length      : {len(job_text)} chars")
    query_vector = get_embedding(job_text)
    print(f"  embedding vector size: {len(query_vector)}")
    print(f"  first 5 values       : {[round(x,6) for x in query_vector[:5]]}")

    # ── Qdrant query ──────────────────────────────────────────────────────────
    section("STEP 2: QDRANT COLLECTION")
    print(f"  collection name      : {INTERNAL_CANDIDATE_COLLECTION_NAME}")
    total_points = count_collection_points(INTERNAL_CANDIDATE_COLLECTION_NAME)
    print(f"  total points in coll : {total_points}")
    print(f"  EMBEDDING_VERSION    : {EMBEDDING_VERSION!r}")
    print(f"  top_k requested      : {INTERNAL_CANDIDATE_RETRIEVAL_TOP_K}")
    print(f"  metadata_filter      : embeddingVersion={EMBEDDING_VERSION!r}")
    print(f"  allow_unfiltered_fallback: False")

    section("STEP 3: QDRANT HITS")
    hits = search_internal_candidate_chunks(
        query_vector=query_vector,
        limit=max(1, INTERNAL_CANDIDATE_RETRIEVAL_TOP_K),
        metadata_filters={"embeddingVersion": EMBEDDING_VERSION},
        raise_on_unavailable=True,
        allow_unfiltered_fallback=False,
    )
    print(f"  total hits returned  : {len(hits)}")

    section("STEP 4: FIRST 20 QDRANT HITS")
    if not hits:
        print("  NO HITS RETURNED")
        # Try without version filter to see if anything exists at all
        print("\n  --- Retry WITHOUT embeddingVersion filter ---")
        hits_unfiltered = search_internal_candidate_chunks(
            query_vector=query_vector,
            limit=max(1, INTERNAL_CANDIDATE_RETRIEVAL_TOP_K),
            metadata_filters=None,
            raise_on_unavailable=False,
            allow_unfiltered_fallback=True,
        )
        print(f"  hits without filter  : {len(hits_unfiltered)}")
        for i, h in enumerate(hits_unfiltered[:20]):
            p = h.get("payload") or {}
            print(f"  [{i+1:02d}] candidate_id={p.get('candidateId','?')!r:40s}  "
                  f"score={h.get('score',0):.6f}  "
                  f"embeddingVersion={p.get('embeddingVersion','?')!r}  "
                  f"candidateRecordId={p.get('candidateRecordId','?')!r}")
    else:
        for i, h in enumerate(hits[:20]):
            p = h.get("payload") or {}
            print(f"  [{i+1:02d}] candidate_id={p.get('candidateId','?')!r:40s}  "
                  f"score={h.get('score',0):.6f}  "
                  f"embeddingVersion={p.get('embeddingVersion','?')!r}  "
                  f"candidateRecordId={p.get('candidateRecordId','?')!r}")

    # Use whichever hits we have for the rest of the audit
    working_hits = hits if hits else (hits_unfiltered if not hits else [])

    section("STEP 5: POSTGRES LOOKUP")
    record_ids = list(dict.fromkeys(
        _text((h.get("payload") or {}).get("candidateRecordId"))
        for h in working_hits
        if _text((h.get("payload") or {}).get("candidateRecordId"))
    ))
    missing_record_id = sum(1 for h in working_hits if not _text((h.get("payload") or {}).get("candidateRecordId")))
    print(f"  unique candidateRecordIds extracted : {len(record_ids)}")
    print(f"  hits missing candidateRecordId      : {missing_record_id}")
    print(f"  sample record_ids                   : {record_ids[:5]}")

    rows = db.scalars(
        select(CandidateProfileEntity).where(CandidateProfileEntity.id.in_(record_ids))
    ).all() if record_ids else []
    print(f"  postgres rows found                 : {len(rows)}")
    if record_ids and not rows:
        print("  WARNING: record_ids present but ZERO rows returned from Postgres")

    section("STEP 6: FILTER STAGE — embedding_status and embedding_version")
    dropped_no_row = dropped_status = dropped_version = 0
    passed = []

    row_by_id = {str(r.id): r for r in rows}
    for h in working_hits:
        p = h.get("payload") or {}
        row = row_by_id.get(_text(p.get("candidateRecordId")))
        if not row:
            dropped_no_row += 1
            continue
        es = getattr(row, "embedding_status", None)
        ev = getattr(row, "embedding_version", None)
        if es != "EMBEDDED":
            dropped_status += 1
            print(f"  DROPPED_STATUS  record_id={row.id}  embedding_status={es!r}")
            continue
        if ev != EMBEDDING_VERSION:
            dropped_version += 1
            print(f"  DROPPED_VERSION record_id={row.id}  row_version={ev!r}  expected={EMBEDDING_VERSION!r}")
            continue
        passed.append((row, h))

    print(f"\n  dropped — no postgres row           : {dropped_no_row}")
    print(f"  dropped — embedding_status != EMBEDDED: {dropped_status}")
    print(f"  dropped — embedding_version mismatch: {dropped_version}")
    print(f"  passed all filters                  : {len(passed)}")

    section("STEP 7: COUNT AFTER EACH STAGE")
    print(f"  [1] Qdrant hits (filtered)          : {len(working_hits)}")
    print(f"  [2] After Postgres lookup           : {len(rows)}")
    print(f"  [3] After no-row drop               : {len(working_hits) - dropped_no_row}")
    print(f"  [4] After status filter             : {len(working_hits) - dropped_no_row - dropped_status}")
    print(f"  [5] After version filter (scored)   : {len(passed)}")

    section("STEP 8: SCORING — TOP 20 CANDIDATES")

    # Replicate exact scoring from internal_candidate_semantic_service.py
    def _job_skills(j):
        sd = getattr(j, "structured_data", {}) or {}
        vals = sd.get("skills_required") or sd.get("required_skills") or sd.get("skills") or getattr(j, "skills_required", [])
        return [_text(v) for v in (vals if isinstance(vals, list) else [vals]) if _text(v)]

    def _job_experience(j):
        sd = getattr(j, "structured_data", {}) or {}
        for k in ("experience_required", "experienceRequired", "experience", "experience_level"):
            v = sd.get(k) or getattr(j, k, "")
            if v not in (None, ""): return _text(v)
        return ""

    def _job_role(j):
        sd = getattr(j, "structured_data", {}) or {}
        return _text(sd.get("role") or sd.get("title") or getattr(j, "title", ""))

    def _candidate_skills(r):
        raw = r.skills if isinstance(r.skills, (list, dict, str)) else []
        rd = r.raw_data if isinstance(r.raw_data, dict) else {}
        pr = r.parsed_resume_json if isinstance(r.parsed_resume_json, dict) else {}
        result = set()
        for v in [raw, rd.get("skills"), pr.get("skills")]:
            result.update(_tokens(v))
        return sorted(result)

    def _candidate_years(r):
        if getattr(r, "total_experience_years", None):
            return max(0.0, float(r.total_experience_years))
        rd = r.raw_data if isinstance(r.raw_data, dict) else {}
        pr = r.parsed_resume_json if isinstance(r.parsed_resume_json, dict) else {}
        for v in (rd.get("years_experience"), rd.get("experience_years"),
                  pr.get("years_experience"), pr.get("experience_years")):
            if v not in (None, ""):
                try: return max(0.0, float(parse_experience(v)))
                except: continue
        return 0.0

    def _experience_match(cy, req):
        try: ry = float(parse_experience(req or ""))
        except: ry = 0.0
        if ry <= 0: return 0.5
        return 1.0 if cy >= ry else max(0.0, cy / ry)

    def _location_match(j, r):
        sd = getattr(j, "structured_data", {}) or {}
        jl = _text(sd.get("location") or getattr(j, "location", "")).lower()
        rp = _text(sd.get("remotePolicy") or getattr(j, "remote_policy", "")).lower()
        cl = _text(getattr(r, "location", "")).lower()
        if not jl or "remote" in rp or "remote" in jl: return 1.0
        if not cl: return 0.5
        return 1.0 if jl in cl or cl in jl else 0.0

    def _role_match(j, r):
        jt = _tokens(_job_role(j))
        ct = _tokens(getattr(r, "current_role", ""))
        if not jt or not ct: return 0.5
        return len(jt.intersection(ct)) / len(jt)

    job_skills   = _job_skills(job)
    job_tokens   = _tokens(job_skills)
    job_exp      = _job_experience(job)
    weights      = INTERNAL_CANDIDATE_MATCH_WEIGHTS

    print(f"  job_skills  : {job_skills}")
    print(f"  job_tokens  : {sorted(job_tokens)}")
    print(f"  job_exp     : {job_exp!r}")
    print(f"  weights     : {weights}")
    print()

    scored = []
    for row, hit in passed:
        cskills = _candidate_skills(row)
        csk_tok = set(cskills)
        resume_words = {
            t.lower()
            for t in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,40}", _text(row.resume_text))
        }
        csk_tok = csk_tok.union(resume_words.intersection(job_tokens))
        matched = sorted(job_tokens.intersection(csk_tok))
        skill_match = len(matched) / len(job_tokens) if job_tokens else 0.5
        cy = _candidate_years(row)
        exp_match = _experience_match(cy, job_exp)
        loc_match = _location_match(job, row)
        role_match = _role_match(job, row)
        semantic = max(0.0, min(1.0, float(hit.get("score") or 0.0)))
        ws = sum(max(0.0, float(weights.get(k, 0))) for k in ("semantic_similarity", "skill_match", "experience_match")) or 1.0
        base = (
            max(0.0, float(weights.get("semantic_similarity", 0.7))) * semantic
            + max(0.0, float(weights.get("skill_match", 0.2))) * skill_match
            + max(0.0, float(weights.get("experience_match", 0.1))) * exp_match
        ) / ws
        final = max(0.0, min(1.0, base * (0.85 + 0.15 * loc_match) * (0.90 + 0.10 * role_match)))
        scored.append({
            "candidate_id": _text(row.candidate_id or row.id),
            "name": _text(row.name or row.candidate_id or row.id),
            "semantic_similarity": round(semantic, 6),
            "skill_match": round(skill_match, 6),
            "experience_match": round(exp_match, 6),
            "location_match": round(loc_match, 6),
            "role_match": round(role_match, 6),
            "final_match_score": round(final, 6),
        })

    scored.sort(key=lambda x: x["final_match_score"], reverse=True)

    if not scored:
        print("  NO CANDIDATES SCORED")
    else:
        print(f"  {'#':<3} {'candidate_id':<38} {'name':<28} {'sem':>7} {'skill':>7} {'exp':>7} {'loc':>5} {'role':>5} {'final':>7}")
        print(f"  {'-'*3} {'-'*38} {'-'*28} {'-'*7} {'-'*7} {'-'*7} {'-'*5} {'-'*5} {'-'*7}")
        for i, c in enumerate(scored[:20]):
            print(f"  {i+1:<3} {c['candidate_id']:<38} {c['name']:<28} "
                  f"{c['semantic_similarity']:>7.4f} {c['skill_match']:>7.4f} "
                  f"{c['experience_match']:>7.4f} {c['location_match']:>5.2f} "
                  f"{c['role_match']:>5.2f} {c['final_match_score']:>7.4f}")

    section("STEP 9: SCORE DISTRIBUTION")
    if scored:
        scores = [c["final_match_score"] for c in scored]
        print(f"  highest_score : {max(scores):.6f}")
        print(f"  lowest_score  : {min(scores):.6f}")
        print(f"  average_score : {sum(scores)/len(scores):.6f}")
        print(f"  total scored  : {len(scores)}")
    else:
        print("  NO SCORES — scored list is empty")

    section("STEP 10: THRESHOLD")
    print(f"  INTERNAL_CANDIDATE_MATCH_THRESHOLD : {INTERNAL_CANDIDATE_MATCH_THRESHOLD}")
    qualified = [c for c in scored if c["final_match_score"] >= INTERNAL_CANDIDATE_MATCH_THRESHOLD]
    print(f"  candidates above threshold         : {len(qualified)}")
    print(f"  candidates below threshold         : {len(scored) - len(qualified)}")

    section("STEP 11: EXACT FAILURE POINT")
    if total_points == 0:
        print("  CAUSE: Qdrant collection is EMPTY — no vectors indexed at all")
    elif len(hits) == 0 and total_points > 0:
        print("  CAUSE: embeddingVersion filter returned 0 hits")
        print(f"         Collection has {total_points} points but NONE match embeddingVersion={EMBEDDING_VERSION!r}")
        # Show what versions exist
        hits_nf = search_internal_candidate_chunks(
            query_vector=query_vector, limit=5,
            metadata_filters=None, raise_on_unavailable=False, allow_unfiltered_fallback=True,
        )
        versions_seen = list({(h.get("payload") or {}).get("embeddingVersion") for h in hits_nf})
        print(f"         Versions actually stored in Qdrant: {versions_seen}")
    elif len(working_hits) > 0 and len(rows) == 0:
        print("  CAUSE: Qdrant returned hits but ZERO Postgres rows matched candidateRecordId")
        print(f"         record_ids queried: {record_ids[:5]}")
    elif dropped_status == len(working_hits) - dropped_no_row:
        print(f"  CAUSE: ALL {dropped_status} candidates dropped — embedding_status != 'EMBEDDED'")
    elif dropped_version == len(working_hits) - dropped_no_row - dropped_status:
        print(f"  CAUSE: ALL {dropped_version} candidates dropped — embedding_version mismatch")
        print(f"         expected={EMBEDDING_VERSION!r}")
    elif len(scored) > 0 and len(qualified) == 0:
        print(f"  CAUSE: THRESHOLD REJECTION")
        print(f"         All {len(scored)} scored candidates are below threshold {INTERNAL_CANDIDATE_MATCH_THRESHOLD}")
        print(f"         Highest score: {max(c['final_match_score'] for c in scored):.6f}")
        print(f"         Gap to threshold: {INTERNAL_CANDIDATE_MATCH_THRESHOLD - max(c['final_match_score'] for c in scored):.6f}")
    elif len(scored) == 0 and len(passed) == 0:
        print("  CAUSE: No candidates survived filter stages — see STEP 6 for breakdown")
    elif len(qualified) > 0:
        print(f"  NO FAILURE — {len(qualified)} qualified candidates found above threshold")
    else:
        print("  CAUSE: UNKNOWN — review steps above")
