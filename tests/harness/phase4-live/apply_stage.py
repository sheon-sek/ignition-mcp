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
check. A verify that fails right after the write is re-run, bounded and read-only,
because a Gateway whose Module has not yet picked the new Project or Server Config
up answers the endpoint before it can serve it; the stage never re-runs a *write*.

Evidence is written to ``--evidence-dir/setup-native-apply.json``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
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
        "--json",
    ]
    return base


async def run_stage(args: argparse.Namespace) -> int:
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    token_file = args.evidence_dir / "setup-native.token"
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
    verify_report = apply_report.get("verify") or {}
    verify_attempts = 1
    if apply_code != 0 and not _verify_ok(verify_report):
        # The write may have landed before the Module served the new endpoint; the
        # readiness wait is read-only and bounded, and no write is ever retried.
        for attempt in range(VERIFY_ATTEMPTS):
            verify_attempts += 1
            time.sleep(VERIFY_WAIT_SECONDS)
            verify_code, verify_text, verify_payload = await run_command("verify", argv)
            evidence.setdefault("verifyRetries", []).append({
                "attempt": attempt + 1, "exitCode": verify_code, "report": verify_payload,
            })
            if verify_code == 0:
                apply_code = 0
                apply_text += "\n" + verify_text
                break
    evidence["steps"]["apply"]["verifyAttempts"] = verify_attempts
    if apply_code != 0:
        _fail(evidence, args, f"apply exited {apply_code}")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    import asyncio

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
