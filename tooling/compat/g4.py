"""G4 close-out evidence row (D21/D23, Phase 4 ticket #23).

Phase 4 spans several live workflows, milestones and heads, so a G4 row is a
*close-out* row rather than one run's row: it carries the Gateway/Module tuple,
the bundle release, every contributing live run, D26's L5 case matrix for both
Planes, the exact profile inventories and every recorded limitation.

The row is composed from two committed inputs plus the harness artifacts that
hang off the cited runs:

- a **close document** (this ticket's authored input: the runs, the L5 matrix,
  the inventories and the limitations), and
- the **harness artifacts** themselves, one per cited run, which the builder
  re-reads to prove that a run recorded as green really was green and that its
  Gateway/Module identity and deployed bundle are the ones the row claims.

The builder refuses to compose anything it cannot corroborate, and the emitted
row is re-validated through ``tooling.compat.evidence`` (the G4 rules) before it
is written, so a row this module produces can never claim more than the
artifacts show and can never claim ``SUPPORTED``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from tooling.compat.evidence import (
    D26_L5_CASES,
    G4_PLANES,
    G4_PROFILES,
    EvidenceError,
    load_evidence,
)
from tooling.contracts.lint import CURRENT_RUNTIME_MUTATION_TOOLS, EXPECTED_PROFILE_TOOLS

SCHEMA_VERSION = 4


class G4Error(ValueError):
    """The close document or a cited artifact contradicts the row it would produce."""


#: How a run's harness artifact is found inside the downloaded artifact tree and
#: which harness document proves it ran green.
RUN_WORKFLOWS: dict[str, dict[str, str]] = {
    "Phase 4 Live Gateway G4a": {"document": "evidence.json", "driver": "runtime"},
    "Phase 4 Live Gateway G4b": {"document": "evidence.json", "driver": "runtime"},
    "Phase 4 Live Gateway REST mutation": {"document": "observations.json", "driver": "rest"},
    "Phase 4 Live Gateway apply": {"document": "setup-native-apply.json", "driver": "apply"},
}

_SHA = re.compile(r"^[0-9a-f]{64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _load(path: Path, what: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise G4Error(f"{path}: unreadable {what}: {error}") from error
    if not isinstance(document, dict):
        raise G4Error(f"{path}: {what} must be a JSON object")
    return document


def _text(document: dict[str, Any], key: str, where: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise G4Error(f"{where}: {key} is missing")
    return value


def _find_run_documents(artifacts: Path, run_id: str, filename: str) -> list[Path]:
    if not artifacts.is_dir():
        raise G4Error(f"{artifacts}: the artifact directory does not exist")
    return sorted(path for path in artifacts.glob(f"run-{run_id}/**/{filename}") if path.is_file())


def _verify_runtime_run(document: dict[str, Any], run: dict[str, Any], source: Path) -> str:
    """A driver summary: green, no drift, and the identity the row claims."""

    identity = document.get("identity")
    if not isinstance(identity, dict):
        raise G4Error(f"{source}: the driver summary carries no identity section")
    for key, expected in (
        ("runId", run["runId"]),
        ("gatewayVersion", run["gatewayVersion"]),
        ("gatewayBuild", run["gatewayBuild"]),
        ("mcpModuleVersion", run["mcpModuleVersion"]),
        ("mcpModuleBuild", run["mcpModuleBuild"]),
        ("mcpModuleSha256", run["mcpModuleSha256"]),
    ):
        if identity.get(key) != expected:
            raise G4Error(
                f"{source}: identity.{key} is {identity.get(key)!r}, the row claims {expected!r}"
            )
    if document.get("drift"):
        raise G4Error(f"{source}: the run drifted from the frozen characterization: {document['drift']}")
    stages = document.get("stages")
    if not isinstance(stages, list) or not stages:
        raise G4Error(f"{source}: the driver summary records no stages")
    for stage in stages:
        if not isinstance(stage, dict) or stage.get("ok") is not True:
            name = stage.get("stage") if isinstance(stage, dict) else stage
            raise G4Error(f"{source}: stage {name!r} did not succeed")
        guard = stage.get("guard")
        checks = guard.get("checks") if isinstance(guard, dict) else None
        if isinstance(checks, dict):
            failed = [name for name, value in checks.items() if value is False]
            if failed:
                raise G4Error(f"{source}: stage {stage.get('stage')!r} failed its guard: {failed}")
    return f"N stages={len(stages)} drift=clean"


def _verify_rest_run(document: dict[str, Any], run: dict[str, Any], source: Path) -> str:
    """A REST observations document: every case ok, on the claimed Gateway."""

    if document.get("passed") is not True:
        raise G4Error(f"{source}: the REST observations do not record a passing run")
    if document.get("gate") != "G4":
        raise G4Error(f"{source}: the REST observations are not a G4 document")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise G4Error(f"{source}: the REST observations record no cases")
    failed = [case.get("case") for case in cases if not isinstance(case, dict) or case.get("ok") is not True]
    if failed:
        raise G4Error(f"{source}: {len(failed)} REST case(s) failed: {failed[:5]}")
    identity_path = source.parent / "identity.json"
    identity = _load(identity_path, "REST identity") if identity_path.is_file() else {}
    for key, expected in (
        ("runId", run["runId"]),
        ("gatewayVersion", run["gatewayVersion"]),
        ("gatewayBuild", run["gatewayBuild"]),
    ):
        if identity.get(key) != expected:
            raise G4Error(f"{identity_path}: {key} is {identity.get(key)!r}, expected {expected!r}")
    return f"{len(cases)} cases, modes={document.get('modes')}"


def _verify_apply_run(document: dict[str, Any], run: dict[str, Any], source: Path) -> str:
    """A setup-native apply report: the write sequence ended with a green verify."""

    if document.get("ok") is not True:
        raise G4Error(f"{source}: apply reports ok={document.get('ok')!r}: {document.get('failure')!r}")
    if document.get("runId") != run["runId"]:
        raise G4Error(f"{source}: apply reports runId {document.get('runId')!r}, the row claims {run['runId']!r}")
    return f"profile={document.get('profile')} serverConfig={document.get('serverConfig')}"


def _bundle_sha_from_artifact(run_dir: Path) -> str | None:
    """The deployed ZIP's SHA-256 as the run's own artifact records it."""

    for name in ("runtime.sha256", "deployed.sha256", "bundle.sha256"):
        candidate = next(iter(sorted(run_dir.glob(name))), None)
        if candidate is None:
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            digest = line.split()[0] if line.split() else ""
            if _SHA.fullmatch(digest):
                return digest
    return None


def _verify_run(artifacts: Path, close: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    workflow = _text(run, "workflow", "runs[]")
    spec = RUN_WORKFLOWS.get(workflow)
    if spec is None:
        raise G4Error(f"runs[]: unknown workflow {workflow!r}")
    run_id = _text(run, "runId", "runs[]")
    documents = _find_run_documents(artifacts, run_id, spec["document"])
    if not documents:
        raise G4Error(
            f"run {run_id}: no {spec['document']} found under {artifacts}/run-{run_id}; "
            "a run may only be cited when its own artifact is available"
        )
    document: dict[str, Any] = {}
    document_path = documents[0]
    verified: str | None = None
    failures: list[str] = []
    for candidate in documents:
        try:
            loaded = _load(candidate, "harness document")
            if spec["driver"] == "runtime":
                detail = _verify_runtime_run(loaded, run, candidate)
            elif spec["driver"] == "rest":
                detail = _verify_rest_run(loaded, run, candidate)
            else:
                detail = _verify_apply_run(loaded, run, candidate)
        except G4Error as error:
            failures.append(str(error))
            continue
        document, document_path, verified = loaded, candidate, detail
        break
    if verified is None:
        # A download tree holds one directory per Gateway row, so the run's own
        # document is the one whose identity is the row's identity.
        raise G4Error(
            f"run {run_id}: no {spec['document']} matches the row's identity: {failures[0] if failures else ''}"
        )

    declared_sha = run.get("bundleSha256")
    artifact_sha = _bundle_sha_from_artifact(document_path.parent)
    if artifact_sha is not None:
        if not isinstance(declared_sha, str) or _SHA.fullmatch(declared_sha) is None:
            raise G4Error(f"run {run_id}: the artifact records a bundle SHA but the run does not declare one")
        if artifact_sha != declared_sha:
            raise G4Error(
                f"run {run_id}: the artifact deployed {artifact_sha}, the run declares {declared_sha}"
            )
    run_revision = document.get("identity", {}).get("sourceRevision") if spec["driver"] == "runtime" else None
    if isinstance(run_revision, str) and _SHA40.fullmatch(run_revision):
        run["runRevision"] = run_revision
    run["harnessDocument"] = document_path.name
    run["verified"] = verified
    return run


def _verify_l5(l5: Any) -> dict[str, Any]:
    if not isinstance(l5, dict) or set(l5) != set(D26_L5_CASES):
        raise G4Error(f"close: l5 must account for exactly the D26 G4 cases {D26_L5_CASES}")
    for case in D26_L5_CASES:
        planes = l5[case]
        if not isinstance(planes, dict) or set(planes) != set(G4_PLANES):
            raise G4Error(f"close: L5 case {case!r} must be recorded on both Planes {G4_PLANES}")
        for plane in G4_PLANES:
            entry = planes[plane]
            if not isinstance(entry, dict) or not entry.get("verdict"):
                raise G4Error(f"close: L5 case {case!r} on the {plane} plane has no verdict")
    return l5


def _verify_inventories(inventories: Any) -> dict[str, Any]:
    """Shape-check the close document's inventories; ``evidence`` owns the exact lists."""

    runtime = inventories.get("runtime") if isinstance(inventories, dict) else None
    if not isinstance(runtime, dict) or set(runtime) != set(G4_PROFILES):
        raise G4Error(f"close: inventories.runtime must cover {G4_PROFILES}")
    for profile in G4_PROFILES:
        entry = runtime[profile]
        tools = entry.get("tools") if isinstance(entry, dict) else None
        if tools != list(EXPECTED_PROFILE_TOOLS[profile]):
            raise G4Error(
                f"close: the {profile} Runtime inventory does not match tooling/contracts/lint.py; "
                f"expected {list(EXPECTED_PROFILE_TOOLS[profile])}, got {tools}"
            )
    rest = inventories.get("rest") if isinstance(inventories, dict) else None
    if not isinstance(rest, dict):
        raise G4Error("close: inventories.rest is missing")
    for key in ("classEnabled", "classDisabled", "classEnabledControl"):
        values = rest.get(key)
        if not isinstance(values, list) or not values or values != sorted(values):
            raise G4Error(f"close: inventories.rest.{key} must be a non-empty sorted list")
    return cast("dict[str, Any]", inventories)


