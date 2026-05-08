from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.redis_service import get_redis

logger = logging.getLogger(__name__)

EVENT_STREAM_KEY = "pontis:events"
EVENT_STREAM_ARCHIVE_KEY = "pontis:event_archive"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)


def record_platform_event(
    *,
    event_type: str,
    source: str,
    payload: dict[str, Any] | None = None,
    db: Session | None = None,
    actor_id: str | None = None,
    entity_type: str = "",
    entity_id: str = "",
) -> dict[str, Any]:
    event = {
        "event_type": (event_type or "").strip(),
        "source": (source or "").strip(),
        "actor_id": (actor_id or "").strip(),
        "entity_type": (entity_type or "").strip(),
        "entity_id": (entity_id or "").strip(),
        "payload": dict(payload or {}),
        "created_at": _utcnow().isoformat(),
    }

    redis = get_redis()
    if redis is not None:
        try:
            redis.xadd(EVENT_STREAM_KEY, event, maxlen=5000, approximate=True)
        except Exception as exc:
            logger.warning("platform_event_stream_write_failed error=%s", str(exc))

    if db is not None:
        try:
            from app.services.audit_service import record_audit_event

            record_audit_event(
                db=db,
                actor_id=actor_id,
                action=event["event_type"] or "platform_event",
                entity_type=entity_type or "platform",
                entity_id=entity_id or source or "global",
                metadata={"source": source, **dict(payload or {})},
            )
        except Exception as exc:
            logger.warning("platform_event_audit_failed error=%s", str(exc))
    return event


def list_recent_platform_events(limit: int = 100) -> list[dict[str, Any]]:
    redis = get_redis()
    if redis is None:
        return []

    try:
        entries = redis.xrevrange(EVENT_STREAM_KEY, max="+", min="-", count=max(1, limit))
        events: list[dict[str, Any]] = []
        for event_id, fields in entries:
            payload = dict(fields or {})
            payload["stream_id"] = event_id
            events.append(payload)
        return events
    except Exception as exc:
        logger.warning("platform_event_stream_read_failed error=%s", str(exc))
        return []
