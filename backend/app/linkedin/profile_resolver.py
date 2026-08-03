from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.core.config import LINKEDIN_PROFILE_ROOT
from app.db.session import SessionLocal
from app.models.entities import CompanyEntity


def resolve_agency_profile_path(identifier: str) -> str:
    """Resolve a LinkedIn worker identifier through agencies metadata.

    The fallback account lookup preserves existing queue payloads while still
    making the agency row the source of truth for the actual profile path.
    """
    value = str(identifier or "").strip()
    if not value:
        raise ValueError("LinkedIn agency identifier is required")
    with SessionLocal() as db:
        agency = db.scalar(select(CompanyEntity).where(CompanyEntity.id == value))
        if agency is None:
            from app.linkedin.models import LinkedInAccountEntity

            account = db.scalar(
                select(LinkedInAccountEntity).where(
                    (LinkedInAccountEntity.id == value)
                    | (LinkedInAccountEntity.browser_profile_name == value)
                )
            )
            agency = db.scalar(select(CompanyEntity).where(CompanyEntity.id == account.agency_id)) if account else None
        path = str(getattr(agency, "linkedin_profile_path", "") or "").strip()
    if not path:
        raise ValueError("LinkedIn is not configured for this agency")
    resolved = Path(path).expanduser().resolve()
    root = Path(LINKEDIN_PROFILE_ROOT).expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("LinkedIn profile path is outside LINKEDIN_PROFILE_ROOT")
    return str(resolved)
