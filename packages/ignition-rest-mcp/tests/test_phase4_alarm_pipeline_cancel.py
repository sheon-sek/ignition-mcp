"""Phase 4 milestone 4c (ticket #18): ``alarm_pipeline_cancel``.

D12 gives the REST plane the runtime side of Alarm Notification Pipelines: this Tool
cancels one *run* — an exact pipeline path plus the Alarm Event it is running for — and
never touches the Alarm Event itself ("Cancelling a notification pipeline does not clear
or acknowledge the Alarm Event"). D30 §6 gives it the two rules it has: its Target is an
exact pipeline path or ``*`` and never a prefix, and its verification is a bounded
``alarm_pipeline_status`` re-read returned as Observed state. D30 §2 gives it no
Precondition token, and an explicit Gateway rejection is final.

Where the rules land:

- the D08 chain runs inside the guarded executor before anything is dispatched, so a
  Target the deployment does not name answers ``permission_denied`` (D30 §7) with
  nothing sent;
- the run the caller addresses must exist at dispatch time: a bounded status read of the
  same path that does not report the event is a ``not_found`` that dispatches nothing,
  and a read whose page does not cover everything the Gateway matched fails the D10
  bound explicitly. Without that pre-state the verification could not attribute anything
  to this call at all;
- a claimed success is confirmed only when the bounded re-read no longer reports the
  event. An ambiguous dispatch whose run disappeared is ``outcome_unknown`` — another
  operator may have cancelled it — so ``recovered_success`` stays unreachable.

These tests drive the real server against the recorded Gateway (fixture first) over
Streamable HTTP, so every assertion is an MCP-visible result, an observed Gateway
request or a persisted row — never an internal call.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import pytest
from starlette.testclient import TestClient

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.authorization import scope_tag
from ignition_rest_mcp.safety.policy import CONTROL_MUTATION
from ignition_rest_mcp.services.alarm_pipeline_cancel import (
    ALARM_PIPELINE_CANCEL,
    MAX_ALARM_EVENT_ID_LENGTH,
    MAX_PIPELINE_PATH_LENGTH,
    STATUS_PAGE_LIMIT,
)
from phase4_fixtures import (
    ALARM_CANCEL_TOOL,
    CONTROL,
    UPDATE_TOOL,
    audit_rows,
    envelope,
    mutation_settings,
    operation_record,
    structured,
    write_requests,
)
from phase4_fixtures import Session as _Session

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

#: The allowlisted pipeline path — the form ``alarm_pipeline_list`` reports — and
#: paths the Target allowlist deliberately does not name.
PIPELINE = "project:MCP_CI_ALARM:/pipeline:Notify"
OTHER_PIPELINE = "project:MCP_CI_ALARM:/pipeline:Other"
UNALLOWLISTED_PIPELINE = "project:MCP_CI_OTHER:/pipeline:Notify"
#: The Alarm Event the cases cancel, and a second run the pipeline also holds.
EVENT = "6f1c8e2a-0f3f-4a44-9d5f-1c2b3a4d5e6f"
OTHER_EVENT = "0b7d2c19-5e64-4c31-8a2f-77c1d0e9b8a4"

OPERATOR_CREDENTIAL = "op-secret"
CONFIG_CREDENTIAL = "cfg-secret"
READER_CREDENTIAL = "reader-secret"
OPERATOR_PRINCIPAL = "static-token:operator-agent"
STATUS_PATH = "/data/alarm-notification/api/v1/pipeline"


def _settings(
    tmp_path: Path, gateway: RecordedGateway, *, targets: dict[str, tuple[str, ...]] | None = None,
    operations: tuple[str, ...] = (ALARM_CANCEL_TOOL,), **overrides: Any,
) -> Any:
    """The cancel Tool's deployment: the CONTROL class on, the CONFIG class off."""

    values: dict[str, Any] = {
        "data_dir": str(tmp_path),
        "gateway_url": gateway.base_url,
        "gateway_api_token": API_TOKEN,
        "config_mutation_enabled": False,
        "control_mutation_enabled": True,
    }
    values.update(overrides)
    if targets is None:
        targets = {ALARM_CANCEL_TOOL: (PIPELINE,)}
    return mutation_settings(*operations, targets=targets, **values)


def _seed(gateway: RecordedGateway, *, instances: list[dict[str, Any]] | None = None) -> None:
    gateway.seed_pipeline(
        PIPELINE,
        instances if instances is not None else [
            {"alarmEventId": EVENT, "status": "Running", "millis": 1200, "blockName": "Delay"},
            {"alarmEventId": OTHER_EVENT, "status": "Running", "millis": 400},
        ],
    )


