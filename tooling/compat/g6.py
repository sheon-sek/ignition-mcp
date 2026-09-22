"""G6 evidence row (D26's final v1 release gate, ticket #56).

Phase 6 closes v1 with a live stage inside the existing `phase4-live-apply`
workflow: the Gateway starts with NO MCP Module, the pinned `.modl` is installed
through `setup-native install-module` (hash check, certificate and EULA acceptance
under their own flags, install, restart, read-back), and then the same deployment
the G4 row proved runs again on top of that install, plus the Bundle upgrade: a
lowered real release first, then the exact release with `--acknowledge-upgrade`.

A G6 row is composed from the stage artifact (`setup-native-apply.json`, the same
document the ticket #21/#22 stage has always written, now carrying the
`install-module`, `install-module-again`, `downgradeApply` and `upgradeApply`
steps) plus the run, which the caller names on the command line because only GitHub
knows it after the run has finished. The builder refuses to compose anything it
cannot corroborate, and the emitted row is re-validated through
`tooling.compat.evidence` (the G6 rules) before it is written, so a row this module
produces can never claim more than the artifacts show and can never claim
`SUPPORTED`.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from tooling.compat.evidence import (
    BINDING_STATUSES,
    D27_TUPLE,
    G6_LIVE_CASES,
    EvidenceError,
    parse_row,
)

SCHEMA_VERSION = 6
GATE = "G6"
#: The workflow a G6 row may cite. The module-install stage lives in the apply
#: workflow, which is the only stage that starts a Gateway with no Module.
RUN_WORKFLOWS = ("Phase 4 Live Gateway apply",)
#: The stage document a cited run must hold, and the store the row records for it.
STAGE_DOCUMENT = "setup-native-apply.json"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class G6Error(ValueError):
    """The stage artifact or the run contradicts the row it would produce."""


def _semver(value: Any, where: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise G6Error(f"{where}: {value!r} is not a MAJOR.MINOR.PATCH bundle version")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _load(path: Path, what: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise G6Error(f"{path}: unreadable {what}: {error}") from error
    if not isinstance(document, dict):
        raise G6Error(f"{path}: {what} must be a JSON object")
    return document


def _text(document: dict[str, Any], key: str, where: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise G6Error(f"{where}: {key} is missing")
    return value


def _stage_step(stage: dict[str, Any], name: str, where: str) -> dict[str, Any]:
    steps = stage.get("steps")
    if not isinstance(steps, dict) or name not in steps:
        raise G6Error(f"{where}: the stage document records no {name} step")
    step = steps[name]
    if not isinstance(step, dict):
        raise G6Error(f"{where}: the {name} step is not an object")
    return step


def _green_install(step: dict[str, Any], where: str) -> str:
    """The install step, judged as the report the CLI emitted."""

    if step.get("exitCode") != 0:
        raise G6Error(f"{where}: install-module exited {step.get('exitCode')}")
    report = step.get("report")
    if not isinstance(report, dict) or report.get("outcome") != "INSTALL":
        raise G6Error(f"{where}: install-module did not record INSTALL")
    restart = report.get("restart")
    if not isinstance(restart, dict) or restart.get("requested") is not True or restart.get("ready") is not True:
        raise G6Error(f"{where}: install-module did not prove the module came back after the restart")
    build = report.get("moduleBuild")
    if not isinstance(build, str) or not build:
        raise G6Error(f"{where}: install-module records no module build")
    return f"installed build={build}, restart ready"


def _green_no_change(step: dict[str, Any], where: str) -> str:
    """The second install run, judged as the NO CHANGE read-back."""

    if step.get("exitCode") != 0:
        raise G6Error(f"{where}: the second install-module exited {step.get('exitCode')}")
    report = step.get("report")
    if not isinstance(report, dict) or report.get("outcome") != "NO CHANGE":
        raise G6Error(f"{where}: the second install-module is not a NO CHANGE run")
    return "NO CHANGE: the installed build is served and nothing was uploaded"


def _green_apply(step: dict[str, Any], where: str, *, kinds: tuple[str, ...]) -> str:
    """An apply step, judged as all-green writes of exactly the named kinds."""

    if step.get("exitCode") != 0:
        raise G6Error(f"{where}: apply exited {step.get('exitCode')}")
    writes = step.get("report", {}).get("writes") if isinstance(step.get("report"), dict) else None
    if not isinstance(writes, list) or not writes:
        raise G6Error(f"{where}: apply records no writes")
    failed = [write for write in writes if isinstance(write, dict) and write.get("ok") is not True]
    if failed:
        raise G6Error(f"{where}: {len(failed)} apply write(s) failed")
    written = sorted(
        str(write.get("kind")) for write in writes
        if isinstance(write, dict) and write.get("action") in ("CREATE", "UPDATE")
    )
    if written != sorted(kinds):
        raise G6Error(f"{where}: apply wrote {written}, not {sorted(kinds)}")
    return f"{len(written)} write(s), all verified"


def _green_second_apply(step: dict[str, Any], where: str) -> str:
    """The D20 idempotency check: the second apply writes nothing."""

    if step.get("exitCode") != 0:
        raise G6Error(f"{where}: the second apply exited {step.get('exitCode')}")
    writes = step.get("report", {}).get("writes") if isinstance(step.get("report"), dict) else None
    if not isinstance(writes, list):
        raise G6Error(f"{where}: the second apply records no writes")
    changed = [
        write.get("action") for write in writes
        if isinstance(write, dict) and write.get("action") in ("CREATE", "UPDATE")
    ]
    if changed:
        raise G6Error(f"{where}: the second apply wrote {changed}")
    return "no write on the second apply (D20 idempotency)"


def _green_upgrade(step: dict[str, Any], where: str) -> str:
    """The Bundle upgrade step: the real bundle applied over an older one, then green.

    The shape is the round-1 order: the starting deployment was the lowered release,
    so the step records the version observed before (and named by the upgrade plan's
    own read of the Gateway) and the released version deployed after.
    """

    if step.get("exitCode") != 0:
        raise G6Error(f"{where}: the upgrade apply exited {step.get('exitCode')}")
    before = _semver(step.get("bundleVersionBefore"), f"{where}: bundleVersionBefore")
    after = _semver(step.get("bundleVersionAfter"), f"{where}: bundleVersionAfter")
    if after <= before:
        raise G6Error(
            f"{where}: the upgrade does not move the bundle version forward "
            f"({step.get('bundleVersionBefore')} -> {step.get('bundleVersionAfter')})"
        )
    plan_line = step.get("planLine")
    if not isinstance(plan_line, dict) or plan_line.get("action") != "UPDATE":
        raise G6Error(f"{where}: the upgrade plan line is not an UPDATE of the managed project")
    reason = str(plan_line.get("reason", ""))
    before_text = str(step.get("bundleVersionBefore"))
    after_text = str(step.get("bundleVersionAfter"))
    if before_text not in reason or after_text not in reason:
        raise G6Error(
            f"{where}: the upgrade plan does not record the observed transition "
            f"{before_text} -> {after_text}"
        )
    if step.get("verifyGreen") is not True or not isinstance(step.get("verifyAttempts"), int) \
            or step["verifyAttempts"] < 1:
        raise G6Error(f"{where}: the upgrade step records no green verify")
    writes = step.get("report", {}).get("writes") if isinstance(step.get("report"), dict) else None
    if not isinstance(writes, list) or not writes:
        raise G6Error(f"{where}: the upgrade apply records no writes")
    failed = [write for write in writes if isinstance(write, dict) and write.get("ok") is not True]
    if failed:
        raise G6Error(f"{where}: {len(failed)} upgrade write(s) failed")
    changed = sorted(
        str(write.get("kind")) for write in writes
        if isinstance(write, dict) and write.get("action") in ("CREATE", "UPDATE")
    )
    if changed != ["bundle-project"]:
        raise G6Error(f"{where}: the upgrade wrote {changed}, not exactly the bundle project")
    return (
        f"bundle upgrade {before_text} -> {after_text} under --acknowledge-upgrade; "
        f"verify green (attempt {step['verifyAttempts']})"
    )


def verify_stage(stage: dict[str, Any], run: dict[str, Any], identity: dict[str, Any],
                 where: str = "stage") -> str:
    """The stage artifact, checked for every case D26's G6 stage owns."""

    if stage.get("ok") is not True:
        raise G6Error(
            f"{where}: the stage does not record a passing run; a G6 row may only be "
            "composed from a green stage"
        )
    deployed = stage.get("bundle")
    if not isinstance(deployed, dict):
        raise G6Error(f"{where}: the stage document records no deployed release")
    for key in ("version", "sha256"):
        if not isinstance(deployed.get(key), str) or not deployed[key]:
            raise G6Error(f"{where}: the deployed release records no {key}")
    if deployed["version"] != identity.get("bundleVersion") or deployed["sha256"] != identity.get("bundleSha256"):
        raise G6Error(
            f"{where}: the stage deployed bundle {deployed['version']!r}/"
            f"{str(deployed['sha256'])[:12]}... but the run identity names "
            f"{identity.get('bundleVersion')!r}/{str(identity.get('bundleSha256'))[:12]}..."
        )
    judgements = {
        "module install": _green_install,
        "module install is idempotent": _green_no_change,
        "fresh apply and exact inventory verification": lambda step, w: _green_apply(
            step, w,
            kinds=("security-level", "runtime-token", "bundle-project", "server-config", "runtime-policy"),
        ),
        "apply idempotency (second plan and apply write nothing)": _green_second_apply,
        "bundle upgrade with acknowledgement": _green_upgrade,
    }
    for case in G6_LIVE_CASES:
        step_name = G6_LIVE_CASES[case]
        step = _stage_step(stage, step_name, where)
        judgements[case](step, f"{where}: {step_name}")
    upgrade = _stage_step(stage, "upgradeApply", where)
    if upgrade.get("bundleVersionAfter") != identity.get("bundleVersion"):
        raise G6Error(
            f"{where}: the upgrade deployed {upgrade.get('bundleVersionAfter')!r}, not the "
            f"released bundle {identity.get('bundleVersion')!r}"
        )
    return f"{len(G6_LIVE_CASES)} cases corroborated from {STAGE_DOCUMENT}"


