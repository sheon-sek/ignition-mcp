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

from pathlib import Path
from typing import Any, Sequence

import httpx

from ignition_rest_mcp.cli.setup_native import documents as docs
from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native import security
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
from ignition_rest_mcp.cli.setup_native.inputs import SECURITY_LEVEL_PARENT, Inputs

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
    """The MCP Module is a precondition ``install-module`` satisfies, never this command."""

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
                "MCP Module is not installed and healthy; run setup-native install-module with the "
                "trusted local .modl, restart the Gateway, then plan again",
            ),
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
    """The security planes this run only detects — a flag-enabled one is planned instead."""

    lines = [
        (inputs.provision_security_levels, "security-level", "security-levels", "Gateway security level"),
        (inputs.create_runtime_token, "runtime-token", "api-token", "Ignition API token for the MCP service user"),
    ]
    actions: list[Action] = []
    for planned, kind, capability, label in lines:
        if planned:
            # ``provisioning_actions`` carries this line, in the order apply runs it.
            continue
        if observation.capability(capability):
            actions.append(
                Action(NO_CHANGE, kind, label, "detect-only, provisioning is apply-phase")
            )
        else:
            actions.append(
                Action(SKIPPED, kind, label, f"Gateway does not document {gw.CAPABILITY_ENDPOINTS[capability][1]}")
            )
    return actions


def provisioning_actions(
    inputs: Inputs, observation: GatewayObservation, observed: security.Observation | None
) -> list[Action]:
    """D20's opt-in Security Level and Runtime API token lines; empty without the flags."""

    if not inputs.provisions_security:
        return []
    state = observed if observed is not None else security.Observation()
    actions: list[Action] = []
    if inputs.provision_security_levels:
        action, detail, _ = _level_standing(inputs, observation, state)
        actions.append(Action(action, "security-level", inputs.security_level_path, detail))
    if inputs.create_runtime_token:
        actions.append(_runtime_token_action(inputs, observation, state))
    return actions


def _level_standing(
    inputs: Inputs, observation: GatewayObservation, observed: security.Observation
) -> tuple[str, str, list[dict[str, Any]] | None]:
    """One action, its reason and the level's granted tree for the Runtime API token."""

    if not observation.capability("security-levels"):
        return BLOCKED, (
            f"Gateway does not document GET {gw.SECURITY_LEVELS_PATH}; the dedicated Runtime Security "
            "Level cannot be read or written"
        ), None
    if observed.levels_error:
        return BLOCKED, f"the Gateway's security tree could not be read ({observed.levels_error})", None
    tree = observed.levels or []
    if not observed.signature:
        return BLOCKED, (
            "the security-levels singleton serves no Resource signature, so a write cannot be "
            "preconditioned on the tree this plan reasoned about"
        ), None
    found = security.find_level(tree, inputs.security_level)
    if found is not None:
        path, node = found
        if path != [SECURITY_LEVEL_PARENT, inputs.security_level]:
            return BLOCKED, (
                f"a level named {inputs.security_level} already exists at {'.'.join(path)}; refusing to "
                "add a second one under a different path"
            ), None
        problem = security.level_shape_problem(node)
        if problem:
            return BLOCKED, (
                f"{inputs.security_level_path} already exists but {problem}; this CLI never modifies an "
                "existing Security Level"
            ), None
        grant = security.grant_tree(tree, [SECURITY_LEVEL_PARENT, inputs.security_level])
        return NO_CHANGE, "the dedicated Runtime level is already present; left unchanged", grant
    merged, error = security.with_managed_level(tree, inputs)
    if merged is None:
        return BLOCKED, error, None
    return CREATE, (
        f"add the dedicated Runtime level under {SECURITY_LEVEL_PARENT}, preserving the other "
        f"{len(tree)} top-level level(s)"
    ), security.token_grant(inputs, tree, creating=True)


