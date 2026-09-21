"""Phase 2 bounded read-only Native REST use cases."""

from __future__ import annotations

from functools import wraps
from typing import Any, Awaitable, Callable, Concatenate, ParamSpec, TypeVar, cast
from urllib.parse import quote

from pydantic import ValidationError

from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
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
from ignition_rest_mcp.services.config_resources import (
    bounded_text,
    redact,
    resource_signature,
    catalog_resource_type,
)

DEFAULT_PAGE_SIZE = 100
HARD_PAGE_SIZE = 500
MAX_OFFSET = 1_000_000
MAX_SEARCH_LENGTH = 256
MAX_IDENTIFIER_LENGTH = 512


P = ParamSpec("P")
TResult = TypeVar("TResult")


def _guard_upstream_response(
    operation: Callable[Concatenate[GatewayClient, CapabilityRegistry, P], Awaitable[TResult]],
) -> Callable[Concatenate[GatewayClient, CapabilityRegistry, P], Awaitable[TResult]]:
    """Classify response validation failures and reconcile metadata once."""

    @wraps(operation)
    async def guarded(
        client: GatewayClient, registry: CapabilityRegistry, *args: P.args, **kwargs: P.kwargs,
    ) -> TResult:
        try:
            return await operation(client, registry, *args, **kwargs)
        except ValidationError as error:
            mismatch = GatewayError("schema_mismatch", "Ignition Gateway returned an unexpected response")
            await registry.observe_failure(mismatch)
            raise mismatch from error
        except GatewayError as error:
            await registry.observe_failure(error)
            raise

    return cast(
        Callable[Concatenate[GatewayClient, CapabilityRegistry, P], Awaitable[TResult]], guarded,
    )


@_guard_upstream_response
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
    payload = await _get(client, "/data/api/v1/projects/list", context, params=params)
    items, page = _collection_response(payload, requested_limit=limit, requested_offset=offset)
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
        page=page,
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
    query = bounded_text(query, "query", MAX_SEARCH_LENGTH, allow_empty=True).lower()
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


@_guard_upstream_response
async def config_resource_describe(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
    *,
    resource_type: str,
) -> ConfigResourceDescribeResult:
    _require(registry, "config_resource_describe")
    capability = catalog_resource_type(registry, resource_type)
    payload = await _get(client, capability.describe_path, context)
    return ConfigResourceDescribeResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        description=redact(payload),
    )


@_guard_upstream_response
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
    capability = catalog_resource_type(registry, resource_type)
    if capability.names_path is None:
        raise GatewayError("unsupported_capability", "This resourceType is singleton and has no names collection")
    limit, offset = _page_request(limit, offset)
    payload = await _get(
        client,
        capability.names_path,
        context,
        params=_collection_params(search, limit, offset),
    )
    items, page = _collection_response(payload, requested_limit=limit, requested_offset=offset)
    return ConfigResourceNamesResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        items=[ConfigResourceName(name=_text(item, "name"), enabled=_bool(item, "enabled")) for item in items],
        page=page,
    )


@_guard_upstream_response
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
    capability = catalog_resource_type(registry, resource_type)
    if capability.list_path is None:
        raise GatewayError("unsupported_capability", "This resourceType is singleton and has no list collection")
    limit, offset = _page_request(limit, offset)
    payload = await _get(
        client,
        capability.list_path,
        context,
        params=_collection_params(search, limit, offset),
    )
    raw_items, page = _collection_response(payload, requested_limit=limit, requested_offset=offset)
    items = [redact(item) for item in raw_items]
    return ConfigResourceListResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        items=items,
        page=page,
    )


@_guard_upstream_response
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
    capability = catalog_resource_type(registry, resource_type)
    collection = bounded_text(collection, "collection", 128, allow_empty=True)
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
        name = bounded_text(name, "name", MAX_IDENTIFIER_LENGTH, allow_empty=False)
        if capability.find_path_template is None:
            raise GatewayError("unsupported_capability", "This resourceType does not expose exact resource lookup")
        path = capability.find_path_template.replace("{name}", quote(name, safe=""))
    payload = await _get(client, path, context, params=params or None)
    return ConfigResourceGetResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        resource=redact(payload),
        signature=resource_signature(payload),
    )


@_guard_upstream_response
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
    profile = bounded_text(profile, "profile", 256, allow_empty=False)
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
        value_text = bounded_text(raw, key, MAX_SEARCH_LENGTH, allow_empty=True)
        if value_text:
            params[key] = value_text
    payload = await _get(
        client,
        "/data/api/v1/audit/log/" + quote(profile, safe=""),
        context,
        params=params,
    )
    items, page = _collection_response(payload, requested_limit=limit, requested_offset=offset)
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
        page=page,
    )


@_guard_upstream_response
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
        "/data/alarm-notification/api/v1/pipelines",
        context,
        params=_collection_params(search, limit, offset),
    )
    items, page = _collection_response(payload, requested_limit=limit, requested_offset=offset)
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
        page=page,
    )


@_guard_upstream_response
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
    path = bounded_text(path, "path", MAX_IDENTIFIER_LENGTH, allow_empty=False)
    limit, offset = _page_request(limit, offset)
    payload = await _get(
        client,
        "/data/alarm-notification/api/v1/pipeline",
        context,
        params={"path": path, "limit": limit, "offset": offset},
    )
    items, page = _collection_response(payload, requested_limit=limit, requested_offset=offset)
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
        page=page,
    )


async def _get(
    client: GatewayClient,
    path: str,
    context: OperationContext,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await client.get_json(path, params=params, context=context)


def _require(registry: CapabilityRegistry, capability: str) -> None:
    if not registry.supports(capability):
        raise GatewayError(
            "unsupported_capability",
            f"{capability} is not present in the current Gateway capability snapshot",
        )


def _page_request(limit: int, offset: int) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > HARD_PAGE_SIZE:
        raise GatewayError("invalid_argument", f"limit must be an integer from 1 to {HARD_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > MAX_OFFSET:
        raise GatewayError("invalid_argument", f"offset must be an integer from 0 to {MAX_OFFSET}")
    return limit, offset


def _collection_params(search: str, limit: int, offset: int) -> dict[str, Any]:
    search = bounded_text(search, "search", MAX_SEARCH_LENGTH, allow_empty=True)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if search:
        params["search"] = search
    return params


def _collection_response(
    payload: dict[str, Any], *, requested_limit: int, requested_offset: int,
) -> tuple[list[dict[str, Any]], PageMetadata]:
    items = _list_of_objects(payload, "items")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise GatewayError("schema_mismatch", "Gateway collection response is missing metadata")
    page = PageMetadata(
        total=_integer(metadata, "total"),
        matching=_integer(metadata, "matching"),
        limit=_integer(metadata, "limit"),
        offset=_integer(metadata, "offset"),
    )
    if page.limit != requested_limit or page.offset != requested_offset:
        raise GatewayError(
            "schema_mismatch",
            "Gateway collection metadata does not match the requested limit and offset",
        )
    if page.matching > page.total:
        raise GatewayError("schema_mismatch", "Gateway collection metadata counts are inconsistent")
    expected_count = min(requested_limit, max(page.matching - requested_offset, 0))
    if len(items) != expected_count:
        raise GatewayError(
            "schema_mismatch",
            "Gateway collection item count is inconsistent with its metadata and requested page",
        )
    return items, page


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
