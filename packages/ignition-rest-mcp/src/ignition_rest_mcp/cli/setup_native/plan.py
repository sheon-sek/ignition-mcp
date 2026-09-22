"""``setup-native plan``: declarative intentions, never an installer (D20, D21).

The plan is derived from the same read-only observations ``doctor`` makes (Gateway
identity, module presence, project ownership marker, server-config document, the
reserved policy provider's served policy) and printed as ``<ACTION> <kind>
<name>: <reason>`` lines.  ``apply`` consumes the very same list, refuses to write
while any line is ``BLOCKED``, and executes the rest in the order printed here; a
standalone ``plan`` run ends with the literal line ``No changes have been
applied.``
"""

from __future__ import annotations

from typing import Any, Sequence

import httpx

from ignition_rest_mcp.cli.setup_native import documents as docs
from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native.action import (
    ACKNOWLEDGEMENT,
    BLOCKED,
    CREATE,
    NO_CHANGE,
    SKIPPED,
    UPDATE,
    Action,
    needs_acknowledgement,
    upgrade_class,
)
from ignition_rest_mcp.cli.setup_native.doctor import (
    GatewayObservation,
    emit,
    make_gateway,
    probe_gateway,
)
from ignition_rest_mcp.cli.setup_native.inputs import Inputs

PLAN_SENTINEL = "No changes have been applied."

#: How many drifting Tool names one line names before it says "+N more".
DRIFT_NAMES = 5


def project_actions(inputs: Inputs, observation: GatewayObservation) -> list[Action]:
    """Bundle-project intentions; takeover of an unmanaged project is always refused."""

    state = observation.project
    name = inputs.bundle_project
    if state is None:
        if not observation.project_probeable:
            return [
                Action(
                    BLOCKED,
                    "bundle-project",
                    name,
                    f"Gateway does not document {gw.PROJECT_FIND_ENDPOINT[1]}; project ownership "
                    "cannot be proven, so nothing will be deployed",
                )
            ]
        return [
            Action(
                BLOCKED,
                "bundle-project",
                name,
                "project state could not be read; refusing to act on an unverified precondition",
            )
        ]
    if state.classification == gw.ABSENT:
        return [
            Action(
                CREATE,
                "bundle-project",
                name,
                f"deploy managed bundle {inputs.bundle_version} (standalone, inheritable=false)",
            )
        ]
    if state.classification in (gw.UNMANAGED_SAME_NAME, gw.MARKER_INVALID):
        detail = "no ownership marker" if state.classification == gw.UNMANAGED_SAME_NAME else "invalid ownership marker"
        return [
            Action(
                BLOCKED,
                "bundle-project",
                name,
                f"refuse takeover of unmanaged project {name} ({detail})",
            )
        ]
    if state.inheritable is True:
        return [
            Action(
                BLOCKED,
                "bundle-project",
                name,
                "managed project is inheritable; the bundle project must be standalone before apply",
            )
        ]
    if state.bundle_version == inputs.bundle_version:
        return [
            Action(
                NO_CHANGE,
                "bundle-project",
                name,
                f"managed bundle {state.bundle_version} already deployed",
            )
        ]
    change = upgrade_class(state.bundle_version, inputs.bundle_version)
    note = f" ({change}; {ACKNOWLEDGEMENT})" if needs_acknowledgement(change) else f" ({change})"
    return [
        Action(
            UPDATE,
            "bundle-project",
            name,
            f"redeploy managed bundle {state.bundle_version} -> {inputs.bundle_version}{note}",
        )
    ]


def module_actions(inputs: Inputs, observation: GatewayObservation) -> list[Action]:
    """The MCP Module is a precondition this CLI cannot satisfy (install-module is Phase 6)."""

    if observation.module_error:
        return [
            Action(
                BLOCKED,
                "mcp-module",
                gw.MCP_MODULE_ID,
                f"module state unknown ({observation.module_error}); refusing to plan on an "
                "unverified precondition",
            )
        ]
    if observation.module is None:
        return [
            Action(
                BLOCKED,
                "mcp-module",
                gw.MCP_MODULE_ID,
                "MCP Module is not installed and healthy; module installation is Phase 6 "
                "(install-module is not implemented)",
            )
        ]
    return [
        Action(
            NO_CHANGE,
            "mcp-module",
            gw.MCP_MODULE_ID,
            f"detected version={observation.module.version or '?'} build={observation.module.build or '?'}",
        )
    ]


