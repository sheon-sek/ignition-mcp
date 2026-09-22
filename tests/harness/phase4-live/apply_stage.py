#!/usr/bin/env python3
"""Ticket #21 live stage: ``setup-native plan`` -> ``apply`` -> ``verify``.

This is the milestone-4d operator tooling stage. It drives the *shipped* CLI (the
product under test, imported the way the G3 driver imports it) against the
disposable Gateway the workflow provisioned, using run-unique names so the run
exercises the write paths instead of reconciling something already there:

* the bundle Project and its archive are the deterministic release the workflow
  built (``--bundle-manifest`` / ``--bundle-zip``), imported under a run-unique
  Project name, so the Project write is a ``CREATE`` and the Server Config maps
  that Project's Tools;
* the MCP Server Config is written under a run-unique name with the profile's
  explicit Tool list and the harness's own permissions tree;
* the Runtime Target Policy is the ticket #6 canonical document
  (``policy_document.POLICY``) written into the reserved provider.

The stage then runs ``plan`` and ``apply`` again: the second plan must be all
``NO CHANGE`` and the second apply must write nothing, which is the D20 idempotency
check.

A verify that fails right after the write is re-run bounded and read-only, and — if
``--compose-file`` is given and it still fails — the stage reloads the disposable
Gateway once and re-runs verify. That reload is the harness's own discipline, not a
product behaviour: the milestone 4a/4b rows install their Project and Server Config
by file copy and only judge an endpoint after the Gateway has started with them in
place, and the ticket #21 row observed the same Module answering a Server Config it
had created live with no primitives at all (``capabilities=[-]``,
``tools/list -> -32600``) while the imported Project had been in place for seconds.
The stage never re-runs a *write*.

Ticket #22 adds the two opt-in writes to the same run: the stage passes
``--provision-security-levels`` and ``--create-runtime-token``, so the run also
creates the dedicated Runtime Security Level for its profile and a Runtime API token
granted exactly that level, and the credential file lands in the same private ``0700``
directory as the operator token. The stage reads both back over Native REST with its
own admin token, judges that the token carries exactly the secret the CLI wrote (its
stored hash), and asserts that the secret appears in no command output and nowhere in
the evidence — which is redacted rather than uploaded if it ever does. The created
credential's *authorization* is recorded as a probe but not judged: the Server Config
this stage creates names the CI security level (D09: the Server Config's permissions
decide who enters the profile), so a token granted only this run's dedicated level is
expected to be refused.

Evidence is written to ``--evidence-dir/setup-native-apply.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness/phase4-live"))

import policy_document  # noqa: E402

from ignition_rest_mcp.cli.setup_native import apply as apply_command  # noqa: E402
from ignition_rest_mcp.cli.setup_native import install_module as install_command  # noqa: E402
from ignition_rest_mcp.cli.setup_native import plan as plan_command  # noqa: E402
from ignition_rest_mcp.cli.setup_native import verify as verify_command  # noqa: E402
from ignition_rest_mcp.cli.setup_native.inputs import load_inputs, load_module_inputs  # noqa: E402

#: The permissions tree the harness's own Server Configs carry, so the config this
#: stage creates is the same deployment shape the other stages run against.
HARNESS_CONFIG = ROOT / "tests/harness/phase4-live/gateway-config/com.inductiveautomation.mcp/server-config/phase4-operator/config.json"
#: Bounded, read-only readiness re-runs of `verify` after a write.
VERIFY_ATTEMPTS = 6

VERIFY_WAIT_SECONDS = 10.0
SENTINEL = "No changes have been applied."

#: The ticket #56 stage bounds. The module install is judged against the identity the
#: pinned ``.modl`` declares (read once, hashed by the CLI itself), and the lowered
#: bundle the upgrade case imports is validated exactly like a release build.
INSTALL_VERIFY_ATTEMPTS = 12
INSTALL_VERIFY_WAIT_SECONDS = 10.0


def permissions_of_harness_config() -> dict[str, Any]:
    document = json.loads(HARNESS_CONFIG.read_text(encoding="utf-8"))
    permissions = document.get("permissions")
    if not isinstance(permissions, dict):  # pragma: no cover - the fixture must carry one
        raise SystemExit("the harness Server Config fixture carries no permissions tree")
    return permissions


def parse_report(text: str, command: str) -> dict[str, Any]:
    lines = [line for line in text.splitlines() if line.strip() and line.strip() != SENTINEL]
    payload = json.loads("\n".join(lines))
    if not isinstance(payload, dict):  # pragma: no cover - the commands always report an object
        raise SystemExit(f"{command} produced no report object")
    return payload


async def run_command(command: str, argv: list[str]) -> tuple[int, str, dict[str, Any]]:
    inputs = load_inputs(argv, command)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        if command == "plan":
            code = await plan_command.run(inputs)
        elif command == "apply":
            code = await apply_command.run(inputs)
        else:
            code = await verify_command.run(inputs)
    text = buffer.getvalue()
    return code, text, parse_report(text, command)


async def run_module_install(args: argparse.Namespace) -> tuple[int, str, dict[str, Any]]:
    """One ``setup-native install-module`` run against the module-less Gateway.

    The CLI is the shipped product: the same ``load_module_inputs`` the entry point
    uses, the same refusal order, and the restart wait it owns. The stage records the
    JSON report the run emitted and judges the identity the Gateway serves afterwards.
    """

    argv = [
        "--file", str(args.module_file),
        "--sha256", args.module_sha256,
        "--gateway-url", args.base_url,
        "--gateway-token-file", str(args.token_file),
        "--accept-certificate",
        "--accept-eula",
        "--acknowledge-upgrade",
        "--restart",
        "--json",
    ]
    inputs = load_module_inputs(argv)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = await install_command.run(inputs)
    text = buffer.getvalue()
    return code, text, parse_report(text, "install-module")


def build_argv(args: argparse.Namespace) -> list[str]:
    base = [
        "--bundle-manifest", str(args.bundle_manifest),
        "--bundle-zip", str(args.bundle_zip),
        "--gateway-url", args.base_url,
        "--gateway-token-file", str(args.token_file),
        "--mcp-token-file", str(args.token_file),
        "--profile", args.profile,
        "--bundle-project", args.project,
        "--server-config-name", args.server_config,
        "--policy-file", str(args.policy_file),
        "--server-config-permissions-file", str(args.permissions_file),
        # Ticket #22: D20's two opt-in writes. The dedicated Security Level and the
        # Runtime API token are created for this run's own profile, and the secret lands
        # in the stage's private 0700 directory. `--runtime-token-insecure-channel` is
        # the disposable compose Gateway's own fact (it serves plain HTTP); a production
        # deployment must not pass it.
        "--provision-security-levels",
        "--create-runtime-token",
        "--runtime-token-file", str(args.runtime_token_file),
        "--runtime-token-insecure-channel",
        # The endpoint apply writes and verifies through; named explicitly so the
        # read-only verify retries below need no derivation.
        "--mcp-url", f"{args.base_url.rstrip('/')}/data/mcp/{args.server_config}",
        "--json",
    ]
    return base


async def run_stage(args: argparse.Namespace) -> int:
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    # The token file lives in a 0700 directory outside the evidence tree, exactly as
    # the G3 driver keeps it: a credential must never reach an uploaded artifact.
    private = Path(tempfile.mkdtemp(prefix="setup-native-private-"))
    os.chmod(private, 0o700)
    token_file = private / "gateway.token"
    token_file.write_text(args.api_token + "\n", encoding="utf-8")
    os.chmod(token_file, 0o600)
    args.token_file = token_file
    # Ticket #22: the credential the opt-in flags create. It lives in the same private
    # 0700 directory as the operator token: the Gateway returns the key once, and the
    # stage must never let it reach the uploaded evidence.
    args.runtime_token_file = private / "runtime.token"
    # Ticket #56: when the run names a module file, the Gateway it talks to has NO
    # MCP Module, and this stage puts it there through the shipped CLI before it
    # applies anything.
    if args.install_only:
        evidence: dict[str, Any] = {
            "schemaVersion": 1,
            "ticket": 56,
            "title": "setup-native install-module (ticket #56)",
            "baseUrl": args.base_url,
            "sourceRevision": args.source_revision,
            "runId": os.environ.get("GITHUB_RUN_ID", ""),
            "steps": {},
        }
        install_code, install_text, install_report = await run_module_install(args)
        evidence["steps"]["install-module"] = {
            "exitCode": install_code, "report": install_report, "output": install_text,
        }
        again_code, again_text, again_report = await run_module_install(args)
        evidence["steps"]["install-module-again"] = {
            "exitCode": again_code, "report": again_report, "output": again_text,
        }
        ok = (
            install_code == 0
            and install_report.get("outcome") == "INSTALL"
            and again_code == 0
            and again_report.get("outcome") == "NO CHANGE"
        )
        evidence["ok"] = ok
        if not ok:
            evidence["failure"] = (
                f"install-module {install_report.get('outcome')} / again {again_report.get('outcome')}"
            )
        _write(evidence, args, "setup-native-install-module.json")
        print(json.dumps({"stage": "setup-native-install-module", "ok": ok}, sort_keys=True))
        return 0 if ok else 1
    policy_file = args.evidence_dir / "runtime-target-policy.json"
    policy_file.write_text(json.dumps(policy_document.POLICY, indent=2, sort_keys=True), encoding="utf-8")
    args.policy_file = policy_file
    permissions_file = args.evidence_dir / "server-config-permissions.json"
    permissions_file.write_text(json.dumps(permissions_of_harness_config(), indent=2, sort_keys=True),
                               encoding="utf-8")
    args.permissions_file = permissions_file

    argv = build_argv(args)
    released_argv = list(argv)
    lowered_version = ""
    if args.bundle_source is not None:
        # Ticket #56, round 1: the starting deployment is the released bundle one
        # version BELOW the release the run built, so the upgrade that follows deploys
        # the real release over an older bundle (D21's upgrade path, under
        # acknowledgement). The build is a real release build of the same project
        # source and the same revision stamp.
        args.lowered_release_keepalive = tempfile.TemporaryDirectory(prefix="setup-native-lowered-")
        lowered_dir, lowered_version = _build_lowered_release(
            args, Path(args.lowered_release_keepalive.name),
        )
        argv = _argv_with_bundle(argv, lowered_dir)
    released = json.loads(args.bundle_manifest.read_text(encoding="utf-8"))
    evidence: dict[str, Any] = {
        "schemaVersion": 1,
        "ticket": 22,
        "title": (
            "setup-native apply: bundle project, Server Config, Runtime Target Policy, "
            "opt-in Security Level and Runtime API token (ticket #21 + #22)"
        ),
        "baseUrl": args.base_url,
        "project": args.project,
        "serverConfig": args.server_config,
        "profile": args.profile,
        "policyProvider": policy_document.POLICY_PROVIDER,
        "policySha256": policy_document.policy_sha256(),
        "policyBytes": policy_document.policy_byte_length(),
        "policyMaxBytes": policy_document.POLICY_MAX_BYTES,
        "mcpUrl": f"{args.base_url.rstrip('/')}/data/mcp/{args.server_config}",
        "sourceRevision": args.source_revision,
        "runId": os.environ.get("GITHUB_RUN_ID", ""),
        "bundle": {
            "filename": str(released["artifact"]["filename"]),
            "version": str(released["bundleVersion"]),
            "sha256": str(released["artifact"]["sha256"]),
            "nativeResponseBindingStatus": str(released["nativeResponseBindingStatus"]),
        },
        "startingBundleVersion": lowered_version,
        "steps": {},
    }

    if args.module_file is not None:
        # The workflow already ran the install with --install-only; this run embeds
        # that phase's recorded INSTALL step (it is the only live record of the
        # install) and adds its own proof: a NO CHANGE read-back that uploads nothing.
        install_document = args.evidence_dir / "setup-native-install-module.json"
        if not install_document.is_file():
            _fail(evidence, args, f"the install-only phase left no {install_document.name} to embed")
            return 1
        installed = json.loads(install_document.read_text(encoding="utf-8"))
        if installed.get("ok") is not True:
            _fail(evidence, args, "the install-only phase did not record a green install")
            return 1
        evidence["steps"]["install-module"] = installed["steps"]["install-module"]
        # The NO CHANGE read-back: the CLI sees the installed build and uploads nothing.
        again_code, again_text, again_report = await run_module_install(args)
        evidence["steps"]["install-module-again"] = {
            "exitCode": again_code, "report": again_report, "output": again_text,
        }
        if again_code != 0 or again_report.get("outcome") != "NO CHANGE":
            _fail(evidence, args, f"the module the install-only phase installed is not served: {again_report.get('outcome')}")
            return 1

    plan_code, plan_text, plan_report = await run_command("plan", argv)
    evidence["steps"]["plan"] = {"exitCode": plan_code, "report": plan_report, "output": plan_text}
    if plan_code != 0:
        _fail(evidence, args, f"plan exited {plan_code} before any write")
        return 1
    # Ticket #22: the opt-in writes must be planned before anything is written.
    planned = {
        action.get("kind"): action.get("action") for action in (plan_report.get("actions") or [])
    }
    if (planned.get("security-level"), planned.get("runtime-token")) != ("CREATE", "CREATE"):
        _fail(evidence, args, f"the first plan did not propose both opt-in writes: {planned}")
        return 1

    apply_code, apply_text, apply_report = await run_command("apply", argv)
    evidence["steps"]["apply"] = {"exitCode": apply_code, "report": apply_report, "output": apply_text}
    writes = apply_report.get("writes") or []
    failed_writes = [write for write in writes if not write.get("ok")]
    if failed_writes:
        _fail(evidence, args, f"apply failed to write: {json.dumps(failed_writes)[:600]}")
        return 1

    # Ticket #22: what the opt-in flags actually wrote, read back with the deployment's
    # own admin token, and the credential file's mode. The secret itself is never
    # recorded, here or in any step output (asserted below).
    provisioning = _provisioning_report(args)
    evidence["provisioning"] = provisioning
    secret = _secret_line(args.runtime_token_file)
    evidence["provisioning"]["secretFileMode"] = _file_mode(args.runtime_token_file)
    if not provisioning["securityLevelPresent"]:
        _fail(evidence, args, f"the dedicated Security Level was not readable after apply: {provisioning}")
        return 1
    if not provisioning["tokenHashMatchesSecret"]:
        _fail(evidence, args, f"the created API token does not carry the secret apply wrote: {provisioning}")
        return 1
    if provisioning["secretFileMode"] != "0o600":
        _fail(evidence, args, f"the credential file is not 0600: {provisioning['secretFileMode']}")
        return 1
    # Not judged, recorded for the operator: whether a token holding only this run's
    # dedicated Security Level may enter the MCP endpoint. The Server Config this stage
    # creates names the CI level, so a refusal is the expected answer (D09: the Server
    # Config's permissions decide who enters the profile).
    evidence["provisioning"]["createdTokenMcpProbe"] = _mcp_probe(args, secret)
    evidence["provisioning"]["createdTokenMcpProbeJudged"] = False

    verify_attempts = 1
    if apply_code != 0 and not _verify_ok(apply_report.get("verify")):
        verify_attempts = await _reverify(evidence, args, argv, verify_attempts)
        if not _last_verify_ok(evidence):
            verify_attempts = await _reverify_after_reload(evidence, args, argv, verify_attempts)
    evidence["steps"]["apply"]["verifyAttempts"] = verify_attempts
    # The judgement is the *most recent* verify this stage ran (apply's own, a bounded
    # retry, or the one after the reload) — never apply's embedded report alone, which
    # is red by definition whenever the retries above had to run.
    if not _last_verify_ok(evidence):
        _fail(evidence, args, f"apply exited {apply_code} and verify never went green")
        return 1

    second_plan_code, second_plan_text, second_plan_report = await run_command("plan", argv)
    evidence["steps"]["secondPlan"] = {
        "exitCode": second_plan_code, "report": second_plan_report, "output": second_plan_text,
    }
    secret_digest = hashlib.sha256(Path(args.runtime_token_file).read_bytes()).hexdigest()
    second_apply_code, second_apply_text, second_apply_report = await run_command("apply", argv)
    evidence["steps"]["secondApply"] = {
        "exitCode": second_apply_code, "report": second_apply_report, "output": second_apply_text,
    }

    writes = apply_report.get("writes") or []
    written = [write for write in writes if write.get("action") in ("CREATE", "UPDATE")]
    if {write.get("kind") for write in written} != {
        "security-level", "runtime-token", "bundle-project", "server-config", "runtime-policy",
    }:
        _fail(evidence, args, f"apply did not write all five intentions: {writes}")
        return 1
    if second_plan_code != 0 or any(
        action.get("action") != "NO CHANGE"
        for action in (second_plan_report.get("actions") or [])
        if action.get("kind") in (
            "security-level", "runtime-token", "bundle-project", "server-config", "runtime-policy",
        )
    ):
        _fail(evidence, args, "the second plan is not a NO CHANGE run")
        return 1
    second_actions = [write.get("action") for write in (second_apply_report.get("writes") or [])]
    if second_apply_code != 0 or any(action in ("CREATE", "UPDATE") for action in second_actions):
        _fail(evidence, args, f"the second apply wrote something: {second_actions}")
        return 1
    # D20's idempotency rule for the credential: the secret file is untouched.
    if hashlib.sha256(Path(args.runtime_token_file).read_bytes()).hexdigest() != secret_digest:
        _fail(evidence, args, "the second run rewrote the credential file")
        return 1
    # Ticket #22's reporting rule, judged over everything this run produced (the step
    # outputs, both reports and this stage's own fields).
    if secret and secret in json.dumps(evidence):
        _fail(evidence, args, "the credential reached a command output or the evidence")
        return 1

    # ---------------------------------------------------------------- ticket #56 upgrade
    if args.bundle_source is not None:
        # The starting deployment is the lowered release; the upgrade deploys the real
        # released bundle over it, under --acknowledge-upgrade (D21's upgrade path).
        plan_code, plan_text, plan_report = await run_command("plan", released_argv)
        plan_line = _bundle_project_action(plan_report)
        if plan_code != 0 or plan_line is None or plan_line.get("action") != "UPDATE":
            _fail(evidence, args, f"the released bundle was not planned as an upgrade: {plan_line}")
            return 1
        real_version = str(released["bundleVersion"])
        if lowered_version not in str(plan_line.get("reason", "")) or real_version not in str(plan_line.get("reason", "")):
            _fail(evidence, args, f"the upgrade plan does not observe {lowered_version} -> {real_version}: {plan_line}")
            return 1
        evidence["steps"]["upgradePlan"] = {
            "exitCode": plan_code, "report": plan_report, "output": plan_text,
        }
        upgrade_argv = [*released_argv, "--acknowledge-upgrade"]
        apply_code, apply_text, apply_report = await run_command("apply", upgrade_argv)
        attempts = 1
        retries: list[dict[str, Any]] = []
        green = _verify_ok(apply_report.get("verify"))
        while not green and attempts <= VERIFY_ATTEMPTS:
            await asyncio.sleep(VERIFY_WAIT_SECONDS)
            verify_code, verify_text, verify_report = await run_command("verify", upgrade_argv)
            retries.append({
                "attempt": attempts, "exitCode": verify_code,
                "report": verify_report, "output": verify_text,
            })
            green = verify_code == 0 and _verify_ok(verify_report)
            attempts += 1
        written_kinds = sorted({
            write.get("kind") for write in (apply_report.get("writes") or [])
            if write.get("action") in ("CREATE", "UPDATE")
        })
        evidence["steps"]["upgradeApply"] = {
            "bundleVersionBefore": lowered_version,
            "bundleVersionAfter": real_version,
            "planLine": plan_line,
            "exitCode": apply_code,
            "verifyAttempts": attempts,
            "verifyGreen": green,
            "verifyRetries": retries,
            "writtenKinds": written_kinds,
            "report": apply_report,
            "output": apply_text,
        }
        if not green:
            _fail(evidence, args, "verify never went green after the bundle upgrade")
            return 1
        if written_kinds != ["bundle-project"]:
            _fail(evidence, args, f"the upgrade wrote more than the bundle project: {written_kinds}")
            return 1

    evidence["ok"] = True
    _write(evidence, args)
    print(json.dumps({
        "stage": "setup-native-apply",
        "ok": True,
        "wrote": [f"{write['kind']}:{write['action']}" for write in written],
        "verifyAttempts": verify_attempts,
        "provisioning": provisioning,
        "secondApply": [write.get("action") for write in (second_apply_report.get("writes") or [])],
        "upgrade": None if "upgradeApply" not in evidence["steps"] else {
            "from": evidence["steps"]["upgradeApply"]["bundleVersionBefore"],
            "to": evidence["steps"]["upgradeApply"]["bundleVersionAfter"],
            "verifyGreen": evidence["steps"]["upgradeApply"]["verifyGreen"],
        },
    }, sort_keys=True))
    return 0


def _verify_ok(report: Any) -> bool:
    return isinstance(report, dict) and report.get("verified") is True


# ------------------------------------------------------------------ ticket #56 upgrade


def _bundle_project_action(report: dict[str, Any]) -> dict[str, Any] | None:
    """The plan's bundle-project line, if the plan carried one."""

    for action in report.get("actions") or []:
        if isinstance(action, dict) and action.get("kind") == "bundle-project":
            return action
    return None


