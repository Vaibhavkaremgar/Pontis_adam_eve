from __future__ import annotations

import logging
import sys
from pathlib import Path

from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal
from app.models.entities import JobEntity
from app.services.job_queue_service import cleanup_orphaned_queue_entries

logger = logging.getLogger(__name__)


def main() -> int:
    with SessionLocal() as db:
        job_count = int(db.scalar(select(func.count()).select_from(JobEntity)) or 0)
        logger.info("queue_cleanup_db_snapshot jobs=%s", job_count)

    result = cleanup_orphaned_queue_entries()
    print(
        "Queue cleanup completed: "
        f"removed={result['removed']} "
        f"dead_removed={result['dead_removed']} "
        f"processing_removed={result['processing_removed']} "
        f"ready_removed={result['ready_removed']} "
        f"delayed_removed={result['delayed_removed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