def _cancel(session: _Session, path: str = PIPELINE, alarm_event_id: str = EVENT) -> dict[str, Any]:
    return session.call(ALARM_CANCEL_TOOL, {"path": path, "alarmEventId": alarm_event_id})


#: The routes the server's own capability refresh reads; they belong to startup, not
#: to the call under test.
_REFRESH_ROUTES = frozenset({
    "/data/api/v1/gateway-info", "/data/api/v1/modules/healthy", "/openapi.json",
})


def _status_reads(gateway: RecordedGateway) -> list[str]:
    """Every bounded status read the call made (the cancel itself is a DELETE)."""

    return [
        str(request["path"]) for request in gateway.requests
        if request["method"] == "GET" and str(request["path"]).startswith(STATUS_PATH)
    ]


def _tool_calls(gateway: RecordedGateway) -> list[dict[str, Any]]:
    """Every Gateway request the call under test made, refresh excluded."""

    return [
        request for request in gateway.requests
        if str(request["path"]).split("?")[0] not in _REFRESH_ROUTES
    ]


def _audit(tmp_path: Path) -> list[dict[str, Any]]:
    return [row for row in audit_rows(tmp_path) if row["tool"] == ALARM_CANCEL_TOOL]


def _outcomes(tmp_path: Path) -> list[str]:
    return [row["outcome"] for row in _audit(tmp_path)]


# ------------------------------------------------------------------ the happy path


def test_an_allowlisted_cancel_dispatches_the_documented_body_and_reports_the_re_read(
    tmp_path: Path,
) -> None:
    """The whole flow: the bounded status read establishes the run, the documented
    DELETE removes it, and the bounded re-read of the same path — the remaining runs
    plus the comparison — is what the caller gets back."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, OPERATOR_CREDENTIAL).call(
                ALARM_CANCEL_TOOL, {"path": PIPELINE, "alarmEventId": EVENT},
            )

        cancels = write_requests(gateway, "DELETE")
        reads = _status_reads(gateway)
        remaining = gateway.pipeline(PIPELINE)

    assert len(cancels) == 1, cancels
    assert cancels[0]["path"] == STATUS_PATH
    assert json.loads(cancels[0]["body"]) == {"path": PIPELINE, "alarmEventId": EVENT}
    assert len(reads) == 2, "the pre-state read and the verification read of one path"
    assert [item["alarmEventId"] for item in remaining or []] == [OTHER_EVENT]

    body = structured(result)
    assert (body["path"], body["alarmEventId"]) == (PIPELINE, EVENT)
    observed = body["observedState"]
    assert observed["alarmEventReported"] is False
    assert [item["alarmEventId"] for item in observed["items"]] == [OTHER_EVENT]
    assert observed["items"][0]["pipelinePath"] == PIPELINE
    assert operation_record(tmp_path, body["correlationId"]) == (ALARM_CANCEL_TOOL, "succeeded", None)
    assert _outcomes(tmp_path) == ["allowed", "attempted", "completed"]


def test_the_cancel_touches_only_the_pipeline_routes(tmp_path: Path) -> None:
    """D12: the Tool cancels a pipeline run. It must not acknowledge or clear the Alarm
    Event, so the only Gateway route it touches is the pipeline runtime surface."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            _Session(http, OPERATOR_CREDENTIAL).call(
                ALARM_CANCEL_TOOL, {"path": PIPELINE, "alarmEventId": EVENT},
            )

        touched = sorted({str(request["path"]).split("?")[0] for request in _tool_calls(gateway)})

    assert touched == [STATUS_PATH]


