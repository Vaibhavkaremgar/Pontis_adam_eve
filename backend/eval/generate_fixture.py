"""
generate_fixture.py
-------------------
Queries the live database for historical (job, candidate) pairs where the
outcome is known, then writes eval/fixture.json.

Selection criteria
~~~~~~~~~~~~~~~~~~
Good-fit  : candidate whose stage is 'shortlisted', 'offer', or 'hired',
            OR whose resume_score >= 70, and who has embedding_status=EMBEDDED.
Bad-fit   : candidate whose stage is 'rejected' or whose resume_score < 30,
            and who has embedding_status=EMBEDDED.

One good + one bad candidate is selected per job.  Jobs that cannot supply
both are skipped.  Up to MAX_PAIRS pairs are written.

Usage
~~~~~
    cd backend
    python -m eval.generate_fixture            # writes eval/fixture.json
    python -m eval.generate_fixture --limit 5  # cap at 5 pairs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: make sure the backend package is importable when run directly.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("JWT_SECRET", "eval-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "eval-internal-key")

from sqlalchemy import select, and_, or_  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.entities import CandidateProfileEntity, JobEntity  # noqa: E402

MAX_PAIRS = 20

_GOOD_STAGES = {"shortlisted", "offer", "hired", "shortlist"}
_BAD_STAGES = {"rejected", "declined", "disqualified"}
_GOOD_SCORE_MIN = 70.0
_BAD_SCORE_MAX = 30.0


def _candidate_id(row: CandidateProfileEntity) -> str:
    return str(row.candidate_id or row.id)


def _collect_pairs(limit: int) -> list[dict]:
    pairs: list[dict] = []
    session = SessionLocal()
    try:
        # Fetch all jobs that have at least one EMBEDDED candidate.
        job_ids_with_embedded = (
            session.scalars(
                select(CandidateProfileEntity.job_id)
                .where(CandidateProfileEntity.embedding_status == "EMBEDDED")
                .distinct()
            ).all()
        )

        for job_id in job_ids_with_embedded:
            if len(pairs) >= limit:
                break
            if not job_id:
                continue

            job = session.get(JobEntity, str(job_id))
            if not job or not getattr(job, "agency_id", None):
                continue

            embedded = (
                session.scalars(
                    select(CandidateProfileEntity).where(
                        and_(
                            CandidateProfileEntity.job_id == str(job_id),
                            CandidateProfileEntity.embedding_status == "EMBEDDED",
                        )
                    )
                ).all()
            )

            good: CandidateProfileEntity | None = None
            bad: CandidateProfileEntity | None = None

            for row in embedded:
                stage = (row.stage or "").lower().strip()
                score = float(row.resume_score or 0.0)

                if good is None:
                    if stage in _GOOD_STAGES or score >= _GOOD_SCORE_MIN:
                        good = row

                if bad is None:
                    if stage in _BAD_STAGES or (score < _BAD_SCORE_MAX and stage not in _GOOD_STAGES):
                        bad = row

                if good is not None and bad is not None:
                    break

            if good is None or bad is None:
                continue
            if _candidate_id(good) == _candidate_id(bad):
                continue

            pairs.append(
                {
                    "job_id": str(job_id),
                    "agency_id": str(job.agency_id),
                    "job_title": str(job.title or ""),
                    "good_candidate_id": _candidate_id(good),
                    "good_candidate_name": str(good.name or ""),
                    "good_stage": str(good.stage or ""),
                    "good_resume_score": float(good.resume_score or 0.0),
                    "bad_candidate_id": _candidate_id(bad),
                    "bad_candidate_name": str(bad.name or ""),
                    "bad_stage": str(bad.stage or ""),
                    "bad_resume_score": float(bad.resume_score or 0.0),
                }
            )
    finally:
        session.close()

    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate eval fixture from live DB")
    parser.add_argument("--limit", type=int, default=MAX_PAIRS, help="Max pairs to collect")
    parser.add_argument("--out", default=str(Path(__file__).parent / "fixture.json"), help="Output path")
    args = parser.parse_args()

    print(f"Querying database for up to {args.limit} (job, good, bad) pairs …")
    pairs = _collect_pairs(args.limit)

    if not pairs:
        print(
            "No pairs found.  Make sure the database has EMBEDDED candidates with "
            "known outcomes (stage=shortlisted/rejected or resume_score thresholds)."
        )
        sys.exit(1)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(pairs, indent=2))
    print(f"Wrote {len(pairs)} pair(s) to {out_path}")


if __name__ == "__main__":
    main()
