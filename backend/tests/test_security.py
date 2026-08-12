from __future__ import annotations

import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_security.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

from app.core.security import create_access_token, has_role, verify_access_token


class SecurityTests(unittest.TestCase):
    def test_access_token_round_trip_includes_role(self) -> None:
        # "admin" is normalized to "superadmin" by the role alias table — this is intentional.
        token = create_access_token(user_id="user-1", email="recruiter@example.com", role="admin")
        claims = verify_access_token(token)
        self.assertEqual(claims["sub"], "user-1")
        self.assertEqual(claims["email"], "recruiter@example.com")
        self.assertEqual(claims["role"], "superadmin")

    def test_role_check_is_least_privilege(self) -> None:
        self.assertTrue(has_role({"role": "admin"}, ["admin", "internal_ops"]))
        self.assertFalse(has_role({"role": "recruiter"}, ["admin", "internal_ops"]))


if __name__ == "__main__":
    unittest.main()
