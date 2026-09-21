"""Slice 6 (Phase 3 / G3): behavioral tests for the guarded D08 mutation executor.

Every layer of the safety chain is exercised against a real SQLite audit/state
stack and an httpx.MockTransport boundary: authentication, authorization scope,
deployment class, operation/target allowlists, capability, precondition hooks,
the exactly-once dispatch with its typed ambiguity boundaries, bounded
verification, fail-closed audit ordering and cancellation hygiene.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import inspect
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import ignition_rest_mcp.auth as auth_module
from ignition_rest_mcp.audit.sink import AuditWriteError, Auditor, SqliteAuditSink
from ignition_rest_mcp.auth import Principal, VerifiedPrincipal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import DispatchOutcome, GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.observability.metrics import Metrics
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.safety.executor import (
    MutationOperation,
    MutationRequest,
    MutationState,
    VerificationOutcome,
    execute_mutation,
)
from ignition_rest_mcp.storage.database import Storage
from ignition_rest_mcp.storage.records import OperationRecordStore
from test_config import _settings

CONFIG_SCOPE = "ignition.config"
PROJECT_IMPORT_PATH = "/data/api/v1/projects/import/{name}"

_OPERATION = MutationOperation(
    op_id="project_import", mutation_class="CONFIG_MUTATION",
    capability="project_import", destructive=True,
)

_CONFIG_PRINCIPAL = VerifiedPrincipal._mint(
    key="jwt:configurator", scopes=frozenset({CONFIG_SCOPE, "ignition.read"}), auth_mode="jwt",
)
_READ_ONLY_PRINCIPAL = VerifiedPrincipal._mint(
    key="none:anonymous", scopes=frozenset({"ignition.read"}), auth_mode="none",
)
_STATIC_READ_ONLY_PRINCIPAL = VerifiedPrincipal._mint(
    key="static:service", scopes=frozenset({"ignition.read"}), auth_mode="static",
)


class _ForgedPrincipal:
    """Duck-typed impostor with the right attributes; not minted by auth.py."""

    def __init__(self) -> None:
        self.key = "jwt:configurator"
        self.scopes = frozenset({CONFIG_SCOPE})
        self.auth_mode = "jwt"

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _openapi(*, with_import: bool = True) -> bytes:
    paths: dict[str, Any] = {
        "/data/api/v1/gateway-info": {"get": {}},
        "/data/api/v1/projects/list": {"get": {}},
    }
    if with_import:
        paths[PROJECT_IMPORT_PATH] = {"post": {}}
    return json.dumps({"paths": paths}).encode()


class _LazyMockTransport(httpx.MockTransport):
    """httpx.MockTransport unconditionally awaits ``request.aread()`` before the
    handler runs, which drains the streaming body and masks the pre-response
    phase flags (a ConnectError/WriteError would then be misclassified as
    SENT_COMPLETE_NO_RESPONSE). This variant keeps the handler contract but, like
    a real transport, only consumes the body when the handler itself reads it."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = self.handler(request)
        if not isinstance(response, httpx.Response):
            response = await response
        return response


