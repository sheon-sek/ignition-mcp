#!/usr/bin/env python3
"""Phase 4 milestone 4c live driver: ``config_resource_update`` on a real Gateway.

Run against a running ``ignition-rest`` server (``--mode gate-on``) and again
against the same server restarted with ``IGNITION_MCP_CONFIG_MUTATION_ENABLED=false``
(``--mode gate-off``). The driver only ever talks MCP over HTTP and the Gateway's
own read routes; it never mutates the Gateway directly, so everything it reports is
something an agent could observe.

Live cases (the ticket's list):

- the effective REST inventory is exact with the class enabled *and* disabled, and
  the config-scoped credential sees the Mutation Tool while the read-only one does
  not (D07 discovery);
- an allowlisted update applies, is verified by an independent re-read, and moves
  the Resource signature;
- a stale ``expectedSignature`` is a ``conflict`` and leaves the resource alone;
- a Refused resource type (the Gateway's own API token) is ``permission_denied``
  under a ``*`` Target allowlist, and the resource it refuses to change still works;
- a change to a resource the Target allowlist does not name never reaches Ignition.

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
REFUSED_TYPE = "ignition/api-token"
REFUSED_NAME = "ignition-mcp-ci"
#: An allowed *singleton* (its documented change item carries no name) and a
#: per-Target denial code D30 §7 decides for the Phase 4 Mutations.
SINGLETON_TYPE = "ignition/cobranding"

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
GATE_ON_INVENTORY = READ_INVENTORY | {UPDATE_TOOL}

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


async def run_gate_on(
    *,
    rest_url: str,
    reader_token: str,
    agent_token: str,
    resource_type: str,
    allowlisted: str,
    unallowlisted: str,
    singleton_type: str,
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
            raw_dir=args.raw_dir,
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
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    args = parser.parse_args()
    args.rest_url = args.rest_url.rstrip("/")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
