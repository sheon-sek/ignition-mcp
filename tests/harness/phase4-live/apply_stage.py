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

Evidence is written to ``--evidence-dir/setup-native-apply.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
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
from ignition_rest_mcp.cli.setup_native import plan as plan_command  # noqa: E402
from ignition_rest_mcp.cli.setup_native import verify as verify_command  # noqa: E402
from ignition_rest_mcp.cli.setup_native.inputs import load_inputs  # noqa: E402

#: The permissions tree the harness's own Server Configs carry, so the config this
#: stage creates is the same deployment shape the other stages run against.
HARNESS_CONFIG = ROOT / "tests/harness/phase4-live/gateway-config/com.inductiveautomation.mcp/server-config/phase4-operator/config.json"
#: Bounded, read-only readiness re-runs of `verify` after a write.
VERIFY_ATTEMPTS = 6

VERIFY_WAIT_SECONDS = 10.0
SENTINEL = "No changes have been applied."


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
    policy_file = args.evidence_dir / "runtime-target-policy.json"
    policy_file.write_text(json.dumps(policy_document.POLICY, indent=2, sort_keys=True), encoding="utf-8")
    args.policy_file = policy_file
    permissions_file = args.evidence_dir / "server-config-permissions.json"
    permissions_file.write_text(json.dumps(permissions_of_harness_config(), indent=2, sort_keys=True),
                               encoding="utf-8")
    args.permissions_file = permissions_file

    argv = build_argv(args)
    evidence: dict[str, Any] = {
        "schemaVersion": 1,
        "ticket": 21,
        "title": "setup-native apply: bundle project, Server Config and Runtime Target Policy",
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
        "steps": {},
    }

    plan_code, plan_text, plan_report = await run_command("plan", argv)
    evidence["steps"]["plan"] = {"exitCode": plan_code, "report": plan_report, "output": plan_text}
    if plan_code != 0:
        _fail(evidence, args, f"plan exited {plan_code} before any write")
        return 1

    apply_code, apply_text, apply_report = await run_command("apply", argv)
    evidence["steps"]["apply"] = {"exitCode": apply_code, "report": apply_report, "output": apply_text}
    writes = apply_report.get("writes") or []
    failed_writes = [write for write in writes if not write.get("ok")]
    if failed_writes:
        _fail(evidence, args, f"apply failed to write: {json.dumps(failed_writes)[:600]}")
        return 1

    verify_attempts = 1
    if apply_code != 0 and not _verify_ok(apply_report.get("verify")):
        verify_attempts = await _reverify(evidence, args, argv, verify_attempts)
        if not _last_verify_ok(evidence):
            verify_attempts = await _reverify_after_reload(evidence, args, argv, verify_attempts)
    evidence["steps"]["apply"]["verifyAttempts"] = verify_attempts
    if not _verify_ok((evidence["steps"]["apply"].get("lastVerify") or apply_report.get("verify")) or {}):
        _fail(evidence, args, f"apply exited {apply_code} and verify never went green")
        return 1

    second_plan_code, second_plan_text, second_plan_report = await run_command("plan", argv)
    evidence["steps"]["secondPlan"] = {
        "exitCode": second_plan_code, "report": second_plan_report, "output": second_plan_text,
    }
    second_apply_code, second_apply_text, second_apply_report = await run_command("apply", argv)
    evidence["steps"]["secondApply"] = {
        "exitCode": second_apply_code, "report": second_apply_report, "output": second_apply_text,
    }

    writes = apply_report.get("writes") or []
    written = [write for write in writes if write.get("action") in ("CREATE", "UPDATE")]
    if {write.get("kind") for write in written} != {"bundle-project", "server-config", "runtime-policy"}:
        _fail(evidence, args, f"apply did not write all three intentions: {writes}")
        return 1
    if second_plan_code != 0 or any(
        action.get("action") != "NO CHANGE"
        for action in (second_plan_report.get("actions") or [])
        if action.get("kind") in ("bundle-project", "server-config", "runtime-policy")
    ):
        _fail(evidence, args, "the second plan is not a NO CHANGE run")
        return 1
    second_actions = [write.get("action") for write in (second_apply_report.get("writes") or [])]
    if second_apply_code != 0 or any(action in ("CREATE", "UPDATE") for action in second_actions):
        _fail(evidence, args, f"the second apply wrote something: {second_actions}")
        return 1
    evidence["ok"] = True
    _write(evidence, args)
    print(json.dumps({
        "stage": "setup-native-apply",
        "ok": True,
        "wrote": [f"{write['kind']}:{write['action']}" for write in written],
        "verifyAttempts": verify_attempts,
        "secondApply": [write.get("action") for write in (second_apply_report.get("writes") or [])],
    }, sort_keys=True))
    return 0


def _verify_ok(report: Any) -> bool:
    return isinstance(report, dict) and report.get("verified") is True


def _fail(evidence: dict[str, Any], args: argparse.Namespace, reason: str) -> None:
    evidence["ok"] = False
    evidence["failure"] = reason
    _write(evidence, args)
    print(f"setup-native apply stage failed: {reason}", file=sys.stderr)


def _write(evidence: dict[str, Any], args: argparse.Namespace) -> None:
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = args.evidence_dir / "setup-native-apply.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"evidence: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-token", required=True)
    parser.add_argument("--bundle-manifest", required=True, type=Path)
    parser.add_argument("--bundle-zip", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--source-revision", default="")
    parser.add_argument("--project", required=True)
    parser.add_argument("--server-config", required=True)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
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


if __name__ == "__main__":
    raise SystemExit(main())


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

    The harness's own discipline: the milestone 4a/4b rows deploy their Project and
    Server Config by file copy and only judge the endpoint after the Gateway has
    started with them in place. A Module that has not scanned a Project the CLI
    imported seconds ago answers a Server Config it accepted live with no primitives
    at all (``capabilities=[-]``, ``tools/list -> -32600``). Nothing is written here;
    the reload only makes the already-applied deployment servable, and the reload and
    every verify attempt are recorded in the evidence.
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