def test_the_cancel_writes_one_audited_decision_attempt_and_result(tmp_path: Path) -> None:
    """D18 ordering, the CONTROL effect class, the destructive declaration and the
    allowlisted safe fields — an allowed Mutation is always audited."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, OPERATOR_CREDENTIAL).call(
                ALARM_CANCEL_TOOL, {"path": PIPELINE, "alarmEventId": EVENT},
            )

        rows = _audit(tmp_path)

    fields = json.dumps(
        {"alarmEventId": EVENT, "path": PIPELINE}, separators=(",", ":"), sort_keys=True,
    )
    assert structured(result)["correlationId"] == rows[0]["correlation_id"]
    assert [
        (row["phase"], row["outcome"], row["operation_class"], row["destructive"],
         row["actor_key"], row["target_id"], row["safe_fields_json"])
        for row in rows
    ] == [
        ("decision", "allowed", "CONTROL", 1, OPERATOR_PRINCIPAL, PIPELINE, fields),
        ("attempt", "attempted", "CONTROL", 1, OPERATOR_PRINCIPAL, PIPELINE, fields),
        ("result", "completed", "CONTROL", 1, OPERATOR_PRINCIPAL, PIPELINE, "{}"),
    ]


def test_the_cancel_operation_declares_the_control_class_and_a_final_rejection() -> None:
    """D30 §2/§7 at the operation: CONTROL class, destructive, a Target denial that is
    ``permission_denied``, and a rejection that ends the call."""

    assert ALARM_PIPELINE_CANCEL.mutation_class == CONTROL_MUTATION
    assert ALARM_PIPELINE_CANCEL.destructive is True
    assert ALARM_PIPELINE_CANCEL.target_denial_code == "permission_denied"
    assert ALARM_PIPELINE_CANCEL.rejection_is_final is True


def test_the_tool_is_registered_with_the_control_scope_and_the_destructive_tag(
    tmp_path: Path,
) -> None:
    with RecordedGateway() as gateway:
        server = server_module.create_server(_settings(tmp_path, gateway))

    async def scenario() -> Any:
        return await server.get_tool(ALARM_CANCEL_TOOL)

    tool = asyncio.run(scenario())
    assert tool is not None
    assert scope_tag(CONTROL) in tool.tags
    assert "destructive" in tool.tags


# ------------------------------------------------------------------ discovery


def test_the_cancel_tool_is_discoverable_only_for_a_control_scoped_credential(
    tmp_path: Path,
) -> None:
    """D07 assigns scope by operation effect: the CONTROL credential sees the cancel
    Tool; the read credential never does, and neither does the config one."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            operator = _Session(http, OPERATOR_CREDENTIAL).tools()
            config = _Session(http, CONFIG_CREDENTIAL).tools()
            reader = _Session(http, READER_CREDENTIAL).tools()

    assert ALARM_CANCEL_TOOL in operator
    assert UPDATE_TOOL not in operator, "the CONFIG class is disabled in this deployment"
    assert ALARM_CANCEL_TOOL not in config
    assert ALARM_CANCEL_TOOL not in reader


def test_the_cancel_tool_is_hidden_when_the_control_class_is_disabled(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway, control_mutation_enabled=False)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, OPERATOR_CREDENTIAL).tools()

    assert ALARM_CANCEL_TOOL not in names


@pytest.mark.parametrize("documented", [("get",), ("delete",)], ids=["no-cancel", "no-read"])
def test_the_cancel_tool_needs_both_documented_pipeline_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, documented: tuple[str, ...],
) -> None:
    """The D04 capability registry decides discovery. The Tool cannot act without the
    cancel route and cannot establish what happened without the bounded status read, so
    a Gateway documenting only one of the two exposes no cancel at all."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        paths = json.loads(
            (ROOT / "tests/fixtures/recorded/gateway-8.3/phase2/openapi-required.json").read_text("utf-8")
        )["paths"]
        paths["/data/alarm-notification/api/v1/pipeline"] = {
            method: {} for method in documented
        }

        async def openapi(self: Any) -> bytes:
            return json.dumps({"paths": paths}).encode("utf-8")

        monkeypatch.setattr(server_module.GatewayClient, "openapi", openapi)
        with TestClient(server_module.create_server(_settings(tmp_path, gateway)).http_app()) as http:
            names = _Session(http, OPERATOR_CREDENTIAL).tools()

    assert ALARM_CANCEL_TOOL not in names


def test_the_cancel_tool_is_discoverable_when_both_pipeline_routes_are_documented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive half of the capability rule: with both routes documented and the
    CONTROL class enabled, the cancel Tool is part of the inventory."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        paths = json.loads(
            (ROOT / "tests/fixtures/recorded/gateway-8.3/phase2/openapi-required.json").read_text("utf-8")
        )["paths"]
        paths["/data/alarm-notification/api/v1/pipeline"] = {"get": {}, "delete": {}}

        async def openapi(self: Any) -> bytes:
            return json.dumps({"paths": paths}).encode("utf-8")

        monkeypatch.setattr(server_module.GatewayClient, "openapi", openapi)
        with TestClient(server_module.create_server(_settings(tmp_path, gateway)).http_app()) as http:
            names = _Session(http, OPERATOR_CREDENTIAL).tools()

    assert ALARM_CANCEL_TOOL in names


# ------------------------------------------------------------------ Target policy


def test_a_pipeline_path_outside_the_allowlist_is_a_permission_denied(tmp_path: Path) -> None:
    """D30 §7: the Target is denied before anything is dispatched — not even the
    bounded read happens — and the denial is audited."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.seed_pipeline(UNALLOWLISTED_PIPELINE, [{"alarmEventId": EVENT}])
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, OPERATOR_CREDENTIAL).call(
                ALARM_CANCEL_TOOL, {"path": UNALLOWLISTED_PIPELINE, "alarmEventId": EVENT},
            )

        cancels = write_requests(gateway, "DELETE")
        reads = _status_reads(gateway)
        rows = _audit(tmp_path)
        remaining = gateway.pipeline(UNALLOWLISTED_PIPELINE)

    assert envelope(result)["code"] == "permission_denied"
    assert cancels == [] and reads == []
    assert [item["alarmEventId"] for item in remaining or []] == [EVENT]
    assert [(row["phase"], row["outcome"], row["target_id"]) for row in rows] == [
        ("decision", "denied:target-allowlist:target-not-allowlisted", UNALLOWLISTED_PIPELINE),
    ]


