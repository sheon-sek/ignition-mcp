"""Phase 4 ticket #21: ``setup-native apply`` — bundle project, Server Config, policy.

The command is driven end to end against the recorded Gateway
(``tests/harness/recorded_gateway.py``), which models the three write paths ticket
#21 uses: the Project import, the Server Config collection routes (create and
modify) and the reserved policy Tag provider with its import readiness flake. Every
case asserts the observable outcome — what the Gateway was asked to write, what it
now serves, and the exit code — never the command's internal calls.

Covered: the happy path (plan CREATEs, apply writes all three, verify is green, a
second plan is NO CHANGE and writes nothing), a BLOCKED line stopping apply before
any write, a Server Config that must not be created without a permissions tree, a
Tool list that can never be ``*``, drift reconciliation, the 32 KiB policy cap and
the policy schema, the MAJOR-change acknowledgement, and a Gateway refusal in the
middle of the sequence.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterator

import pytest

from ignition_rest_mcp.cli.setup_native import main as cli_main
from ignition_rest_mcp.cli.setup_native import plan as plan_module
from ignition_rest_mcp.cli.setup_native import writer as writer_module
from ignition_rest_mcp.cli.setup_native.inputs import Endpoint

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, DEFAULT_COLLECTION, RecordedGateway  # noqa: E402

BUNDLE_VERSION = "0.6.0"
SOURCE_REVISION = "a" * 40
PROJECT = "ignition_runtime"
SERVER_CONFIG = "phase4-apply"
TOOLS = ["bundle_info", "tag_browse", "tag_read"]
RESOURCES = ["ignition://?contracts/bundle-info-output"]
PERMISSIONS = {
    "type": "AllOf",
    "securityLevels": [{"name": "Authenticated", "children": [{"name": "IgnitionMcpCi", "children": []}]}],
}
POLICY = {
    "schemaVersion": 1,
    "allowlists": {"tag_write": ["[default]IgnitionMCP_CI"]},
    "serviceIdentity": "ignition-mcp-service",
    "auditMode": "best_effort",
}
#: The recorded Gateway answers reads and writes only for its own API token.
GATEWAY_TOKEN = API_TOKEN


# ------------------------------------------------------------------------- fixtures


def project_archive(version: str = BUNDLE_VERSION, *, managed: bool = True, inheritable: bool = False) -> bytes:
    """One Designer project archive, with or without the D20 ownership marker."""

    description = "Runtime MCP Bundle."
    if managed:
        description += f"\nignition-mcp-managed: product=ignition-runtime-bundle; bundle={version}"
    document = json.dumps(
        {"title": "Runtime MCP Bundle", "description": description, "enabled": True, "inheritable": inheritable},
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", document)
        archive.writestr("com.inductiveautomation.mcp/tools/bundle_info/resource.json", b"{}")
    return buffer.getvalue()


def make_manifest(version: str = BUNDLE_VERSION, archive: bytes | None = None) -> dict[str, Any]:
    payload = archive if archive is not None else project_archive(version)

    def profile(permissions: list[str], tools: list[str], resources: list[str]) -> dict[str, Any]:
        return {"permissions": permissions, "tools": tools, "resources": resources, "prompts": []}

    return {
        "schemaVersion": 1,
        "bundleVersion": version,
        "sourceRevision": SOURCE_REVISION,
        "resourceSchemaVersion": 1,
        "nativeResponseBindingStatus": "VERIFIED_WITH_LIMITATION",
        "artifact": {
            "filename": f"ignition-runtime-bundle-{version}.zip",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "sizeBytes": len(payload),
        },
        "tools": sorted(TOOLS),
        "resources": sorted(RESOURCES),
        "prompts": [],
        "toolRequirements": {
            name: {"budgetClass": "FAST", "permissionClass": "READ", "nativeRequirements": ["system.tag.readBlocking"]}
            for name in TOOLS
        },
        "profileInventories": {
            "readonly": profile(["READ"], sorted(TOOLS), sorted(RESOURCES)),
            "operator": profile(["CONTROL", "READ"], sorted(TOOLS), []),
            "configurator": profile(["CONFIG", "READ"], sorted(TOOLS), []),
            "full": profile(["CONFIG", "CONTROL", "READ"], sorted(TOOLS), []),
        },
        "testedTuples": [],
    }


class Workdir:
    """The operator-side artifacts one apply run needs, written once per test."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.policy = tmp_path / "policy.json"
        self.policy.write_text(json.dumps(POLICY, sort_keys=True), encoding="utf-8")
        self.permissions = tmp_path / "permissions.json"
        self.permissions.write_text(json.dumps(PERMISSIONS, sort_keys=True), encoding="utf-8")
        self.token = tmp_path / "gateway.token"
        self.token.write_text(GATEWAY_TOKEN + "\n", encoding="utf-8")
        self.token.chmod(0o600)

    def manifest(self, document: dict[str, Any]) -> Path:
        path = self.root / "bundle.manifest.json"
        path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        return path

    def archive(self, payload: bytes) -> Path:
        path = self.root / "bundle.zip"
        path.write_bytes(payload)
        return path

    def argv(self, command: str, base: str, *, version: str = BUNDLE_VERSION, archive: bytes | None = None,
             policy_file: Path | None = None, permissions_file: Path | None = None,
             server_config: str | None = SERVER_CONFIG, extra: tuple[str, ...] = ()) -> list[str]:
        payload = archive if archive is not None else project_archive(version)
        args = [
            "setup-native", command,
            "--bundle-manifest", str(self.manifest(make_manifest(version, payload))),
            "--bundle-zip", str(self.archive(payload)),
            "--gateway-url", base,
            "--gateway-token-file", str(self.token),
            "--mcp-token-file", str(self.token),
            "--profile", "readonly",
        ]
        if policy_file is not None:
            args += ["--policy-file", str(policy_file)]
        if permissions_file is not None:
            args += ["--server-config-permissions-file", str(permissions_file)]
        if server_config is not None:
            args += ["--server-config-name", server_config]
        return args + list(extra)


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main.main(argv)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def gateway() -> Iterator[RecordedGateway]:
    with RecordedGateway(
        runtime_tools=tuple(TOOLS),
        runtime_resources=tuple(RESOURCES),
        source_revision=SOURCE_REVISION,
        bundle_version=BUNDLE_VERSION,
        policy_provider="IgnitionMCPPolicy",
    ) as fake:
        yield fake


