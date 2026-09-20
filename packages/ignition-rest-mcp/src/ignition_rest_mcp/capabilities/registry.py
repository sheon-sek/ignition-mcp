"""D04 immutable capability snapshot and singleflight refresh."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping

from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError

GATEWAY_INFO_PATH = "/data/api/v1/gateway-info"


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    state: str
    generation: int
    fetched_at: datetime | None
    gateway_version: str | None
    environment_fingerprint: str | None
    openapi_sha256: str | None
    endpoints: frozenset[tuple[str, str]]
    semantic_capabilities: frozenset[str]
    module_versions: Mapping[str, str]

    @classmethod
    def unavailable(cls) -> "CapabilitySnapshot":
        return cls(
            state="UNAVAILABLE",
            generation=0,
            fetched_at=None,
            gateway_version=None,
            environment_fingerprint=None,
            openapi_sha256=None,
            endpoints=frozenset(),
            semantic_capabilities=frozenset(),
            module_versions=MappingProxyType({}),
        )


class CapabilityRegistry:
    def __init__(self, client: GatewayClient) -> None:
        self._client = client
        self._snapshot = CapabilitySnapshot.unavailable()
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[CapabilitySnapshot] | None = None

    @property
    def snapshot(self) -> CapabilitySnapshot:
        return self._snapshot

    async def refresh(self) -> CapabilitySnapshot:
        # No await between observing and publishing the task: concurrent callers join it.
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh())
        return await asyncio.shield(self._refresh_task)

    def mark_stale(self) -> None:
        if self._snapshot.generation > 0:
            self._snapshot = replace(self._snapshot, state="STALE")
        else:
            self._snapshot = CapabilitySnapshot.unavailable()

    async def observe_failure(self, error: GatewayError) -> None:
        if error.code not in {
            "gateway_unavailable", "timeout", "upstream_error", "permission_denied",
            "schema_mismatch", "not_found", "unsupported_capability",
        }:
            return
        self.mark_stale()
        if error.code in {"schema_mismatch", "not_found", "unsupported_capability"}:
            try:
                # Reconcile metadata only; never replay the failed operation.
                await self.refresh()
            finally:
                # A metadata refresh cannot validate the failed tool's response schema.
                self.mark_stale()

    async def aclose(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)

    async def _refresh(self) -> CapabilitySnapshot:
        async with self._refresh_lock:
            try:
                gateway_info, modules, openapi_bytes = await _gather_requests(
                    self._client.gateway_info(),
                    self._client.healthy_modules(),
                    self._client.openapi(),
                )
                openapi = json.loads(openapi_bytes)
                if not isinstance(openapi, dict) or not isinstance(openapi.get("paths"), dict):
                    raise ValueError("OpenAPI paths missing")
                endpoints = _endpoint_inventory(openapi["paths"])
                semantic = set()
                if ("GET", GATEWAY_INFO_PATH) in endpoints:
                    semantic.add("gateway_info")
                module_versions = _module_versions(modules)
                fingerprint = _fingerprint(gateway_info, module_versions)
                self._snapshot = CapabilitySnapshot(
                    state="READY",
                    generation=self._snapshot.generation + 1,
                    fetched_at=datetime.now(timezone.utc),
                    gateway_version=_required_str(gateway_info, "ignitionVersion"),
                    environment_fingerprint=fingerprint,
                    openapi_sha256=hashlib.sha256(openapi_bytes).hexdigest(),
                    endpoints=frozenset(endpoints),
                    semantic_capabilities=frozenset(semantic),
                    module_versions=MappingProxyType(module_versions),
                )
            except (GatewayError, ValueError, TypeError, json.JSONDecodeError):
                if self._snapshot.generation > 0:
                    self._snapshot = replace(self._snapshot, state="STALE")
                else:
                    self._snapshot = CapabilitySnapshot.unavailable()
            return self._snapshot

    async def fingerprint_changed(self) -> bool:
        try:
            gateway_info, modules = await _gather_requests(
                self._client.gateway_info(),
                self._client.healthy_modules(),
            )
            current = _fingerprint(gateway_info, _module_versions(modules))
        except (GatewayError, ValueError, TypeError):
            if self._snapshot.generation > 0:
                self._snapshot = replace(self._snapshot, state="STALE")
            else:
                self._snapshot = CapabilitySnapshot.unavailable()
            return False
        return self._snapshot.state != "READY" or current != self._snapshot.environment_fingerprint

    def supports(self, capability: str) -> bool:
        return capability in self._snapshot.semantic_capabilities and self._snapshot.state in {"READY", "STALE"}


async def _gather_requests(*requests: Any) -> list[Any]:
    tasks = [asyncio.create_task(request) for request in requests]
    try:
        return await asyncio.gather(*tasks)
    finally:
        # gather does not cancel siblings when one request fails.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _endpoint_inventory(paths: dict[str, Any]) -> set[tuple[str, str]]:
    endpoints: set[tuple[str, str]] = set()
    for path, item in paths.items():
        if not isinstance(path, str) or not isinstance(item, dict):
            continue
        for method in item:
            method_upper = str(method).upper()
            if method_upper in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                endpoints.add((method_upper, path))
    return endpoints


def _module_versions(payload: dict[str, Any]) -> dict[str, str]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("module items missing")
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        module_id, version = item.get("id"), item.get("version")
        if isinstance(module_id, str) and isinstance(version, str):
            result[module_id] = version
    return dict(sorted(result.items()))


def _fingerprint(gateway_info: dict[str, Any], modules: Mapping[str, str]) -> str:
    identity = {
        "ignitionVersion": _required_str(gateway_info, "ignitionVersion"),
        "modules": dict(modules),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _required_str(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result
