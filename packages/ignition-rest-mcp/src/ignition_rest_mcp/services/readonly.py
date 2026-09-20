"""Phase 2 bounded read-only Native REST use cases."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ignition_rest_mcp.capabilities.registry import CapabilityRegistry, ConfigResourceCapability
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import (
    AlarmPipelineInstance,
    AlarmPipelineListResult,
    AlarmPipelineStatusResult,
    AlarmPipelineSummary,
    AuditQueryResult,
    AuditRecord,
    ConfigResourceDescribeResult,
    ConfigResourceGetResult,
    ConfigResourceListResult,
    ConfigResourceName,
    ConfigResourceNamesResult,
    ConfigResourceSearchResult,
    ConfigResourceTypeSummary,
    PageMetadata,
    ProjectListResult,
    ProjectSummary,
)
from ignition_rest_mcp.operation import OperationContext

DEFAULT_PAGE_SIZE = 100
HARD_PAGE_SIZE = 500
MAX_OFFSET = 1_000_000
MAX_SEARCH_LENGTH = 256
MAX_IDENTIFIER_LENGTH = 512

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


async def project_list(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    search: str,
    limit: int,
    offset: int,
) -> ProjectListResult:
    _require(registry, "project_list")
    limit, offset = _page_request(limit, offset)
    params = _collection_params(search, limit, offset)
    payload = await _get(client, registry, "/data/api/v1/projects/list", context, params=params)
    items = _list_of_objects(payload, "items")
    return ProjectListResult(
        correlationId=context.correlation_id,
        items=[
            ProjectSummary(
                name=_text(item, "name"),
                description=_optional_text(item, "description"),
                title=_optional_text(item, "title"),
                enabled=_bool(item, "enabled"),
                parent=_optional_text(item, "parent"),
                inheritable=_bool(item, "inheritable"),
                invalidParent=_bool(item, "invalidParent"),
                mutable=_bool(item, "mutable"),
                defaultDb=_optional_text(item, "defaultDb"),
                tagProvider=_optional_text(item, "tagProvider"),
                userSource=_optional_text(item, "userSource"),
                identityProvider=_optional_text(item, "identityProvider"),
            )
            for item in items
        ],
        page=_page_metadata(payload),
    )


def config_resource_search(
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    query: str,
    limit: int,
    offset: int,
) -> ConfigResourceSearchResult:
    _require(registry, "config_resource_search")
    limit, offset = _page_request(limit, offset)
    query = _bounded_text(query, "query", MAX_SEARCH_LENGTH, allow_empty=True).lower()
    all_items = [
        ConfigResourceTypeSummary(
            resourceType=item.resource_type,
            module=item.module,
            typeId=item.type_id,
            singleton=item.singleton,
            supportsNames=item.names_path is not None,
            supportsList=item.list_path is not None,
            supportsGet=item.find_path_template is not None or item.singleton_path is not None,
        )
        for item in registry.snapshot.resource_types.values()
        if not query or query in item.resource_type.lower()
    ]
    page_items = all_items[offset:offset + limit]
    return ConfigResourceSearchResult(
        correlationId=context.correlation_id,
        items=page_items,
        page=PageMetadata(
            total=len(registry.snapshot.resource_types),
            matching=len(all_items),
            limit=limit,
            offset=offset,
        ),
    )


async def config_resource_describe(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    resource_type: str,
) -> ConfigResourceDescribeResult:
    _require(registry, "config_resource_describe")
    capability = _resource_type(registry, resource_type)
    payload = await _get(client, registry, capability.describe_path, context)
    return ConfigResourceDescribeResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        description=_redact(payload),
    )


async def config_resource_names(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    resource_type: str,
    search: str,
    limit: int,
    offset: int,
) -> ConfigResourceNamesResult:
    _require(registry, "config_resource_names")
    capability = _resource_type(registry, resource_type)
    if capability.names_path is None:
        raise GatewayError("unsupported_capability", "This resourceType is singleton and has no names collection")
    limit, offset = _page_request(limit, offset)
    payload = await _get(
        client,
        registry,
        capability.names_path,
        context,
        params=_collection_params(search, limit, offset),
    )
    items = _list_of_objects(payload, "items")
    return ConfigResourceNamesResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        items=[ConfigResourceName(name=_text(item, "name"), enabled=_bool(item, "enabled")) for item in items],
        page=_page_metadata(payload),
    )


async def config_resource_list(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    resource_type: str,
    search: str,
    limit: int,
    offset: int,
) -> ConfigResourceListResult:
    _require(registry, "config_resource_list")
    capability = _resource_type(registry, resource_type)
    if capability.list_path is None:
        raise GatewayError("unsupported_capability", "This resourceType is singleton and has no list collection")
    limit, offset = _page_request(limit, offset)
    payload = await _get(
        client,
        registry,
        capability.list_path,
        context,
        params=_collection_params(search, limit, offset),
    )
    items = [_redact(item) for item in _list_of_objects(payload, "items")]
    return ConfigResourceListResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        items=items,
        page=_page_metadata(payload),
    )


async def config_resource_get(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    resource_type: str,
    name: str,
    collection: str,
    default_if_undefined: bool,
) -> ConfigResourceGetResult:
    _require(registry, "config_resource_get")
    capability = _resource_type(registry, resource_type)
    collection = _bounded_text(collection, "collection", 128, allow_empty=True)
    params: dict[str, Any] = {}
    if collection:
        params["collection"] = collection
    if capability.singleton:
        if name:
            raise GatewayError("invalid_argument", "name must be empty for a singleton resourceType")
        if capability.singleton_path is None:
            raise GatewayError("unsupported_capability", "Singleton resource path is unavailable")
        params["defaultIfUndefined"] = bool(default_if_undefined)
        path = capability.singleton_path
    else:
        if default_if_undefined:
            raise GatewayError("invalid_argument", "defaultIfUndefined is valid only for singleton resourceType")
        name = _bounded_text(name, "name", MAX_IDENTIFIER_LENGTH, allow_empty=False)
        if capability.find_path_template is None:
            raise GatewayError("unsupported_capability", "This resourceType does not expose exact resource lookup")
        path = capability.find_path_template.replace("{name}", quote(name, safe=""))
    payload = await _get(client, registry, path, context, params=params or None)
    return ConfigResourceGetResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        resource=_redact(payload),
    )


async def audit_query(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    profile: str,
    actor: str,
    action: str,
    target: str,
    value: str,
    system: str,
    originating_context: str,
    start_time: str,
    end_time: str,
    limit: int,
    offset: int,
) -> AuditQueryResult:
    _require(registry, "audit_query")
    profile = _bounded_text(profile, "profile", 256, allow_empty=False)
    limit, offset = _page_request(limit, offset)
    params: dict[str, Any] = {"limit": limit, "offset": offset, "maxRecordLimit": HARD_PAGE_SIZE}
    filters = {
        "actorFilter": actor,
        "actionFilter": action,
        "targetFilter": target,
        "valueFilter": value,
        "systemFilter": system,
        "contextFilter": originating_context,
        "startTime": start_time,
        "endTime": end_time,
    }
    for key, raw in filters.items():
        value_text = _bounded_text(raw, key, MAX_SEARCH_LENGTH, allow_empty=True)
        if value_text:
            params[key] = value_text
    payload = await _get(
        client,
        registry,
        "/data/api/v1/audit/log/" + quote(profile, safe=""),
        context,
        params=params,
    )
    items = _list_of_objects(payload, "items")
    return AuditQueryResult(
        correlationId=context.correlation_id,
        profile=profile,
        items=[
            AuditRecord(
                action=_optional_text(item, "action"),
                actionTarget=_optional_text(item, "actionTarget"),
                actionValue=_optional_text(item, "actionValue"),
                actor=_optional_text(item, "actor"),
                actorHost=_optional_text(item, "actorHost"),
                originatingContext=_integer(item, "originatingContext"),
                originatingSystem=_optional_text(item, "originatingSystem"),
                statusCode=_integer(item, "statusCode"),
                timestamp=_integer(item, "timestamp"),
                result=_optional_text(item, "result"),
            )
            for item in items
        ],
        page=_page_metadata(payload),
    )


async def alarm_pipeline_list(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    search: str,
    limit: int,
    offset: int,
) -> AlarmPipelineListResult:
    _require(registry, "alarm_pipeline_list")
    limit, offset = _page_request(limit, offset)
    payload = await _get(
        client,
        registry,
        "/data/alarm-notification/api/v1/pipelines",
        context,
        params=_collection_params(search, limit, offset),
    )
    items = _list_of_objects(payload, "items")
    return AlarmPipelineListResult(
        correlationId=context.correlation_id,
        items=[
            AlarmPipelineSummary(
                path=_text(item, "path"),
                projectName=_text(item, "projectName"),
                pipelineName=_text(item, "pipelineName"),
                itemCount=_integer(item, "itemCount"),
                active=_bool(item, "active"),
            )
            for item in items
        ],
        page=_page_metadata(payload),
    )


async def alarm_pipeline_status(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    path: str,
    limit: int,
    offset: int,
) -> AlarmPipelineStatusResult:
    _require(registry, "alarm_pipeline_status")
    path = _bounded_text(path, "path", MAX_IDENTIFIER_LENGTH, allow_empty=False)
    limit, offset = _page_request(limit, offset)
    payload = await _get(
        client,
        registry,
        "/data/alarm-notification/api/v1/pipeline",
        context,
        params={"path": path, "limit": limit, "offset": offset},
    )
    items = _list_of_objects(payload, "items")
    return AlarmPipelineStatusResult(
        correlationId=context.correlation_id,
        path=path,
        items=[
            AlarmPipelineInstance(
                pipelinePath=_text(item, "pipelinePath"),
                source=_optional_text(item, "source"),
                displayPath=_optional_text(item, "displayPath"),
                blockName=_optional_text(item, "blockName"),
                status=_optional_text(item, "status"),
                millis=_integer(item, "millis"),
                alarmEventId=_text(item, "alarmEventId"),
            )
            for item in items
        ],
        page=_page_metadata(payload),
    )


async def _get(
    client: GatewayClient,
    registry: CapabilityRegistry,
    path: str,
    context: OperationContext,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return await client.get_json(path, params=params, context=context)
    except GatewayError as error:
        await registry.observe_failure(error)
        raise


def _require(registry: CapabilityRegistry, capability: str) -> None:
    if not registry.supports(capability):
        raise GatewayError(
            "unsupported_capability",
            f"{capability} is not present in the current Gateway capability snapshot",
        )


def _resource_type(registry: CapabilityRegistry, value: str) -> ConfigResourceCapability:
    value = _bounded_text(value, "resourceType", 512, allow_empty=False)
    if value.startswith("/") or value.endswith("/") or value.count("/") != 1:
        raise GatewayError("invalid_argument", "resourceType must use exact '<module>/<type>' form")
    return registry.resource_type(value)


def _page_request(limit: int, offset: int) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > HARD_PAGE_SIZE:
        raise GatewayError("invalid_argument", f"limit must be an integer from 1 to {HARD_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > MAX_OFFSET:
        raise GatewayError("invalid_argument", f"offset must be an integer from 0 to {MAX_OFFSET}")
    return limit, offset


def _collection_params(search: str, limit: int, offset: int) -> dict[str, Any]:
    search = _bounded_text(search, "search", MAX_SEARCH_LENGTH, allow_empty=True)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if search:
        params["search"] = search
    return params


def _page_metadata(payload: dict[str, Any]) -> PageMetadata:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise GatewayError("schema_mismatch", "Gateway collection response is missing metadata")
    return PageMetadata(
        total=_integer(metadata, "total"),
        matching=_integer(metadata, "matching"),
        limit=_integer(metadata, "limit"),
        offset=_integer(metadata, "offset"),
    )


def _list_of_objects(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise GatewayError("schema_mismatch", f"Gateway response field {key} is invalid")
    return value


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GatewayError("schema_mismatch", f"Gateway response field {key} is invalid")
    return value


def _optional_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GatewayError("schema_mismatch", f"Gateway response field {key} is invalid")
    return value


def _bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise GatewayError("schema_mismatch", f"Gateway response field {key} is invalid")
    return value


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise GatewayError("schema_mismatch", f"Gateway response field {key} is invalid")
    return int(value)


def _bounded_text(value: str, name: str, maximum: int, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise GatewayError("invalid_argument", f"{name} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise GatewayError("invalid_argument", f"{name} must be non-empty")
    if len(value) > maximum:
        raise GatewayError("limit_exceeded", f"{name} exceeds the {maximum}-character limit")
    return value


def _redact(value: Any, *, key: str = "") -> Any:
    normalized = key.replace("-", "_").lower()
    if normalized in _SECRET_KEYS:
        return "<redacted>"
    if isinstance(value, dict):
        if value.get("type") == "Embedded" and isinstance(value.get("data"), dict):
            data = value["data"]
            if {"protected", "encrypted_key", "iv", "ciphertext", "tag"}.issubset(data):
                return {"type": "Embedded", "data": "<redacted>"}
        return {str(child_key): _redact(child, key=str(child_key)) for child_key, child in value.items()}
    if isinstance(value, list):
        return [_redact(child) for child in value]
    return value
