"""D16 Gateway identity: explicit operator-chosen Gateway IDs and the
derived display fallback used only in read-only deployments."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class GatewayIdentity:
    """Lock keys use ``key`` only when it is not ``derived`` (slice 7 contract)."""

    key: str
    derived: bool


def gateway_identity(gateway_id: str, gateway_url: str) -> GatewayIdentity:
    if gateway_id:
        return GatewayIdentity(key=gateway_id, derived=False)
    parsed = urlparse(gateway_url)
    netloc = (parsed.netloc or "").lower()
    scheme = (parsed.scheme or "http").lower()
    return GatewayIdentity(key=f"{scheme}://{netloc}", derived=True)
