#!/usr/bin/env python3
"""The G7 live stage (issue #78): the ``ignition-mcp`` CLI end to end on a fresh Gateway.

The Gateway comes from ``docker-compose-install.yml``, so it carries no MCP Module.
The workflow has already installed the CI API key and its Security Level. The stage
then plays the operator of D32 and runs the shipped CLI as a subprocess:

1. ``tick-setup-key``: the operator's one manual step (D32 section 9). The CI key's
   level is ticked under every permission in Security > General Settings. The
   workflow's bootstrap ticks access, read and write, and this ticks Designer.
2. ``setup``: one one-line ``ignition-mcp setup`` in ``dev`` with ``--yes``,
   ``--accept-certificate`` and ``--accept-eula``. It installs the Module from
   ``tests/fixtures/modules/``, deploys the bundle and creates both Assistant roles.
3. ``roles``: per role, ``initialize`` and ``tools/list`` at ``/data/mcp/<role>``
   with that role's Runtime token, compared with the role's profile in
   ``contracts/profiles/``. Each token must be refused at the other role's endpoint.
4. ``restGrant`` (issue #79): the ``ignition-mcp-rest`` token holds only
   ``Authenticated/IgnitionMcpRest``, and General Settings lists that level under
   ``readPermissions`` and ``writePermissions`` (dev) and nowhere else.
   ``rest``: ``ignition-mcp start``, then the Analysis Named static token calls a
   read Tool and is refused a Mutation Tool, and the Engineer token is given the
   Mutation scope. A call with empty arguments shows that; it cannot write. Both
   roles call the read Tool through the dedicated level, and the Engineer role
   creates and deletes one audit profile, a real REST Mutation that reaches the Gateway.
5. ``setupAgain``: the same command again. It must plan nothing and change nothing.
6. ``reset``: ``ignition-mcp reset --yes``. Every resource the deployment recorded
   as created must be gone from the Gateway, and the deployment directory too.

The deployment directory, and with it every secret file, lives in a temporary HOME
that is deleted at the end. It is never copied into the evidence directory.

Every wait runs on :data:`driver.CLOCK`, so a test can install a virtual clock.
Exit 0 means every step passed. The evidence document is ``setup-g7.json``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))

import driver  # noqa: E402
import gateway_rest  # noqa: E402
import mcp_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_FILE = "setup-g7.json"
DEPLOYMENT = "g7"
EXPECTED_ORIGIN = "127.0.0.1:8093"
REST_BIND = "127.0.0.1:8094"
#: D32 section 8: each role's Runtime profile.
ROLE_PROFILES = {"analysis": "readonly", "engineer": "full"}
MODULE_ID = "com.inductiveautomation.mcp"
PROJECT = "ignition_runtime"
POLICY_PROVIDER = "IgnitionMCPPolicy"
SECURITY_PROPERTIES = "/data/api/v1/resources/singleton/ignition/security-properties"
SECURITY_LEVELS = "/data/api/v1/resources/singleton/ignition/security-levels"
GATEWAY_PERMISSIONS = ("accessPermissions", "readPermissions", "writePermissions", "designerPermissions")
#: A read Tool and a Mutation Tool of the REST server (D32 section 8).
REST_READ_TOOL = "gateway_info"
REST_MUTATION_TOOL = "config_resource_update"
#: Issue #79: the REST server's own level and the General Settings entries dev grants it.
REST_LEVEL = ("Authenticated", "IgnitionMcpRest")
REST_GRANTED = ("readPermissions", "writePermissions")
REST_CREATE_TOOL = "config_resource_create"
REST_DELETE_TOOL = "config_resource_delete"
REST_MUTATION_TYPE = "ignition/audit-profile"
REST_MUTATION_NAME = "IgnitionMcpG7RestProbe"
REST_READY_SECONDS = 60.0
REST_POLL_SECONDS = 0.5
REST_STOP_SECONDS = 20.0


class StageError(RuntimeError):
    """A step did not produce what the stage requires."""


# ----------------------------------------------------------------------- helpers


def require_origin(base_url: str, expected: str) -> None:
    """Refuse any Gateway but the compose one before the first request."""

    parts = urllib.parse.urlsplit(base_url)
    if (
        parts.scheme != "http"
        or f"{parts.hostname}:{parts.port}" != expected
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise StageError(f"{base_url!r} is not the disposable Gateway http://{expected}")


def cli_path() -> str:
    """The ``ignition-mcp`` script of the environment this stage runs in."""

    script = Path(sys.executable).parent / ("ignition-mcp.exe" if os.name == "nt" else "ignition-mcp")
    if not script.is_file():
        raise StageError(f"no ignition-mcp script next to {sys.executable}; run uv sync first")
    return str(script)


def run_cli(home: Path, *words: str, timeout: float = 1500.0) -> dict[str, Any]:
    """Run one ``ignition-mcp`` command with ``--json`` and return its document."""

    completed = subprocess.run(
        [cli_path(), *words, "--deployment", DEPLOYMENT, "--json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, check=False,
        env={**os.environ, "HOME": str(home)},
    )
    try:
        document = json.loads(completed.stdout)
    except ValueError as error:
        raise StageError(
            f"ignition-mcp {words[0]} printed no JSON (exit {completed.returncode}): {completed.stderr[-800:]}"
        ) from error
    if not isinstance(document, dict):
        raise StageError(f"ignition-mcp {words[0]} printed a JSON {type(document).__name__}")
    document["stderrTail"] = completed.stderr[-2000:]
    return document


def steps_by_name(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(step["step"]): step for step in document.get("steps", [])}


def get_json(base_url: str, token: str, path: str, query: dict[str, str] | None = None) -> tuple[int, Any]:
    status, payload = gateway_rest.request(base_url, token, "GET", path, query=query, timeout=30.0)
    return status, gateway_rest.decode(payload) if status == 200 else None


def read_profile(profile: str) -> list[str]:
    document = json.loads((ROOT / "contracts" / "profiles" / f"{profile}.yaml").read_text("utf-8"))
    return sorted(str(tool) for tool in document["tools"])


def leaves(nodes: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    for node in nodes if isinstance(nodes, list) else ():
        here = (*prefix, str(node.get("name")))
        children = node.get("children")
        found += leaves(children, here) if children else [here]
    return found


# ------------------------------------------------------------------------- steps


def tick_setup_key(base_url: str, token: str) -> dict[str, Any]:
    """Add the key's level to every ``AnyOf`` Gateway permission that lacks it."""

    name = token.partition(":")[0]
    status, key = get_json(base_url, token, f"/data/api/v1/resources/find/ignition/api-token/{name}")
    if status != 200:
        raise StageError(f"the setup key {name} is not readable (HTTP {status})")
    held = leaves(key["config"]["profile"]["securityLevels"])
    if len(held) != 1 or len(held[0]) != 2:
        raise StageError(f"the setup key must hold one Authenticated/<level>, not {held}")
    parent, level = held[0]
    status, properties = get_json(base_url, token, SECURITY_PROPERTIES, {"defaultIfUndefined": "true"})
    if status != 200:
        raise StageError(f"security-properties is not readable (HTTP {status})")
    config = properties["config"]
    ticked = []
    for field in GATEWAY_PERMISSIONS:
        permission = config.get(field) or {"type": "AnyOf", "securityLevels": []}
        if held[0] in leaves(permission.get("securityLevels")):
            continue
        if permission.get("type") != "AnyOf" and permission.get("securityLevels"):
            raise StageError(f"{field} is {permission.get('type')}; the stage only adds to an AnyOf permission")
        tree = permission.setdefault("securityLevels", [])
        node = next((item for item in tree if item.get("name") == parent), None)
        if node is None:
            node = {"name": parent, "children": []}
            tree.append(node)
        node.setdefault("children", []).append({"name": level, "children": []})
        permission["type"] = "AnyOf"
        config[field] = permission
        ticked.append(field)
    if ticked:
        body = [{"collection": properties["collection"], "signature": properties["signature"], "config": config}]
        status, payload = gateway_rest.request(
            base_url, token, "PUT", "/data/api/v1/resources/ignition/security-properties",
            body=json.dumps(body).encode("utf-8"), content_type="application/json", timeout=30.0,
        )
        if status != 200:
            raise StageError(f"ticking the setup key's level failed (HTTP {status}): {payload[:400]!r}")
    return {"level": f"{parent}/{level}", "ticked": ticked}


