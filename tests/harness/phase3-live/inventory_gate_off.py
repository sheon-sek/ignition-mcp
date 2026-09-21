#!/usr/bin/env python3
"""G3 gate-off inventory probe.

Run against the ignition-rest server started with
``IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=false``: the two sensitive exports
must disappear from discovery, the mutation set must stay empty, and an
explicit call to ``project_export`` must fail at call time (a disabled
component must never execute). Exit 0 when all three hold; the JSON report is
merged into the evidence row by ``tooling.compat.generate``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from driver import make_jwt
from harness_common import EXPECTED_GATE_OFF_TOOLS, MUTATION_TOOL_NAMES, McpHttp, ProbeError, error_envelope


async def probe(rest_url: str, bearer: str) -> dict[str, Any]:
    mcp = McpHttp(rest_url + "/mcp", bearer, timeout=60.0)
    try:
        await mcp.initialize()
        names = {str(t.get("name")) for t in await mcp.tools_list()}
        missing = sorted(EXPECTED_GATE_OFF_TOOLS - names)
        unexpected = sorted(names - EXPECTED_GATE_OFF_TOOLS)
        call_code: str
        try:
            call = await mcp.tool_call("project_export", {"projectName": "mcp_g3_gateoff_probe"})
        except ProbeError as error:
            # A disabled component is rejected at the router (JSON-RPC error):
            # exactly the call-time failure this probe demands.
            call_code = f"router-refused: {str(error)[:120]}"
        else:
            if call.get("isError"):
                call_code = f"isError:{error_envelope(call).get('code')}"
            else:
                call_code = "EXECUTED-UNEXPECTED"
        executed = call_code == "EXECUTED-UNEXPECTED"
        exports_visible = bool({"project_export", "tag_config_export"} & names)
        ok = (not missing and not unexpected and not exports_visible
              and not names & MUTATION_TOOL_NAMES and not executed)
        return {
            "tools": sorted(names),
            "missing": missing,
            "unexpected": unexpected,
            "exportsVisible": sorted({"project_export", "tag_config_export"} & names),
            "mutationTools": sorted(names & MUTATION_TOOL_NAMES),
            "projectExportCall": call_code,
            "passed": bool(ok),
        }
    finally:
        await mcp.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rest-url", default="http://127.0.0.1:8765")
    parser.add_argument("--jwt-private-key", required=True, type=Path)
    parser.add_argument("--jwt-issuer", required=True)
    parser.add_argument("--jwt-audience", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bearer = make_jwt(args.jwt_private_key.read_text(encoding="utf-8"), args.jwt_issuer, args.jwt_audience,
                      sub="g3-gateoff", scopes=["ignition.read"])
    report = asyncio.run(probe(args.rest_url, bearer))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("missing", "unexpected", "exportsVisible", "mutationTools", "projectExportCall", "passed")},
                     sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
