#!/usr/bin/env python3
"""Phase 4 milestone 4c live driver: the config-resource Mutations on a real Gateway.

Run against a running ``ignition-rest`` server (``--mode gate-on``) and again
against the same server restarted with ``IGNITION_MCP_CONFIG_MUTATION_ENABLED=false``
(``--mode gate-off``). The driver only ever talks MCP over HTTP and the Gateway's
own read routes; it never mutates the Gateway directly, so everything it reports is
something an agent could observe.

Live cases (tickets #14 and #15):

- the effective REST inventory is exact with the class enabled *and* disabled, and
  the config-scoped credential sees the Mutation Tools while the read-only one does
  not (D07 discovery);
- an allowlisted update applies, is verified by an independent re-read, and moves
  the Resource signature;
- a stale ``expectedSignature`` is a ``conflict`` and leaves the resource alone;
- a Refused resource type (the Gateway's own API token) is ``permission_denied``
  under a ``*`` Target allowlist, and the resource it refuses to change still works;
- a change to a resource the Target allowlist does not name never reaches Ignition;
- a create publishes a resource, an existing target is a ``conflict``, and a
  refused or non-allowlisted create leaves nothing behind;
- a delete removes the resource (the signature travels in the native path), a second
  delete is ``not_found``, a stale signature is a ``conflict``, and the refused and
  non-allowlisted cases change nothing;
- a rename moves the resource, an occupied destination is a ``conflict``, a
  non-allowlisted source or destination is ``permission_denied``, and a stale
  signature is a ``conflict``.

One note on the Target-allowlist expectation: D30 §7 decides ``permission_denied``
for a Phase 4 Mutation whose Target is not in the Target allowlist, and the Tool-scoped
mapping keeps the frozen Phase 3 machinery on its recorded ``operation_disabled`` (G3
evidence asserts that code). This driver therefore asserts exactly one code.

No compatibility evidence row is produced here: a G4 row carries the Gateway/Module
tuple, and this harness deploys no MCP Module. The observations are uploaded as
workflow artifacts and referenced by the G4 close-out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS.parent / "phase3-live"))

from harness_common import McpHttp, ProbeError, error_envelope  # noqa: E402

UPDATE_TOOL = "config_resource_update"
CREATE_TOOL = "config_resource_create"
DELETE_TOOL = "config_resource_delete"
RENAME_TOOL = "config_resource_rename"
MUTATION_TOOLS = (UPDATE_TOOL, CREATE_TOOL, DELETE_TOOL, RENAME_TOOL)
REFUSED_TYPE = "ignition/api-token"
REFUSED_NAME = "ignition-mcp-ci"
#: An allowed *singleton* (its documented change item carries no name).
SINGLETON_TYPE = "ignition/cobranding"
#: The names the create, delete and rename cases use. They are provisioned by
#: ``provision.py`` except the one the create case publishes.
CREATED_RESOURCE = "MCP_CI_AUDIT_CREATED"
RENAME_SOURCE = "MCP_CI_AUDIT_RENAME_SOURCE"
RENAME_SOURCE_2 = "MCP_CI_AUDIT_RENAME_SOURCE_2"
RENAMED_RESOURCE = "MCP_CI_AUDIT_RENAMED"
#: A destination name no Target allowlist lists, so the denial is the allowlist's.
UNALLOWLISTED_RENAMED = "MCP_CI_AUDIT_RENAMED_NOT_ALLOWED"
#: A name of the allowlisted type that the Target allowlists deliberately omit.
NOT_ALLOWLISTED_RESOURCE = "MCP_CI_AUDIT_OTHER"

#: The effective REST inventory with the config mutation class disabled, and with
#: it enabled. ``readonly`` is unchanged by Phase 4: no read Tool is added or removed.
READ_INVENTORY = frozenset({
    "gateway_info",
    "gateway_diagnose",
    "project_list",
    "config_resource_search",
    "config_resource_describe",
    "config_resource_names",
    "config_resource_list",
    "config_resource_get",
    "audit_query",
    "alarm_pipeline_list",
    "alarm_pipeline_status",
    "artifact_list",
    "artifact_info",
    "operation_diagnose",
})
GATE_ON_INVENTORY = READ_INVENTORY | set(MUTATION_TOOLS)

#: D30 §7: a Target outside the Target allowlist is `permission_denied` for a Phase 4
#: Mutation. Exactly one code is accepted; the driver must not tolerate the frozen
#: Phase 3 code, or the Tool's contract would be unverified.
TARGET_DENIAL_CODE = "permission_denied"


class DriverError(RuntimeError):
    pass


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _check(cases: list[dict[str, Any]], name: str, expected: Any, observed: Any) -> None:
    cases.append({"case": name, "expected": expected, "observed": observed, "ok": expected == observed})


class Session:
    """One MCP session plus the raw-body recorder for the evidence directory."""

    def __init__(self, url: str, token: str, raw_dir: Path) -> None:
        self._mcp = McpHttp(url, token)
        self._raw = raw_dir
        self._serial = 0

    async def aclose(self) -> None:
        await self._mcp.aclose()

    async def initialize(self) -> None:
        await self._mcp.initialize()

    async def tools(self) -> frozenset[str]:
        tools = await self._mcp.tools_list()
        names = frozenset(str(tool.get("name")) for tool in tools)
        self._record("tools-list", [{"name": tool.get("name"), "tags": tool.get("tags")} for tool in tools])
        return names

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._mcp.tool_call(tool, arguments)
        self._record(f"call-{tool}", result)
        return result

    async def signature(self, resource_type: str, name: str = "") -> str:
        body = await self.get(resource_type, name)
        signature = body.get("signature")
        if not isinstance(signature, str) or not signature:
            raise DriverError(f"{resource_type}/{name} reported no Resource signature")
        return signature

    async def get(self, resource_type: str, name: str = "") -> dict[str, Any]:
        result = await self.call("config_resource_get", {
            "resourceType": resource_type, "name": name, "collection": "", "defaultIfUndefined": False,
        })
        if result.get("isError"):
            raise DriverError(f"config_resource_get failed: {error_envelope(result).get('code')}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise DriverError("config_resource_get returned no structuredContent")
        return structured

    def _record(self, label: str, value: Any) -> None:
        self._serial += 1
        _write(self._raw / f"{self._serial:02d}-{label}.json", value)


def _envelope_code(result: dict[str, Any]) -> str:
    envelope = error_envelope(result)
    code = envelope.get("code")
    if not isinstance(code, str) or not code:
        raise DriverError(f"error envelope carried no code: {envelope!r}")
    return code


def structured(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError"):
        raise DriverError(f"expected a successful result, got {error_envelope(result)!r}")
    body = result.get("structuredContent")
    if not isinstance(body, dict):
        raise DriverError("the Tool returned no structuredContent")
    return body


async def _absent(agent: "Session", resource_type: str, name: str) -> bool:
    """Whether the Gateway really has no such resource.

    The read Tool answering ``not_found`` is the only shape that counts as absent: a
    denial or a transport failure must not be mistaken for a missing resource.
    """

    result = await agent.call("config_resource_get", {
        "resourceType": resource_type, "name": name, "collection": "", "defaultIfUndefined": False,
    })
    if not result.get("isError"):
        return False
    return _envelope_code(result) == "not_found"


async def run_gate_on(
    *,
    rest_url: str,
    reader_token: str,
    agent_token: str,
    resource_type: str,
    allowlisted: str,
    unallowlisted: str,
    singleton_type: str,
    created_name: str,
    rename_source: str,
    rename_source_2: str,
    renamed_name: str,
    unallowlisted_renamed: str,
    raw_dir: Path,
) -> dict[str, Any]:
    """The live cases that need the CONFIG_MUTATION class enabled."""

    cases: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    reader = Session(rest_url + "/mcp", reader_token, raw_dir)
    agent = Session(rest_url + "/mcp", agent_token, raw_dir)
    try:
        await reader.initialize()
        await agent.initialize()

        _check(cases, "inventory-agent-exact", sorted(GATE_ON_INVENTORY), sorted(await agent.tools()))
        _check(cases, "inventory-reader-exact", sorted(READ_INVENTORY), sorted(await reader.tools()))

        before = await agent.signature(resource_type, allowlisted)
        observations["signatureBefore"] = before
        updated = await agent.call(UPDATE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": before,
            "name": allowlisted,
            "description": "Disposable Phase 4 CI audit profile (updated live)",
            "config": {"profile": {"type": "local", "retentionDays": 21}},
        })
        _check(cases, "allowlisted-update-applies", True, not updated.get("isError"))
        body = updated.get("structuredContent") if isinstance(updated.get("structuredContent"), dict) else {}
        after = body.get("signature")
        observations["signatureAfter"] = after
        _check(cases, "update-moves-the-signature", True, isinstance(after, str) and after != before)
        _check(
            cases, "observed-state-carries-the-change",
            {"retentionDays": 21, "description": "Disposable Phase 4 CI audit profile (updated live)"},
            {
                "retentionDays": (body.get("observedState") or {}).get("config", {}).get("profile", {})
                .get("retentionDays"),
                "description": (body.get("observedState") or {}).get("description"),
            },
        )
        reread = await agent.get(resource_type, allowlisted)
        _check(cases, "independent-reread-confirms", after, reread.get("signature"))

        stale = await agent.call(UPDATE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": before,
            "name": allowlisted,
            "enabled": False,
        })
        _check(cases, "stale-signature-is-conflict", "conflict", _envelope_code(stale))
        _check(cases, "stale-signature-changes-nothing", after, await agent.signature(resource_type, allowlisted))

        refused_signature = await agent.signature(REFUSED_TYPE, REFUSED_NAME)
        refused = await agent.call(UPDATE_TOOL, {
            "resourceType": REFUSED_TYPE,
            "expectedSignature": refused_signature,
            "name": REFUSED_NAME,
            "enabled": False,
        })
        _check(cases, "refused-resource-type-is-permission-denied", "permission_denied", _envelope_code(refused))
        _check(
            cases, "refused-resource-still-usable", refused_signature,
            await agent.signature(REFUSED_TYPE, REFUSED_NAME),
        )

        other_before = await agent.signature(resource_type, unallowlisted)
        denied = await agent.call(UPDATE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": other_before,
            "name": unallowlisted,
            "enabled": False,
        })
        denied_code = _envelope_code(denied)
        observations["targetDenialCode"] = denied_code
        _check(cases, "non-allowlisted-target-is-permission-denied", TARGET_DENIAL_CODE, denied_code)
        _check(
            cases, "non-allowlisted-target-changes-nothing", other_before,
            await agent.signature(resource_type, unallowlisted),
        )

        # A singleton's documented change item carries no name; its update proves the
        # item is built from the Gateway's own request schema.
        singleton_before = await agent.signature(singleton_type)
        singleton = await agent.call(UPDATE_TOOL, {
            "resourceType": singleton_type,
            "expectedSignature": singleton_before,
            "description": "Disposable Phase 4 CI branding (updated live)",
        })
        _check(cases, "singleton-update-applies", True, not singleton.get("isError"))
        singleton_signature = (
            singleton.get("structuredContent") or {}
        ).get("signature")
        _check(
            cases, "singleton-update-moves-the-signature", True,
            isinstance(singleton_signature, str) and singleton_signature != singleton_before,
        )

        # ------------------------------------------------------------ create (#15)
        created = await agent.call(CREATE_TOOL, {
            "resourceType": resource_type,
            "name": created_name,
            "description": "Disposable Phase 4 CI audit profile (created live)",
            "config": {"profile": {"type": "local", "retentionDays": 11}},
        })
        _check(cases, "allowlisted-create-applies", True, not created.get("isError"))
        created_body = structured(created)
        created_signature = created_body.get("signature")
        _check(
            cases, "create-reports-the-published-resource",
            {"name": created_name, "retentionDays": 11,
             "description": "Disposable Phase 4 CI audit profile (created live)"},
            {
                "name": created_body.get("name"),
                "retentionDays": (created_body.get("observedState") or {})
                .get("config", {}).get("profile", {}).get("retentionDays"),
                "description": (created_body.get("observedState") or {}).get("description"),
            },
        )
        published = await agent.get(resource_type, created_name)
        _check(cases, "create-independent-reread-confirms", created_signature, published.get("signature"))

        again = await agent.call(CREATE_TOOL, {
            "resourceType": resource_type, "name": created_name,
        })
        _check(cases, "create-of-an-existing-target-is-conflict", "conflict", _envelope_code(again))
        _check(
            cases, "create-of-an-existing-target-changes-nothing", created_signature,
            await agent.signature(resource_type, created_name),
        )

        refused_create = await agent.call(CREATE_TOOL, {
            "resourceType": REFUSED_TYPE, "name": f"{REFUSED_NAME}-impostor",
        })
        _check(
            cases, "create-of-a-refused-resource-type-is-permission-denied",
            "permission_denied", _envelope_code(refused_create),
        )
        _check(
            cases, "create-of-a-refused-resource-type-publishes-nothing", True,
            await _absent(agent, REFUSED_TYPE, f"{REFUSED_NAME}-impostor"),
        )

        denied_create = await agent.call(CREATE_TOOL, {
            "resourceType": resource_type, "name": f"{unallowlisted}_CREATED",
        })
        _check(
            cases, "create-outside-the-target-allowlist-is-permission-denied",
            TARGET_DENIAL_CODE, _envelope_code(denied_create),
        )
        _check(
            cases, "create-outside-the-target-allowlist-publishes-nothing", True,
            await _absent(agent, resource_type, f"{unallowlisted}_CREATED"),
        )

        # ------------------------------------------------------------ delete (#15)
        deleted = await agent.call(DELETE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": created_signature,
            "name": created_name,
        })
        _check(cases, "allowlisted-delete-applies", True, not deleted.get("isError"))
        _check(cases, "delete-reports-absence", False, structured(deleted).get("present"))
        _check(cases, "delete-independent-reread-shows-absence", True,
               await _absent(agent, resource_type, created_name))

        gone_again = await agent.call(DELETE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": created_signature,
            "name": created_name,
        })
        _check(cases, "delete-of-an-absent-target-is-not-found", "not_found", _envelope_code(gone_again))

        stale_delete = await agent.call(DELETE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": singleton_signature,
            "name": allowlisted,
        })
        _check(cases, "delete-with-a-stale-signature-is-conflict", "conflict",
               _envelope_code(stale_delete))
        _check(cases, "delete-with-a-stale-signature-changes-nothing", after,
               await agent.signature(resource_type, allowlisted))

        refused_delete = await agent.call(DELETE_TOOL, {
            "resourceType": REFUSED_TYPE,
            "expectedSignature": await agent.signature(REFUSED_TYPE, REFUSED_NAME),
            "name": REFUSED_NAME,
        })
        _check(
            cases, "delete-of-a-refused-resource-type-is-permission-denied",
            "permission_denied", _envelope_code(refused_delete),
        )
        _check(cases, "refused-resource-survives-the-delete-denial", True,
               not await _absent(agent, REFUSED_TYPE, REFUSED_NAME))

        denied_delete = await agent.call(DELETE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": await agent.signature(resource_type, unallowlisted),
            "name": unallowlisted,
        })
        _check(
            cases, "delete-outside-the-target-allowlist-is-permission-denied",
            TARGET_DENIAL_CODE, _envelope_code(denied_delete),
        )
        _check(cases, "delete-outside-the-target-allowlist-changes-nothing", True,
               not await _absent(agent, resource_type, unallowlisted))

        # ------------------------------------------------------------ rename (#15)
        source_signature = await agent.signature(resource_type, rename_source)
        renamed = await agent.call(RENAME_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": source_signature,
            "name": rename_source,
            "newName": renamed_name,
        })
        _check(cases, "allowlisted-rename-applies", True, not renamed.get("isError"))
        renamed_body = structured(renamed)
        _check(
            cases, "rename-reports-both-names",
            {"previousName": rename_source, "name": renamed_name},
            {"previousName": renamed_body.get("previousName"), "name": renamed_body.get("name")},
        )
        moved_signature = renamed_body.get("signature")
        _check(cases, "rename-independently-shows-the-old-name-vacant", True,
               await _absent(agent, resource_type, rename_source))
        _check(cases, "rename-independently-shows-the-new-name-holding-it", moved_signature,
               (await agent.get(resource_type, renamed_name)).get("signature"))

        occupied = await agent.call(RENAME_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": await agent.signature(resource_type, rename_source_2),
            "name": rename_source_2,
            "newName": renamed_name,
        })
        _check(cases, "rename-onto-an-occupied-destination-is-conflict", "conflict",
               _envelope_code(occupied))
        _check(cases, "rename-onto-an-occupied-destination-changes-nothing", True,
               not await _absent(agent, resource_type, rename_source_2))

        stale_rename = await agent.call(RENAME_TOOL, {
            "resourceType": resource_type,
            # A token that belongs to a different resource: the read-compare refuses it
            # before anything is dispatched, whatever the destination looks like.
            "expectedSignature": moved_signature,
            "name": rename_source_2,
            "newName": renamed_name,
        })
        _check(cases, "rename-with-a-stale-signature-is-conflict", "conflict",
               _envelope_code(stale_rename))
        _check(cases, "rename-with-a-stale-signature-changes-nothing", True,
               not await _absent(agent, resource_type, rename_source_2))

        refused_rename = await agent.call(RENAME_TOOL, {
            "resourceType": REFUSED_TYPE,
            "expectedSignature": await agent.signature(REFUSED_TYPE, REFUSED_NAME),
            "name": REFUSED_NAME,
            "newName": f"{REFUSED_NAME}-renamed",
        })
        _check(
            cases, "rename-of-a-refused-resource-type-is-permission-denied",
            "permission_denied", _envelope_code(refused_rename),
        )
        _check(cases, "refused-resource-survives-the-rename-denial", True,
               not await _absent(agent, REFUSED_TYPE, REFUSED_NAME))

        denied_rename = await agent.call(RENAME_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": moved_signature,
            "name": renamed_name,
            "newName": unallowlisted_renamed,
        })
        _check(
            cases, "rename-into-an-unallowlisted-destination-is-permission-denied",
            TARGET_DENIAL_CODE, _envelope_code(denied_rename),
        )
        _check(cases, "rename-into-an-unallowlisted-destination-changes-nothing", moved_signature,
               await agent.signature(resource_type, renamed_name))
    finally:
        await reader.aclose()
        await agent.aclose()
    return {"mode": "gate-on", "cases": cases, "observations": observations}


async def run_gate_off(*, rest_url: str, agent_token: str, raw_dir: Path) -> dict[str, Any]:
    """The same inventory question with the class disabled, plus the call-time deny."""

    cases: list[dict[str, Any]] = []
    agent = Session(rest_url + "/mcp", agent_token, raw_dir)
    try:
        await agent.initialize()
        _check(cases, "inventory-gate-off-exact", sorted(READ_INVENTORY), sorted(await agent.tools()))

        executed = "EXECUTED-UNEXPECTED"
        try:
            result = await agent.call(UPDATE_TOOL, {
                "resourceType": "ignition/audit-profile", "expectedSignature": "sig", "name": "MCP_CI_AUDIT",
            })
        except ProbeError as error:
            # A component FastMCP disabled is refused at the router: a JSON-RPC
            # error is exactly the call-time refusal this case demands.
            executed = f"router-refused: {str(error)[:160]}"
        else:
            if result.get("isError"):
                try:
                    executed = f"isError:{_envelope_code(result)}"
                except (DriverError, ProbeError):
                    # FastMCP refuses a disabled component with a plain-text error
                    # result rather than the D06 envelope. Any refusal shape is
                    # admissible here; EXECUTED-UNEXPECTED is not.
                    executed = f"isError:{str(result.get('content'))[:160]}"
        _check(cases, "disabled-class-call-is-refused", True, executed != "EXECUTED-UNEXPECTED")
    finally:
        await agent.aclose()
    return {"mode": "gate-off", "cases": cases, "observations": {}}


def _load_report(path: Path) -> dict[str, Any]:
    """The merged report, so the two modes of one live run land in one file."""

    if not path.is_file():
        return {"cases": [], "observations": {}, "modes": []}
    existing = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise DriverError(f"{path}: existing observations must be an object")
    return existing


async def _run(args: argparse.Namespace) -> int:
    if args.mode == "gate-on":
        mode = await run_gate_on(
            rest_url=args.rest_url, reader_token=args.reader_token, agent_token=args.agent_token,
            resource_type=args.resource_type, allowlisted=args.allowlisted,
            unallowlisted=args.unallowlisted, singleton_type=args.singleton_type,
            created_name=args.created_name, rename_source=args.rename_source,
            rename_source_2=args.rename_source_2, renamed_name=args.renamed_name,
            unallowlisted_renamed=args.unallowlisted_renamed, raw_dir=args.raw_dir,
        )
    else:
        mode = await run_gate_off(
            rest_url=args.rest_url, agent_token=args.agent_token, raw_dir=args.raw_dir,
        )

    report = _load_report(args.observations)
    report.update({"schemaVersion": 1, "gate": "G4", "milestone": "4c", "tool": UPDATE_TOOL})
    report["modes"] = [*report.get("modes", []), args.mode]
    report["cases"] = [*report.get("cases", []), *mode["cases"]]
    report["observations"] = {**report.get("observations", {}), **mode["observations"]}
    report["passed"] = all(case["ok"] for case in report["cases"])
    _write(args.observations, report)
    for case in report["cases"]:
        print(f"{'ok  ' if case['ok'] else 'FAIL'} {case['case']}: {json.dumps(case['observed'])[:160]}")
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("gate-on", "gate-off"), required=True)
    parser.add_argument("--rest-url", default="http://127.0.0.1:8765")
    parser.add_argument("--reader-token", required=True)
    parser.add_argument("--agent-token", required=True)
    parser.add_argument("--resource-type", default="ignition/audit-profile")
    parser.add_argument("--allowlisted", default="MCP_CI_AUDIT")
    parser.add_argument("--unallowlisted", default="MCP_CI_AUDIT_OTHER")
    parser.add_argument("--singleton-type", default=SINGLETON_TYPE)
    parser.add_argument("--created-name", default=CREATED_RESOURCE)
    parser.add_argument("--rename-source", default=RENAME_SOURCE)
    parser.add_argument("--rename-source-2", default=RENAME_SOURCE_2)
    parser.add_argument("--renamed-name", default=RENAMED_RESOURCE)
    parser.add_argument("--unallowlisted-renamed", default=UNALLOWLISTED_RENAMED)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    args = parser.parse_args()
    args.rest_url = args.rest_url.rstrip("/")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
