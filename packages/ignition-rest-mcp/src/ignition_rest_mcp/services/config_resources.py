"""Shared config-resource vocabulary for the Native REST configuration plane.

The read Tools and the Phase 4 config Mutation Tools must agree exactly on three
things: which ``<module>/<type>`` a call addresses, how a Target is named for the
deployment allowlist, and where the Resource signature — D30's Precondition token
for a config change — lives in a Gateway resource document. Keeping them here
means the mutation path cannot drift from the read path.
"""

from __future__ import annotations

from typing import Any

from ignition_rest_mcp.capabilities.registry import CapabilityRegistry, ConfigResourceCapability
from ignition_rest_mcp.errors import GatewayError

MAX_RESOURCE_TYPE_LENGTH = 512

#: The exact string :func:`redact` substitutes for a secret-named or embedded-secret
#: value. A write refuses a document that carries it (D15/D17: a caller must never be
#: able to write the placeholder back as if it were the secret), so the writer's check
#: and the redactor's output are one constant rather than two literals.
REDACTED_PLACEHOLDER = "<redacted>"

_SECRET_KEYS = {
    "password",
    "passwd",
    "secret",
    "clientsecret",
    "client_secret",
    "accesstoken",
    "access_token",
    "refreshtoken",
    "refresh_token",
    "apikey",
    "api_key",
    "privatekey",
    "private_key",
}


def bounded_text(value: str, name: str, maximum: int, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise GatewayError("invalid_argument", f"{name} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise GatewayError("invalid_argument", f"{name} must be non-empty")
    if len(value) > maximum:
        raise GatewayError("limit_exceeded", f"{name} exceeds the {maximum}-character limit")
    return value


def redact(value: Any, *, key: str = "") -> Any:
    normalized = key.replace("-", "_").lower()
    if normalized in _SECRET_KEYS:
        return REDACTED_PLACEHOLDER
    if isinstance(value, dict):
        if value.get("type") == "Embedded" and isinstance(value.get("data"), dict):
            data = value["data"]
            if {"protected", "encrypted_key", "iv", "ciphertext", "tag"}.issubset(data):
                return {"type": "Embedded", "data": REDACTED_PLACEHOLDER}
        return {str(child_key): redact(child, key=str(child_key)) for child_key, child in value.items()}
    if isinstance(value, list):
        return [redact(child) for child in value]
    return value


def catalog_resource_type(registry: CapabilityRegistry, value: str) -> ConfigResourceCapability:
    """The exact, OpenAPI-catalogued resource type a call addresses.

    The caller never names a REST path: the type is resolved only through the D04
    capability registry, and the ``<module>/<type>`` form is enforced first so no
    caller-supplied path segment can reach the Gateway.
    """

    value = bounded_text(value, "resourceType", MAX_RESOURCE_TYPE_LENGTH, allow_empty=False)
    if value.startswith("/") or value.endswith("/") or value.count("/") != 1:
        raise GatewayError("invalid_argument", "resourceType must use exact '<module>/<type>' form")
    return registry.resource_type(value)


def resource_signature(payload: dict[str, Any]) -> str | None:
    """The Resource signature of one Gateway resource document (D30).

    A Gateway that reports no signature leaves the caller with nothing to send as
    ``expectedSignature``; that is the caller's problem to solve, not something to
    invent here, so the value stays ``None``. A present-but-invalid signature is a
    Gateway contract violation and fails closed.
    """

    value = payload.get("signature")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise GatewayError("schema_mismatch", "Gateway resource signature is invalid")
    return value


def resource_target_id(capability: ConfigResourceCapability, name: str) -> str:
    """The Target allowlist identity of one config resource (D08/D30).

    It is the exact ``<module>/<type>/<name>`` for a named resource and the bare
    ``<module>/<type>`` for a singleton, so an operator can allowlist one resource
    without opening every resource of its type. Allowing everything still needs an
    explicit ``*``.
    """

    return capability.resource_type if capability.singleton else f"{capability.resource_type}/{name}"