def _build_lowered_release(args: argparse.Namespace, work: Path) -> tuple[Path, str]:
    """Build the starting deployment: the released bundle one version below.

    The lowered build is a real, validated release of the same project source and the
    same revision stamp (D21: only BUNDLE_VERSION moves), so the apply that lands it
    exercises the same import the real upgrade then replaces.
    """

    released = json.loads(args.bundle_manifest.read_text(encoding="utf-8"))
    real_version = str(released["bundleVersion"])
    major, minor, patch = (int(part) for part in real_version.split("."))
    if patch:
        lowered = f"{major}.{minor}.{patch - 1}"
    elif minor:
        lowered = f"{major}.{minor - 1}.0"
    else:
        raise SystemExit(f"cannot lower {real_version}: there is no version below it to deploy first")
    source = work / "source"
    source.mkdir()
    shutil.copytree(args.bundle_source / "project", source / "project")
    for sibling in ("BUNDLE_VERSION", "RESOURCE_SCHEMA_VERSION"):
        shutil.copy(args.bundle_source / sibling, source / sibling)
    (source / "BUNDLE_VERSION").write_text(lowered + "\n", encoding="utf-8")
    _repoint_bundle_version(source / "project", lowered)
    release_dir = _rebuild_release(source, work / "release", lowered,
                                   source_revision=args.source_revision)
    return release_dir, lowered