def server_config_actions(
    inputs: Inputs, observation: GatewayObservation, documents: docs.Documents
) -> list[Action]:
    """The Server Config intention for the selected profile (D09's explicit lists)."""

    kind = "server-config"
    if not observation.capability("server-config"):
        return [
            Action(
                BLOCKED,
                kind,
                inputs.server_config_name or "com.inductiveautomation.mcp",
                "Gateway does not document the server-config resource; the MCP server configuration "
                "cannot be verified or created",
            )
        ]
    if inputs.server_config_name is None:
        return [
            Action(
                SKIPPED,
                kind,
                "com.inductiveautomation.mcp",
                "no --server-config-name supplied; presence not probed",
            )
        ]
    name = inputs.server_config_name
    if observation.server_config_error:
        return [
            Action(
                BLOCKED,
                kind,
                name,
                f"server-config presence unknown ({observation.server_config_error})",
            )
        ]
    desired = inputs.profile_inventory("tools")
    summary = f"explicit Tool inventory (never *), profile {inputs.profile}, {len(desired)} Tools"
    document = observation.server_config
    if document is None:
        if documents.permissions is None:
            return [
                Action(
                    BLOCKED,
                    kind,
                    name,
                    "refusing to create an unauthenticated MCP Server Config: pass "
                    "--server-config-permissions-file (Security Level provisioning is issue #22)",
                )
            ]
        return [Action(CREATE, kind, name, summary)]
    observed, note = docs.observed_tools(document, inputs.bundle_project)
    if observed is not None and sorted(observed) == sorted(desired):
        return [Action(NO_CHANGE, kind, name, "explicit Tool inventory managed by apply")]
    held = document.get("config")
    if documents.permissions is None and not (
        isinstance(held, dict) and isinstance(held.get("permissions"), dict)
    ):
        return [
            Action(
                BLOCKED,
                kind,
                name,
                "the deployed Server Config carries no permissions tree to preserve: pass "
                "--server-config-permissions-file (Security Level provisioning is issue #22)",
            )
        ]
    drift = note or _tool_drift(observed, desired)
    return [Action(UPDATE, kind, name, f"{summary}; current state: {drift}")]


def _tool_drift(observed: Sequence[str] | None, desired: Sequence[str]) -> str:
    """A bounded description of how an existing Tool list differs from the profile."""

    current = set(observed or ())
    wanted = set(desired)
    adds = sorted(wanted - current)
    removes = sorted(current - wanted)
    parts = []
    if adds:
        parts.append(f"adds {_names(adds)}")
    if removes:
        parts.append(f"removes {_names(removes)}")
    return ", ".join(parts) or "no name-level difference"


def _names(values: Sequence[str], limit: int = DRIFT_NAMES) -> str:
    listed = list(values)
    shown = ", ".join(listed[:limit])
    return "[" + shown + (f", +{len(listed) - limit} more" if len(listed) > limit else "") + "]"