def test_the_allowlist_matches_the_exact_path_and_never_a_prefix(tmp_path: Path) -> None:
    """D30 §6: the Target allowlist holds exact pipeline paths, never prefixes, so
    neither a path *under* the allowlisted one nor the allowlisted path's own parent is
    authorized."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.seed_pipeline(f"{PIPELINE}/child", [{"alarmEventId": EVENT}])
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            session = _Session(http, OPERATOR_CREDENTIAL)
            child = _cancel(session, path=f"{PIPELINE}/child")
            parent = _cancel(session, path="project:MCP_CI_ALARM:/pipeline")

        calls = _tool_calls(gateway)

    assert envelope(child)["code"] == "permission_denied"
    assert envelope(parent)["code"] == "permission_denied"
    assert calls == []


def test_a_wildcard_allowlist_does_not_make_a_wildcard_a_pipeline(tmp_path: Path) -> None:
    """A `*` Target allowlist authorizes every Target, but `*` is an allowlist entry,
    not a pipeline path: the caller still has to name the run it cancels."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway, targets={ALARM_CANCEL_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL), path="*")

        calls = _tool_calls(gateway)

    assert envelope(result)["code"] == "invalid_argument"
    assert calls == []


def test_an_operation_outside_the_deployment_allowlist_is_operation_disabled(
    tmp_path: Path,
) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(
            tmp_path, gateway, operations=(UPDATE_TOOL,),
            config_mutation_enabled=True, targets={UPDATE_TOOL: ("ignition/cobranding",)},
        )
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        calls = _tool_calls(gateway)

    assert envelope(result)["code"] == "operation_disabled"
    assert calls == []


# ------------------------------------------------------------------ input bounds


def test_a_path_beyond_the_limit_reports_the_requested_length_and_the_limit(
    tmp_path: Path,
) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        too_long = "project:MCP_CI_ALARM:/pipeline:" + "P" * MAX_PIPELINE_PATH_LENGTH
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL), path=too_long)

        calls = _tool_calls(gateway)

    error = envelope(result)
    assert error["code"] == "limit_exceeded"
    assert str(len(too_long)) in error["message"]
    assert str(MAX_PIPELINE_PATH_LENGTH) in error["message"]
    assert calls == []


def test_an_alarm_event_id_beyond_the_limit_reports_the_requested_length_and_the_limit(
    tmp_path: Path,
) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        too_long = "e" * (MAX_ALARM_EVENT_ID_LENGTH + 1)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL), alarm_event_id=too_long)

        calls = _tool_calls(gateway)

    error = envelope(result)
    assert error["code"] == "limit_exceeded"
    assert str(len(too_long)) in error["message"]
    assert str(MAX_ALARM_EVENT_ID_LENGTH) in error["message"]
    assert calls == []


@pytest.mark.parametrize(
    "path,alarm_event_id", [("", EVENT), ("   ", EVENT), (PIPELINE, ""), (PIPELINE, "  ")],
    ids=["empty-path", "blank-path", "empty-event", "blank-event"],
)
def test_an_empty_target_component_is_an_invalid_argument(
    tmp_path: Path, path: str, alarm_event_id: str,
) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(
                _Session(http, OPERATOR_CREDENTIAL), path=path, alarm_event_id=alarm_event_id,
            )

        calls = _tool_calls(gateway)

    assert envelope(result)["code"] == "invalid_argument"
    assert calls == []


