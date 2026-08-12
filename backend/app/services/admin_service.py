from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.core.security import is_super_admin_role
from app.db.admin_repositories import AdminRepository
from app.schemas.admin import AgencyCreateRequest, AgencyUpdateRequest, UserCreateRequest, UserUpdateRequest
from app.services.audit_service import record_audit_event
from app.utils.exceptions import APIError

logger = logging.getLogger(__name__)


def get_admin_dashboard(*, db: Session) -> dict[str, int]:
    return AdminRepository(db).dashboard_summary()


def list_admin_agencies(*, db: Session, search: str = "", status: str = "", page: int = 1, page_size: int = 20) -> dict[str, object]:
    return AdminRepository(db).list_agencies(search=search, status=status, page=page, page_size=page_size)


def create_admin_agency(*, db: Session, actor_id: str, payload: AgencyCreateRequest) -> dict[str, object]:
    row = AdminRepository(db).create_agency(name=payload.name)
    record_audit_event(
        db=db,
        actor_id=actor_id,
        action="admin_agency_created",
        entity_type="agency",
        entity_id=str(row.id),
        metadata={"name": row.name, "slug": row.slug},
    )
    db.commit()
    logger.info("agency created agency_id=%s name=%s", row.id, row.name)
    # Agency creation is durable before browser startup. A browser failure
    # therefore cannot roll back the agency; onboarding marks the row failed.
    try:
        from app.linkedin.services.onboarding_service import start_linkedin_onboarding

        logger.info("starting LinkedIn onboarding agency_id=%s", row.id)
        profile_path = start_linkedin_onboarding(str(row.id))
    except Exception:
        logger.exception("LinkedIn onboarding failed to start agency_id=%s", row.id)
        profile_path = None
        row.linkedin_connected = False
        row.linkedin_connection_status = "failed"
        db.commit()
    return {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "status": "Active" if row.is_active else "Inactive",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "linkedin_profile_path": profile_path or row.linkedin_profile_path,
        "linkedin_connected": bool(row.linkedin_connected),
        "linkedin_connection_status": row.linkedin_connection_status,
    }


def update_admin_agency(*, db: Session, actor_id: str, agency_id: str, payload: AgencyUpdateRequest) -> dict[str, object]:
    row = AdminRepository(db).update_agency(agency_id=agency_id, name=payload.name, is_active=payload.is_active)
    record_audit_event(
        db=db,
        actor_id=actor_id,
        action="admin_agency_updated",
        entity_type="agency",
        entity_id=str(row.id),
        metadata={"name": row.name, "slug": row.slug, "isActive": row.is_active},
    )
    db.commit()
    return {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "status": "Active" if row.is_active else "Inactive",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def set_admin_agency_status(*, db: Session, actor_id: str, agency_id: str, is_active: bool) -> dict[str, object]:
    return update_admin_agency(db=db, actor_id=actor_id, agency_id=agency_id, payload=AgencyUpdateRequest(is_active=is_active))


def delete_admin_agency(*, db: Session, actor_id: str, agency_id: str) -> dict[str, object]:
    repository = AdminRepository(db)
    row = repository.get_agency(agency_id)
    if row is None:
        raise APIError("Agency not found", status_code=404)
    deleted_id = str(row.id)
    deleted_name = str(row.name or "")
    repository.delete_agency(agency_id=agency_id)
    db.commit()
    logger.info("agency deleted agency_id=%s name=%s actor_id=%s", deleted_id, deleted_name, actor_id)
    return {"id": deleted_id, "name": deleted_name, "deleted": True}


def list_admin_users(
    *,
    db: Session,
    search: str = "",
    agency_id: str = "",
    role: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    return AdminRepository(db).list_users(search=search, agency_id=agency_id, role=role, status=status, page=page, page_size=page_size)


def _allowed_user_payload(row: object) -> dict[str, object]:
    note = str(getattr(row, "note", "") or "").strip()
    metadata: dict[str, object] = {}
    if note:
        try:
            parsed = json.loads(note)
            if isinstance(parsed, dict):
                metadata = parsed
        except json.JSONDecodeError:
            metadata = {}
    return {
        "id": str(getattr(row, "id", "") or ""),
        "name": str(metadata.get("name") or ""),
        "email": str(getattr(row, "email", "") or ""),
        "agency_id": str(getattr(row, "agency_id", "") or ""),
        "role": str(metadata.get("role") or "AGENCY_USER"),
        "status": "Active" if getattr(row, "is_active", True) else "Inactive",
        "created_at": getattr(row, "created_at", None).isoformat() if getattr(row, "created_at", None) else None,
        "updated_at": None,
    }


def create_admin_user(*, db: Session, actor_id: str, payload: UserCreateRequest) -> dict[str, object]:
    row = AdminRepository(db).upsert_user(
        agency_id=payload.agency_id,
        name=payload.name,
        email=payload.email,
        role=payload.role,
        is_active=payload.is_active,
        added_by=actor_id,
    )
    record_audit_event(
        db=db,
        actor_id=actor_id,
        action="admin_user_created",
        entity_type="allowed_user",
        entity_id=str(row.id),
        metadata={"email": row.email, "agencyId": row.agency_id, "role": payload.role, "isActive": row.is_active},
    )
    db.commit()
    return _allowed_user_payload(row)


def update_admin_user(*, db: Session, actor_id: str, user_id: str, payload: UserUpdateRequest) -> dict[str, object]:
    row = AdminRepository(db).update_user(
        user_id=user_id,
        agency_id=payload.agency_id,
        name=payload.name,
        email=payload.email,
        role=payload.role,
        is_active=payload.is_active,
    )
    record_audit_event(
        db=db,
        actor_id=actor_id,
        action="admin_user_updated",
        entity_type="allowed_user",
        entity_id=str(row["id"]),
        metadata={"email": row["email"], "agencyId": row["agency_id"], "role": row["role"], "isActive": row["status"] == "Active"},
    )
    db.commit()
    return row


def admin_assert_actor(user: dict[str, str]) -> dict[str, str]:
    if not is_super_admin_role((user or {}).get("role")):
        raise APIError("Forbidden", status_code=403)
    return user
