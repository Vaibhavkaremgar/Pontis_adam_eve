from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_database_url(url: str) -> str:
    """
    Normalize database URLs for SQLAlchemy/Alembic.

    Railway's PostgreSQL proxy endpoints often need explicit SSL mode and a
    bounded connect timeout to avoid connection resets during startup.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return cleaned

    parts = urlsplit(cleaned)
    if not parts.scheme.startswith(("postgresql", "postgres")):
        return cleaned
    if not parts.hostname or not parts.hostname.endswith(".proxy.rlwy.net"):
        return cleaned

    query_pairs = dict(parse_qsl(parts.query, keep_blank_values=True))
    changed = False

    if query_pairs.get("sslmode") != "require":
        query_pairs["sslmode"] = "require"
        changed = True
    if query_pairs.get("connect_timeout") != "10":
        query_pairs["connect_timeout"] = "10"
        changed = True

    if not changed:
        return cleaned

    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_pairs), parts.fragment))
