"""Phase 4 ticket #20: the audit row of a Mutation whose dispatch was cancelled.

D23's injected failures include timeout, ambiguous outcome and cancellation. The live
harness proves them against a real Gateway through the fault-injecting proxy
(``tests/harness/phase4-live-rest/fault_proxy.py``); this module pins what a live run can
only observe from the outside: a dispatch that dies in flight leaves D18's
decision/attempt/result triple with **one** result row that names the boundary and keeps
the Target on it, and the caller ends with the deployment's timeout — never with a
second, contradictory ``failed`` row, and never with a replay.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastmcp.exceptions import ToolError
import httpx
import pytest

from ignition_rest_mcp.audit.sink import SqliteAuditSink
from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry, CapabilitySnapshot
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.invocation.lifecycle import invoke_tool
from ignition_rest_mcp.models import GatewayInfoResult
from ignition_rest_mcp.observability.metrics import Metrics
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.safety.executor import (
    MutationRequest,
    VerificationOutcome,
    execute_mutation,
    mutation_failure,
)
from ignition_rest_mcp.safety.policy import CONFIG_MUTATION, MutationOperation
from ignition_rest_mcp.storage.database import Storage
from ignition_rest_mcp.storage.records import OperationRecordStore
from test_config import _settings

#: The Target the preflight has to allowlist, and the one the audit row must name.
TARGET = "ignition/audit-profile/MCP_CI_AUDIT"
UPDATE = MutationOperation(
    op_id="config_resource_update",
    mutation_class=CONFIG_MUTATION,
    capability="config_resource_update",
    destructive=False,
    target_denial_code="permission_denied",
    rejection_is_final=True,
)
PRINCIPAL = VerifiedPrincipal._mint(
    key="static-token:config-agent",
    scopes=frozenset({"ignition.read", "ignition.config"}),
    auth_mode="static-token",
)
RESULT = GatewayInfoResult(
    correlationId="c", name="g", edition="standard", ignitionVersion="8.3.8",
    redundancyRole="Independent", deploymentMode="", timeZoneId="UTC", jvmVersion="17",
)


class _HangingTransport(httpx.AsyncBaseTransport):
    """A transport that takes the whole write and then never answers.

    The body is consumed first, so this is the fault the proxy injects with
    ``delay_response``: the complete request reached the Gateway and no answer came back
    before the deployment's deadline.
    """

    def __init__(self, started: asyncio.Event) -> None:
        self._started = started

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self._started.set()
        await asyncio.Event().wait()  # pragma: no cover - the deadline always wins
        raise AssertionError("the hanging transport answered")  # pragma: no cover


class _State:
    """The parts of ``RuntimeState`` an audited invocation needs, storage-backed."""

    def __init__(self, tmp_path: Path) -> None:
        self.metrics = Metrics()
        self.storage = Storage(tmp_path)

    async def open(self) -> None:
        await self.storage.open()
        self.records = OperationRecordStore(self.storage.state)
        self.audit_sink = SqliteAuditSink(self.storage.audit)

    async def close(self) -> None:
        await self.storage.close()

    def require_metrics(self) -> Metrics:
        return self.metrics

    def require_records(self) -> OperationRecordStore:
        return self.records  # type: ignore[attr-defined,no-any-return]

    def require_audit_sink(self) -> SqliteAuditSink:
        return self.audit_sink  # type: ignore[attr-defined,no-any-return]

    def require_registry(self) -> CapabilityRegistry:
        registry = CapabilityRegistry.__new__(CapabilityRegistry)
        registry._snapshot = CapabilitySnapshot.unavailable()
        registry._refresh_lock = asyncio.Lock()
        registry._refresh_task = None
        return registry

    async def audit(self) -> list[dict[str, Any]]:
        rows = await self.storage.audit.run(
            lambda conn: conn.execute(
                "SELECT phase, outcome, error_code, target_type, target_id FROM audit_log"
                " ORDER BY seq",
            ).fetchall()
        )
        keys = ("phase", "outcome", "error_code", "target_type", "target_id")
        return [dict(zip(keys, row, strict=True)) for row in rows]


class _Env:
    def __init__(self, tmp_path: Path, started: asyncio.Event, **overrides: Any) -> None:
        self.settings: Settings = _settings(
            data_dir=str(tmp_path),
            config_mutation_enabled=True,
            mutation_operations=("config_resource_update",),
            mutation_targets={"config_resource_update": (TARGET,)},
            **overrides,
        )
        self.state = _State(tmp_path)
        self.client = GatewayClient(
            base_url="http://gw", api_token="t", timeout_seconds=10,
            transport=_HangingTransport(started),
        )
        # The capability layer only has to answer "is the route there"; the D04 registry
        # itself is exercised by the tools' own tests.
        self.registry: Any = SimpleNamespace(supports=lambda _capability: True)

    async def invoke(self) -> GatewayInfoResult:
        async def handler(context: OperationContext) -> GatewayInfoResult:
            mutation = await execute_mutation(
                client=self.client, registry=self.registry, settings=self.settings,
                context=context, request=self.request(),
            )
            failure = mutation_failure(mutation)
            if failure is not None:
                raise failure
            return RESULT

        return await invoke_tool(
            state=self.state,  # type: ignore[arg-type]
            settings=self.settings,
            tool=UPDATE.op_id,
            budget_class="FAST",
            principal=PRINCIPAL,
            handler=handler,
            permission_class="CONFIG",
            destructive=False,
            audited=True,
        )

    def request(self) -> MutationRequest:
        async def body() -> Any:
            yield b'{"config": {}}'

        async def verify(_dispatch: Any) -> VerificationOutcome:
            return VerificationOutcome.INDETERMINATE

        return MutationRequest(
            operation=UPDATE, principal=PRINCIPAL, target_id=TARGET,
            request_path="/data/api/v1/resources/ignition%2Faudit-profile",
            method="PUT", body_chunks=body(), content_type="application/json",
            dispatch_deadline_seconds=self.settings.tool_timeout_seconds,
            verification_deadline_seconds=self.settings.tool_timeout_seconds,
            verify=verify, audit_fields={"resourceType": "ignition/audit-profile"},
            target_type="config-resource",
        )


def _envelope(message: str) -> dict[str, Any]:
    return dict(json.loads(str(message)))


def test_dispatch_deadline_audits_the_boundary_and_the_callers_code(tmp_path: Path) -> None:
    """A dispatch that outlives the budget records its boundary, then the caller's code.

    D18's log carries two result rows for one invocation and the frozen suite pins the
    shape: the guarded executor's row names what happened to the *dispatch*
    (``cancelled`` with ``outcome_unknown``, the write possibly applied), and the
    lifecycle's row is the outcome the call ended with (the D06 code the caller saw).
    """

    env = _Env(tmp_path, asyncio.Event(), tool_timeout_seconds=0.2)

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        await env.state.open()
        try:
            with pytest.raises(ToolError) as caught:
                await env.invoke()
            return _envelope(caught.value.args[0] if caught.value.args else str(caught.value)), (
                await env.state.audit()
            )
        finally:
            await env.state.close()

    envelope, rows = asyncio.run(scenario())

    assert envelope["code"] == "timeout"
    # One decision, one attempt, and the boundary: the executor's row must land even
    # though the task is being cancelled as it writes it.
    assert [row["phase"] for row in rows] == ["decision", "attempt", "result", "result"]
    assert [row["outcome"] for row in rows] == ["allowed", "attempted", "cancelled", "failed"]
    assert rows[2]["error_code"] == "outcome_unknown"
    # ...and the row is complete: it names the Target the cancelled write was about.
    assert rows[2]["target_type"] == "config-resource"
    assert rows[2]["target_id"] == TARGET
    assert rows[3]["error_code"] == "timeout"


def test_client_cancellation_records_the_boundary_once(tmp_path: Path) -> None:
    """Cancelling the call mid-dispatch still leaves one row naming the boundary."""

    started = asyncio.Event()
    env = _Env(tmp_path, started, tool_timeout_seconds=30.0)

    async def scenario() -> list[dict[str, Any]]:
        await env.state.open()
        try:
            call = asyncio.create_task(env.invoke())
            await asyncio.wait_for(started.wait(), timeout=5)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            return await env.state.audit()
        finally:
            await env.state.close()

    rows = asyncio.run(scenario())

    assert [row["phase"] for row in rows] == ["decision", "attempt", "result", "result"]
    assert [row["outcome"] for row in rows] == ["allowed", "attempted", "cancelled", "cancelled"]
    assert rows[2]["error_code"] == "outcome_unknown"
    assert rows[2]["target_id"] == TARGET
