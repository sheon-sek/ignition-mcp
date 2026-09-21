"""``setup-native plan``: declarative intentions, never an installer (D20, D21).

The plan is derived from the same read-only observations ``doctor`` makes (Gateway
identity, module presence, project ownership marker, server-config presence) and
printed as ``<ACTION> <kind> <name>: <reason>`` lines.  Provisioning belongs to
``apply`` (Phase 4) and module installation to Phase 6; neither exists here, so
every run ends with the literal line ``No changes have been applied.``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native.doctor import (
    GatewayObservation,
    emit,
    make_gateway,
    probe_gateway,
)
from ignition_rest_mcp.cli.setup_native.inputs import Inputs

CREATE = "CREATE"
UPDATE = "UPDATE"
NO_CHANGE = "NO CHANGE"
BLOCKED = "BLOCKED"
#: Detect-only lines carry no intention; ``SKIP`` marks them as out of scope here.
SKIPPED = "SKIP"

PLAN_SENTINEL = "No changes have been applied."

ACKNOWLEDGEMENT = "requires explicit acknowledgement in apply"


@dataclass(frozen=True, slots=True)
class Action:
    """One planned intention."""

    action: str
    kind: str
    name: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"action": self.action, "kind": self.kind, "name": self.name, "reason": self.reason}

    def as_line(self) -> str:
        return f"{self.action} {self.kind} {self.name}: {self.reason}"


def parse_semver(value: str | None) -> tuple[int, int, int] | None:
    """Strict ``MAJOR.MINOR.PATCH`` parse; anything else is unparseable (``None``)."""

    if not isinstance(value, str):
        return None
    parts = value.strip().split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def upgrade_class(installed: str | None, target: str) -> str:
    """D21 change class: ``patch``, ``minor``, ``major`` or ``downgrade``."""

    current = parse_semver(installed)
    wanted = parse_semver(target)
    if current is None or wanted is None:
        return "major"
    if wanted < current:
        return "downgrade"
    if wanted[0] > current[0]:
        return "major"
    if wanted[1] > current[1]:
        return "minor"
    return "patch"


def needs_acknowledgement(change_class: str) -> bool:
    return change_class in ("major", "downgrade")


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


def server_config_actions(inputs: Inputs, observation: GatewayObservation) -> list[Action]:
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
    if observation.server_config_error:
        return [
            Action(
                BLOCKED,
                kind,
                inputs.server_config_name,
                f"server-config presence unknown ({observation.server_config_error})",
            )
        ]
    if observation.server_config_exists is True:
        return [
            Action(
                NO_CHANGE,
                kind,
                inputs.server_config_name,
                "explicit Tool inventory managed by apply",
            )
        ]
    return [
        Action(
            CREATE,
            kind,
            inputs.server_config_name,
            "explicit Tool inventory (never *)",
        )
    ]


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


def build_actions(inputs: Inputs, observation: GatewayObservation) -> list[Action]:
    actions = module_actions(inputs, observation)
    actions.extend(project_actions(inputs, observation))
    actions.extend(server_config_actions(inputs, observation))
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

    async with make_gateway(inputs, gateway_transport) as client:
        _, observation = await probe_gateway(client, inputs)
    error = None if observation.reachable else (observation.error or "Gateway unreachable")
    actions = [] if error else build_actions(inputs, observation)
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