def check_setup(document: dict[str, Any], *, first: bool) -> dict[str, Any]:
    if document.get("exit_code") != 0 or document.get("error") is not None:
        raise StageError(f"setup failed: {json.dumps(document.get('error'))}")
    steps = steps_by_name(document)
    failed = [name for name, step in steps.items() if step["status"] == "FAILED"]
    if failed:
        raise StageError(f"setup reported FAILED steps {failed}")
    for role in ROLE_PROFILES:
        if steps.get(f"runtime check {role}", {}).get("status") != "OK":
            raise StageError(f"setup's closing check for {role} did not pass")
    changed = sorted(name for name, step in steps.items() if step["status"] == "CHANGED")
    if first:
        module = steps.get("runtime module", {})
        if module.get("status") != "CHANGED" or "tests/fixtures/modules/" not in module.get("reason", ""):
            raise StageError(f"setup did not install the Module from tests/fixtures/modules/: {module}")
        wanted = [
            "runtime bundle", "runtime security levels", "runtime policy",
            *(f"runtime token {role}" for role in ROLE_PROFILES),
            *(f"runtime server config {role}" for role in ROLE_PROFILES),
            "rest token ignition-mcp-rest",
        ]
        missing = [name for name in wanted if name not in changed]
        if missing:
            raise StageError(f"a first setup on an empty Gateway must create {missing}")
    elif changed or document.get("plan"):
        raise StageError(f"the second setup changed {changed} and planned {document.get('plan')}")
    return {
        "exitCode": document["exit_code"],
        "plannedChanges": len(document.get("plan", [])),
        "changedSteps": changed,
        "steps": [{"step": s["step"], "status": s["status"], "reason": s["reason"]} for s in document["steps"]],
        "accepted": document.get("accepted", []),
    }


