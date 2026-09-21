from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.services.readonly import (
    alarm_pipeline_list,
    config_resource_get,
    config_resource_search,
    project_list,
)


def _transport() -> httpx.MockTransport:
    paths = {
        "/data/api/v1/gateway-info": {"get": {}},
        "/data/api/v1/projects/list": {"get": {}},
        "/data/api/v1/audit/log/{name}": {"get": {}},
        "/data/alarm-notification/api/v1/pipelines": {"get": {}},
        "/data/alarm-notification/api/v1/pipeline": {"get": {}},
        "/data/api/v1/resources/type/ignition/database-connection": {"get": {}},
        "/data/api/v1/resources/names/ignition/database-connection": {"get": {}},
        "/data/api/v1/resources/list/ignition/database-connection": {"get": {}},
        "/data/api/v1/resources/find/ignition/database-connection/{name}": {"get": {}},
        "/data/api/v1/resources/type/ignition/system-properties": {"get": {}},
        "/data/api/v1/resources/singleton/ignition/system-properties": {"get": {}},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/data/api/v1/gateway-info":
            return httpx.Response(200, json={"ignitionVersion": "8.3.8 (b2026071409)"}, request=request)
        if path == "/data/api/v1/modules/healthy":
            return httpx.Response(200, json={"items": []}, request=request)
        if path == "/openapi.json":
            return httpx.Response(200, content=json.dumps({"paths": paths}).encode(), request=request)
        if path == "/data/api/v1/projects/list":
            assert request.url.params["limit"] == "2"
            assert request.url.params["offset"] == "0"
            return httpx.Response(
                200,
                json={
                    "items": [{
                        "name": "Demo", "description": "", "title": "Demo", "enabled": True,
                        "parent": "", "inheritable": False, "invalidParent": False, "mutable": True,
                        "defaultDb": "", "tagProvider": "default", "userSource": "",
                        "identityProvider": "",
                    }],
                    "metadata": {"total": 1, "matching": 1, "limit": 2, "offset": 0},
                },
                request=request,
            )
        if path == "/data/api/v1/resources/find/ignition/database-connection/Main":
            return httpx.Response(
                200,
                json={
                    "type": "database-connection",
                    "name": "Main",
                    "config": {
                        "username": "alice",
                        "password": "plaintext-secret",
                        "credential": {
                            "type": "Embedded",
                            "data": {
                                "protected": "x", "encrypted_key": "x", "iv": "x",
                                "ciphertext": "x", "tag": "x",
                            },
                        },
                    },
                },
                request=request,
            )
        if path == "/data/api/v1/resources/singleton/ignition/system-properties":
            assert request.url.params["defaultIfUndefined"] == "true"
            return httpx.Response(
                200,
                json={"type": "system-properties", "name": "system-properties", "config": {"systemName": "ci"}},
                request=request,
            )
        raise AssertionError(str(request.url))

    return httpx.MockTransport(handler)


def test_openapi_resource_catalog_and_readonly_routing() -> None:
    async def scenario() -> None:
        client = GatewayClient(
            base_url="http://gateway",
            api_token="ci:key",
            timeout_seconds=10,
            transport=_transport(),
        )
        registry = CapabilityRegistry(client)
        try:
            snapshot = await registry.refresh()
            assert snapshot.state == "READY"
            assert set(snapshot.resource_types) == {
                "ignition/database-connection",
                "ignition/system-properties",
            }
            assert snapshot.resource_types["ignition/database-connection"].singleton is False
            assert snapshot.resource_types["ignition/system-properties"].singleton is True
            assert {
                "project_list",
                "audit_query",
                "alarm_pipeline_list",
                "alarm_pipeline_status",
                "config_resource_search",
                "config_resource_describe",
                "config_resource_names",
                "config_resource_list",
                "config_resource_get",
            }.issubset(snapshot.semantic_capabilities)
            with pytest.raises(TypeError):
                snapshot.resource_types["x"] = snapshot.resource_types["ignition/system-properties"]  # type: ignore[index]

            context = OperationContext.start("config_resource_search", "test", "FAST")
            found = config_resource_search(registry, context, query="system", limit=100, offset=0)
            assert [item.resourceType for item in found.items] == ["ignition/system-properties"]
            assert found.items[0].singleton is True
            assert found.items[0].supportsNames is False

            project_context = OperationContext.start("project_list", "test", "FAST")
            projects = await project_list(client, registry, project_context, search="", limit=2, offset=0)
            assert [item.name for item in projects.items] == ["Demo"]

            get_context = OperationContext.start("config_resource_get", "test", "FAST")
            normal = await config_resource_get(
                client,
                registry,
                get_context,
                resource_type="ignition/database-connection",
                name="Main",
                collection="",
                default_if_undefined=False,
            )
            assert normal.resource["config"]["username"] == "alice"
            assert normal.resource["config"]["password"] == "<redacted>"
            assert normal.resource["config"]["credential"] == {"type": "Embedded", "data": "<redacted>"}

            singleton = await config_resource_get(
                client,
                registry,
                get_context,
                resource_type="ignition/system-properties",
                name="",
                collection="",
                default_if_undefined=True,
            )
            assert singleton.resource["config"]["systemName"] == "ci"

            with pytest.raises(GatewayError, match="unsupported_capability"):
                await config_resource_get(
                    client,
                    registry,
                    get_context,
                    resource_type="ignition/not-from-openapi",
                    name="anything",
                    collection="",
                    default_if_undefined=False,
                )
        finally:
            await registry.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "limit,offset",
    [(0, 0), (501, 0), (100, -1), (100, 1_000_001)],
)
def test_collection_budget_rejects_out_of_range(limit: int, offset: int) -> None:
    async def scenario() -> None:
        client = GatewayClient(
            base_url="http://gateway",
            api_token="ci:key",
            timeout_seconds=10,
            transport=_transport(),
        )
        registry = CapabilityRegistry(client)
        try:
            await registry.refresh()
            context = OperationContext.start("project_list", "test", "FAST")
            with pytest.raises(GatewayError, match="invalid_argument"):
                await project_list(client, registry, context, search="", limit=limit, offset=offset)
        finally:
            await registry.aclose()
            await client.aclose()

    asyncio.run(scenario())