def _repoint_bundle_version(project: Path, bundle_version: str) -> None:
    """Keep the D21 identity trio consistent in a re-versioned bundle source copy.

    The validator ties BUNDLE_VERSION, the handler's ``bundleVersion`` literal and the
    project.json marker line together; a lowered build moves all three, and only the
    version literals (never a Tool, Resource or Prompt) change.
    """

    handler = project / "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py"
    text = handler.read_text(encoding="utf-8")
    handler.write_text(
        re.sub(r'bundleVersion = "[^"]+"', f'bundleVersion = "{bundle_version}"', text, count=1),
        encoding="utf-8",
    )
    manifest_path = project / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lines = str(manifest["description"]).splitlines()
    lines[-1] = f"ignition-mcp-managed: product=ignition-runtime-bundle; bundle={bundle_version}"
    manifest["description"] = "\n".join(lines)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _rebuild_release(source: Path, out: Path, bundle_version: str, *, source_revision: str) -> Path:
    """Build one release from a validated bundle source copy.

    ``source_revision`` must be the same stamp the run's release carries: the deployed
    artifact and the endpoint's ``bundle_info`` answer are judged against it.
    """

    out.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable, "-m", "tooling.native.cli", "release",
            "--project-dir", str(source / "project"),
            "--out-dir", str(out),
            "--source-revision", source_revision,
            "--evidence-dir", "tests/compatibility/evidence",
        ],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"the {bundle_version} release build failed: {completed.stdout} {completed.stderr}"
        )
    return out


