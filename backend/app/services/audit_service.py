from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.entities import AuditEventEntity

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def record_audit_event(
    *,
    db: Session | None = None,
    actor_id: str | None = None,
    actor_type: str = "user",
    action: str,
    entity_type: str,
    entity_id: str,
    metadata: dict | None = None,
    company_id: str | None = None,
    user_id: str | None = None,
    slack_user_id: str = "",
    action_type: str = "",
    payload: dict | None = None,
    ip_address: str = "",
    user_agent: str = "",
    request_id: str = "",
) -> AuditEventEntity:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        event = AuditEventEntity(
            id=str(uuid4()),
            actor_id=actor_id,
            company_id=(company_id or "").strip() or None,
            user_id=(user_id or actor_id or "").strip() or None,
            slack_user_id=(slack_user_id or "").strip(),
            actor_type=actor_type,
            action=action,
            action_type=(action_type or action or "").strip(),
            entity_type=entity_type,
            entity_id=entity_id,
            event_metadata=dict(metadata or {}),
            payload=dict(payload or metadata or {}),
            ip_address=(ip_address or "")[:64],
            user_agent=(user_agent or "")[:512],
            request_id=(request_id or "")[:128],
            created_at=_utcnow(),
        )
        session.add(event)
        session.flush()
        if owns_session:
            session.commit()
        logger.info(
            "audit_event_recorded action=%s entity_type=%s entity_id=%s actor_id=%s",
            action,
            entity_type,
            entity_id,
            actor_id or "",
        )
        return event
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()