def _runtime_token_action(
    inputs: Inputs, observation: GatewayObservation, observed: security.Observation
) -> Action:
    """The Runtime API token intention: create it once, never overwrite it (D09, D20)."""

    kind = "runtime-token"
    name = inputs.runtime_token
    level_action, level_detail, _ = _level_standing(inputs, observation, observed)
    if level_action == BLOCKED:
        return Action(
            BLOCKED, kind, name,
            f"the Runtime API token is granted the dedicated Security Level, and {level_detail}",
        )
    if level_action == CREATE and not inputs.provision_security_levels:
        return Action(
            BLOCKED, kind, name,
            f"{inputs.security_level_path} does not exist and --provision-security-levels was not passed; "
            "a Runtime credential must be granted a dedicated Security Level",
        )
    if not observation.capability("api-token"):
        return Action(
            BLOCKED, kind, name,
            f"Gateway does not document POST {gw.API_TOKEN_PATH}; the Runtime API token cannot be read "
            "or created",
        )
    if observed.token_error:
        return Action(
            BLOCKED, kind, name,
            f"an API token of this name could not be read ({observed.token_error})",
        )
    token_file = inputs.runtime_token_file
    assert token_file is not None  # load_inputs requires it together with --create-runtime-token
    if observed.secret.error:
        return Action(
            BLOCKED, kind, name,
            f"--runtime-token-file {token_file} is unusable: {observed.secret.error}",
        )
    if observed.token is not None:
        return _existing_token_action(inputs, name, token_file, observed)
    if observed.secret.exists:
        return Action(
            BLOCKED, kind, name,
            f"--runtime-token-file already holds a credential ({observed.secret.name!r}); refusing to "
            "overwrite it — move it aside or pass a different path",
        )
    blocked = security.check_secret_file_target(token_file)
    if blocked:
        return Action(BLOCKED, kind, name, blocked)
    channel = "secureChannelRequired=false" if inputs.runtime_token_insecure_channel else "secureChannelRequired=true"
    level_note = "the level this run creates" if level_action == CREATE else "the existing level"
    return Action(
        CREATE, kind, name,
        f"create API token granted {inputs.security_level_path} ({level_note}), {channel}; the secret is "
        f"written to {token_file} with mode 0600 and never reported",
    )


def _existing_token_action(
    inputs: Inputs, name: str, token_file: Path, observed: security.Observation
) -> Action:
    """What to do about an API token the Gateway already serves: never overwrite it."""

    kind = "runtime-token"
    stored = security.stored_token_hash(observed.token)
    if not stored:
        return Action(
            BLOCKED, kind, name,
            "an API token of this name already exists and the Gateway serves no readable token hash; "
            "refusing to overwrite it",
        )
    if not observed.secret.exists:
        return Action(
            BLOCKED, kind, name,
            f"an API token of this name already exists ({token_file} holds no credential to prove "
            "ownership); refusing to overwrite it — reuse the existing token, or pass "
            "--runtime-token-name to provision a new one",
        )
    if observed.secret.name != name:
        return Action(
            BLOCKED, kind, name,
            f"{token_file} holds the credential {observed.secret.name!r}, not {name!r}; refusing to "
            "compare or overwrite it",
        )
    if not observed.secret.hashes_to(stored):
        return Action(
            BLOCKED, kind, name,
            f"the API token's stored hash is not the secret in {token_file}; refusing to overwrite it — "
            "reuse the existing credential, or pass --runtime-token-name to provision a new one",
        )
    return Action(
        NO_CHANGE, kind, name,
        f"the Runtime API token already exists and matches {token_file}; left unchanged",
    )


def build_actions(
    inputs: Inputs,
    observation: GatewayObservation,
    documents: docs.Documents | None = None,
    policy: docs.PolicyObservation | None = None,
    observed: security.Observation | None = None,
) -> list[Action]:
    """Every intention, in the order ``apply`` executes them."""

    wanted = documents if documents is not None else docs.Documents()
    actions = module_actions(inputs, observation)
    actions.extend(provisioning_actions(inputs, observation, observed))
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
    observed: security.Observation | None = None
    async with make_gateway(inputs, gateway_transport) as client:
        _, observation = await probe_gateway(client, inputs)
        if observation.reachable and documents.policy_text is not None:
            policy = await docs.observe_policy(client)
        if observation.reachable and inputs.provisions_security:
            observed = await security.observe(client, inputs)
    error = None if observation.reachable else (observation.error or "Gateway unreachable")
    actions = [] if error else build_actions(inputs, observation, documents, policy, observed)
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