def test_an_embedded_control_character_is_an_invalid_argument(tmp_path: Path) -> None:
    """Neither component ever carries a control character into an audit row or a log
    line. Surrounding whitespace is trimmed, but an embedded control character is not
    something this Tool can send or record."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            session = _Session(http, OPERATOR_CREDENTIAL)
            path = _cancel(session, path=f"{PIPELINE}\x00")
            event = _cancel(session, alarm_event_id=f"{EVENT}\x07")

        calls = _tool_calls(gateway)

    assert envelope(path)["code"] == "invalid_argument"
    assert envelope(event)["code"] == "invalid_argument"
    assert calls == []


def test_surrounding_whitespace_is_trimmed_before_the_target_is_matched(tmp_path: Path) -> None:
    """One path is one Target identity: the string that is matched, read, dispatched and
    audited is the trimmed one, so a padded copy cannot address a different pipeline."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL), path=f"  {PIPELINE}\t")

        bodies = [json.loads(request["body"]) for request in write_requests(gateway, "DELETE")]

    assert structured(result)["path"] == PIPELINE
    assert bodies == [{"path": PIPELINE, "alarmEventId": EVENT}]


# ------------------------------------------------- the run must exist at dispatch


def test_a_pipeline_that_holds_no_run_for_the_event_is_not_found_and_dispatches_nothing(
    tmp_path: Path,
) -> None:
    """Without the pre-state there is nothing to attribute: the Tool refuses, sends no
    cancel, and names the path and the event it could not find."""

    with RecordedGateway() as gateway:
        _seed(gateway, instances=[{"alarmEventId": OTHER_EVENT}])
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        cancels = write_requests(gateway, "DELETE")
        rows = _audit(tmp_path)

    error = envelope(result)
    assert error["code"] == "not_found"
    assert PIPELINE in error["message"] and EVENT in error["message"]
    assert cancels == []
    assert [(row["phase"], row["outcome"], row["target_id"]) for row in rows] == [
        ("decision", "denied:precondition:not_found", PIPELINE),
    ]


def test_a_pipeline_the_gateway_does_not_serve_is_not_found_and_dispatches_nothing(
    tmp_path: Path,
) -> None:
    """A path the Gateway does not serve has no run either, so the status read's
    ``not_found`` is the empty truth about that path and nothing is dispatched."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        cancels = write_requests(gateway, "DELETE")
        reads = _status_reads(gateway)

    assert envelope(result)["code"] == "not_found"
    assert cancels == []
    assert len(reads) == 1, "the bounded read happened, and nothing followed it"


def test_a_read_that_cannot_cover_every_match_fails_the_d10_bound_explicitly(
    tmp_path: Path,
) -> None:
    """D10: the Tool reads one page of instances. When the Gateway matches more than
    the page holds, the pre-state cannot be established, so the call fails with both
    numbers rather than guessing — and nothing is dispatched."""

    with RecordedGateway() as gateway:
        _seed(gateway, instances=[
            {"alarmEventId": f"00000000-0000-4000-8000-{index:012d}"}
            for index in range(STATUS_PAGE_LIMIT * 5 // 2)
        ])
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        cancels = write_requests(gateway, "DELETE")
        rows = _audit(tmp_path)

    error = envelope(result)
    assert error["code"] == "limit_exceeded"
    assert str(STATUS_PAGE_LIMIT * 5 // 2) in error["message"]
    assert str(STATUS_PAGE_LIMIT) in error["message"]
    assert cancels == []
    assert [row["outcome"] for row in rows] == ["denied:precondition:limit_exceeded"]


def test_a_page_that_covers_every_match_establishes_the_absence(tmp_path: Path) -> None:
    """The bound is about coverage, not page size: a pipeline whose last run is the
    page's last item has nothing hidden behind it, so the absence is definitive."""

    with RecordedGateway() as gateway:
        _seed(gateway, instances=[
            {"alarmEventId": f"00000000-0000-4000-8000-{index:012d}"}
            for index in range(STATUS_PAGE_LIMIT)
        ])
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        cancels = write_requests(gateway, "DELETE")

    assert envelope(result)["code"] == "not_found"
    assert cancels == []


# ------------------------------------------------------------------ verification