def _argv_with_bundle(argv: list[str], release_dir: Path) -> list[str]:
    """The same apply argv, aimed at one release directory's artifacts."""

    replaced: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--bundle-manifest":
            replaced.extend([
                item,
                str(next(release_dir.glob("ignition-runtime-bundle-*.manifest.json"))),
            ])
            index += 2  # the flag and the value it had
            continue
        if item == "--bundle-zip":
            replaced.extend([
                item,
                str(next(release_dir.glob("ignition-runtime-bundle-*.zip"))),
            ])
            index += 2
            continue
        replaced.append(item)
        index += 1
    return replaced


# ------------------------------------------------------------------ ticket #22 probes


def _level_name(profile: str) -> str:
    """The dedicated Runtime Security Level's name, exactly as the CLI derives it."""

    return f"IgnitionMcpRuntime{profile.capitalize()}"


def _secret_line(path: Path) -> str:
    """The one-line credential the CLI wrote, or ``""`` when there is none (never logged)."""

    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return ""
    return lines[0] if len(lines) == 1 else ""


def _file_mode(path: Path) -> str:
    try:
        return oct(stat.S_IMODE(path.stat().st_mode))
    except OSError as error:
        return f"unreadable ({type(error).__name__})"


def _token_hash_of(secret: str) -> str:
    """The hash the Gateway stores for a ``name:key`` credential, from the key itself."""

    _, _, key = secret.partition(":")
    padded = key + "=" * (-len(key) % 4)
    try:
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError):
        return ""
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")


