"""G7 evidence row (issue #78, the setup CLI decided in D32).

The G7 stage runs inside the existing `phase4-live-apply` workflow. On a Gateway
that started with no MCP Module it drives the shipped `ignition-mcp` CLI end to end:
one one-line `setup` in `dev`, `initialize` and `tools/list` with each Assistant
role's Runtime token (and the refusal of each token at the other role's endpoint),
`start` with each role's Named static token, a second `setup` that changes nothing,
and `reset`, after which nothing `setup` recorded as created is left on the Gateway.

A G7 row is composed from the stage document (`setup-g7.json`) and the run identity
the workflow writes (`identity.json`, the same file G6 uses), plus the run, which the
caller names because only GitHub knows it after the run finished. The composer checks
every case against the stage document, and the emitted row is validated again through
`tooling.compat.evidence` before it is written. A row never claims `SUPPORTED`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tooling.compat.evidence import G7_LIVE_CASES, EvidenceError, parse_row
from tooling.compat.g6 import _SHA40, G6Error, _identity_facts, _load, _text, _verify_identity

SCHEMA_VERSION = 7
GATE = "G7"
RUN_WORKFLOWS = ("Phase 4 Live Gateway apply",)
STAGE_DOCUMENT = "setup-g7.json"
ROLES = ("analysis", "engineer")


class G7Error(ValueError):
    """The stage document or the run contradicts the row it would produce."""


def _step(stage: dict[str, Any], name: str, where: str) -> dict[str, Any]:
    steps = stage.get("steps")
    step = steps.get(name) if isinstance(steps, dict) else None
    if not isinstance(step, dict):
        raise G7Error(f"{where}: the stage document records no {name} step")
    return step


def _green_setup(step: dict[str, Any], where: str) -> str:
    if step.get("exitCode") != 0:
        raise G7Error(f"{where}: setup exited {step.get('exitCode')}")
    changed = step.get("changedSteps")
    if not isinstance(changed, list) or "runtime module" not in changed:
        raise G7Error(f"{where}: the first setup did not install the Module")
    statuses = {str(item.get("step")): item.get("status") for item in step.get("steps", []) if isinstance(item, dict)}
    for role in ROLES:
        if statuses.get(f"runtime check {role}") != "OK":
            raise G7Error(f"{where}: setup's closing check for {role} did not pass")
    if "FAILED" in statuses.values():
        raise G7Error(f"{where}: setup reported a FAILED step")
    return f"{len(changed)} steps CHANGED, both closing checks OK"


def _green_roles(step: dict[str, Any], where: str) -> str:
    counts = []
    for role in ROLES:
        data = step.get(role)
        if not isinstance(data, dict) or data.get("inventoryMatches") is not True:
            raise G7Error(f"{where}: {role}'s tools/list is not recorded as the role's exact inventory")
        tools = data.get("tools")
        if not isinstance(tools, list) or not tools or data.get("toolCount") != len(tools):
            raise G7Error(f"{where}: {role} records no Tool inventory")
        refused = data.get("otherTokensRefused")
        others = [other for other in ROLES if other != role]
        if not isinstance(refused, dict) or sorted(refused) != others:
            raise G7Error(f"{where}: {role} does not record the other role's token as refused")
        counts.append(f"{role} {len(tools)} Tools")
    return ", ".join(counts) + "; each token refused at the other endpoint"


def _green_rest(step: dict[str, Any], where: str) -> str:
    analysis = step.get("analysis")
    engineer = step.get("engineer")
    if not isinstance(analysis, dict) or analysis.get("readAllowed") is not True \
            or analysis.get("mutationRefused") != "permission_denied":
        raise G7Error(f"{where}: the Analysis token is not recorded as read-only")
    if not isinstance(engineer, dict) or engineer.get("mutationScopeGranted") is not True:
        raise G7Error(f"{where}: the Engineer token is not recorded with the Mutation scope")
    return "Analysis reads and is refused a Mutation; Engineer holds the Mutation scope"


def _green_setup_again(step: dict[str, Any], where: str) -> str:
    if step.get("exitCode") != 0:
        raise G7Error(f"{where}: the second setup exited {step.get('exitCode')}")
    if step.get("plannedChanges") != 0 or step.get("changedSteps") != []:
        raise G7Error(f"{where}: the second setup planned or changed something")
    return "no planned change and no CHANGED step"


def _green_reset(step: dict[str, Any], where: str) -> str:
    if step.get("exitCode") != 0:
        raise G7Error(f"{where}: reset exited {step.get('exitCode')}")
    created = step.get("created")
    if not isinstance(created, list) or "module" not in created:
        raise G7Error(f"{where}: reset records no created resources, or not the Module")
    if step.get("leftOnGateway") != {} or step.get("deploymentDirectoryRemoved") is not True:
        raise G7Error(f"{where}: reset left something behind")
    return f"{len(created)} created resources gone, deployment directory removed"


JUDGEMENTS = {
    "setup": _green_setup,
    "roles": _green_roles,
    "rest": _green_rest,
    "setupAgain": _green_setup_again,
    "reset": _green_reset,
}


def verify_stage(stage: dict[str, Any], identity: dict[str, Any], where: str = "stage") -> str:
    """Check the stage document for every case G7 owns."""

    if stage.get("ok") is not True:
        raise G7Error(f"{where}: the stage does not record a passing run; a G7 row needs a green stage")
    if stage.get("bundleVersion") != identity.get("bundleVersion"):
        raise G7Error(
            f"{where}: setup deployed bundle {stage.get('bundleVersion')!r}, the run identity names "
            f"{identity.get('bundleVersion')!r}"
        )
    for case, step_name in G7_LIVE_CASES.items():
        JUDGEMENTS[step_name](_step(stage, step_name, where), f"{where}: {step_name} ({case})")
    return f"{len(G7_LIVE_CASES)} cases corroborated from {STAGE_DOCUMENT}"


def build_g7_row(stage: dict[str, Any], identity: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Compose one G7 row from the stage document, the run identity and the run."""

    try:
        workflow = _text(run, "workflow", "run")
        head = _text(run, "head", "run")
        verified = _verify_identity(identity, run, "identity")
        facts = _identity_facts(identity)
    except G6Error as error:
        raise G7Error(str(error)) from error
    if workflow not in RUN_WORKFLOWS:
        raise G7Error(f"run: workflow {workflow!r} is not the G7 stage workflow ({RUN_WORKFLOWS})")
    if run.get("conclusion") != "success":
        raise G7Error(f"run: conclusion is {run.get('conclusion')!r}; only a green run is live evidence")
    if _SHA40.fullmatch(head) is None:
        raise G7Error("run: head must be a 40-character commit SHA")
    if not facts["mcpModuleArtifactVersion"]:
        raise G7Error("identity: mcpModuleArtifactVersion is missing from the run identity")
    corroborated = verify_stage(stage, identity)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "gate": GATE,
        **facts,
        "gatewayVersion": verified["gatewayVersion"],
        "gatewayBuild": verified["gatewayBuild"],
        "gatewayImage": verified["gatewayImage"],
        "gatewayImageDigest": verified["gatewayImageDigest"],
        "compatibilityStatus": "UNTESTED",
        "gateResult": "VERIFIED",
        "sourceRevision": head,
        "ownerAcceptedDeviations": ["phase4-live-environment-protection"],
        "runs": [
            {
                "runId": run["runId"],
                "workflow": workflow,
                "head": head,
                "conclusion": run["conclusion"],
                "gatewayVersion": verified["gatewayVersion"],
                "gatewayBuild": verified["gatewayBuild"],
                "gatewayImage": verified["gatewayImage"],
                "gatewayImageDigest": verified["gatewayImageDigest"],
                "harnessDocument": STAGE_DOCUMENT,
                "citation": (
                    "the G7 stage: ignition-mcp setup, the role checks, start, setup again and reset. "
                    "setup built the bundle from the same checkout the release was built from"
                ),
            }
        ],
        "g7": {
            "cases": {
                case: {
                    "verdict": "LIVE",
                    "runIds": [run["runId"]],
                    "source": f"{workflow} run {run['runId']}: the {step} step of {STAGE_DOCUMENT}",
                }
                for case, step in G7_LIVE_CASES.items()
            },
        },
        "limitations": [],
        "unsatisfiedAcceptance": [],
        "stageCorroboration": corroborated,
    }


def generate_g7(
    stage_path: Path,
    identity_path: Path,
    out_dir: Path,
    *,
    evidence_root: Path | None = None,
    run: dict[str, Any],
) -> Path:
    """Write and validate one G7 row; returns the row directory."""

    try:
        stage = _load(stage_path, "stage document")
        identity = _load(identity_path, "run identity")
    except G6Error as error:
        raise G7Error(str(error)) from error
    row = build_g7_row(stage, identity, dict(run))
    name = f"g7-{row['gatewayVersion']}-mcp-{row['mcpModuleBuild']}"
    directory = out_dir / name
    if directory.exists():
        raise G7Error(f"{directory}: refusing to overwrite existing evidence (frozen once written)")
    directory.mkdir(parents=True)
    (directory / "evidence.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        parse_row((evidence_root or out_dir) / name, row)
    except EvidenceError as error:
        raise G7Error(f"generated row rejected by the validator: {error}") from error
    return directory