def _verify_identity(identity: dict[str, Any], run: dict[str, Any], where: str) -> dict[str, Any]:
    for key, expected in (
        ("runId", run["runId"]),
        ("sourceRevision", run["head"]),
    ):
        if identity.get(key) != expected:
            raise G6Error(f"{where}: {key} is {identity.get(key)!r}, the row claims {expected!r}")
    for key in ("gatewayVersion", "gatewayBuild", "gatewayImage", "gatewayImageDigest"):
        if not isinstance(identity.get(key), str) or not identity[key]:
            raise G6Error(f"{where}: {key} is missing from the run identity")
    return identity


def _identity_facts(identity: dict[str, Any]) -> dict[str, Any]:
    """The tuple and binding facts the row records, derived from the run identity.

    The identity carries the machine-readable facts only: the gateway and module
    tuple, the deployed release's version, hash and the manifest's binding status.
    Everything the D27 rules judge is derived here, so no hand-authored field can
    flip a row's binding story:

    - the D27 exception is exact-tuple only (D27_TUPLE, fail-closed);
    - a tuple without a published outputSchema is recorded honestly
      (``UNVERIFIED_LIMITATION`` / ``FAILED_NATIVE_BINDING``), never excused;
    - ``outputSchemaPublished`` follows the release manifest's binding status, which
      is what the bundle's own toolRequirements recorded at build time.
    """

    for key in ("gatewayVersion", "gatewayBuild", "mcpModuleVersion", "mcpModuleBuild",
                "mcpModuleSha256", "bundleVersion", "bundleSha256",
                "nativeResponseBindingStatus"):
        if not isinstance(identity.get(key), str) or not identity[key]:
            raise G6Error(f"identity: {key} is missing from the run identity")
    manifest_status = identity["nativeResponseBindingStatus"]
    if manifest_status not in BINDING_STATUSES:
        raise G6Error(f"identity: nativeResponseBindingStatus {manifest_status!r} is unknown")
    d27 = all(
        D27_TUPLE[key] == identity[key]
        for key in ("gatewayVersion", "gatewayBuild", "mcpModuleVersion",
                    "mcpModuleBuild", "mcpModuleSha256")
    )
    if manifest_status == "VERIFIED":
        binding, status, published = "VERIFIED", "VERIFIED", True
    elif d27 and manifest_status == "VERIFIED_WITH_LIMITATION":
        # The D27 exception: the exact characterized tuple, outputSchema still absent.
        binding, status, published = "VERIFIED_WITH_LIMITATION", "VERIFIED", False
    elif manifest_status in {"UNVERIFIED_LIMITATION", "FAILED_NATIVE_BINDING"}:
        binding, status, published = manifest_status, "FAILED_NATIVE_BINDING", False
    else:
        # D21's honest mapping: VERIFIED_WITH_LIMITATION or NATIVE_BINDING_PENDING on
        # a tuple that is not the D27 one is recorded as an unverified limitation.
        binding, status, published = "UNVERIFIED_LIMITATION", "FAILED_NATIVE_BINDING", False
    return {
        "bundleVersion": identity["bundleVersion"],
        "bundleSha256": identity["bundleSha256"],
        "mcpModuleVersion": identity["mcpModuleVersion"],
        "mcpModuleArtifactVersion": identity.get("mcpModuleArtifactVersion", ""),
        "mcpModuleBuild": identity["mcpModuleBuild"],
        "mcpModuleSha256": identity["mcpModuleSha256"],
        "nativeResponseBinding": binding,
        "d27ExceptionApplied": d27,
        "outputSchemaPublished": published,
        "status": status,
    }


