"""The post-provisioning acceptance sequence a role's endpoint is checked with (D20).

endpoint reachable → ``initialize`` → exact ``tools/list`` → exact
``resources/list`` → exact ``prompts/list`` → ``resources/read`` smoke for every
expected resource → ``prompts/get`` smoke for every expected prompt →
``bundle_info``.  A superset is as much a failure as a subset: an endpoint that
advertises more than the profile permits has not been configured as planned.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import httpx

from ignition_rest_mcp.cli.gateway_ops.checks import (
    FAIL,
    NOT_APPLICABLE,
    PASS,
    SKIP,
    Check,
    bundle_info_check,
    inventory_check,
    make_mcp,
)
from ignition_rest_mcp.cli.gateway_ops.inputs import Endpoint, Inputs
from ignition_rest_mcp.cli.gateway_ops.mcp_http import McpHttpClient, McpMethodNotFound, McpProbeError

#: Statuses that still count as verified.
_OK = frozenset({PASS, NOT_APPLICABLE})


async def _inventory(client: McpHttpClient, inputs: Inputs, kind: str) -> tuple[Check, list[str]]:
    """One exact inventory comparison, tolerating an unadvertised empty capability."""

    name = f"inventory-{kind}"
    expected = inputs.profile_inventory(kind)
    listed = client.tools_list if kind == "tools" else (
        client.resources_list if kind == "resources" else client.prompts_list
    )
    try:
        actual = await listed()
    except McpMethodNotFound as error:
        if not client.advertises(kind) and not expected:
            return Check(name, NOT_APPLICABLE, f"endpoint does not offer {kind}; profile expects none"), []
        return Check(name, FAIL, f"{error} (profile {inputs.profile} expects {len(expected)})"), []
    except McpProbeError as error:
        return Check(name, FAIL, str(error)), []
    return inventory_check(name, expected, actual), actual


async def _resource_smokes(client: McpHttpClient, inputs: Inputs) -> list[Check]:
    uris = inputs.profile_inventory("resources")
    if not uris:
        return [Check("resources-read", NOT_APPLICABLE, "profile inventory declares no Text Resources")]
    checks: list[Check] = []
    for uri in uris:
        name = f"resources-read {uri}"
        try:
            text = await client.resource_read(uri)
        except McpProbeError as error:
            checks.append(Check(name, FAIL, str(error)))
        else:
            checks.append(Check(name, PASS, f"{len(text)} UTF-8 characters"))
    return checks


async def _prompt_smokes(client: McpHttpClient, inputs: Inputs) -> list[Check]:
    names = inputs.profile_inventory("prompts")
    if not names:
        # The D28 lesson: absence is only acceptable when the endpoint really
        # does not advertise the prompts capability.
        detail = (
            "profile inventory declares no Prompts"
            if not client.advertises("prompts")
            else "endpoint advertises prompts but the profile inventory declares none"
        )
        status = NOT_APPLICABLE if not client.advertises("prompts") else FAIL
        return [Check("prompts-get", status, detail)]
    checks: list[Check] = []
    for prompt in names:
        name = f"prompts-get {prompt}"
        try:
            result = await client.prompt_get(prompt)
        except McpProbeError as error:
            checks.append(Check(name, FAIL, str(error)))
        else:
            messages = result.get("messages")
            count = len(messages) if isinstance(messages, list) else 0
            checks.append(Check(name, PASS, f"{count} message(s)"))
    return checks


def verify_report(
    inputs: Inputs, checks: Sequence[Check], verified: bool, exit_code: int, endpoint: Endpoint | None
) -> dict[str, Any]:
    return {
        "command": "verify",
        "manifest": str(inputs.manifest_path),
        "bundleVersion": inputs.bundle_version,
        "profile": inputs.profile,
        "mcpUrl": None if endpoint is None else endpoint.url,
        "checks": [check.as_dict() for check in checks],
        "verified": verified,
        "exitCode": exit_code,
    }


async def collect(
    inputs: Inputs,
    *,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any], list[str], int]:
    """Execute the verify sequence and return its report, text lines and exit code.

    ``apply`` embeds the report, so the sequence is exposed as data rather than as
    a second JSON document on stdout; ``run`` is the printer.
    """

    checks: list[Check] = []
    endpoint = inputs.runtime_endpoint()
    async with make_mcp(inputs, mcp_transport) as client:
        try:
            answer = await client.reachability()
        except McpProbeError as error:
            checks.append(Check("endpoint-reachable", FAIL, str(error)))
            checks.extend(
                Check(name, SKIP, "endpoint unreachable")
                for name in (
                    "mcp-initialize",
                    "inventory-tools",
                    "inventory-resources",
                    "inventory-prompts",
                    "resources-read",
                    "prompts-get",
                    "bundle-info",
                )
            )
            return _finish(inputs, checks)
        checks.append(Check("endpoint-reachable", PASS, f"{endpoint.url if endpoint else '?'} {answer}"))

        try:
            await client.initialize()
        except McpProbeError as error:
            checks.append(Check("mcp-initialize", FAIL, str(error)))
            checks.extend(
                Check(name, SKIP, "no MCP session")
                for name in (
                    "inventory-tools",
                    "inventory-resources",
                    "inventory-prompts",
                    "resources-read",
                    "prompts-get",
                    "bundle-info",
                )
            )
            return _finish(inputs, checks)
        advertised = ", ".join(
            sorted(kind for kind in ("tools", "resources", "prompts") if client.advertises(kind))
        )
        checks.append(
            Check(
                "mcp-initialize",
                PASS,
                f"protocol={client.protocol_version or '?'} "
                f"server={json.dumps(client.server_info, sort_keys=True)[:120]} capabilities=[{advertised or '-'}]",
            )
        )

        for kind in ("tools", "resources", "prompts"):
            check, _ = await _inventory(client, inputs, kind)
            checks.append(check)
        checks.extend(await _resource_smokes(client, inputs))
        checks.extend(await _prompt_smokes(client, inputs))
        bundle_check, _ = await bundle_info_check(client, inputs)
        checks.append(bundle_check)
    return _finish(inputs, checks)


def _finish(inputs: Inputs, checks: Sequence[Check]) -> tuple[dict[str, Any], list[str], int]:
    verified = all(check.status in _OK for check in checks)
    exit_code = 0 if verified else 1
    lines = [f"{check.status:<15}{check.name}: {check.detail}" for check in checks]
    lines.append(f"verify: verified={str(verified).lower()} => exit {exit_code}")
    return verify_report(inputs, checks, verified, exit_code, inputs.runtime_endpoint()), lines, exit_code