@pytest.fixture
def workdir(tmp_path: Path) -> Workdir:
    return Workdir(tmp_path)


def writes(gateway: RecordedGateway) -> list[tuple[str, str]]:
    """Every write-shaped Gateway request (the MCP plane is POST too, so it is named)."""

    return [
        (str(request["method"]), str(request["path"]))
        for request in gateway.requests
        if request["method"] in ("POST", "PUT") and not str(request["path"]).startswith("/data/mcp/")
    ]


def server_config_body(gateway: RecordedGateway) -> dict[str, Any]:
    for request in gateway.requests:
        if request["method"] in ("POST", "PUT") and "server-config" in str(request["path"]):
            items = json.loads(request["body"])
            return items[0]
    raise AssertionError("no Server Config write reached the Gateway")


def served_policy(gateway: RecordedGateway) -> tuple[str | None, Any]:
    """The policy Tag value and declared-length Tag the reserved provider serves."""

    value: str | None = None
    declared: Any = None
    for tag in gateway.served_policy_tags():
        if tag.get("name") == "RuntimeTargetPolicy":
            value = tag.get("value")
        if tag.get("name") == "RuntimeTargetPolicyLength":
            declared = tag.get("value")
    return value, declared


# ------------------------------------------------------------------------ happy path


def test_plan_reports_create_for_a_fresh_gateway(gateway: RecordedGateway, workdir: Workdir) -> None:
    code, out, err = run_cli(workdir.argv(
        "plan", gateway.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
    ))
    assert code == 0 and err == ""
    assert f"CREATE bundle-project {PROJECT}: deploy managed bundle {BUNDLE_VERSION}" in out
    assert f"CREATE server-config {SERVER_CONFIG}: explicit Tool inventory (never *)" in out
    assert "CREATE runtime-policy [IgnitionMCPPolicy]RuntimeTargetPolicy" in out
    assert plan_module.PLAN_SENTINEL in out
    assert writes(gateway) == []


