from __future__ import annotations

import os


DEFAULT_LINKEDIN_DEV_ACCOUNT_ID = "linkedin-dev-account"


def get_development_account_id() -> str:
    return os.getenv("LINKEDIN_DEV_ACCOUNT_ID", DEFAULT_LINKEDIN_DEV_ACCOUNT_ID).strip() or DEFAULT_LINKEDIN_DEV_ACCOUNT_ID
