from __future__ import annotations

from app.services.ats.base import ATSProvider
from app.services.ats.mock import MockATSProvider


def get_ats_provider(provider: str) -> ATSProvider:
    normalized = (provider or "").strip().lower()
    if normalized == "mock":
        return MockATSProvider()

    return MockATSProvider()
