"""run_job_posting_dry_run.py — Phase 2.4 live dry-run validation.

Executes JobPostingWorker against a real LinkedIn account.

NEVER publishes.  NEVER saves a draft.  NEVER modifies LinkedIn data.

Usage (from backend/):
    python -m scripts.run_job_posting_dry_run --account-id linkedin-dev-account
    python -m scripts.run_job_posting_dry_run --account-id linkedin-dev-account --headless

The script:
  1. Builds a JobPostingSpec from CLI args (or sensible defaults).
  2. Runs JobPostingWorker.run() in dry_run mode.
  3. Prints a step-by-step execution report to stdout.
  4. Writes a machine-readable execution_report.json to debug_logs/.
  5. Exits 0 if review_reached, 1 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.linkedin.job_posting import JobPostingWorker, JobPostingSpec, WorkerStatus
from app.linkedin.playwright.browser_context import BrowserContextConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_job_posting_dry_run")

_REPORT_ROOT = Path(__file__).resolve().parents[1] / "debug_logs" / "job_posting_dry_run"

# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_separator(char: str = "─", width: int = 70) -> None:
    print(char * width)


def _print_report(result, spec: JobPostingSpec, run_ts: str) -> None:
    _print_separator("═")
    print("  PHASE 2.4 — JOB POSTING DRY RUN VALIDATION REPORT")
    _print_separator("═")
    print(f"  Run timestamp  : {run_ts}")
    print(f"  Account        : {spec.company!r} / spec.title={spec.title!r}")
    print(f"  dry_run        : {result.dry_run}   ← always True")
    print(f"  Status         : {result.status}")
    print(f"  Review reached : {result.review_reached}")
    print(f"  Duration       : {result.duration_ms} ms")
    print(f"  Steps completed: {len(result.completed_steps)}")
    _print_separator()

    # Per-step table
    print(f"  {'#':<4} {'Step label':<28} {'Filled':<22} {'Skipped':<18} {'Verif':<6} {'Nav':<5} {'ms':<6} {'Errors'}")
    _print_separator()
    for d in result.diagnostics:
        filled  = ",".join(d.fields_filled)  or "—"
        skipped = ",".join(d.fields_skipped) or "—"
        verif   = "✓" if d.verification_passed else "✗"
        nav     = "✓" if d.navigation_succeeded else "✗"
        errs    = "; ".join(d.validation_errors) or "—"
        print(
            f"  {d.step_index:<4} {(d.step_label or 'unknown')[:27]:<28} "
            f"{filled[:21]:<22} {skipped[:17]:<18} {verif:<6} {nav:<5} "
            f"{d.elapsed_ms:<6} {errs[:40]}"
        )
        if d.screenshot_path:
            print(f"       screenshot → {d.screenshot_path}")
        if d.html_path:
            print(f"       html       → {d.html_path}")
        if d.notes:
            print(f"       notes      : {d.notes}")

    _print_separator()

    # Errors / warnings
    if result.errors:
        print("  ERRORS:")
        for e in result.errors:
            print(f"    ✗ {e}")
    if result.warnings:
        print("  WARNINGS:")
        for w in result.warnings:
            print(f"    ⚠ {w}")

    _print_separator("═")

    # Final verdicts
    print(f"  ① Review page reached      : {'YES ✓' if result.review_reached else 'NO  ✗'}")
    print(f"  ② Publish ever attempted   : NO  ✓  (dry_run enforced)")
    print(f"  ③ Draft ever saved         : NO  ✓  (dry_run enforced)")

    _print_separator("═")


# ---------------------------------------------------------------------------
# Discrepancy analyser
# ---------------------------------------------------------------------------

def _analyse_discrepancies(result) -> list[str]:
    """Compare live execution against the Phase 2.2 discovery model."""
    issues: list[str] = []

    for d in result.diagnostics:
        if d.notes == "no_filler_matched":
            issues.append(
                f"Step {d.step_index} ({d.step_label!r}): no filler matched — "
                "live step label differs from discovery model keywords"
            )
        if d.fields_skipped:
            issues.append(
                f"Step {d.step_index} ({d.step_label!r}): fields skipped "
                f"({d.fields_skipped}) — selectors may need updating"
            )
        if d.validation_errors:
            issues.append(
                f"Step {d.step_index} ({d.step_label!r}): validation errors "
                f"({d.validation_errors}) — field values or selectors incorrect"
            )
        if not d.navigation_succeeded and d.notes != "no_safe_next_button":
            issues.append(
                f"Step {d.step_index} ({d.step_label!r}): navigation failed — "
                "Next button selector may have changed"
            )

    if not result.review_reached:
        issues.append(
            "Review page was NOT reached — wizard may have additional steps "
            "not covered by the current filler map, or a selector mismatch "
            "prevented navigation"
        )

    return issues


def _changes_before_publish(result, discrepancies: list[str]) -> list[str]:
    """Return a checklist of items that must be resolved before enabling Publish."""
    items: list[str] = []

    if not result.review_reached:
        items.append("BLOCKER: Review page must be reached reliably before Publish is safe")

    for d in result.diagnostics:
        if d.fields_skipped:
            items.append(
                f"Fix selectors for skipped fields at step {d.step_index}: "
                f"{d.fields_skipped}"
            )
        if d.validation_errors:
            items.append(
                f"Resolve validation errors at step {d.step_index}: "
                f"{d.validation_errors}"
            )

    if discrepancies:
        items.append(
            f"{len(discrepancies)} discrepancy(ies) between live UI and discovery model "
            "— see discrepancies section of report"
        )

    if not items:
        items.append("No blockers found — worker is ready for Publish enablement")

    return items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> int:
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    description = args.description
    if args.description_file:
        description_path = Path(args.description_file)
        description = description_path.read_text(encoding="utf-8")
        logger.info("job_description_loaded_from_file path=%s", description_path)
        logger.info("description_character_count=%d", len(description))

    spec = JobPostingSpec(
        title=args.title,
        company=args.company,
        workplace_type=args.workplace_type,
        location=args.location,
        job_type=args.job_type,
        experience_level=args.experience_level,
        description=description,
        skills=args.skills,
        application_method=args.application_method,
        application_url=args.application_url,
        dry_run=True,
    )

    missing = spec.missing_required()
    if missing:
        logger.error("Spec is missing required fields: %s", missing)
        logger.error("Use --title, --company, --workplace-type, --location, --job-type, --description")
        return 1

    config = BrowserContextConfig(headless=args.headless)

    logger.info("Starting dry-run validation account_id=%s", args.account_id)
    logger.info("Spec: title=%r company=%r location=%r", spec.title, spec.company, spec.location)

    worker = JobPostingWorker(
        account_id=args.account_id,
        spec=spec,
        config=config,
        lock_timeout=90.0,
    )

    wall_start = time.monotonic()
    result = await worker.run()
    wall_ms = int((time.monotonic() - wall_start) * 1000)

    # Print human-readable report
    _print_report(result, spec, run_ts)

    # Discrepancy analysis
    discrepancies = _analyse_discrepancies(result)
    changes_needed = _changes_before_publish(result, discrepancies)

    if discrepancies:
        print("\n  DISCREPANCIES vs DISCOVERY MODEL:")
        for i, d in enumerate(discrepancies, 1):
            print(f"    {i}. {d}")
        print()

    print("  CHANGES REQUIRED BEFORE ENABLING PUBLISH:")
    for i, c in enumerate(changes_needed, 1):
        print(f"    {i}. {c}")
    _print_separator("═")

    # Write machine-readable report
    _REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = _REPORT_ROOT / f"{run_ts}_execution_report.json"
    report = {
        "run_ts": run_ts,
        "account_id": args.account_id,
        "wall_ms": wall_ms,
        "spec": {
            "title": spec.title,
            "company": spec.company,
            "workplace_type": spec.workplace_type,
            "location": spec.location,
            "job_type": spec.job_type,
            "experience_level": spec.experience_level,
            "description_len": len(spec.description),
            "skills": spec.skills,
            "application_method": spec.application_method,
            "application_url": spec.application_url,
        },
        "result": {
            "status": result.status,
            "dry_run": result.dry_run,
            "review_reached": result.review_reached,
            "current_step": result.current_step,
            "current_step_label": result.current_step_label,
            "completed_steps": result.completed_steps,
            "duration_ms": result.duration_ms,
            "errors": result.errors,
            "warnings": result.warnings,
            "diagnostics": [asdict(d) for d in result.diagnostics],
        },
        "discrepancies": discrepancies,
        "changes_before_publish": changes_needed,
        "publish_attempted": False,   # always False — enforced by dry_run
        "draft_saved": False,         # always False — enforced by dry_run
    }
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info("Execution report written: %s", report_path)

    return 0 if result.review_reached else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Phase 2.4 — LinkedIn Job Posting live dry-run validation"
    )
    parser.add_argument("--account-id", default="linkedin-dev-account",
                        help="LinkedIn browser profile account ID")
    parser.add_argument("--headless", action="store_true", default=False,
                        help="Run browser in headless mode")

    # Spec fields — all have defaults so the script runs out of the box
    parser.add_argument("--title",           default="Senior Software Engineer")
    parser.add_argument("--company",         default="")
    parser.add_argument("--workplace-type",  default="Remote",
                        dest="workplace_type")
    parser.add_argument("--location",        default="United States")
    parser.add_argument("--job-type",        default="Full-time",
                        dest="job_type")
    parser.add_argument("--experience-level", default="Mid-Senior level",
                        dest="experience_level")
    parser.add_argument("--description",     default=(
        "We are looking for a Senior Software Engineer to join our team. "
        "You will design, build, and maintain efficient, reusable, and reliable code. "
        "Requirements: 5+ years of experience, strong Python skills, AWS experience."
    ))
    parser.add_argument(
        "--description-file",
        default=None,
        dest="description_file",
        help="Read the complete job description from a UTF-8 text file",
    )
    parser.add_argument("--skills",          nargs="*", default=["Python", "AWS", "PostgreSQL"])
    parser.add_argument("--application-method", default="Through LinkedIn",
                        dest="application_method")
    parser.add_argument(
        "--application-url",
        default="https://eve.pontis.one/?job_id=73a0e8c7-a7ff-4ac2-abde-60d8645218e1",
        dest="application_url",
        help="External application URL used by the Manage applicants review step",
    )

    args = parser.parse_args()

    # --company is required for the wizard but has no safe default
    if not args.company:
        parser.error(
            "--company is required (must match a company page you admin on LinkedIn)\n"
            "Example: --company 'Acme Corp'"
        )

    sys.exit(asyncio.run(main(args)))
