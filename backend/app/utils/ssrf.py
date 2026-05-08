"""
SSRF protection for outbound HTTP requests.

Blocks requests to:
- localhost / loopback (127.x.x.x, ::1)
- Private RFC-1918 ranges (10.x, 172.16-31.x, 192.168.x)
- Link-local / AWS metadata (169.254.x.x)
- Non-HTTPS schemes
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.utils.exceptions import APIError

_BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript"}

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / AWS metadata
    ipaddress.ip_network("100.64.0.0/10"),    # shared address space
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def validate_public_url(url: str) -> str:
    """
    Validate that a URL is safe to fetch (public HTTPS only).
    Raises APIError if the URL is blocked.
    Returns the normalized URL on success.
    """
    raw = (url or "").strip()
    if not raw:
        raise APIError("URL is required", status_code=400)

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()

    if scheme in _BLOCKED_SCHEMES:
        raise APIError(f"URL scheme '{scheme}' is not allowed", status_code=400)

    if scheme not in {"http", "https"}:
        raise APIError("Only http and https URLs are allowed", status_code=400)

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        raise APIError("URL must include a valid hostname", status_code=400)

    # Block bare IP addresses that are private
    try:
        addr = ipaddress.ip_address(hostname)
        if _is_private_ip(str(addr)):
            raise APIError("Requests to private IP addresses are not allowed", status_code=400)
    except ValueError:
        # It's a hostname — resolve it and check the resolved IPs
        try:
            resolved = socket.getaddrinfo(hostname, None)
            for item in resolved:
                ip_str = item[4][0]
                if _is_private_ip(ip_str):
                    raise APIError(
                        f"URL resolves to a private/internal address and cannot be fetched",
                        status_code=400,
                    )
        except APIError:
            raise
        except OSError:
            # DNS resolution failed — let the HTTP client handle it
            pass

    return raw
