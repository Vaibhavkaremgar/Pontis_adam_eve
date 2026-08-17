"""
run_eval.py
-----------
Offline evaluation harness for Adam's semantic candidate matching.

For each (job, good_candidate, bad_candidate) triple in fixture.json it calls
match_internal_candidates_for_job() and checks that the good candidate scores
strictly higher than the bad candidate.

A pair PASSES  when  score(good) > score(bad).
A pair FAILS   when  score(good) <= score(bad), or when either candidate is
                     absent from the result set (score treated as 0.0).

The threshold is temporarily lowered to 0.0 for the duration of the run so
that both candidates are always scored regardless of the production threshold.
This means the harness tests ranking quality, not threshold filtering.

Usage
~~~~~
    cd backend
    python -m eval.run_eval                        # uses eval/fixture.json
    python -m eval.run_eval --fixture path/to.json
    python -m eval.run_eval --verbose              # print score breakdowns
    python -m eval.run_eval --fail-fast            # stop on first failure

Exit codes
~~~~~~~~~~
    0  all pairs passed
    1  one or more pairs failed
    2  fixture is empty or all pairs errored
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: make the backend package importable when run as a module.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("JWT_SECRET", "eval-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "eval-internal-key")

import app.services.internal_candidate_semantic_service as _matcher  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.utils.exceptions import APIError  # noqa: E402

_FIXTURE_DEFAULT = Path(__file__).parent / "fixture.json"

# Sentinel score returned when a candidate does not appear in the result set.
_ABSENT_SCORE = -1.0


# ---------------------------------------------------------------------------
# Score extraction
# ---------------------------------------------------------------------------

def _score_from_results(candidates: list[Any], candidate_id: str) -> float:
    """Return the finalScore for candidate_id, or _ABSENT_SCORE if not found."""
    for c in candidates:
        if str(getattr(c, "id", "")) == str(candidate_id):
            explanation = getattr(c, "explanation", None)
            if explanation is not None:
                return float(getattr(explanation, "finalScore", _ABSENT_SCORE))
    return _ABSENT_SCORE


# ---------------------------------------------------------------------------
# Single-pair evaluation
# ---------------------------------------------------------------------------

def _eval_pair(pair: dict, *, verbose: bool) -> dict:
    """
    Run matching for one fixture pair and return a result dict with keys:
        job_id, good_candidate_id, bad_candidate_id,
        good_score, bad_score, passed, error, skipped
    """
    job_id = pair["job_id"]
    agency_id = pair["agency_id"]
    good_id = pair["good_candidate_id"]
    bad_id = pair["bad_candidate_id"]

    # Skip placeholder entries left in the fixture template.
    if "REPLACE_WITH" in job_id or "REPLACE_WITH" in good_id or "REPLACE_WITH" in bad_id:
        return {
            "job_id": job_id,
            "job_title": pair.get("job_title", ""),
            "good_candidate_id": good_id,
            "bad_candidate_id": bad_id,
            "good_score": None,
            "bad_score": None,
            "passed": None,
            "skipped": True,
            "error": "placeholder entry — run generate_fixture.py first",
        }

    db = SessionLocal()
    try:
        # Lower the threshold to 0.0 so both candidates are always scored.
        # We patch the module-level constant for this call only.
        original_threshold = _matcher.INTERNAL_CANDIDATE_MATCH_THRESHOLD
        _matcher.INTERNAL_CANDIDATE_MATCH_THRESHOLD = 0.0
        try:
            result = _matcher.match_internal_candidates_for_job(
                db=db,
                job_id=job_id,
                agency_id=agency_id,
                limit=500,  # retrieve enough to find both candidates
            )
        finally:
            _matcher.INTERNAL_CANDIDATE_MATCH_THRESHOLD = original_threshold

        candidates = result.get("candidates", [])
        good_score = _score_from_results(candidates, good_id)
        bad_score = _score_from_results(candidates, bad_id)
        passed = good_score > bad_score

        if verbose:
            _print_breakdown(pair, candidates, good_id, bad_id, good_score, bad_score)

        return {
            "job_id": job_id,
            "job_title": pair.get("job_title", ""),
            "good_candidate_id": good_id,
            "good_candidate_name": pair.get("good_candidate_name", ""),
            "bad_candidate_id": bad_id,
            "bad_candidate_name": pair.get("bad_candidate_name", ""),
            "good_score": good_score,
            "bad_score": bad_score,
            "passed": passed,
            "skipped": False,
            "error": None,
        }

    except APIError as exc:
        return {
            "job_id": job_id,
            "job_title": pair.get("job_title", ""),
            "good_candidate_id": good_id,
            "bad_candidate_id": bad_id,
            "good_score": None,
            "bad_score": None,
            "passed": False,
            "skipped": False,
            "error": f"APIError {exc.status_code}: {exc.message}",
        }
    except Exception as exc:
        return {
            "job_id": job_id,
            "job_title": pair.get("job_title", ""),
            "good_candidate_id": good_id,
            "bad_candidate_id": bad_id,
            "good_score": None,
            "bad_score": None,
            "passed": False,
            "skipped": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        db.close()


def _print_breakdown(
    pair: dict,
    candidates: list[Any],
    good_id: str,
    bad_id: str,
    good_score: float,
    bad_score: float,
) -> None:
    """Print per-candidate score breakdown when --verbose is set."""
    def _row(label: str, cid: str, score: float) -> None:
        c = next((x for x in candidates if str(getattr(x, "id", "")) == cid), None)
        if c is None:
            print(f"    {label}: {cid}  score=ABSENT")
            return
        exp = getattr(c, "explanation", None)
        if exp is None:
            print(f"    {label}: {cid}  score={score:.4f}")
            return
        print(
            f"    {label}: {cid}  "
            f"final={score:.4f}  "
            f"semantic={getattr(exp, 'semanticScore', 0):.4f}  "
            f"skill={getattr(exp, 'skillOverlap', 0):.4f}  "
            f"exp={getattr(exp, 'experienceMatch', '?')}  "
            f"loc={getattr(exp, 'locationMatch', 0):.2f}  "
            f"role={getattr(exp, 'roleMatch', 0):.2f}"
        )

    print(f"  Job: {pair.get('job_title', pair['job_id'])} ({pair['job_id']})")
    _row("  GOOD", good_id, good_score)
    _row("  BAD ", bad_id, bad_score)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _fmt_score(score: float | None) -> str:
    if score is None:
        return "ERROR"
    if score == _ABSENT_SCORE:
        return "ABSENT"
    return f"{score:.4f}"


def _print_results(results: list[dict]) -> None:
    passed = sum(1 for r in results if r["passed"] is True)
    failed = sum(1 for r in results if r["passed"] is False and not r["skipped"])
    skipped = sum(1 for r in results if r["skipped"])
    errored = sum(1 for r in results if r["error"] and not r["skipped"])

    print()
    print("=" * 72)
    print(f"  Semantic Matching Eval  —  {len(results)} pair(s)")
    print("=" * 72)

    for r in results:
        if r["skipped"]:
            status = "SKIP"
        elif r["passed"]:
            status = "PASS"
        else:
            status = "FAIL"

        good_name = r.get("good_candidate_name") or r["good_candidate_id"]
        bad_name = r.get("bad_candidate_name") or r["bad_candidate_id"]
        title = r.get("job_title") or r["job_id"]

        print(
            f"  [{status}]  {title[:40]:<40}  "
            f"good={_fmt_score(r['good_score'])}  "
            f"bad={_fmt_score(r['bad_score'])}"
        )
        if r["error"]:
            print(f"         error: {r['error']}")
        elif status == "FAIL":
            print(
                f"         good={good_name!r} scored {_fmt_score(r['good_score'])} "
                f"<= bad={bad_name!r} scored {_fmt_score(r['bad_score'])}"
            )

    print("-" * 72)
    print(f"  PASSED: {passed}  FAILED: {failed}  SKIPPED: {skipped}  ERRORED: {errored}")
    print("=" * 72)
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline eval harness for Adam's semantic candidate matching"
    )
    parser.add_argument(
        "--fixture",
        default=str(_FIXTURE_DEFAULT),
        help="Path to fixture JSON (default: eval/fixture.json)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-candidate score breakdowns",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failing pair",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path to write JSON results",
    )
    args = parser.parse_args()

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"Fixture not found: {fixture_path}")
        print("Run  python -m eval.generate_fixture  to create it.")
        sys.exit(2)

    pairs: list[dict] = json.loads(fixture_path.read_text())
    # Strip comment-only entries.
    pairs = [p for p in pairs if "job_id" in p]

    if not pairs:
        print("Fixture is empty.  Run  python -m eval.generate_fixture  first.")
        sys.exit(2)

    print(f"Running eval on {len(pairs)} pair(s) from {fixture_path} …")
    print(f"Threshold override: 0.0 (both candidates always scored)")
    print()

    results: list[dict] = []
    for pair in pairs:
        r = _eval_pair(pair, verbose=args.verbose)
        results.append(r)

        # Immediate one-line progress.
        if r["skipped"]:
            print(f"  SKIP  {r.get('job_title') or r['job_id']}")
        elif r["passed"]:
            print(
                f"  PASS  {r.get('job_title') or r['job_id']:<40}  "
                f"good={_fmt_score(r['good_score'])}  bad={_fmt_score(r['bad_score'])}"
            )
        else:
            print(
                f"  FAIL  {r.get('job_title') or r['job_id']:<40}  "
                f"good={_fmt_score(r['good_score'])}  bad={_fmt_score(r['bad_score'])}"
                + (f"  [{r['error']}]" if r["error"] else "")
            )

        if args.fail_fast and r["passed"] is False and not r["skipped"]:
            print("\n  --fail-fast: stopping after first failure.")
            break

    _print_results(results)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"Results written to {out_path}")

    passed = sum(1 for r in results if r["passed"] is True)
    failed = sum(1 for r in results if r["passed"] is False and not r["skipped"])
    actionable = passed + failed

    if actionable == 0:
        sys.exit(2)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
