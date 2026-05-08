from __future__ import annotations

from app.core.config import STALE_DAYS


def refresh_stale_vectors(*, batch_size: int = 100, stale_days: int = STALE_DAYS) -> dict[str, int]:
    from app.services.candidate_refresh_service import refresh_candidates as _refresh_candidates

    return _refresh_candidates(batch_size=batch_size, stale_days=stale_days)

