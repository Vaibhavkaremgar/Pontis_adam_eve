from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import SUPER_ADMIN_ROLE, normalize_app_role
from app.models.entities import AllowedUserEntity, CandidateProfileEntity, CompanyEntity, JobEntity, UserEntity
from app.utils.exceptions import APIError


def _normalized_text(value: object) -> str:
    return str(value or "").strip()


def _pagination(page: int, page_size: int, total: int) -> dict[str, int]:
    safe_page_size = max(1, int(page_size or 1))
    total_pages = max(1, ceil(total / safe_page_size)) if total else 0
    safe_page = max(1, int(page or 1))
    return {
        "page": safe_page,
        "page_size": safe_page_size,
        "total": total,
        "total_pages": total_pages,
    }


def _agency_status(row: CompanyEntity) -> str:
    return "Active" if bool(getattr(row, "is_active", True)) else "Inactive"


def _user_status(row: UserEntity) -> str:
    return "Active" if getattr(row, "is_active", True) is not False else "Inactive"


def _allowed_user_note_data(note: str | None) -> dict[str, object]:
    raw_note = _normalized_text(note)
    if not raw_note:
        return {}
    try:
        parsed = json.loads(raw_note)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _allowed_user_note_json(*, name: str = "", agency_id: str = "", role: str = "") -> str:
    payload: dict[str, object] = {}
    normalized_name = _normalized_text(name)
    normalized_agency_id = _normalized_text(agency_id)
    normalized_role = normalize_app_role(role) if role else ""
    if normalized_name:
        payload["name"] = normalized_name
    if normalized_agency_id:
        payload["agencyId"] = normalized_agency_id
    if normalized_role:
        payload["role"] = normalized_role
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) if payload else ""


def _allowed_user_agency_id(allowed: AllowedUserEntity | None) -> str:
    if not allowed:
        return ""
    allowed_agency_id = _normalized_text(getattr(allowed, "agency_id", "") or "")
    if allowed_agency_id:
        return allowed_agency_id
    note_data = _allowed_user_note_data(getattr(allowed, "note", None))
    return _normalized_text(note_data.get("agencyId") or note_data.get("agency_id") or "")


@dataclass
class AdminAgencyRow:
    row: CompanyEntity
    total_users: int
    total_jobs: int
    total_candidates: int


class AdminRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _allowed_user_rows(self) -> list[AllowedUserEntity]:
        return list(self.db.scalars(select(AllowedUserEntity)).all())

    def _user_rows_by_email(self) -> dict[str, UserEntity]:
        rows = self.db.scalars(select(UserEntity)).all()
        return {str(row.email or "").strip().lower(): row for row in rows if str(row.email or "").strip()}

    def _agency_names_by_id(self, agency_ids: set[str]) -> dict[str, str]:
        normalized_ids = {str(agency_id).strip() for agency_id in agency_ids if str(agency_id).strip()}
        if not normalized_ids:
            return {}
        rows = self.db.scalars(select(CompanyEntity).where(CompanyEntity.id.in_(sorted(normalized_ids)))).all()
        return {str(row.id): str(row.name or "") for row in rows}

    def _user_summary_from_sources(
        self,
        *,
        allowed: AllowedUserEntity | None,
        user: UserEntity | None,
        agency_name: str = "",
    ) -> dict[str, object]:
        allowed_note = _allowed_user_note_data(getattr(allowed, "note", None))
        allowed_email = str(getattr(allowed, "email", "") or "").strip().lower()
        user_email = str(getattr(user, "email", "") or "").strip().lower()
        email = allowed_email or user_email
        name = str(
            (getattr(user, "full_name", "") or allowed_note.get("name") or allowed_note.get("fullName") or "")
        ).strip()
        agency_id = str(getattr(user, "agency_id", "") or _allowed_user_agency_id(allowed)).strip()
        role = normalize_app_role(
            str(getattr(user, "role", "") or allowed_note.get("role") or allowed_note.get("userRole") or "AGENCY_USER")
        )
        if role == SUPER_ADMIN_ROLE and not agency_id:
            agency_id = ""
        created_at = getattr(allowed, "created_at", None) or getattr(user, "created_at", None)
        updated_at = getattr(user, "updated_at", None) or getattr(allowed, "created_at", None)
        allowed_active = True if allowed is None else bool(getattr(allowed, "is_active", True))
        user_active = True if user is None else getattr(user, "is_active", True) is not False
        status = "Active" if allowed_active and user_active else "Inactive"
        return {
            "id": str(getattr(allowed, "id", None) or getattr(user, "id", "") or ""),
            "name": name,
            "email": email,
            "agency_id": agency_id,
            "agency_name": agency_name,
            "role": role,
            "status": status,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }

    def _find_allowed_user(self, user_id: str) -> AllowedUserEntity | None:
        normalized = _normalized_text(user_id)
        if not normalized:
            return None
        allowed = self.db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.id == normalized))
        if allowed is not None:
            return allowed
        user = self.db.scalar(select(UserEntity).where(UserEntity.id == normalized))
        if user and user.email:
            return self.db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == str(user.email).strip().lower()))
        return None

    def dashboard_summary(self) -> dict[str, int]:
        allowed_rows = self._allowed_user_rows()
        user_rows = self._user_rows_by_email()
        summaries = [
            self._user_summary_from_sources(
                allowed=allowed,
                user=user_rows.get(str(allowed.email or "").strip().lower()),
            )
            for allowed in allowed_rows
        ]
        user_only_rows = [
            self._user_summary_from_sources(allowed=None, user=user)
            for email, user in user_rows.items()
            if not self.db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == email))
        ]
        merged = summaries + user_only_rows
        active_users = sum(1 for row in merged if row["status"] == "Active")
        inactive_users = sum(1 for row in merged if row["status"] == "Inactive")
        return {
            "total_agencies": int(self.db.scalar(select(func.count()).select_from(CompanyEntity)) or 0),
            "total_users": int(len(merged)),
            "active_users": int(active_users),
            "inactive_users": int(inactive_users),
            "total_jobs": int(self.db.scalar(select(func.count()).select_from(JobEntity)) or 0),
            "total_candidates": int(self.db.scalar(select(func.count()).select_from(CandidateProfileEntity)) or 0),
        }

    def get_agency(self, agency_id: str) -> CompanyEntity | None:
        return self.db.scalar(select(CompanyEntity).where(CompanyEntity.id == agency_id))

    def list_agencies(
        self,
        *,
        search: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        normalized_search = _normalized_text(search).lower()
        normalized_status = _normalized_text(status).lower()

        user_counts = (
            select(UserEntity.agency_id.label("agency_id"), func.count().label("total_users"))
            .group_by(UserEntity.agency_id)
            .subquery()
        )
        job_counts = (
            select(JobEntity.agency_id.label("agency_id"), func.count().label("total_jobs"))
            .group_by(JobEntity.agency_id)
            .subquery()
        )
        candidate_counts = (
            select(CandidateProfileEntity.agency_id.label("agency_id"), func.count().label("total_candidates"))
            .group_by(CandidateProfileEntity.agency_id)
            .subquery()
        )

        stmt = (
            select(
                CompanyEntity,
                func.coalesce(user_counts.c.total_users, 0).label("total_users"),
                func.coalesce(job_counts.c.total_jobs, 0).label("total_jobs"),
                func.coalesce(candidate_counts.c.total_candidates, 0).label("total_candidates"),
            )
            .outerjoin(user_counts, user_counts.c.agency_id == CompanyEntity.id)
            .outerjoin(job_counts, job_counts.c.agency_id == CompanyEntity.id)
            .outerjoin(candidate_counts, candidate_counts.c.agency_id == CompanyEntity.id)
        )
        if normalized_search:
            pattern = f"%{normalized_search}%"
            stmt = stmt.where(or_(CompanyEntity.name.ilike(pattern), CompanyEntity.slug.ilike(pattern), CompanyEntity.id.ilike(pattern)))
        if normalized_status == "active":
            stmt = stmt.where(CompanyEntity.is_active.is_(True))
        elif normalized_status == "inactive":
            stmt = stmt.where(CompanyEntity.is_active.is_(False))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int(self.db.scalar(count_stmt) or 0)
        offset = max(0, (max(1, page) - 1) * max(1, page_size))
        rows = self.db.execute(
            stmt.order_by(CompanyEntity.created_at.desc(), CompanyEntity.name.asc()).offset(offset).limit(max(1, page_size))
        ).all()
        items: list[dict[str, object]] = []
        for row, total_users, total_jobs, total_candidates in rows:
            items.append(
                {
                    "id": str(row.id),
                    "name": str(row.name or ""),
                    "slug": str(row.slug or ""),
                    "status": _agency_status(row),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "total_users": int(total_users or 0),
                    "total_jobs": int(total_jobs or 0),
                    "total_candidates": int(total_candidates or 0),
                    "linkedin_email": row.linkedin_email,
                    "linkedin_connected": bool(row.linkedin_connected),
                    "linkedin_connected_at": row.linkedin_connected_at.isoformat() if row.linkedin_connected_at else None,
                    "linkedin_last_verified_at": row.linkedin_last_verified_at.isoformat() if row.linkedin_last_verified_at else None,
                    "linkedin_profile_path": row.linkedin_profile_path,
                    "linkedin_connection_status": row.linkedin_connection_status,
                }
            )
        return {"items": items, "pagination": _pagination(page, page_size, total)}

    def create_agency(self, *, name: str) -> CompanyEntity:
        normalized_name = _normalized_text(name)
        if not normalized_name:
            raise APIError("Agency name is required", status_code=400)
        slug_base = "".join(ch.lower() if ch.isalnum() else "-" for ch in normalized_name).strip("-") or "agency"
        slug = slug_base
        suffix = 1
        while self.db.scalar(select(CompanyEntity).where(CompanyEntity.slug == slug)):
            suffix += 1
            slug = f"{slug_base}-{suffix}"
        row = CompanyEntity(
            id=str(uuid4()),
            name=normalized_name,
            slug=slug,
            is_active=True,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def update_agency(self, *, agency_id: str, name: str | None = None, is_active: bool | None = None) -> CompanyEntity:
        row = self.get_agency(agency_id)
        if not row:
            raise APIError("Agency not found", status_code=404)
        if name is not None:
            normalized_name = _normalized_text(name)
            if not normalized_name:
                raise APIError("Agency name is required", status_code=400)
            row.name = normalized_name
            slug_base = "".join(ch.lower() if ch.isalnum() else "-" for ch in normalized_name).strip("-") or "agency"
            slug = slug_base
            suffix = 1
            while self.db.scalar(select(CompanyEntity).where(CompanyEntity.slug == slug, CompanyEntity.id != agency_id)):
                suffix += 1
                slug = f"{slug_base}-{suffix}"
            row.slug = slug
        if is_active is not None:
            row.is_active = bool(is_active)
        row.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return row

    def list_users(
        self,
        *,
        search: str = "",
        agency_id: str = "",
        role: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        normalized_search = _normalized_text(search).lower()
        normalized_agency_id = _normalized_text(agency_id)
        normalized_role = normalize_app_role(role) if role else ""
        normalized_status = _normalized_text(status).lower()

        allowed_rows = self._allowed_user_rows()
        user_rows = self._user_rows_by_email()
        agency_ids = {
            str(getattr(user, "agency_id", "") or "").strip()
            for user in user_rows.values()
            if str(getattr(user, "agency_id", "") or "").strip()
        }
        for allowed in allowed_rows:
            allowed_agency_id = _allowed_user_agency_id(allowed)
            if allowed_agency_id:
                agency_ids.add(allowed_agency_id)
        agency_names = self._agency_names_by_id(agency_ids)

        merged: list[dict[str, object]] = []
        seen_emails: set[str] = set()
        for allowed in allowed_rows:
            user = user_rows.get(str(allowed.email or "").strip().lower())
            summary = self._user_summary_from_sources(
                allowed=allowed,
                user=user,
                agency_name=agency_names.get(
                    str((getattr(user, "agency_id", "") or _allowed_user_agency_id(allowed) or "").strip()),
                    "",
                ),
            )
            seen_emails.add(str(summary["email"]).strip().lower())
            merged.append(summary)

        for email, user in user_rows.items():
            if email in seen_emails:
                continue
            summary = self._user_summary_from_sources(
                allowed=None,
                user=user,
                agency_name=agency_names.get(str(getattr(user, "agency_id", "") or "").strip(), ""),
            )
            merged.append(summary)

        def _matches(item: dict[str, object]) -> bool:
            if normalized_search:
                haystack = " ".join(
                    str(item.get(key) or "")
                    for key in ("name", "email", "agency_id", "agency_name", "role", "status")
                ).lower()
                if normalized_search not in haystack:
                    return False
            if normalized_agency_id and str(item.get("agency_id") or "") != normalized_agency_id:
                return False
            if normalized_role and normalize_app_role(str(item.get("role") or "")) != normalized_role:
                return False
            if normalized_status == "active" and str(item.get("status") or "") != "Active":
                return False
            if normalized_status == "inactive" and str(item.get("status") or "") != "Inactive":
                return False
            return True

        filtered = [item for item in merged if _matches(item)]
        filtered.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("name") or "").lower(),
                str(item.get("email") or "").lower(),
            ),
            reverse=True,
        )
        total = len(filtered)
        safe_page = max(1, page)
        safe_page_size = max(1, page_size)
        offset = (safe_page - 1) * safe_page_size
        items = filtered[offset : offset + safe_page_size]
        return {"items": items, "pagination": _pagination(page, page_size, total)}

    def upsert_user(
        self,
        *,
        agency_id: str,
        name: str,
        email: str,
        role: str = "AGENCY_USER",
        is_active: bool = True,
        added_by: str | None = None,
    ) -> AllowedUserEntity:
        normalized_email = _normalized_text(email).lower()
        if not normalized_email:
            raise APIError("Email is required", status_code=400)
        agency = self.get_agency(agency_id)
        if not agency:
            raise APIError("Agency not found", status_code=404)
        normalized_role = normalize_app_role(role)
        allowed = self.db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == normalized_email))
        if not allowed:
            allowed = AllowedUserEntity(id=uuid4(), email=normalized_email, is_active=True)
            self.db.add(allowed)
        allowed.email = normalized_email
        allowed.is_active = bool(is_active)
        allowed.agency_id = None if normalized_role == SUPER_ADMIN_ROLE else str(agency.id)
        allowed.note = _allowed_user_note_json(name=name, agency_id=allowed.agency_id or "", role=normalized_role)
        if added_by:
            try:
                allowed.added_by = UUID(str(added_by))
            except (TypeError, ValueError):
                allowed.added_by = None
        self.db.flush()
        return allowed

    def update_user(
        self,
        *,
        user_id: str,
        agency_id: str | None = None,
        name: str | None = None,
        email: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, object]:
        allowed = self._find_allowed_user(user_id)
        user = self.db.scalar(select(UserEntity).where(UserEntity.id == _normalized_text(user_id)))
        if not user and allowed and allowed.email:
            user = self.db.scalar(select(UserEntity).where(UserEntity.email == allowed.email))
        if not allowed and user and user.email:
            allowed = self.db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == str(user.email).strip().lower()))
            if not allowed:
                allowed = AllowedUserEntity(id=uuid4(), email=str(user.email).strip().lower(), is_active=bool(user.is_active is not False))
                self.db.add(allowed)
        if not allowed and not user:
            raise APIError("User not found", status_code=404)

        if allowed and not allowed.note:
            allowed.note = _allowed_user_note_json(
                name=str(getattr(user, "full_name", "") or ""),
                agency_id=str(getattr(user, "agency_id", "") or ""),
                role=str(getattr(user, "role", "") or ""),
            )

        original_email = str((allowed.email if allowed else getattr(user, "email", "")) or "").strip().lower()
        normalized_email = original_email
        if email is not None:
            normalized_email = _normalized_text(email).lower()
            if not normalized_email:
                raise APIError("Email is required", status_code=400)
            other_allowed = self.db.scalar(
                select(AllowedUserEntity).where(
                    AllowedUserEntity.email == normalized_email,
                    AllowedUserEntity.id != getattr(allowed, "id", None),
                )
            )
            if other_allowed:
                raise APIError("Email already exists", status_code=409)
            if allowed:
                allowed.email = normalized_email
            if user:
                user.email = normalized_email
        if name is not None:
            if allowed:
                note_data = _allowed_user_note_data(allowed.note)
                allowed.note = _allowed_user_note_json(
                    name=name,
                    agency_id=agency_id if agency_id is not None else str(getattr(user, "agency_id", "") or _allowed_user_agency_id(allowed) or ""),
                    role=role or str(getattr(user, "role", "") or note_data.get("role") or ""),
                )
            if user:
                user.full_name = _normalized_text(name)
        if agency_id is not None:
            agency = self.get_agency(agency_id)
            if not agency:
                raise APIError("Agency not found", status_code=404)
            if allowed:
                note_data = _allowed_user_note_data(allowed.note)
                allowed.note = _allowed_user_note_json(
                    name=name if name is not None else str(note_data.get("name") or user.full_name or ""),
                    agency_id=str(agency.id),
                    role=role or str(note_data.get("role") or getattr(user, "role", "") or ""),
                )
                allowed.agency_id = str(agency.id)
            if user:
                user.agency_id = str(agency.id)
        if role is not None:
            normalized_role = normalize_app_role(role)
            if allowed:
                note_data = _allowed_user_note_data(allowed.note)
                allowed.note = _allowed_user_note_json(
                    name=name if name is not None else str(note_data.get("name") or user.full_name or ""),
                    agency_id=str(agency_id or (getattr(user, "agency_id", "") or _allowed_user_agency_id(allowed) or "")),
                    role=normalized_role,
                )
                if normalized_role == SUPER_ADMIN_ROLE:
                    allowed.agency_id = None
            if user:
                user.role = normalized_role
                if normalized_role == SUPER_ADMIN_ROLE:
                    user.agency_id = None
        if is_active is not None:
            if allowed:
                allowed.is_active = bool(is_active)
            if user:
                user.is_active = bool(is_active)
        if allowed:
            allowed.note = allowed.note or _allowed_user_note_json(
                name=name if name is not None else str(getattr(user, "full_name", "") or ""),
                agency_id=str(agency_id or (getattr(user, "agency_id", "") or _allowed_user_agency_id(allowed) or "")),
                role=role or str(getattr(user, "role", "") or _allowed_user_note_data(allowed.note).get("role") or ""),
            )
            if not allowed.created_at:
                allowed.created_at = datetime.now(timezone.utc)
        if user:
            user.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        resolved_agency_id = str(
            agency_id or getattr(user, "agency_id", "") or _allowed_user_agency_id(allowed) or ""
        ).strip()
        agency_name = self._agency_names_by_id({resolved_agency_id}).get(resolved_agency_id, "") if resolved_agency_id else ""
        return self._user_summary_from_sources(allowed=allowed, user=user, agency_name=agency_name)
