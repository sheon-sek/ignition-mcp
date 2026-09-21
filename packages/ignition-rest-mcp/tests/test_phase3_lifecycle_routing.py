"""Slice 1 static + wire proof that every registered Tool routes through the
single central invocation lifecycle (D18/D10)."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.models import (
    AlarmPipelineListResult,
    AlarmPipelineStatusResult,
    AuditQueryResult,
    ConfigResourceDescribeResult,
    ConfigResourceGetResult,
    ConfigResourceListResult,
    ConfigResourceNamesResult,
    ConfigResourceSearchResult,
    ConfigResourceTypeSummary,
    GatewayDiagnoseResult,
    GatewayInfoResult,
    PageMetadata,
    ProjectListResult,
)
from ignition_rest_mcp.operation import is_uuid7
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.records import OperationRecordStore
from ignition_rest_mcp.storage.schema import STATE_DDL
from test_config import _settings

SERVER_SOURCE = Path(server_module.__file__).resolve()


def _registered_tool_functions() -> list[ast.AsyncFunctionDef]:
    tree = ast.parse(SERVER_SOURCE.read_text(encoding="utf-8"))
    tools: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                tools.append(node)
    return tools


def test_every_registered_tool_routes_through_the_single_invoke() -> None:
    tools = _registered_tool_functions()
    # 11 Phase 2 tools + the two sensitive exports (slice 5) + three storage tools
    # (slice 8) + the first Phase 4 REST Mutation Tool (milestone 4c), whose
    # operation-record path is pinned in test_phase4_config_resource_update.py
    # because a CONFIG-scope credential is needed to reach it.
    assert len(tools) == 17, "registered REST Tool functions drifted from the routed inventory"
    for node in tools:
        calls = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        assert "_invoke" in calls, f"{node.name} bypasses the central lifecycle"
        # Handlers must not build their own correlation context.
        constructed = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "OperationContext"
        }
        assert not constructed, f"{node.name} constructs its own OperationContext"


def _page() -> PageMetadata:
    return PageMetadata(total=1, matching=1, limit=100, offset=0)


def _stub_services(context_name: str) -> dict[str, Any]:
    async def gateway_info(*args: Any) -> GatewayInfoResult:
        return GatewayInfoResult(
            correlationId=args[2].correlation_id, name="g", edition="standard",
            ignitionVersion="8.3.8", redundancyRole="Independent", deploymentMode="",
            timeZoneId="UTC", jvmVersion="17",
        )

    async def gateway_diagnose(*args: Any) -> GatewayDiagnoseResult:
        return GatewayDiagnoseResult(
            correlationId=args[2].correlation_id, gatewayReachable=True, authenticationOk=True,
            registryState="READY", registryGeneration=1, openapiSha256=None,
            gatewayVersion="8.3.8", moduleCount=0, message="ok",
        )

    async def project_list(*args: Any, **kwargs: Any) -> ProjectListResult:
        return ProjectListResult(
            correlationId=args[2].correlation_id, items=[], page=_page(),
        )

    def config_resource_search(*args: Any, **kwargs: Any) -> ConfigResourceSearchResult:
        context = kwargs["context"] if "context" in kwargs else args[1]
        return ConfigResourceSearchResult(
            correlationId=context.correlation_id,
            items=[ConfigResourceTypeSummary(
                resourceType="ignition/database-connection", module="ignition", typeId="database-connection",
                singleton=False, supportsNames=True, supportsList=True, supportsGet=True,
            )],
            page=_page(),
        )

    async def config_resource_describe(*args: Any, **kwargs: Any) -> ConfigResourceDescribeResult:
        return ConfigResourceDescribeResult(
            correlationId=args[2].correlation_id, resourceType="ignition/database-connection",
            description={},
        )

    async def config_resource_names(*args: Any, **kwargs: Any) -> ConfigResourceNamesResult:
        return ConfigResourceNamesResult(
            correlationId=args[2].correlation_id, resourceType="ignition/database-connection",
            items=[], page=_page(),
        )

    async def config_resource_list(*args: Any, **kwargs: Any) -> ConfigResourceListResult:
        return ConfigResourceListResult(
            correlationId=args[2].correlation_id, resourceType="ignition/database-connection",
            items=[], page=_page(),
        )

    async def config_resource_get(*args: Any, **kwargs: Any) -> ConfigResourceGetResult:
        return ConfigResourceGetResult(
            correlationId=args[2].correlation_id, resourceType="ignition/database-connection",
            resource={}, signature="sig-1",
        )

    async def audit_query(*args: Any, **kwargs: Any) -> AuditQueryResult:
        return AuditQueryResult(correlationId=args[2].correlation_id, profile="default", items=[], page=_page())

    async def alarm_pipeline_list(*args: Any, **kwargs: Any) -> AlarmPipelineListResult:
        return AlarmPipelineListResult(correlationId=args[2].correlation_id, items=[], page=_page())

    async def alarm_pipeline_status(*args: Any, **kwargs: Any) -> AlarmPipelineStatusResult:
        return AlarmPipelineStatusResult(
            correlationId=args[2].correlation_id, path="x", items=[], page=_page(),
        )

    return {
        "info_service": gateway_info,
        "diagnose_service": gateway_diagnose,
        "project_list_service": project_list,
        "config_resource_search_service": config_resource_search,
        "config_resource_describe_service": config_resource_describe,
        "config_resource_names_service": config_resource_names,
        "config_resource_list_service": config_resource_list,
        "config_resource_get_service": config_resource_get,
        "audit_query_service": audit_query,
        "alarm_pipeline_list_service": alarm_pipeline_list,
        "alarm_pipeline_status_service": alarm_pipeline_status,
    }


CALLS: dict[str, dict[str, Any]] = {
    "gateway_info": {},
    "gateway_diagnose": {},
    "project_list": {"search": "", "limit": 100, "offset": 0},
    "config_resource_search": {"query": "db", "limit": 100, "offset": 0},
    "config_resource_describe": {"resourceType": "ignition/database-connection"},
    "config_resource_names": {"resourceType": "ignition/database-connection"},
    "config_resource_list": {"resourceType": "ignition/database-connection"},
    "config_resource_get": {"resourceType": "ignition/database-connection", "name": "Main"},
    "audit_query": {"profile": "default"},
    "alarm_pipeline_list": {},
    "alarm_pipeline_status": {"path": "Pipelines/Notify"},
}


@pytest.fixture
def stubbed_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    async def info(self: GatewayClient, context: Any = None) -> dict[str, str]:
        return {"ignitionVersion": "8.3.8"}

    async def modules(self: GatewayClient, context: Any = None) -> dict[str, list[Any]]:
        return {"items": []}

    async def openapi(self: GatewayClient) -> bytes:
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
        }
        return json.dumps({"paths": paths}).encode()

    monkeypatch.setattr(GatewayClient, "gateway_info", info)
    monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
    monkeypatch.setattr(GatewayClient, "openapi", openapi)
    for name, replacement in _stub_services("unused").items():
        monkeypatch.setattr(server_module, name, replacement)


def test_every_tool_produces_exactly_one_completed_operation_record(
    tmp_path: Path, stubbed_gateway: None,
) -> None:
    settings = _settings(data_dir=str(tmp_path))

    async def scenario() -> dict[str, str]:
        correlations: dict[str, str] = {}
        server = server_module.create_server(settings)
        async with Client(server) as client:
            for tool, arguments in CALLS.items():
                result = await client.call_tool(tool, arguments)
                assert not result.is_error, f"{tool} failed: {result.content}"
                correlation = result.data.correlationId
                assert is_uuid7(correlation), tool
                correlations[tool] = correlation
        return correlations

    correlations = asyncio.run(scenario())
    assert set(correlations) == set(CALLS)
    assert len(set(correlations.values())) == len(CALLS), "each Tool must get its own correlation ID"

    async def read_records() -> list[Any]:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        store = OperationRecordStore(db)
        rows = []
        for tool, correlation in correlations.items():
            record = await store.fetch(correlation)
            assert record is not None, f"{tool} skipped operation recording"
            assert record.outcome == "succeeded", tool
            assert record.tool == tool
            assert record.status == "completed"
            rows.append(record)
        assert await store.count() == len(CALLS)
        await db.close()
        return rows

    asyncio.run(read_records())