def policy_actions(
    inputs: Inputs,
    observation: GatewayObservation,
    documents: docs.Documents,
    policy: docs.PolicyObservation | None,
) -> list[Action]:
    """The Runtime Target Policy intention (D30 §1, owner ruling 1)."""

    kind = "runtime-policy"
    name = docs.POLICY_PATH
    if documents.policy_text is None:
        return [
            Action(
                SKIPPED,
                kind,
                name,
                "no --policy-file supplied; the Runtime Target Policy is neither written nor diffed",
            )
        ]
    if policy is None:
        return [Action(BLOCKED, kind, name, "the reserved policy provider was not observed")]
    if policy.error:
        return [Action(BLOCKED, kind, name, f"reserved provider state unknown ({policy.error})")]
    size = docs.byte_length(documents.policy_text)
    digest = docs.sha256_text(documents.policy_text)[:16]
    if not policy.provider_present:
        return [
            Action(
                CREATE,
                kind,
                name,
                f"create provider {docs.PROVIDER} and write {size} bytes (sha256 {digest}...), "
                f"declared-length cap {docs.MAX_BYTES} bytes",
            )
        ]
    if policy.matches(documents.policy_text):
        return [
            Action(
                NO_CHANGE,
                kind,
                name,
                f"deployment policy already matches {size} bytes (sha256 {digest}...)",
            )
        ]
    if policy.policy_text is None:
        detail = "the provider serves no policy Tag"
    elif policy.declared_length != size:
        detail = f"declared length {policy.declared_length} != {size}"
    else:
        detail = f"sha256 {docs.sha256_text(policy.policy_text)[:16]}... -> {digest}..."
    return [Action(UPDATE, kind, name, f"replace the served policy ({size} bytes; {detail})")]


def detect_only_actions(inputs: Inputs, observation: GatewayObservation) -> list[Action]:
    """Security level and runtime token are detected, never provisioned, by this CLI."""

    lines = [
        ("security-level", "security-levels", "Gateway security level"),
        ("runtime-token", "api-token", "Ignition API token for the MCP service user"),
    ]
    actions: list[Action] = []
    for kind, capability, label in lines:
        if observation.capability(capability):
            actions.append(
                Action(NO_CHANGE, kind, label, "detect-only, provisioning is apply-phase")
            )
        else:
            actions.append(
                Action(SKIPPED, kind, label, f"Gateway does not document {gw.CAPABILITY_ENDPOINTS[capability][1]}")
            )
    return actions


def build_actions(
    inputs: Inputs,
    observation: GatewayObservation,
    documents: docs.Documents | None = None,
    policy: docs.PolicyObservation | None = None,
) -> list[Action]:
    """Every intention, in the order ``apply`` executes them."""

    wanted = documents if documents is not None else docs.Documents()
    actions = module_actions(inputs, observation)
    actions.extend(project_actions(inputs, observation))
    actions.extend(server_config_actions(inputs, observation, wanted))
    actions.extend(policy_actions(inputs, observation, wanted, policy))
    actions.extend(detect_only_actions(inputs, observation))
    return actions


def plan_report(inputs: Inputs, actions: Sequence[Action], exit_code: int) -> dict[str, Any]:
    return {
        "command": "plan",
        "manifest": str(inputs.manifest_path),
        "bundleVersion": inputs.bundle_version,
        "profile": inputs.profile,
        "bundleProject": inputs.bundle_project,
        "actions": [action.as_dict() for action in actions],
        "applied": False,
        "exitCode": exit_code,
    }


async def run(
    inputs: Inputs,
    *,
    gateway_transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Observe the Gateway subset, print the intentions, and promise nothing was applied."""

    documents = docs.load(inputs)
    policy: docs.PolicyObservation | None = None
    async with make_gateway(inputs, gateway_transport) as client:
        _, observation = await probe_gateway(client, inputs)
        if observation.reachable and documents.policy_text is not None:
            policy = await docs.observe_policy(client)
    error = None if observation.reachable else (observation.error or "Gateway unreachable")
    actions = [] if error else build_actions(inputs, observation, documents, policy)
    return _emit(inputs, actions, error=error)


def _emit(inputs: Inputs, actions: Sequence[Action], *, error: str | None) -> int:
    """Report the plan and always end with the unchanged-state promise."""

    exit_code = 1 if error else (3 if any(action.action == BLOCKED for action in actions) else 0)
    if inputs.as_json:
        payload = plan_report(inputs, actions, exit_code)
        if error:
            payload["error"] = error
        emit(inputs, payload, [])
    else:
        for action in actions:
            print(action.as_line())
        if error:
            print(f"plan could not observe the Gateway: {error}")
    print(PLAN_SENTINEL)
    return exit_code