def _collection_transport(
    operation_path: str, payload: dict[str, object], state: dict[str, int],
) -> httpx.MockTransport:
    paths = {
        "/data/api/v1/gateway-info": {"get": {}},
        operation_path: {"get": {}},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/api/v1/gateway-info":
            return httpx.Response(200, json={"ignitionVersion": "8.3.8"}, request=request)
        if request.url.path == "/data/api/v1/modules/healthy":
            return httpx.Response(200, json={"items": []}, request=request)
        if request.url.path == "/openapi.json":
            state["refreshes"] += 1
            return httpx.Response(200, content=json.dumps({"paths": paths}).encode(), request=request)
        if request.url.path == operation_path:
            state["operations"] += 1
            return httpx.Response(200, json=payload, request=request)
        raise AssertionError(str(request.url))

    return httpx.MockTransport(handler)


def _project(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": "",
        "title": name,
        "enabled": True,
        "parent": "",
        "inheritable": False,
        "invalidParent": False,
        "mutable": True,
        "defaultDb": "",
        "tagProvider": "default",
        "userSource": "",
        "identityProvider": "",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "items": [_project("one"), _project("two")],
            "metadata": {"total": 2, "matching": 2, "limit": 1, "offset": 0},
        },
        {
            "items": [],
            "metadata": {"total": 1, "matching": 1, "limit": 1, "offset": 0},
        },
        {
            "items": [_project("one")],
            "metadata": {"total": 1, "matching": 1, "limit": 2, "offset": 0},
        },
        {
            "items": [_project("one")],
            "metadata": {"total": 1, "matching": 1, "limit": 1, "offset": 1},
        },
        {
            "items": [_project("one")],
            "metadata": {"total": 0, "matching": 1, "limit": 1, "offset": 0},
        },
    ],
    ids=["over-return", "under-return", "limit", "offset", "counts"],
)
def test_collection_response_mismatch_marks_registry_stale_without_replay(
    payload: dict[str, object],
) -> None:
    async def scenario() -> None:
        state = {"refreshes": 0, "operations": 0}
        client = GatewayClient(
            base_url="http://gateway",
            api_token="ci:key",
            timeout_seconds=10,
            transport=_collection_transport("/data/api/v1/projects/list", payload, state),
        )
        registry = CapabilityRegistry(client)
        try:
            assert (await registry.refresh()).state == "READY"
            with pytest.raises(GatewayError) as captured:
                await project_list(
                    client,
                    registry,
                    OperationContext.start("project_list", "test", "FAST"),
                    search="",
                    limit=1,
                    offset=0,
                )
            assert captured.value.code == "schema_mismatch"
            assert registry.snapshot.state == "STALE"
            assert state == {"refreshes": 2, "operations": 1}
        finally:
            await registry.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_pydantic_response_validation_is_schema_mismatch_and_reconciles_once() -> None:
    async def scenario() -> None:
        payload: dict[str, object] = {
            "items": [{
                "path": "project:/pipeline",
                "projectName": "project",
                "pipelineName": "pipeline",
                "itemCount": -1,
                "active": True,
            }],
            "metadata": {"total": 1, "matching": 1, "limit": 1, "offset": 0},
        }
        state = {"refreshes": 0, "operations": 0}
        client = GatewayClient(
            base_url="http://gateway",
            api_token="ci:key",
            timeout_seconds=10,
            transport=_collection_transport("/data/alarm-notification/api/v1/pipelines", payload, state),
        )
        registry = CapabilityRegistry(client)
        try:
            assert (await registry.refresh()).state == "READY"
            with pytest.raises(GatewayError) as captured:
                await alarm_pipeline_list(
                    client,
                    registry,
                    OperationContext.start("alarm_pipeline_list", "test", "FAST"),
                    search="",
                    limit=1,
                    offset=0,
                )
            assert captured.value.code == "schema_mismatch"
            assert registry.snapshot.state == "STALE"
            assert state == {"refreshes": 2, "operations": 1}
        finally:
            await registry.aclose()
            await client.aclose()

    asyncio.run(scenario())