def _rest_json(base_url: str, token: str, path: str) -> tuple[int, Any]:
    """One authenticated Native REST read; the status is returned even when it refuses."""

    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers={"X-Ignition-API-Token": token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - the compose origin
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return int(error.code), None
    except (urllib.error.URLError, OSError, ValueError) as error:
        return 0, {"transportError": type(error).__name__}


def _provisioning_report(args: argparse.Namespace) -> dict[str, Any]:
    """Read back the level and the token the opt-in flags created, never the secret."""

    status, levels = _rest_json(
        args.base_url, args.api_token, "/data/api/v1/resources/singleton/ignition/security-levels",
    )
    token_status, token = _rest_json(
        args.base_url, args.api_token, f"/data/api/v1/resources/find/ignition/api-token/{args.server_config}",
    )
    tree = ((levels or {}).get("config") or {}).get("securityLevels") if isinstance(levels, dict) else None
    name = _level_name(args.profile)
    config = (token or {}).get("config") if isinstance(token, dict) else None
    profile = (config or {}).get("profile") if isinstance(config, dict) else None
    stored = ((config or {}).get("settings") or {}).get("tokenHash") if isinstance(config, dict) else None
    secret = _secret_line(args.runtime_token_file)
    return {
        "securityLevelsHttpStatus": status,
        "securityLevelPath": f"Authenticated/{name}",
        "securityLevelPresent": bool(tree) and any(
            isinstance(node, dict) and node.get("name") == "Authenticated"
            and any(
                isinstance(child, dict) and child.get("name") == name
                for child in (node.get("children") or [])
            )
            for node in tree
        ),
        "tokenHttpStatus": token_status,
        "tokenName": args.server_config,
        "tokenEnabled": (token or {}).get("enabled") if isinstance(token, dict) else None,
        "tokenGrantedLevels": [
            child.get("name")
            for node in ((profile or {}).get("securityLevels") or [])
            if isinstance(node, dict)
            for child in (node.get("children") or [])
            if isinstance(child, dict)
        ],
        "tokenSecureChannelRequired": (profile or {}).get("secureChannelRequired"),
        "tokenHashMatchesSecret": bool(stored) and stored == _token_hash_of(secret),
    }


def _mcp_probe(args: argparse.Namespace, token: str) -> dict[str, Any]:
    """One MCP ``initialize`` with the created credential; recorded, never judged.

    The Server Config this stage writes names the CI security level, so a token granted
    only this run's dedicated level is *expected* to be refused (D09: the Server Config
    permissions decide who enters the profile). The probe records the answer the pinned
    Module gives, which is live knowledge the local fixtures cannot produce.
    """

    if not token:
        return {"status": 0, "note": "no credential was written"}
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "phase4-apply-stage", "version": "1"},
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/data/mcp/{args.server_config}",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-Ignition-API-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - the compose origin
            body = response.read(400).decode("utf-8", "replace")
            return {"status": int(response.status), "body": " ".join(body.split())}
    except urllib.error.HTTPError as error:
        body = error.read(400).decode("utf-8", "replace")
        return {"status": int(error.code), "body": " ".join(body.split())}
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"status": 0, "error": type(error).__name__}