def test_a_claimed_cancel_that_leaves_the_run_in_place_is_recovery_required(
    tmp_path: Path,
) -> None:
    """The Gateway claimed the cancel while the bounded re-read still reports the run,
    so the call is unresolved — never reported as applied — and nothing is replayed."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.claim_cancels_without_applying()
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        cancels = write_requests(gateway, "DELETE")
        outcomes = _outcomes(tmp_path)
        remaining = gateway.pipeline(PIPELINE)

    error = envelope(result)
    assert error["code"] == "outcome_unknown"
    assert EVENT in error["message"]
    assert len(cancels) == 1, "the claim is unresolved, and the cancel is never replayed"
    assert [item["alarmEventId"] for item in remaining or []] == [EVENT, OTHER_EVENT]
    assert outcomes == ["allowed", "attempted", "recovery_required", "outcome_unknown"]
    assert "recovered_success" not in outcomes


def test_an_ambiguous_dispatch_whose_run_disappeared_is_outcome_unknown(
    tmp_path: Path,
) -> None:
    """Another operator can cancel the same run inside the ambiguous window, so a
    re-read showing it gone proves nothing about this call (D30 §2) — and the state the
    caller gets is ``outcome_unknown``, never a success."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.fail_cancels_with(500)
        gateway.race_cancel_with(PIPELINE, EVENT)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        outcomes = _outcomes(tmp_path)
        remaining = gateway.pipeline(PIPELINE)

    assert envelope(result)["code"] == "outcome_unknown"
    assert [item["alarmEventId"] for item in remaining or []] == [OTHER_EVENT]
    assert outcomes == ["allowed", "attempted", "outcome_unknown", "outcome_unknown"]
    assert "recovered_success" not in outcomes


def test_an_ambiguous_dispatch_that_changed_nothing_is_a_conflict(tmp_path: Path) -> None:
    """The only negative conclusion an ambiguous dispatch supports: the bounded re-read
    shows the run exactly as it was, so nothing landed and nothing is replayed."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.fail_cancels_with(500)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        outcomes = _outcomes(tmp_path)
        remaining = gateway.pipeline(PIPELINE)

    assert envelope(result)["code"] == "conflict"
    assert [item["alarmEventId"] for item in remaining or []] == [EVENT, OTHER_EVENT]
    assert outcomes == ["allowed", "attempted", "not_applied", "failed"]


def test_a_gateway_refusal_inside_a_200_is_final_even_when_the_run_is_gone(
    tmp_path: Path,
) -> None:
    """D30 §2: a 2xx carrying ``success=false`` is the Gateway's answer. Another writer
    making the run disappear at dispatch time must not turn that refusal into this
    caller's success."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.refuse_cancels_with("No such alarm event is running on this pipeline")
        gateway.race_cancel_with(PIPELINE, EVENT)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        cancels = write_requests(gateway, "DELETE")
        outcomes = _outcomes(tmp_path)
        remaining = gateway.pipeline(PIPELINE)

    assert envelope(result)["code"] == "conflict"
    assert len(cancels) == 1, "the refusal is the result, and no cancel is replayed"
    assert [item["alarmEventId"] for item in remaining or []] == [OTHER_EVENT]
    assert outcomes == ["allowed", "attempted", "rejected", "failed"]


def test_a_2xx_without_a_claim_is_never_reported_as_applied(tmp_path: Path) -> None:
    """A Gateway that answers 200 with no ``success`` field has made no claim, so the
    bounded re-read decides — and a re-read that cannot attribute the change to this
    call is unresolved, not a success (D30 §2)."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.answer_cancels_without_claiming()
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, OPERATOR_CREDENTIAL))

        outcomes = _outcomes(tmp_path)
        remaining = gateway.pipeline(PIPELINE)

    assert envelope(result)["code"] == "outcome_unknown"
    assert [item["alarmEventId"] for item in remaining or []] == [OTHER_EVENT]
    assert "recovered_success" not in outcomes


def test_a_config_scoped_credential_is_refused_by_the_control_scope(tmp_path: Path) -> None:
    """D07: the scope follows the operation's effect. The config credential is denied
    before the Tool's body runs, and the denial is durable."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _cancel(_Session(http, CONFIG_CREDENTIAL))

        calls = _tool_calls(gateway)
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "permission_denied"
    assert calls == []
    assert [
        (row["operation_class"], row["destructive"], row["outcome"]) for row in rows
    ] == [("CONTROL", 1, "denied:authz-scope:missing-scope:ignition.control")]
