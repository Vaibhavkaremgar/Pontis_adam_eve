from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import EMBEDDING_MODEL_NAME, EMBEDDING_VERSION, VECTOR_SIZE
from app.db.session import SessionLocal
from app.models.entities import EmbeddingVersionRegistryEntity

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_embedding_version_registry(db: Session | None = None) -> EmbeddingVersionRegistryEntity:  # noqa: C901
    owns_session = db is None
    session = db or SessionLocal()
    try:
        row = session.scalar(
            select(EmbeddingVersionRegistryEntity).where(
                EmbeddingVersionRegistryEntity.embedding_version == EMBEDDING_VERSION
            )
        )
        if row:
            if row.status != "active":
                row.status = "active"
                row.activated_at = row.activated_at or _utcnow()
                row.updated_at = _utcnow()
                session.flush()
                if owns_session:
                    session.commit()
            check_embedding_model_drift(session)
            return row

        row = EmbeddingVersionRegistryEntity(
            id=str(uuid4()),
            embedding_version=EMBEDDING_VERSION,
            status="active",
            vector_size=VECTOR_SIZE,
            details={"source": "bootstrap", "model_name": EMBEDDING_MODEL_NAME},
            activated_at=_utcnow(),
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(row)
        session.flush()
        if owns_session:
            session.commit()
        logger.info(
            "embedding_version_registered embedding_version=%s vector_size=%s model_name=%s",
            EMBEDDING_VERSION,
            VECTOR_SIZE,
            EMBEDDING_MODEL_NAME,
        )
        return row
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def check_embedding_model_drift(db: Session | None = None) -> None:
    """Warn at startup if the active registry row was built with a different model.

    This catches the case where EMBEDDING_MODEL_NAME is changed in config without
    bumping EMBEDDING_VERSION, which would silently mix incompatible vector geometries
    in the same Qdrant collection.

    # TODO (option a): replace this warning with a derived EMBEDDING_VERSION that is
    # computed as hash(EMBEDDING_MODEL_NAME + str(actual_dimension)) so the two values
    # can never drift. Requires a full re-index migration to invalidate existing rows
    # whose embedding_version was set under the old naming scheme.
    """
    owns_session = db is None
    session = db or SessionLocal()
    try:
        row = session.scalar(
            select(EmbeddingVersionRegistryEntity)
            .where(
                EmbeddingVersionRegistryEntity.embedding_version == EMBEDDING_VERSION,
                EmbeddingVersionRegistryEntity.status == "active",
            )
        )
        if row is None:
            return
        recorded_model = (row.details or {}).get("model_name", "") if isinstance(row.details, dict) else ""
        if not recorded_model:
            # Row predates model-name tracking; backfill silently so future restarts
            # have a baseline to compare against.
            updated_details = dict(row.details or {})
            updated_details["model_name"] = EMBEDDING_MODEL_NAME
            row.details = updated_details
            row.updated_at = _utcnow()
            session.flush()
            if owns_session:
                session.commit()
            logger.info(
                "embedding_registry_model_name_backfilled embedding_version=%s model_name=%s",
                EMBEDDING_VERSION,
                EMBEDDING_MODEL_NAME,
            )
            return
        if recorded_model != EMBEDDING_MODEL_NAME:
            logger.warning(
                "EMBEDDING_MODEL_DRIFT DETECTED: embedding_version=%s was indexed with "
                "model_name=%r but the current config has EMBEDDING_MODEL_NAME=%r. "
                "Cosine similarity between old and new vectors is meaningless. "
                "Bump EMBEDDING_VERSION and re-index all candidates, or revert "
                "EMBEDDING_MODEL_NAME to %r.",
                EMBEDDING_VERSION,
                recorded_model,
                EMBEDDING_MODEL_NAME,
                recorded_model,
            )
    finally:
        if owns_session:
            session.close()


def get_active_embedding_version(db: Session | None = None) -> str:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        row = session.scalar(
            select(EmbeddingVersionRegistryEntity)
            .where(EmbeddingVersionRegistryEntity.status == "active")
            .order_by(EmbeddingVersionRegistryEntity.updated_at.desc())
        )
        if row and row.embedding_version:
            return row.embedding_version
        ensure_embedding_version_registry(session)
        return EMBEDDING_VERSION
    finally:
        if owns_session:
            session.close()


def promote_embedding_version(
    db: Session,
    *,
    embedding_version: str,
    vector_size: int,
    details: dict | None = None,
) -> EmbeddingVersionRegistryEntity:
    now = _utcnow()
    active_rows = session_scalar_all(db, status="active")
    for row in active_rows:
        if row.embedding_version != embedding_version:
            row.status = "retired"
            row.retired_at = now
            row.updated_at = now
    row = db.scalar(
        select(EmbeddingVersionRegistryEntity).where(
            EmbeddingVersionRegistryEntity.embedding_version == embedding_version
        )
    )
    if row is None:
        row = EmbeddingVersionRegistryEntity(
            id=str(uuid4()),
            embedding_version=embedding_version,
            status="active",
            vector_size=vector_size,
            details=details or {},
            activated_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.status = "active"
        row.vector_size = vector_size
        row.details = details or row.details or {}
        row.activated_at = row.activated_at or now
        row.retired_at = None
        row.updated_at = now
    db.flush()
    db.commit()
    logger.info("embedding_version_promoted embedding_version=%s vector_size=%s", embedding_version, vector_size)
    return row


def session_scalar_all(db: Session, *, status: str | None = None) -> list[EmbeddingVersionRegistryEntity]:
    stmt = select(EmbeddingVersionRegistryEntity)
    if status:
        stmt = stmt.where(EmbeddingVersionRegistryEntity.status == status)
    return list(db.scalars(stmt).all())
