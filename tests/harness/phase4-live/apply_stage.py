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
        "steps": {},
    }

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
    if not _verify_ok((evidence["steps"]["apply"].get("lastVerify") or apply_report.get("verify")) or {}):
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
    evidence["ok"] = True
    _write(evidence, args)
    print(json.dumps({
        "stage": "setup-native-apply",
        "ok": True,
        "wrote": [f"{write['kind']}:{write['action']}" for write in written],
        "verifyAttempts": verify_attempts,
        "provisioning": provisioning,
        "secondApply": [write.get("action") for write in (second_apply_report.get("writes") or [])],
    }, sort_keys=True))
    return 0


def _verify_ok(report: Any) -> bool:
    return isinstance(report, dict) and report.get("verified") is True


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


def _write(evidence: dict[str, Any], args: argparse.Namespace) -> None:
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = args.evidence_dir / "setup-native-apply.json"
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


if __name__ == "__main__":
    raise SystemExit(main())
