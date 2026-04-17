import logging

import requests

from app.core.config import HTTP_TIMEOUT_SECONDS, PROXYCURL_API_KEY, PROXYCURL_URL, QDRANT_SEARCH_LIMIT

logger = logging.getLogger(__name__)


def search_candidates(query: str, limit: int | None = None) -> list[dict]:
    if not PROXYCURL_API_KEY:
        logger.warning("PROXYCURL_API_KEY is not configured; returning empty candidate set")
        return []

    headers = {
        "Authorization": f"Bearer {PROXYCURL_API_KEY}"
    }

    params = {
        "query": query,
        "limit": limit or QDRANT_SEARCH_LIMIT,
    }

    response = requests.get(PROXYCURL_URL, headers=headers, params=params, timeout=HTTP_TIMEOUT_SECONDS)

    if response.status_code != 200:
        logger.error("Proxycurl request failed", extra={"status_code": response.status_code})
        raise Exception(f"Proxycurl Error: {response.text}")

    payload = response.json()
    return payload.get("data", []) if isinstance(payload, dict) else []