def _fail(evidence: dict[str, Any], args: argparse.Namespace, reason: str) -> None:
    evidence["ok"] = False
    evidence["failure"] = reason
    _write(evidence, args)
    print(f"setup-native apply stage failed: {reason}", file=sys.stderr)


def _write(evidence: dict[str, Any], args: argparse.Namespace, name: str = "setup-native-apply.json") -> None:
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = args.evidence_dir / name
    blob = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    secret = _secret_line(args.runtime_token_file)
    if secret and secret in blob:
        # A credential must never reach an uploaded artifact. Redact it, say so loudly,
        # and leave the judgement to the stage's own leak check.
        evidence["secretLeakRedacted"] = True
        blob = json.dumps(evidence, indent=2, sort_keys=True).replace(secret, "[redacted]") + "\n"
        print("setup-native apply stage: a credential reached the evidence; redacted it", file=sys.stderr)
    path.write_text(blob, encoding="utf-8")
    print(f"evidence: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-token", required=True)
    parser.add_argument("--bundle-manifest", type=Path, default=None,
                        help="required for the apply phase; --install-only stops before it")
    parser.add_argument("--bundle-zip", type=Path, default=None,
                        help="required for the apply phase; --install-only stops before it")
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--source-revision", default="")
    parser.add_argument("--project", default=None,
                        help="required for the apply phase; --install-only stops before it")
    parser.add_argument("--server-config", default=None,
                        help="required for the apply phase; --install-only stops before it")
    parser.add_argument("--profile", default="readonly")
    parser.add_argument(
        "--expected-origin", default="",
        help="the origin the run may talk to (host:port); the live workflow passes the compose one",
    )
    parser.add_argument(
        "--compose-file", type=Path, default=None,
        help="the compose file of the disposable Gateway; when given, a verify that stays red "
             "reloads the Gateway once before the stage judges it",
    )
    parser.add_argument(
        "--module-file", type=Path, default=None,
        help="the pinned .modl this stage installs through the CLI before it applies (ticket #56); "
             "when absent the stage assumes the workflow deployed the module and skips the install",
    )
    parser.add_argument(
        "--module-sha256", default="",
        help="the SHA-256 of --module-file, named exactly as an operator names it",
    )
    parser.add_argument(
        "--install-only", action="store_true",
        help="run only the install-module step of ticket #56 and stop; the workflow calls the "
             "stage once with this before the apply run, so each phase has its own log",
    )
    parser.add_argument(
        "--bundle-source", type=Path, default=None,
        help="a directory holding a validated BUNDLE_VERSION + project/ copy of the release the "
             "run lowers and re-imports for the Bundle upgrade case (ticket #56)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.install_only and args.module_file is None:
        print("--install-only runs the ticket #56 module install and needs --module-file", file=sys.stderr)
        return 2
    if not args.install_only and not (
        args.bundle_manifest and args.bundle_zip and args.project and args.server_config
    ):
        print(
            "the apply phase needs --bundle-manifest, --bundle-zip, --project and --server-config "
            "(or run --install-only for the ticket #56 module install)",
            file=sys.stderr,
        )
        return 2
    if args.expected_origin:
        origin = args.base_url.split("//", 1)[-1].split("/", 1)[0]
        if origin != args.expected_origin:
            print(
                f"refusing to talk to {origin}: this stage only runs against the disposable "
                f"Gateway at {args.expected_origin}",
                file=sys.stderr,
            )
            return 3
    return asyncio.run(run_stage(args))




def _last_verify_ok(evidence: dict[str, Any]) -> bool:
    """Whether the most recent verify attempt in this run was green."""

    attempts = evidence.get("verifyRetries") or []
    last = attempts[-1] if attempts else None
    if last is not None:
        return _verify_ok(last.get("report"))
    return _verify_ok(((evidence.get("steps") or {}).get("apply") or {}).get("report", {}).get("verify"))


async def _reverify(
    evidence: dict[str, Any], args: argparse.Namespace, argv: list[str], attempts: int
) -> int:
    """Bounded, read-only verify retries: the Module may still be picking the write up."""

    for attempt in range(VERIFY_ATTEMPTS):
        attempts += 1
        await asyncio.sleep(VERIFY_WAIT_SECONDS)
        code, text, report = await run_command("verify", argv)
        evidence.setdefault("verifyRetries", []).append({
            "attempt": attempt + 1, "exitCode": code, "report": report, "output": text,
        })
        if code == 0:
            break
    return attempts


async def _reverify_after_reload(
    evidence: dict[str, Any], args: argparse.Namespace, argv: list[str], attempts: int
) -> int:
    """Reload the disposable Gateway once, then verify again.

    Last resort only: `apply` re-announces a Server Config whose endpoint serves no
    primitives (the Module resolves a Server Config's Tool list when the resource is
    written and registers a Project's provider on the Project's own thread, so a
    server built in that window serves nothing), and a green verify there means this
    never runs. If the CLI could not make the endpoint serve, one Gateway reload is
    the harness's own discipline — the 4a/4b rows always judge an endpoint the Gateway
    started with. Nothing of the plan is written here; the reload and every verify
    attempt are recorded in the evidence.
    """

    if not args.compose_file:
        return attempts
    command = ["docker", "compose", "-f", str(args.compose_file), "restart", "gateway"]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=300)
    evidence["gatewayReload"] = {
        "command": " ".join(command),
        "exitCode": completed.returncode,
        "output": (completed.stdout + completed.stderr)[-2000:],
    }
    if completed.returncode != 0:
        return attempts
    if not _wait_for_rest(args.base_url, args.api_token):
        evidence["gatewayReload"]["restReady"] = False
        return attempts
    evidence["gatewayReload"]["restReady"] = True
    return await _reverify(evidence, args, argv, attempts)


def _wait_for_rest(base_url: str, api_token: str, deadline_seconds: float = 300.0) -> bool:
    """Wait until the authenticated Native REST plane answers again after a reload."""

    deadline = time.monotonic() + deadline_seconds
    request = urllib.request.Request(
        base_url.rstrip("/") + "/data/api/v1/gateway-info",
        headers={"X-Ignition-API-Token": api_token, "Accept": "application/json"},
    )
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - the compose origin
                if int(response.status) == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(3.0)
    return False


if __name__ == "__main__":
    raise SystemExit(main())