def test_apply_writes_the_three_intentions_and_verifies(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    code, out, err = run_cli(workdir.argv(
        "apply", gateway.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
    ))
    assert code == 0 and err == ""
    assert f"WRITE bundle-project {PROJECT}" in out
    assert f"WRITE server-config {SERVER_CONFIG}" in out
    assert "WRITE runtime-policy [IgnitionMCPPolicy]RuntimeTargetPolicy" in out
    assert "verify: verified=true => exit 0" in out
    assert "apply: wrote=3 skipped=0 failed=0 => exit 0" in out

    methods = writes(gateway)
    assert ("POST", f"/data/api/v1/projects/import/{PROJECT}") in methods
    assert ("POST", "/data/api/v1/resources/com.inductiveautomation.mcp/server-config") in methods
    assert ("POST", "/data/api/v1/resources/ignition/tag-provider") in methods
    assert ("POST", "/data/api/v1/tags/import?provider=IgnitionMCPPolicy&path=&type=json&collisionPolicy=MergeOverwrite") in methods

    # The Project the Gateway serves now is the managed bundle the manifest declares.
    project = json.loads(zipfile.ZipFile(io.BytesIO(gateway.project(PROJECT))).read("project.json"))
    assert f"bundle={BUNDLE_VERSION}" in project["description"]
    assert project["inheritable"] is False

    # The Server Config carries the profile's explicit Tool list and the operator's
    # permissions tree — and never a wildcard in the Tool mapping (D09/D20).
    item = server_config_body(gateway)
    assert item["name"] == SERVER_CONFIG and item["collection"] == DEFAULT_COLLECTION
    assert item["config"]["tools"] == {f"project/{PROJECT}": sorted(TOOLS)}
    assert "*" not in json.dumps(item["config"]["tools"])
    assert item["config"]["permissions"] == PERMISSIONS
    assert item["config"]["version"] == BUNDLE_VERSION
    enabled = [json.loads(request["body"])[0]["enabled"] for request in gateway.requests
               if request["method"] == "PUT" and "server-config" in str(request["path"])]
    assert enabled == [True]

    # The reserved provider serves exactly the document apply wrote, with the
    # declared-length companion the reader gates on.
    value, declared = served_policy(gateway)
    canonical = json.dumps(POLICY, sort_keys=True, separators=(",", ":"))
    assert value == canonical
    assert declared == len(canonical.encode("utf-8"))


def test_a_second_plan_is_no_change_and_a_second_apply_writes_nothing(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    argv = workdir.argv(
        "apply", gateway.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
    )
    assert run_cli(argv)[0] == 0
    before = writes(gateway)

    code, out, _ = run_cli(workdir.argv(
        "plan", gateway.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
    ))
    assert code == 0
    assert f"NO CHANGE bundle-project {PROJECT}: managed bundle {BUNDLE_VERSION} already deployed" in out
    assert f"NO CHANGE server-config {SERVER_CONFIG}: explicit Tool inventory managed by apply" in out
    assert "NO CHANGE runtime-policy [IgnitionMCPPolicy]RuntimeTargetPolicy: deployment policy already matches" in out

    code, out, _ = run_cli(argv)
    assert code == 0
    assert "apply: wrote=0 skipped=3 failed=0 => exit 0" in out
    assert writes(gateway) == before


# --------------------------------------------------------------------- safety refusals


def test_apply_stops_before_writing_when_a_plan_line_is_blocked(
    workdir: Workdir,
) -> None:
    with RecordedGateway(
        projects={PROJECT: project_archive(managed=False)},
        runtime_tools=tuple(TOOLS),
        runtime_resources=tuple(RESOURCES),
        source_revision=SOURCE_REVISION,
        bundle_version=BUNDLE_VERSION,
        policy_provider="IgnitionMCPPolicy",
    ) as fake:
        code, out, err = run_cli(workdir.argv(
            "apply", fake.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
        ))
        assert code == 3 and err == ""
        assert f"BLOCKED bundle-project {PROJECT}: refuse takeover of unmanaged project {PROJECT}" in out
        assert "apply writes nothing until they are resolved" in out
        assert plan_module.PLAN_SENTINEL in out
        assert writes(fake) == []


def test_apply_refuses_to_create_an_unauthenticated_server_config(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    # The Server Config does not exist yet, so plan cannot promise a permissions tree.
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url, policy_file=workdir.policy))
    assert code == 3
    assert "BLOCKED server-config" in out and "--server-config-permissions-file" in out
    assert "issue #22" in out
    assert writes(gateway) == []


def test_a_wildcard_tool_list_can_never_be_written() -> None:
    """D20: the Server Config Tool mapping is explicit, and a create needs permissions."""

    endpoint = Endpoint(url="http://127.0.0.1:1", scheme="http", host="127.0.0.1", port=1)
    config = {
        "title": SERVER_CONFIG,
        "permissions": PERMISSIONS,
        "tools": {f"project/{PROJECT}": ["*"]},
    }

    async def attempt(document: dict[str, Any]) -> None:
        async with writer_module.GatewayWriter(endpoint, "token") as writer:
            await writer.create_server_config(SERVER_CONFIG, document, enabled=True)

    with pytest.raises(writer_module.WriteError, match="must not contain a wildcard"):
        asyncio.run(attempt(config))
    config["tools"] = {f"project/{PROJECT}": TOOLS}
    config["permissions"] = {}
    with pytest.raises(writer_module.WriteError, match="permissions tree"):
        asyncio.run(attempt(config))


def test_apply_reconciles_a_drifted_server_config(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(
        "com.inductiveautomation.mcp/server-config",
        SERVER_CONFIG,
        enabled=True,
        config={
            "title": "Operator managed",
            "version": "0.0.1",
            "permissions": PERMISSIONS,
            "permissionsMarker": "kept",
            "tools": {f"project/{PROJECT}": ["bundle_info"], "project/other_bundle": ["other_tool"]},
            "resources": {f"project/{PROJECT}": "*"},
            "prompts": {f"project/{PROJECT}": "*"},
        },
    )
    code, out, _ = run_cli(workdir.argv(
        "plan", gateway.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
    ))
    assert code == 0
    assert f"UPDATE server-config {SERVER_CONFIG}: explicit Tool inventory (never *), profile readonly, 3 Tools" in out
    assert "adds [tag_browse, tag_read]" in out

    code, out, _ = run_cli(workdir.argv(
        "apply", gateway.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
    ))
    assert code == 0 and f"WRITE server-config {SERVER_CONFIG} [UPDATE]" in out
    document = gateway.resource("com.inductiveautomation.mcp/server-config", SERVER_CONFIG)
    assert document["config"]["tools"][f"project/{PROJECT}"] == sorted(TOOLS)
    assert document["config"]["permissions"] == PERMISSIONS
    assert document["config"]["title"] == "Operator managed"
    assert document["config"]["permissionsMarker"] == "kept"
    # Another project's mapping is the operator's: apply reconciles only this bundle.
    assert document["config"]["tools"]["project/other_bundle"] == ["other_tool"]


def test_a_gateway_refusal_stops_the_sequence_and_still_verifies(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.refuse_imports_with("the Project could not be written")
    code, out, _ = run_cli(workdir.argv(
        "apply", gateway.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
    ))
    assert code == 1
    assert f"FAILED bundle-project {PROJECT} [CREATE]: import project was refused by the Gateway" in out
    assert "verify: verified=" in out
    methods = writes(gateway)
    assert methods == [("POST", f"/data/api/v1/projects/import/{PROJECT}")]
    assert gateway.project(PROJECT) is None


# ------------------------------------------------------------------------- documents


def test_a_policy_over_the_cap_is_refused_before_any_write(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    oversize = dict(POLICY, allowlists={"tag_write": ["[default]" + "x" * 40000]})
    path = workdir.root / "oversize.json"
    path.write_text(json.dumps(oversize), encoding="utf-8")
    code, _, err = run_cli(workdir.argv(
        "apply", gateway.base_url, policy_file=path, permissions_file=workdir.permissions,
    ))
    assert code == 2
    assert "32768 bytes" in err and "--policy-file" in err
    assert writes(gateway) == []


@pytest.mark.parametrize("document", [
    {"allowlists": {}, "serviceIdentity": "svc", "auditMode": "best_effort"},
    {"schemaVersion": 1, "allowlists": {}, "auditMode": "best_effort"},
    {"schemaVersion": 1, "allowlists": {}, "serviceIdentity": "svc", "auditMode": "sometimes"},
])
def test_a_malformed_policy_document_is_refused_before_any_write(
    gateway: RecordedGateway, workdir: Workdir, document: dict[str, Any]
) -> None:
    path = workdir.root / "broken.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    code, _, err = run_cli(workdir.argv(
        "apply", gateway.base_url, policy_file=path, permissions_file=workdir.permissions,
    ))
    assert code == 2 and "--policy-file" in err
    assert writes(gateway) == []


def test_a_major_change_needs_the_explicit_acknowledgement(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.change_project_out_of_band(PROJECT, {"project.json": json.dumps({
        "title": "Runtime MCP Bundle",
        "description": "Runtime MCP Bundle.\nignition-mcp-managed: product=ignition-runtime-bundle; bundle=0.6.0",
        "enabled": True,
        "inheritable": False,
    }).encode()})
    upgrade = "1.0.0"
    argv = workdir.argv(
        "apply", gateway.base_url, version=upgrade,
        policy_file=workdir.policy, permissions_file=workdir.permissions,
    )
    code, out, _ = run_cli(argv)
    assert code == 3
    assert "is a major (0.6.0 -> 1.0.0) and --acknowledge-upgrade was not passed" in out
    assert writes(gateway) == []

    backup_dir = workdir.root / "backups"
    code, out, _ = run_cli([*argv, "--acknowledge-upgrade", "--backup-dir", str(backup_dir)])
    assert code == 0
    assert ("POST", f"/data/api/v1/projects/import/{PROJECT}?overwrite=true") in writes(gateway)
    project = json.loads(zipfile.ZipFile(io.BytesIO(gateway.project(PROJECT))).read("project.json"))
    assert "bundle=1.0.0" in project["description"]
    # D20's snapshot before a replace: the archive that was deployed, kept locally.
    backup = json.loads(zipfile.ZipFile(backup_dir / f"{PROJECT}.zip").read("project.json"))
    assert "bundle=0.6.0" in backup["description"]


def test_no_gateway_path_escapes_a_resource_type(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    """The recorded-live failure of the ticket #21 row: an escaped type id is a 404.

    The fake unquotes the path before it routes, exactly as the Gateway does, so a
    path that escapes the `/` inside `ignition/tag-provider` still reached the
    resource here while the live Gateway answered 404 for 60 s — which left the
    policy write reporting a provider that never became readable. The raw request
    paths are what the fake records, so this is the pin that catches it.
    """

    code, out, _ = run_cli(workdir.argv(
        "apply", gateway.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
    ))
    assert code == 0, out
    escaped = [
        str(request["path"]) for request in gateway.requests
        if "%2f" in str(request["path"]).lower()
    ]
    assert escaped == []
    assert ("GET", "/data/api/v1/resources/find/ignition/tag-provider/IgnitionMCPPolicy") in [
        (str(request["method"]), str(request["path"])) for request in gateway.requests
    ]


def test_verify_derives_its_endpoint_from_the_server_config(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    """Apply verifies through the Server Config it just wrote, so verify accepts it too."""

    code, out, err = run_cli(workdir.argv("verify", gateway.base_url, server_config=SERVER_CONFIG))
    assert code == 0, out + err
    assert "endpoint-reachable" in out and "inventory-tools: exact" in out

    code, _, err = run_cli(workdir.argv("verify", gateway.base_url, server_config=None))
    assert code == 2
    assert "an MCP endpoint is required for verify" in err and "--server-config-name" in err


def test_apply_never_reports_the_gateway_token(workdir: Workdir) -> None:
    secret = "GWSENTINEL" + "z" * 30
    workdir.token.write_text(secret + "\n", encoding="utf-8")
    workdir.token.chmod(0o600)
    with RecordedGateway(
        runtime_tools=tuple(TOOLS), runtime_resources=tuple(RESOURCES),
        source_revision=SOURCE_REVISION, bundle_version=BUNDLE_VERSION,
        policy_provider="IgnitionMCPPolicy",
    ) as fake:
        code, out, err = run_cli(workdir.argv(
            "apply", fake.base_url, policy_file=workdir.policy, permissions_file=workdir.permissions,
        ))
    assert code == 1
    assert secret not in out and secret not in err