def _mutations_enabled(tools: list[str]) -> list[str]:
    return sorted(name for name in tools if name in CURRENT_RUNTIME_MUTATION_TOOLS)


def _gate_result(l5: dict[str, Any]) -> str:
    """The honest L5 verdict for the whole gate.

    ``VERIFIED`` needs every D26 case live on both Planes. A case with no
    evidence at all on *either* Plane is ``UNVERIFIED_LIMITATION``; any other
    recorded limitation (D30's Runtime fixture-only ruling, a unit-only proof)
    makes it ``VERIFIED_WITH_LIMITATION``.
    """

    entries = {(case, plane): l5[case][plane] for case in D26_L5_CASES for plane in G4_PLANES}
    if all(entry.get("verdict") == "LIVE" for entry in entries.values()):
        return "VERIFIED"
    for case in D26_L5_CASES:
        if all(l5[case][plane].get("verdict") == "NONE" for plane in G4_PLANES):
            return "UNVERIFIED_LIMITATION"
    return "VERIFIED_WITH_LIMITATION"


def build_g4_row(close: dict[str, Any], artifacts: Path) -> dict[str, Any]:
    """Compose one schema-valid G4 row from the close document and its artifacts."""

    where = "close"
    if close.get("schemaVersion") != 1:
        raise G4Error(f"{where}: schemaVersion must be 1")
    runs = close.get("runs")
    if not isinstance(runs, list) or not runs:
        raise G4Error(f"{where}: runs must be a non-empty list")
    verified_runs = [
        _verify_run(artifacts, close, dict(run) if isinstance(run, dict) else run) for run in runs
    ]
    tuple_fields = {
        key: _text(close, key, where)
        for key in (
            "gatewayVersion", "gatewayBuild", "gatewayImage", "gatewayImageDigest",
            "mcpModuleVersion", "mcpModuleArtifactVersion", "mcpModuleBuild", "mcpModuleSha256",
            "bundleVersion", "bundleSha256", "sourceRevision",
        )
    }
    bundle_sha = tuple_fields["bundleSha256"]
    if not _SHA.fullmatch(bundle_sha):
        raise G4Error(f"{where}: bundleSha256 must be a SHA-256")
    if not any(run.get("bundleSha256") == bundle_sha for run in verified_runs):
        raise G4Error(
            f"{where}: no cited run deployed the declared bundle {bundle_sha}; the row would claim a "
            "release no live run verified"
        )

    l5 = _verify_l5(close.get("l5"))
    inventories = _verify_inventories(close.get("inventories"))
    limitations = close.get("limitations")
    if not isinstance(limitations, list):
        raise G4Error(f"{where}: limitations must be a list (it may be empty)")

    gate_result = _gate_result(l5)
    declared_result = close.get("gateResult")
    if declared_result != gate_result:
        raise G4Error(
            f"{where}: gateResult is {declared_result!r}, the L5 matrix supports {gate_result!r}"
        )
    unsatisfied = [
        f"{case} on the {plane} plane"
        for case in D26_L5_CASES
        for plane in G4_PLANES
        if l5[case][plane].get("verdict") == "NONE"
    ]

    row: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "gate": "G4",
        **tuple_fields,
        "deployedBundleSha256": bundle_sha,
        "gateResult": gate_result,
        "unsatisfiedAcceptance": sorted(unsatisfied),
        "runs": verified_runs,
        "l5": l5,
        "limitations": limitations,
        "inventories": inventories,
        "mutationsDisabledByDefault": close.get("mutationsDisabledByDefault"),
        "unsafeAutomaticRetryAbsent": close.get("unsafeAutomaticRetryAbsent"),
        "ownerAcceptedDeviations": [
            "phase4-live-environment-protection",
            *[d for d in close.get("additionalDeviations", []) if isinstance(d, str)],
        ],
        "runtimeNullEncoding": "ignition-null-v1",
        "nativeResponseBinding": close.get("nativeResponseBinding"),
        "d27ExceptionApplied": close.get("d27ExceptionApplied"),
        "outputSchemaPublished": False,
        "status": close.get("status"),
        # D21: Phase 4 never certifies deployment compatibility; the row records what
        # was verified, never a SUPPORTED promotion.
        "compatibilityStatus": "UNTESTED",
        "runtimeMutations": {
            "enabledTools": _mutations_enabled(inventories["runtime"]["full"]["tools"]),
        },
    }
    for key in ("mutationsDisabledByDefault", "unsafeAutomaticRetryAbsent", "d27ExceptionApplied"):
        if not isinstance(row[key], bool):
            raise G4Error(f"{where}: {key} must be an explicit boolean")
    if row["status"] not in ("VERIFIED", "VERIFIED_WITH_LIMITATION", "UNVERIFIED_LIMITATION", "FAILED_NATIVE_BINDING"):
        raise G4Error(f"{where}: status must be a documented binding status, never a promotion")
    return row


def generate_g4(close_path: Path, out_dir: Path, artifacts: Path) -> Path:
    """Write and self-validate one G4 row; returns the row directory."""

    close = _load(close_path, "close document")
    row = build_g4_row(close, artifacts)
    directory = out_dir / f"g4-{row['gatewayVersion']}-mcp-{row['mcpModuleBuild']}"
    if directory.exists():
        raise G4Error(f"{directory}: refusing to overwrite existing evidence (frozen once written)")
    directory.mkdir(parents=True)
    (directory / "evidence.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        rows = load_evidence(out_dir)
    except EvidenceError as error:
        raise G4Error(f"generated row rejected by the validator: {error}") from error
    if not any(item.gate == "G4" and item.gateway_version == row["gatewayVersion"] for item in rows):
        raise G4Error("generated G4 row not found on re-validation")
    return directory
