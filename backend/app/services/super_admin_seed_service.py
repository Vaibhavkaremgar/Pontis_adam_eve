from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import AGENCY_USER_ROLE, SUPER_ADMIN_ROLE
from app.models.entities import AllowedUserEntity, UserEntity

PRIMARY_SUPER_ADMIN_EMAIL = "vaibhav@pontis.one"
LEGACY_SUPER_ADMIN_EMAIL = "karemgarvaibhav@gmail.com"
PRIMARY_SUPER_ADMIN_NAME = "Vaibhav"


def ensure_primary_super_admin_account(*, db: Session) -> None:
    now = datetime.now(timezone.utc)

    legacy_allowed = db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == LEGACY_SUPER_ADMIN_EMAIL))
    if legacy_allowed is not None:
        legacy_allowed.is_active = False
        legacy_allowed.note = "legacy super admin disabled"

    target_allowed = db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == PRIMARY_SUPER_ADMIN_EMAIL))
    if target_allowed is None:
        target_allowed = AllowedUserEntity(
            id=uuid4(),
            email=PRIMARY_SUPER_ADMIN_EMAIL,
            agency_id=None,
            note="primary super admin",
            is_active=True,
            created_at=now,
        )
        db.add(target_allowed)
    else:
        target_allowed.is_active = True
        target_allowed.agency_id = None
        target_allowed.note = "primary super admin"

    target_user = db.scalar(select(UserEntity).where(UserEntity.email == PRIMARY_SUPER_ADMIN_EMAIL))
    if target_user is None:
        target_user = UserEntity(
            id=str(uuid4()),
            email=PRIMARY_SUPER_ADMIN_EMAIL,
            full_name=PRIMARY_SUPER_ADMIN_NAME,
            role=SUPER_ADMIN_ROLE,
            agency_id=None,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(target_user)
    else:
        target_user.email = PRIMARY_SUPER_ADMIN_EMAIL
        target_user.full_name = target_user.full_name or PRIMARY_SUPER_ADMIN_NAME
        target_user.role = SUPER_ADMIN_ROLE
        target_user.agency_id = None
        target_user.is_active = True
        target_user.updated_at = now

    legacy_user = db.scalar(select(UserEntity).where(UserEntity.email == LEGACY_SUPER_ADMIN_EMAIL))
    if legacy_user is not None:
        legacy_user.role = AGENCY_USER_ROLE
        legacy_user.is_active = True
        legacy_user.agency_id = None
        legacy_user.updated_at = now

    db.commit()
