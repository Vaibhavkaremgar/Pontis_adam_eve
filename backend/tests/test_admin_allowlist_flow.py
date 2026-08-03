from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_admin_allowlist.db")
os.environ.setdefault("JWT_SECRET", "allowlist-test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "allowlist-internal-key")

from sqlalchemy import select

from app.db.admin_repositories import AdminRepository
from app.db.repositories import CompanyRepository
from app.db.session import SessionLocal, engine
from app.models.entities import AllowedUserEntity, AuditEventEntity, Base, CompanyEntity, UserEntity
from app.schemas.admin import UserCreateRequest
from app.services.admin_service import create_admin_user
from app.services.auth_service import _load_portal_user
from app.services.super_admin_seed_service import ensure_primary_super_admin_account


class AdminAllowlistFlowTests(unittest.TestCase):
    TABLES = [CompanyEntity.__table__, AllowedUserEntity.__table__, UserEntity.__table__, AuditEventEntity.__table__]

    @classmethod
    def setUpClass(cls) -> None:
        engine.dispose()
        db_path = getattr(engine.url, "database", None)
        if db_path:
            Path(db_path).unlink(missing_ok=True)
        Base.metadata.create_all(bind=engine, tables=cls.TABLES)

    def setUp(self) -> None:
        Base.metadata.drop_all(bind=engine, tables=self.TABLES)
        Base.metadata.create_all(bind=engine, tables=self.TABLES)
        self.db = SessionLocal()
        self.repo = AdminRepository(self.db)
        self.agency = CompanyRepository(self.db).create(
            user_id=str(uuid4()),
            name="Acme Agency",
            website="https://acme.test",
            description="Test agency",
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_super_admin_user_creation_only_writes_allowlist(self) -> None:
        payload = UserCreateRequest(
            agency_id=self.agency.id,
            name="Jane Recruiter",
            email="jane@acme.test",
            role="AGENCY_USER",
            is_active=True,
        )

        result = create_admin_user(db=self.db, actor_id=str(uuid4()), payload=payload)

        self.assertEqual(result["email"], "jane@acme.test")
        self.assertEqual(result["name"], "Jane Recruiter")
        self.assertEqual(result["agency_id"], self.agency.id)

        allowed = self.db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == "jane@acme.test"))
        user = self.db.scalar(select(UserEntity).where(UserEntity.email == "jane@acme.test"))

        self.assertIsNotNone(allowed)
        self.assertIsNone(user)
        self.assertIsNotNone(allowed)
        assert allowed is not None
        self.assertEqual(allowed.agency_id, self.agency.id)
        metadata = json.loads(allowed.note or "{}")
        self.assertEqual(metadata.get("name"), "Jane Recruiter")
        self.assertEqual(metadata.get("role"), "AGENCY_USER")

    def test_allowlist_entry_bootstraps_portal_user_on_login(self) -> None:
        allowed = AllowedUserEntity(
            id=uuid4(),
            email="sam@acme.test",
            agency_id=self.agency.id,
            note=json.dumps(
                {
                    "name": "Sam Builder",
                    "role": "AGENCY_USER",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            is_active=True,
        )
        self.db.add(allowed)
        self.db.commit()

        user, agency_id, role = _load_portal_user(db=self.db, email="sam@acme.test")

        self.assertEqual(user.email, "sam@acme.test")
        self.assertEqual(user.full_name, "Sam Builder")
        self.assertEqual(agency_id, self.agency.id)
        self.assertEqual(role, "AGENCY_USER")

        stored_user = self.db.scalar(select(UserEntity).where(UserEntity.email == "sam@acme.test"))
        self.assertIsNotNone(stored_user)
        assert stored_user is not None
        self.assertEqual(stored_user.full_name, "Sam Builder")
        self.assertEqual(stored_user.agency_id, self.agency.id)

    def test_primary_super_admin_bootstraps_without_agency(self) -> None:
        allowed = AllowedUserEntity(
            id=uuid4(),
            email="superadmin@pontis.test",
            agency_id=None,
            note=json.dumps(
                {
                    "name": "Global Admin",
                    "role": "superadmin",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            is_active=True,
        )
        self.db.add(allowed)
        self.db.commit()

        user, agency_id, role = _load_portal_user(db=self.db, email="superadmin@pontis.test")

        self.assertEqual(user.email, "superadmin@pontis.test")
        self.assertEqual(user.full_name, "Global Admin")
        self.assertIsNone(agency_id)
        self.assertEqual(role, "superadmin")
        self.assertIsNone(user.agency_id)

    def test_primary_super_admin_seed_keeps_account_global(self) -> None:
        ensure_primary_super_admin_account(db=self.db)

        allowed = self.db.scalar(select(AllowedUserEntity).where(AllowedUserEntity.email == "vaibhav@pontis.one"))
        user = self.db.scalar(select(UserEntity).where(UserEntity.email == "vaibhav@pontis.one"))

        self.assertIsNotNone(allowed)
        self.assertIsNotNone(user)
        assert allowed is not None
        assert user is not None
        self.assertIsNone(allowed.agency_id)
        self.assertIsNone(user.agency_id)
        self.assertEqual(user.role, "superadmin")


if __name__ == "__main__":
    unittest.main()