def build_g6_row(
    stage: dict[str, Any],
    identity: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    """Compose one schema-valid G6 row from the stage artifact and the run."""

    workflow = _text(run, "workflow", "run")
    if workflow not in RUN_WORKFLOWS:
        raise G6Error(f"run: workflow {workflow!r} is not a G6 stage workflow ({RUN_WORKFLOWS})")
    if run.get("conclusion") != "success":
        raise G6Error(f"run: conclusion is {run.get('conclusion')!r}; a green run is the only admissible live evidence")
    head = _text(run, "head", "run")
    if _SHA40.fullmatch(head) is None:
        raise G6Error("run: head must be a 40-character commit SHA")

    verified = _verify_identity(identity, run, "identity")
    facts = _identity_facts(identity)
    if not facts["mcpModuleArtifactVersion"]:
        raise G6Error("identity: mcpModuleArtifactVersion is missing from the run identity")
    corroborated = verify_stage(stage, run, identity)

    row: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "gate": GATE,
        # The tuple is the one the stage installed: this run both puts the Module on
        # the Gateway and deploys the release, so the row measures its own tuple.
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
                "citation": "the module-install stage, whose install, apply and upgrade steps are this row's live evidence",
            }
        ],
        "g6": {
            "cases": {
                case: {
                    "verdict": "LIVE",
                    "runIds": [run["runId"]],
                    "source": (
                        f"{workflow} run {run['runId']}: the {G6_LIVE_CASES[case]} step "
                        f"of {STAGE_DOCUMENT}"
                    ),
                }
                for case in G6_LIVE_CASES
            },
        },
        "limitations": [],
        "unsatisfiedAcceptance": [],
        "stageCorroboration": corroborated,
    }
    return row


def generate_g6(
    stage_path: Path,
    identity_path: Path,
    out_dir: Path,
    *,
    evidence_root: Path | None = None,
    run: dict[str, Any],
) -> Path:
    """Write and self-validate one G6 row; returns the row directory."""

    stage = _load(stage_path, "stage document")
    identity = _load(identity_path, "run identity")
    tree = evidence_root or out_dir
    row = build_g6_row(stage, identity, dict(run))
    directory = out_dir / f"g6-{row['gatewayVersion']}-mcp-{row['mcpModuleBuild']}"
    if directory.exists():
        raise G6Error(f"{directory}: refusing to overwrite existing evidence (frozen once written)")
    directory.mkdir(parents=True)
    (directory / "evidence.json").write_text(
        json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    try:
        # Validate the row the way the tree's loader will.
        parse_row(tree / f"g6-{row['gatewayVersion']}-mcp-{row['mcpModuleBuild']}", row)
    except EvidenceError as error:
        raise G6Error(f"generated row rejected by the validator: {error}") from error
    return directory
