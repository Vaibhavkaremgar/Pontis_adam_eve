"""run_job_posting_discovery.py — Run the LinkedIn Job Posting discovery.

DISCOVERY ONLY. Never publishes, never saves, never modifies LinkedIn data.

Usage (from backend/):
    python -m scripts.run_job_posting_discovery --account-id linkedin-dev-account
    python -m scripts.run_job_posting_discovery --account-id linkedin-dev-account --max-steps 8
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure backend/ is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.linkedin.job_posting import JobPostingDiscovery, DiscoveryStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_job_posting_discovery")


async def main(account_id: str, max_steps: int) -> int:
    logger.info("Starting job posting discovery account_id=%s max_steps=%d", account_id, max_steps)

    discovery = JobPostingDiscovery(account_id=account_id, max_steps=max_steps)
    result = await discovery.run()

    logger.info("Discovery complete status=%s steps=%d fields=%d duration_ms=%d",
                result.status, result.total_steps, len(result.all_fields), result.duration_ms)
    logger.info("Output dir: %s", result.output_dir)

    if result.overview_json_path:
        logger.info("Overview: %s", result.overview_json_path)
    if result.workflow_json_path:
        logger.info("Workflow: %s", result.workflow_json_path)

    if result.errors:
        logger.warning("Errors encountered:")
        for e in result.errors:
            logger.warning("  %s", e)

    if result.warnings:
        for w in result.warnings:
            logger.warning("  %s", w)

    # Print a compact summary to stdout
    summary = {
        "status": result.status,
        "total_steps": result.total_steps,
        "total_fields": len(result.all_fields),
        "required_fields": result.required_fields,
        "rich_text_fields": result.rich_text_fields,
        "dropdown_fields": result.dropdown_fields,
        "has_draft_support": result.has_draft_support,
        "has_review_page": result.has_review_page,
        "output_dir": result.output_dir,
    }
    print(json.dumps(summary, indent=2))

    return 0 if result.status in (DiscoveryStatus.OK, DiscoveryStatus.PARTIAL) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn Job Posting Discovery (read-only)")
    parser.add_argument("--account-id", required=True, help="LinkedIn browser profile account ID")
    parser.add_argument("--max-steps", type=int, default=10, help="Maximum wizard steps to walk")
    args = parser.parse_args()

    sys.exit(asyncio.run(main(args.account_id, args.max_steps)))
