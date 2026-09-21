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
PROJECT_LIST_PATH = "/data/api/v1/projects/list"
AUDIT_QUERY_PATH = "/data/api/v1/audit/log/{name}"
ALARM_PIPELINE_LIST_PATH = "/data/alarm-notification/api/v1/pipelines"
ALARM_PIPELINE_STATUS_PATH = "/data/alarm-notification/api/v1/pipeline"
PROJECT_EXPORT_PATH = "/data/api/v1/projects/export/{name}"
TAG_CONFIG_EXPORT_PATH = "/data/api/v1/tags/export"
PROJECT_IMPORT_PATH = "/data/api/v1/projects/import/{name}"
DESIGNERS_PATH = "/data/api/v1/designers"
PROJECT_FIND_PATH = "/data/api/v1/projects/find/{name}"
RESOURCE_TYPE_PREFIX = "/data/api/v1/resources/type/"
RESOURCE_COLLECTION_PREFIX = "/data/api/v1/resources/"


@dataclass(frozen=True, slots=True)
class ConfigResourceCapability:
    resource_type: str
    module: str
    type_id: str
    describe_path: str
    names_path: str | None
    list_path: str | None
    find_path_template: str | None
    singleton_path: str | None
    #: The collection path an update is dispatched to. Present only when the
    #: Gateway documents ``PUT`` for this type; Phase 4 never writes otherwise.
    update_path: str | None = None

    @property
    def singleton(self) -> bool:
        return self.singleton_path is not None


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
    resource_types: Mapping[str, ConfigResourceCapability]

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
            resource_types=MappingProxyType({}),
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
                resource_types = _resource_type_inventory(endpoints)
                semantic = _semantic_capabilities(endpoints, resource_types)
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
                    resource_types=MappingProxyType(resource_types),
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

    def resource_type(self, resource_type: str) -> ConfigResourceCapability:
        if self._snapshot.state not in {"READY", "STALE"}:
            raise GatewayError("unsupported_capability", "Gateway capability registry is unavailable")
        capability = self._snapshot.resource_types.get(resource_type)
        if capability is None:
            raise GatewayError(
                "unsupported_capability",
                "resourceType is not present in the current Gateway OpenAPI capability registry",
            )
        return capability


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


def _resource_type_inventory(
    endpoints: set[tuple[str, str]],
) -> dict[str, ConfigResourceCapability]:
    result: dict[str, ConfigResourceCapability] = {}
    for method, path in endpoints:
        if method != "GET" or not path.startswith(RESOURCE_TYPE_PREFIX):
            continue
        resource_type = path[len(RESOURCE_TYPE_PREFIX):]
        parts = resource_type.split("/", 1)
        if len(parts) != 2 or not all(parts):
            continue
        module, type_id = parts
        names_path = f"/data/api/v1/resources/names/{resource_type}"
        list_path = f"/data/api/v1/resources/list/{resource_type}"
        find_path = f"/data/api/v1/resources/find/{resource_type}/{{name}}"
        singleton_path = f"/data/api/v1/resources/singleton/{resource_type}"
        collection_path = f"{RESOURCE_COLLECTION_PREFIX}{resource_type}"
        result[resource_type] = ConfigResourceCapability(
            resource_type=resource_type,
            module=module,
            type_id=type_id,
            describe_path=path,
            names_path=names_path if ("GET", names_path) in endpoints else None,
            list_path=list_path if ("GET", list_path) in endpoints else None,
            find_path_template=find_path if ("GET", find_path) in endpoints else None,
            singleton_path=singleton_path if ("GET", singleton_path) in endpoints else None,
            update_path=collection_path if ("PUT", collection_path) in endpoints else None,
        )
    return dict(sorted(result.items()))


def _semantic_capabilities(
    endpoints: set[tuple[str, str]],
    resource_types: Mapping[str, ConfigResourceCapability],
) -> set[str]:
    semantic: set[str] = set()
    exact = {
        "gateway_info": GATEWAY_INFO_PATH,
        "project_list": PROJECT_LIST_PATH,
        "audit_query": AUDIT_QUERY_PATH,
        "alarm_pipeline_list": ALARM_PIPELINE_LIST_PATH,
        "alarm_pipeline_status": ALARM_PIPELINE_STATUS_PATH,
        "project_export": PROJECT_EXPORT_PATH,
        "tag_config_export": TAG_CONFIG_EXPORT_PATH,
    }
    for capability, path in exact.items():
        if ("GET", path) in endpoints:
            semantic.add(capability)
    if resource_types:
        semantic.add("config_resource_search")
        semantic.add("config_resource_describe")
    if any(item.names_path is not None for item in resource_types.values()):
        semantic.add("config_resource_names")
    if any(item.list_path is not None for item in resource_types.values()):
        semantic.add("config_resource_list")
    if any(
        item.find_path_template is not None or item.singleton_path is not None
        for item in resource_types.values()
    ):
        semantic.add("config_resource_get")
    if any(item.update_path is not None for item in resource_types.values()):
        semantic.add("config_resource_update")
    # Write-side and auxiliary capabilities exist exactly when the method+path pair
    # is in the OpenAPI inventory. Phase 3 never dispatches the import; the
    # capability only gates internal machinery and future Phase 4 exposure (D08/D26).
    if ("POST", PROJECT_IMPORT_PATH) in endpoints:
        semantic.add("project_import")
    if ("GET", DESIGNERS_PATH) in endpoints:
        semantic.add("designer_sessions")
    if ("GET", PROJECT_FIND_PATH) in endpoints:
        semantic.add("project_find")
    return semantic


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