def check_roles(base_url: str, deployment: Path) -> dict[str, Any]:
    tokens = {role: (deployment / f"runtime-{role}.secret").read_text("utf-8").strip() for role in ROLE_PROFILES}
    result: dict[str, Any] = {}
    for role, profile in ROLE_PROFILES.items():
        url = f"{base_url.rstrip('/')}/data/mcp/{role}"
        client = mcp_client.McpClient(url, tokens[role], timeout=60.0)
        init = client.initialize()
        listed = sorted(client.tools_list())
        expected = read_profile(profile)
        if listed != expected:
            raise StageError(
                f"{role}: tools/list differs from {profile}: "
                f"extra {sorted(set(listed) - set(expected))}, missing {sorted(set(expected) - set(listed))}"
            )
        refusals = {}
        for other in ROLE_PROFILES:
            if other == role:
                continue
            try:
                mcp_client.McpClient(url, tokens[other], timeout=60.0).initialize()
            except mcp_client.McpError as error:
                refusals[other] = str(error)[:200]
            else:
                raise StageError(f"the {other} token initialized the {role} endpoint")
        result[role] = {
            "endpoint": f"/data/mcp/{role}",
            "profile": profile,
            "protocolVersion": init.get("protocolVersion"),
            "tools": listed,
            "toolCount": len(listed),
            "inventoryMatches": True,
            "otherTokensRefused": refusals,
        }
    return result


def _call_text(client: mcp_client.McpClient, tool: str) -> tuple[bool, str]:
    """``(refused_or_error, text)`` for one call with empty arguments."""

    try:
        result = client.tool_result(tool, {})
    except mcp_client.McpError as error:
        return True, str(error)
    return bool(result.get("isError")), json.dumps(result)[:600]


