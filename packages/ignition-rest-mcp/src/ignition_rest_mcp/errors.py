"""Stable safe error mapping for the external capability plane."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class GatewayError(Exception):
    code: str
    message: str
    status_code: int | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def map_http_error(error: Exception) -> GatewayError:
    if isinstance(error, httpx.TimeoutException):
        return GatewayError("timeout", "Ignition Gateway request timed out")
    if isinstance(error, httpx.NetworkError):
        return GatewayError("gateway_unavailable", "Ignition Gateway is unavailable")
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in {401, 403}:
            return GatewayError("permission_denied", "Ignition Gateway rejected the service credential", status)
        if status == 404:
            return GatewayError("not_found", "Ignition Gateway resource was not found", status)
        if status == 409:
            return GatewayError("conflict", "Ignition Gateway reported a conflict", status)
        if status == 429:
            return GatewayError("rate_limited", "Ignition Gateway rate limited the request", status)
        if 500 <= status <= 599:
            return GatewayError("upstream_error", "Ignition Gateway returned a server error", status)
        return GatewayError("upstream_error", f"Ignition Gateway returned HTTP {status}", status)
    if isinstance(error, (ValueError, TypeError)):
        return GatewayError("schema_mismatch", "Ignition Gateway returned an unexpected response")
    return GatewayError("internal_error", "Unexpected external server error")