class _Env:
    def __init__(self, tmp_path: Path, handler: Any, *, with_import: bool = True,
                 **settings_overrides: Any) -> None:
        settings_kwargs: dict[str, Any] = {
            "config_mutation_enabled": True,
            "mutation_operations": ("project_import",),
            "mutation_targets": {"project_import": ("disposable",)},
            "sensitive_exports_enabled": True,
        }
        settings_kwargs.update(settings_overrides)
        self.settings = _settings(data_dir=str(tmp_path), **settings_kwargs)
        self._openapi_doc = _openapi(with_import=with_import)
        self.calls = 0
        self.requests: list[httpx.Request] = []

        async def counting(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            self.requests.append(request)
            result = handler(request)
            if inspect.isawaitable(result):
                result = await result
            return result

        self.client = GatewayClient(
            base_url="http://gw", api_token="t", timeout_seconds=10,
            transport=_LazyMockTransport(counting),
        )
        self.registry = CapabilityRegistry(self.client)
        self.storage = Storage(tmp_path)
        self.metrics = Metrics()

    async def start(self) -> None:
        await self.storage.open()
        self.records = OperationRecordStore(self.storage.state)
        self.sink = SqliteAuditSink(self.storage.audit)

        async def openapi() -> bytes:
            return self._openapi_doc

        async def gateway_info(context: Any = None) -> dict[str, str]:
            return {"ignitionVersion": "8.3.8"}

        async def modules(context: Any = None) -> dict[str, list[Any]]:
            return {"items": []}

        self.client.openapi = openapi  # type: ignore[method-assign]
        self.client.gateway_info = gateway_info  # type: ignore[method-assign]
        self.client.healthy_modules = modules  # type: ignore[method-assign]
        await self.registry.refresh()

        # Deviation note (asserted production behavior): the real CapabilityRegistry
        # derives semantic capabilities only from GET read paths, so "project_import"
        # is NOT yet a derivable capability (the mapping lands with the Phase 4
        # mutation tools). The shim below derives presence from the *real* OpenAPI
        # endpoint inventory of the refreshed snapshot, so the "registry without the
        # import path" denial is still exercised against genuine registry contents.
        real_supports = self.registry.supports

        def supports(capability: str) -> bool:
            if capability == "project_import":
                return (
                    ("POST", PROJECT_IMPORT_PATH) in self.registry.snapshot.endpoints
                    and self.registry.snapshot.state in {"READY", "STALE"}
                )
            return real_supports(capability)

        self.registry.supports = supports  # type: ignore[method-assign]

    async def stop(self) -> None:
        await self.client.aclose()
        await self.storage.close()

    def context(self) -> OperationContext:
        context = OperationContext.start("project_import", "jwt:configurator", "ARTIFACT")
        context.auditor = Auditor(self.sink, self.records, context, self.metrics)
        return context

    async def audit_rows(self) -> list[tuple[str, str]]:
        return list(await self.storage.audit.run(
            lambda conn: conn.execute("SELECT phase, outcome FROM audit_log ORDER BY seq").fetchall()
        ))


def _single_chunk_body() -> Any:
    async def body() -> Any:
        yield b"payload"

    return body()


def _request(**overrides: Any) -> MutationRequest:
    values: dict[str, Any] = {
        "operation": _OPERATION,
        "principal": _CONFIG_PRINCIPAL,
        "target_id": "disposable",
        "request_path": "/data/api/v1/projects/import/disposable",
        "body_chunks": _single_chunk_body(),
        "content_type": "application/zip",
        "method": "POST",
        "dispatch_deadline_seconds": 2.0,
        "verification_deadline_seconds": 2.0,
    }
    values.update(overrides)
    if "target_id" in overrides:
        values["request_path"] = f"/data/api/v1/projects/import/{overrides['target_id']}"
    return MutationRequest(**values)


def _verifier(outcome: VerificationOutcome, calls: list[str]) -> Any:
    async def verify(dispatch: Any) -> VerificationOutcome:
        calls.append(dispatch.outcome.value)
        return outcome

    return verify


def _raising_verifier(calls: list[str]) -> Any:
    async def verify(dispatch: Any) -> VerificationOutcome:
        calls.append(dispatch.outcome.value)
        raise RuntimeError("state probe exploded")

    return verify


async def _slow_verify(dispatch: Any) -> VerificationOutcome:
    await asyncio.sleep(1.0)
    return VerificationOutcome.CONFIRMED


# ------------------------------------------------------------------ transport handlers


async def _connect_fail(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


async def _write_fail(request: httpx.Request) -> httpx.Response:
    # Deliberately does NOT read the body: the generator stays un-drained, so the
    # boundary is classified from the phase flags as SENT_PARTIAL.
    raise httpx.WriteError("write", request=request)


async def _read_fail(request: httpx.Request) -> httpx.Response:
    await request.aread()  # pulls httpx through the tracking generator
    raise httpx.ReadError("gone", request=request)


def _status(status: int) -> Any:
    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(status, json={"ok": True}, request=request)

    return handler


def _run(env: _Env, request: MutationRequest) -> tuple[Any, list[tuple[str, str]]]:
    async def scenario() -> tuple[Any, list[tuple[str, str]]]:
        await env.start()
        try:
            context = env.context()
            result = await execute_mutation(
                client=env.client, registry=env.registry,
                settings=env.settings, context=context, request=request,
            )
            return result, await env.audit_rows()
        finally:
            await env.stop()

    return asyncio.run(scenario())


# ------------------------------------------------------------------ happy path

def test_allowed_verified_dispatch_succeeds_exactly_once(tmp_path: Path) -> None:
    env = _Env(tmp_path, _status(201))
    calls: list[str] = []
    result, rows = _run(env, _request(verify=_verifier(VerificationOutcome.CONFIRMED, calls)))

    assert result.state is MutationState.SUCCEEDED
    assert result.error is None
    assert result.dispatch is not None and result.dispatch.status == 201
    assert result.dispatch.outcome is DispatchOutcome.RESPONDED
    assert calls == ["responded"]
    assert rows == [("decision", "allowed"), ("attempt", "attempted"), ("result", "completed")]
    assert env.calls == 1  # never retried
    request = env.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/data/api/v1/projects/import/disposable"
    assert request.headers["content-type"] == "application/zip"
    assert request.content == b"payload"


# ------------------------------------------------------------------ known non-attempt

def test_connect_failure_is_a_known_non_attempt(tmp_path: Path) -> None:
    env = _Env(tmp_path, _connect_fail)
    calls: list[str] = []
    result, rows = _run(env, _request(verify=_verifier(VerificationOutcome.CONFIRMED, calls)))

    assert result.state is MutationState.NOT_SENT
    assert result.error is not None and result.error.code == "gateway_unavailable"
    assert result.dispatch is not None and result.dispatch.outcome is DispatchOutcome.NOT_SENT
    assert calls == []  # verification is never run for a known non-attempt
    assert rows == [("decision", "allowed"), ("attempt", "attempted"), ("result", "not_sent")]
    assert env.calls == 1  # no retry


# ------------------------------------------------------------------ ambiguous boundaries

def _ambiguous_cases() -> list[tuple[str, str, MutationState, str | None, str, DispatchOutcome]]:
    cases: list[tuple[str, str, MutationState, str | None, str, DispatchOutcome]] = []
    for boundary, outcome in (
        ("write", DispatchOutcome.SENT_PARTIAL),
        ("read", DispatchOutcome.SENT_COMPLETE_NO_RESPONSE),
        ("status500", DispatchOutcome.RESPONDED),
    ):
        cases.append((boundary, "unchanged", MutationState.NOT_APPLIED, "conflict", "not_applied", outcome))
        cases.append((boundary, "confirmed", MutationState.RECOVERED_SUCCESS, None, "recovered_success", outcome))
        cases.append(
            (boundary, "indeterminate", MutationState.OUTCOME_UNKNOWN, "outcome_unknown",
             "outcome_unknown", outcome),
        )
    return cases


@pytest.mark.parametrize(
    ("boundary", "verified", "state", "error_code", "result_row", "dispatch_outcome"),
    _ambiguous_cases(),
    ids=[f"{b}-{v}" for b, v, *_ in _ambiguous_cases()],
)
def test_ambiguous_boundaries_verify_then_classify_never_replay(
    tmp_path: Path, boundary: str, verified: str, state: MutationState,
    error_code: str | None, result_row: str, dispatch_outcome: DispatchOutcome,
) -> None:
    handlers = {"write": _write_fail, "read": _read_fail, "status500": _status(500)}
    env = _Env(tmp_path, handlers[boundary])
    calls: list[str] = []
    request = _request(verify=_verifier(VerificationOutcome(verified), calls))
    result, rows = _run(env, request)

    assert result.state is state
    assert result.dispatch is not None and result.dispatch.outcome is dispatch_outcome
    assert (result.error.code if result.error else None) == error_code
    assert result.possibly_dispatched is True
    assert calls == [dispatch_outcome.value]  # verified exactly once against the boundary
    assert rows == [("decision", "allowed"), ("attempt", "attempted"), ("result", result_row)]
    assert env.calls == 1  # ambiguous outcomes are verified, never replayed


# ------------------------------------------------------------------ 2xx interpretation

def test_2xx_with_unchanged_state_requires_recovery(tmp_path: Path) -> None:
    env = _Env(tmp_path, _status(200))
    calls: list[str] = []
    result, rows = _run(env, _request(verify=_verifier(VerificationOutcome.UNCHANGED, calls)))

    assert result.state is MutationState.RECOVERY_REQUIRED
    assert result.error is not None and result.error.code == "outcome_unknown"
    assert rows == [
        ("decision", "allowed"), ("attempt", "attempted"), ("result", "recovery_required"),
    ]
    assert env.calls == 1


def test_2xx_with_indeterminate_verification_is_recovery_required(tmp_path: Path) -> None:
    # Plan 7.6: OUTCOME_UNKNOWN is reachable only from possibly-dispatched
    # (ambiguous) boundaries. A claimed success that cannot be verified is
    # dispatch-certain with unconfirmed state -> RECOVERY_REQUIRED, artifacts
    # preserved, never replayed.
    env = _Env(tmp_path, _status(200))
    calls: list[str] = []
    request = _request(verify=_verifier(VerificationOutcome.INDETERMINATE, calls))
    result, rows = _run(env, request)

    assert result.state is MutationState.RECOVERY_REQUIRED
    assert result.error is not None and result.error.code == "outcome_unknown"
    assert calls == ["responded"]
    assert rows == [
        ("decision", "allowed"), ("attempt", "attempted"), ("result", "recovery_required"),
    ]
    assert env.calls == 1


def test_2xx_with_failing_verifier_maps_to_recovery_required(tmp_path: Path) -> None:
    # _verify maps any verification *exception* to INDETERMINATE (no replay);
    # for a claimed success that is RECOVERY_REQUIRED.
    env = _Env(tmp_path, _status(200))
    calls: list[str] = []
    result, rows = _run(env, _request(verify=_raising_verifier(calls)))

    assert result.state is MutationState.RECOVERY_REQUIRED
    assert result.error is not None and result.error.code == "outcome_unknown"
    assert calls == ["responded"]
    assert rows == [
        ("decision", "allowed"), ("attempt", "attempted"), ("result", "recovery_required"),
    ]
    assert env.calls == 1


def test_2xx_verification_timeout_maps_to_recovery_required(tmp_path: Path) -> None:
    # A claimed success whose bounded verification times out cannot be
    # established: RECOVERY_REQUIRED with a result row (C unobtainable after a
    # claimed dispatch per D16 slice-7), exactly one dispatch, never a replay.
    env = _Env(tmp_path, _status(200))

    async def scenario() -> list[tuple[str, str]]:
        await env.start()
        try:
            context = env.context()
            request = _request(verify=_slow_verify, verification_deadline_seconds=0.05)
            result = await execute_mutation(
                client=env.client, registry=env.registry,
                settings=env.settings, context=context, request=request,
            )
            assert result.state is MutationState.RECOVERY_REQUIRED
            assert result.error is not None and result.error.code == "outcome_unknown"
            assert result.possibly_dispatched is True
            return await env.audit_rows()
        finally:
            await env.stop()

    rows = asyncio.run(scenario())
    assert rows == [
        ("decision", "allowed"), ("attempt", "attempted"), ("result", "recovery_required"),
    ]
    assert env.calls == 1


# ------------------------------------------------------------------ 4xx interpretation

def test_4xx_with_unchanged_state_is_rejected(tmp_path: Path) -> None:
    env = _Env(tmp_path, _status(409))
    calls: list[str] = []
    result, rows = _run(env, _request(verify=_verifier(VerificationOutcome.UNCHANGED, calls)))

    assert result.state is MutationState.REJECTED
    assert result.error is not None and result.error.code == "conflict"
    assert result.possibly_dispatched is False
    assert rows == [("decision", "allowed"), ("attempt", "attempted"), ("result", "rejected")]
    assert env.calls == 1


def test_4xx_with_changed_state_is_recorded_as_recovered_success(tmp_path: Path) -> None:
    # Honest recording: the Gateway said "no" but the verified state moved anyway —
    # the executor must not claim rejection.
    env = _Env(tmp_path, _status(409))
    calls: list[str] = []
    result, rows = _run(env, _request(verify=_verifier(VerificationOutcome.CONFIRMED, calls)))

    assert result.state is MutationState.RECOVERED_SUCCESS
    assert result.error is None
    assert rows == [
        ("decision", "allowed"), ("attempt", "attempted"), ("result", "recovered_success"),
    ]
    assert env.calls == 1


# ------------------------------------------------------------------ deny layers

async def _deny_precondition() -> None:
    raise GatewayError("conflict", "concurrent change detected")


_DENIAL_CASES: list[dict[str, Any]] = [
    {
        "id": "unauthenticated", "request": {"principal": Principal(
            key="jwt:configurator", scopes=frozenset({CONFIG_SCOPE}), auth_mode="jwt",
        )},
        "outcome": "denied:unauthenticated-principal", "code": "permission_denied",
    },
    {
        "id": "forged-duck-type", "request": {"principal": _ForgedPrincipal()},
        "outcome": "denied:unauthenticated-principal", "code": "permission_denied",
    },
    {
        "id": "authz-scope", "request": {"principal": _READ_ONLY_PRINCIPAL},
        "outcome": "denied:authz-scope:missing-scope:ignition.config", "code": "permission_denied",
    },
    {
        "id": "authz-scope-static-read-only", "request": {"principal": _STATIC_READ_ONLY_PRINCIPAL},
        "outcome": "denied:authz-scope:missing-scope:ignition.config", "code": "permission_denied",
    },
    {
        "id": "deployment-class", "settings": {"config_mutation_enabled": False},
        "outcome": "denied:deployment-class:class-disabled:CONFIG_MUTATION", "code": "operation_disabled",
    },
    {
        "id": "operation-allowlist", "settings": {"mutation_operations": ()},
        "outcome": "denied:operation-allowlist:operation-not-allowlisted", "code": "operation_disabled",
    },
    {
        "id": "target-allowlist", "settings": {"mutation_targets": {"project_import": ("another",)}},
        "outcome": "denied:target-allowlist:target-not-allowlisted", "code": "operation_disabled",
    },
    {
        "id": "capability", "with_import": False,
        "outcome": "denied:capability:capability-missing:project_import",
        "code": "unsupported_capability",
    },
    {
        "id": "precondition", "request": {"precondition": _deny_precondition},
        "outcome": "denied:precondition:conflict", "code": "conflict",
    },
]


def _assert_denied(env: _Env, request_kwargs: dict[str, Any], outcome: str, code: str) -> None:
    async def scenario() -> tuple[str, list[tuple[str, str]]]:
        await env.start()
        try:
            context = env.context()
            with pytest.raises(GatewayError) as caught:
                await execute_mutation(
                    client=env.client, registry=env.registry,
                    settings=env.settings, context=context, request=_request(**request_kwargs),
                )
            return caught.value.code, await env.audit_rows()
        finally:
            await env.stop()

    error_code, rows = asyncio.run(scenario())
    assert error_code == code
    # Exactly one denied decision row and no attempt: the deny is audited, the
    # request never reaches the transport.
    assert rows == [("decision", outcome)]
    assert env.calls == 0


@pytest.mark.parametrize("case", _DENIAL_CASES, ids=[case["id"] for case in _DENIAL_CASES])
def test_every_deny_layer_audits_one_decision_and_never_dispatches(tmp_path: Path, case: dict) -> None:
    env = _Env(
        tmp_path, _status(201),
        with_import=case.get("with_import", True),
        **case.get("settings", {}),
    )
    _assert_denied(env, case.get("request", {}), case["outcome"], case["code"])


def test_full_allowlists_still_deny_without_the_caller_scope(tmp_path: Path) -> None:
    # No single flag bypasses authentication/authorization: everything permissive,
    # but a read-only principal is still stopped at the authz-scope layer.
    env = _Env(
        tmp_path, _status(201),
        config_mutation_enabled=True, control_mutation_enabled=True, admin_mutation_enabled=True,
        mutation_operations=("*",), mutation_targets={"project_import": ("*",)},
    )
    _assert_denied(
        env, {"principal": _READ_ONLY_PRINCIPAL},
        "denied:authz-scope:missing-scope:ignition.config", "permission_denied",
    )


def test_class_flag_off_denies_even_with_everything_else_permissive(tmp_path: Path) -> None:
    env = _Env(
        tmp_path, _status(201),
        config_mutation_enabled=False,
        mutation_operations=("*",), mutation_targets={"project_import": ("*",)},
    )
    _assert_denied(
        env, {}, "denied:deployment-class:class-disabled:CONFIG_MUTATION", "operation_disabled",
    )


def test_wildcard_target_allowlist_dispatches_the_requested_target(tmp_path: Path) -> None:
    env = _Env(
        tmp_path, _status(201),
        mutation_targets={"project_import": ("*",)},
    )
    calls: list[str] = []
    result, rows = _run(
        env,
        _request(target_id="anything", verify=_verifier(VerificationOutcome.CONFIRMED, calls)),
    )
    assert result.state is MutationState.SUCCEEDED
    assert rows == [("decision", "allowed"), ("attempt", "attempted"), ("result", "completed")]
    assert env.calls == 1
    assert env.requests[0].url.path == "/data/api/v1/projects/import/anything"


# ------------------------------------------------------------------ audit failure paths

def test_failed_decision_audit_fails_closed_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _Env(tmp_path, _status(201))

    async def broken_write(self: SqliteAuditSink, row: Any) -> None:
        raise AuditWriteError("audit device is gone")

    monkeypatch.setattr(SqliteAuditSink, "write", broken_write)

    async def scenario() -> str:
        await env.start()
        try:
            context = env.context()
            with pytest.raises(GatewayError) as caught:
                await execute_mutation(
                    client=env.client, registry=env.registry, settings=env.settings,
                    context=context, request=_request(verify=_verifier(
                        VerificationOutcome.CONFIRMED, [],
                    )),
                )
            return caught.value.code
        finally:
            await env.stop()

    assert asyncio.run(scenario()) == "internal_error"
    assert env.calls == 0  # no dispatch after a failed audited decision
    assert env.metrics.audit_write_failures["decision"] == 1


def test_missing_result_row_marks_the_operation_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _Env(tmp_path, _status(201))
    real_write = SqliteAuditSink.write

    async def fail_result_only(self: SqliteAuditSink, row: Any) -> None:
        if row.phase == "result":
            raise AuditWriteError("result row lost")
        await real_write(self, row)

    monkeypatch.setattr(SqliteAuditSink, "write", fail_result_only)

    async def scenario() -> tuple[Any, Any, list[tuple[str, str]]]:
        await env.start()
        try:
            context = env.context()
            await env.records.start(context)  # the row the D18 marker updates
            calls: list[str] = []
            result = await execute_mutation(
                client=env.client, registry=env.registry, settings=env.settings,
                context=context, request=_request(verify=_verifier(
                    VerificationOutcome.CONFIRMED, calls,
                )),
            )
            record = await env.records.fetch(context.correlation_id)
            return result, record, await env.audit_rows()
        finally:
            await env.stop()

    result, record, rows = asyncio.run(scenario())
    # A lost result row never rewrites a known outcome...
    assert result.state is MutationState.SUCCEEDED
    assert rows == [("decision", "allowed"), ("attempt", "attempted")]
    # ...but the interruption is durably flagged on the operation record.
    assert record is not None
    assert record.audit_result_missing is True
    assert env.calls == 1


# ------------------------------------------------------------------ cancellation

def test_mid_stream_cancellation_shields_the_cancelled_result_row(tmp_path: Path) -> None:
    env = _Env(tmp_path, _status(201))  # the handler reads the body: cancel hits mid-stream

    async def cancelling_body() -> Any:
        yield b"first-chunk"
        raise asyncio.CancelledError

    request = replace(_request(verify=_verifier(VerificationOutcome.CONFIRMED, [])),
                      body_chunks=cancelling_body())

    async def scenario() -> list[tuple[str, str]]:
        await env.start()
        try:
            context = env.context()
            with pytest.raises(asyncio.CancelledError):
                await execute_mutation(
                    client=env.client, registry=env.registry,
                    settings=env.settings, context=context, request=request,
                )
            return await env.audit_rows()
        finally:
            await env.stop()

    rows = asyncio.run(scenario())
    # The executor re-raises the cancellation but shields the result-row write, and
    # because no further cancellation is delivered the row lands: this asserts the
    # full expectation (decision + attempt + cancelled result), and separately that
    # no success/verification outcome was ever recorded.
    assert rows == [
        ("decision", "allowed"), ("attempt", "attempted"), ("result", "cancelled"),
    ]
    assert ("result", "completed") not in rows
    assert env.calls == 1


# ------------------------------------------------------------------ mint confinement (behavioral)

def test_plain_principal_cannot_mint_a_verified_principal() -> None:
    assert auth_module.VerifiedPrincipal is VerifiedPrincipal
    with pytest.raises(TypeError):
        VerifiedPrincipal(key="k", scopes=frozenset(), auth_mode="jwt")
    with pytest.raises(TypeError):
        VerifiedPrincipal("k", frozenset(), "jwt")
    assert not auth_module.is_verified_principal(_ForgedPrincipal())
    assert auth_module.is_verified_principal(_CONFIG_PRINCIPAL)