def check_rest(home: Path, deployment: Path, evidence: Path) -> dict[str, Any]:
    log = (evidence / "ignition-mcp-start.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [cli_path(), "start", "--deployment", DEPLOYMENT, "--bind", REST_BIND],
        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "HOME": str(home)},
    )
    try:
        base = f"http://{REST_BIND}"
        deadline = driver.CLOCK.now() + REST_READY_SECONDS
        ready = 0
        while driver.CLOCK.now() < deadline and server.poll() is None:
            try:
                with urllib.request.urlopen(base + "/health/ready", timeout=3.0) as response:
                    ready = int(response.status)
            except (urllib.error.URLError, OSError):
                ready = 0
            if ready == 200:
                break
            driver.CLOCK.wait(REST_POLL_SECONDS)
        if ready != 200:
            raise StageError(f"ignition-mcp start never answered /health/ready (exit {server.poll()})")
        tokens = {
            role: (deployment / f"rest-{role}-token.secret").read_text("utf-8").strip() for role in ROLE_PROFILES
        }
        analysis = mcp_client.McpClient(base + "/mcp", tokens["analysis"], timeout=60.0)
        analysis.initialize()
        analysis_tools = set(analysis.tools_list())
        read_error, read_text = _call_text(analysis, REST_READ_TOOL)
        if read_error or REST_READ_TOOL not in analysis_tools:
            raise StageError(f"the Analysis token could not call {REST_READ_TOOL}: {read_text}")
        refused, refused_text = _call_text(analysis, REST_MUTATION_TOOL)
        if not refused or "permission_denied" not in refused_text or REST_MUTATION_TOOL in analysis_tools:
            raise StageError(f"the Analysis token was not refused {REST_MUTATION_TOOL}: {refused_text}")
        engineer = mcp_client.McpClient(base + "/mcp", tokens["engineer"], timeout=60.0)
        engineer.initialize()
        engineer_tools = set(engineer.tools_list())
        _, engineer_text = _call_text(engineer, REST_MUTATION_TOOL)
        if REST_MUTATION_TOOL not in engineer_tools or "permission_denied" in engineer_text:
            raise StageError(f"the Engineer token was not given the Mutation scope: {engineer_text}")
        dedicated = check_rest_through_level(analysis, engineer)
        return {
            "throughDedicatedLevel": dedicated,
            "bind": REST_BIND,
            "analysis": {
                "readTool": REST_READ_TOOL,
                "readAllowed": True,
                "mutationTool": REST_MUTATION_TOOL,
                "mutationListed": False,
                "mutationRefused": "permission_denied",
            },
            "engineer": {
                "mutationTool": REST_MUTATION_TOOL,
                "mutationListed": True,
                "mutationScopeGranted": True,
                "emptyArgumentsAnswer": engineer_text[:300],
            },
        }
    finally:
        server.send_signal(signal.SIGINT)
        try:
            server.wait(timeout=REST_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        log.close()


def check_rest_grant(base_url: str, token: str) -> dict[str, Any]:
    """Issue #79: the REST token holds only its own level, granted read and write in dev."""

    status, document = get_json(base_url, token, "/data/api/v1/resources/find/ignition/api-token/ignition-mcp-rest")
    if status != 200:
        raise StageError(f"ignition-mcp-rest is not readable (HTTP {status})")
    held = leaves(document["config"]["profile"]["securityLevels"])
    if held != [REST_LEVEL]:
        raise StageError(f"ignition-mcp-rest holds {held}, not only {'/'.join(REST_LEVEL)}")
    status, properties = get_json(base_url, token, SECURITY_PROPERTIES)
    if status != 200:
        raise StageError(f"security-properties is not readable (HTTP {status})")
    listed = {
        field: REST_LEVEL in leaves((properties["config"].get(field) or {}).get("securityLevels"))
        for field in GATEWAY_PERMISSIONS
    }
    wanted = {field: field in REST_GRANTED for field in GATEWAY_PERMISSIONS}
    if listed != wanted:
        raise StageError(f"General Settings list {'/'.join(REST_LEVEL)} under {listed}, not {wanted}")
    return {"tokenLevels": ["/".join(path) for path in held], "generalSettings": listed}


def check_rest_through_level(analysis: mcp_client.McpClient, engineer: mcp_client.McpClient) -> dict[str, Any]:
    """Both roles read through the dedicated level, and one Engineer Mutation reaches the Gateway."""

    reads = {}
    for role, client in (("analysis", analysis), ("engineer", engineer)):
        info = client.structured(REST_READ_TOOL, {})
        reads[role] = {"tool": REST_READ_TOOL, "ignitionVersion": info.get("ignitionVersion")}
    created = engineer.structured(REST_CREATE_TOOL, {
        "resourceType": REST_MUTATION_TYPE,
        "name": REST_MUTATION_NAME,
        "description": "Disposable G7 probe for the REST server's own level (issue #79)",
        "config": {"profile": {"type": "local", "retentionDays": 1}},
    })
    signature = created.get("signature")
    if not isinstance(signature, str) or not signature:
        raise StageError(f"{REST_CREATE_TOOL} returned no signature: {json.dumps(created)[:400]}")
    deleted = engineer.structured(REST_DELETE_TOOL, {
        "resourceType": REST_MUTATION_TYPE, "name": REST_MUTATION_NAME, "expectedSignature": signature,
    })
    if deleted.get("present") is not False:
        raise StageError(f"{REST_DELETE_TOOL} did not report the probe gone: {json.dumps(deleted)[:400]}")
    return {
        "reads": reads,
        "mutation": {"create": REST_CREATE_TOOL, "delete": REST_DELETE_TOOL, "resourceType": REST_MUTATION_TYPE,
                     "name": REST_MUTATION_NAME, "created": True, "deleted": True},
    }


def check_reset(document: dict[str, Any], created: list[str], base_url: str, token: str, directory: Path) -> dict[str, Any]:
    if document.get("exit_code") != 0 or document.get("error") is not None:
        raise StageError(f"reset failed: {json.dumps(document.get('error'))}")
    status, modules = get_json(base_url, token, "/data/api/v1/modules/healthy", {"limit": "200"})
    module_ids = {item.get("id") for item in (modules or {}).get("items", [])} if status == 200 else None
    status, levels = get_json(base_url, token, SECURITY_LEVELS)
    level_paths = {"/".join(path) for path in leaves(levels["config"]["securityLevels"])} if status == 200 else None
    left: dict[str, str] = {}
    for entry in created:
        kind, _, name = entry.partition(":")
        if kind == "module":
            if module_ids is None or MODULE_ID in module_ids:
                left[entry] = "the Gateway still lists the MCP Module" if module_ids else "modules unreadable"
            continue
        if kind in ("level", "rest-level"):
            if level_paths is None or any(path == name or path.startswith(name + "/") for path in level_paths):
                left[entry] = "the level is still in the Security Level tree"
            continue
        if kind == "rest-permission":
            left.update(rest_permission_left(entry, name, base_url, token))
            continue
        path = {
            "project": f"/data/api/v1/projects/find/{name}",
            "runtime-token": f"/data/api/v1/resources/find/ignition/api-token/{name}",
            "rest-token": f"/data/api/v1/resources/find/ignition/api-token/{name}",
            "server-config": f"/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/{name}",
            "policy": f"/data/api/v1/resources/find/ignition/tag-provider/{POLICY_PROVIDER}",
        }.get(kind)
        if path is None:
            left[entry] = "the stage has no check for this kind of resource"
            continue
        found, _ = gateway_rest.request(base_url, token, "GET", path, timeout=30.0)
        if found == 200:
            left[entry] = f"GET {path} still answers 200"
    if left:
        raise StageError(f"reset left resources on the Gateway: {left}")
    if directory.exists():
        raise StageError(f"reset left the deployment directory {directory}")
    return {
        "exitCode": document["exit_code"],
        "created": created,
        "leftOnGateway": {},
        "deploymentDirectoryRemoved": True,
        "steps": [{"step": s["step"], "status": s["status"], "reason": s["reason"]} for s in document["steps"]],
    }


def rest_permission_left(entry: str, field: str, base_url: str, token: str) -> dict[str, str]:
    """Issue #79: a recorded General Settings grant must be gone after reset."""

    status, properties = get_json(base_url, token, SECURITY_PROPERTIES)
    if status != 200:
        return {entry: f"security-properties is not readable (HTTP {status})"}
    if REST_LEVEL in leaves((properties["config"].get(field) or {}).get("securityLevels")):
        return {entry: f"{field} still lists {'/'.join(REST_LEVEL)}"}
    return {}


# -------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G7: ignition-mcp setup, start, setup again and reset, live")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-origin", default=EXPECTED_ORIGIN)
    parser.add_argument("--api-token", required=True, help="the operator's setup key, <name>:<key>")
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    require_origin(args.base_url, args.expected_origin)
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="g7-home-"))
    deployment = home / ".config" / "ignition-mcp" / "deployments" / DEPLOYMENT
    key_file = home / "setup-key"
    key_file.write_text(args.api_token + "\n", encoding="utf-8")
    key_file.chmod(0o600)
    setup_words = [
        "setup", "--gateway-url", args.base_url, "--environment", "dev",
        "--gateway-token-file", str(key_file), "--yes", "--accept-certificate", "--accept-eula",
    ]
    evidence: dict[str, Any] = {
        "schemaVersion": 1,
        "stage": "g7-setup-cli",
        #: setup builds the bundle from this checkout, so its version is the checkout's.
        "bundleVersion": (ROOT / "packages/ignition-runtime-bundle/BUNDLE_VERSION").read_text("utf-8").strip(),
        "command": "ignition-mcp " + " ".join(setup_words).replace(str(key_file), "<setup-key-file>"),
        "steps": {},
        #: Each CLI run's own --json document, as printed. The reporter hides secrets.
        "cli": {},
        "ok": False,
    }
    steps = evidence["steps"]
    cli = evidence["cli"]
    try:
        steps["tick-setup-key"] = tick_setup_key(args.base_url, args.api_token)
        print("tick-setup-key:", json.dumps(steps["tick-setup-key"]), flush=True)
        cli["setup"] = run_cli(home, *setup_words)
        steps["setup"] = check_setup(cli["setup"], first=True)
        print("setup:", steps["setup"]["changedSteps"], flush=True)
        steps["roles"] = check_roles(args.base_url, deployment)
        print("roles:", {role: data["toolCount"] for role, data in steps["roles"].items()}, flush=True)
        steps["restGrant"] = check_rest_grant(args.base_url, args.api_token)
        print("restGrant:", json.dumps(steps["restGrant"]), flush=True)
        steps["rest"] = check_rest(home, deployment, args.evidence_dir)
        print("rest: analysis read allowed, mutation refused; engineer mutation scope granted", flush=True)
        cli["setupAgain"] = run_cli(home, *setup_words)
        steps["setupAgain"] = check_setup(cli["setupAgain"], first=False)
        print("setupAgain: no change", flush=True)
        created = tomllib.loads((deployment / "deployment.toml").read_text("utf-8")).get("created", [])
        cli["reset"] = run_cli(home, "reset", "--gateway-token-file", str(key_file), "--yes")
        steps["reset"] = check_reset(
            cli["reset"], [str(item) for item in created], args.base_url, args.api_token, deployment
        )
        print("reset: removed", steps["reset"]["created"], flush=True)
        evidence["ok"] = True
    except (StageError, mcp_client.McpError, gateway_rest.RestError, OSError, KeyError, subprocess.SubprocessError) as error:
        evidence["failure"] = f"{type(error).__name__}: {error}"
        print("FAILED:", evidence["failure"], file=sys.stderr, flush=True)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        (args.evidence_dir / EVIDENCE_FILE).write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
