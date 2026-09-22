"""Phase 4 ticket #22: ``setup-native`` opt-in Security Level and API token (D09, D20).

Both writes are opt-in and both fail closed on anything already there. These cases
drive the shipped CLI against the recorded Gateway (``tests/harness/recorded_gateway.py``),
which now models the Security Levels singleton, the API-token create route and the
Gateway's own ``{key, hash}`` generator, and they assert the observable outcome — what
the Gateway was asked to write, what it serves now, what the operator's file holds, and
what the command printed.

Covered: no flags leave the plan and the Gateway untouched; the flags add exactly two
plan lines and write them in D20's order; the created level keeps the rest of the tree
and the created token carries the dedicated level grant and the Gateway's own hash; the
secret lands in a ``0600`` file and appears in no output; a second run is ``NO CHANGE``
and leaves the file byte-identical; an existing token, an existing level of another
shape, an existing credential file and a group-readable file are all ``BLOCKED`` with
nothing written; a stale signature, an inconsistent generated pair and a Gateway refusal
stop the sequence; and the flags are validated as usage errors.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Iterator

import pytest

from ignition_rest_mcp.cli.setup_native import main as cli_main
from ignition_rest_mcp.cli.setup_native import plan as plan_module
from ignition_rest_mcp.cli.setup_native import security as security_module
from ignition_rest_mcp.cli.setup_native import writer as writer_module
from ignition_rest_mcp.cli.setup_native.inputs import Endpoint, Inputs, UsageError, load_inputs

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests/harness"))

from recorded_gateway import (  # noqa: E402
    API_TOKEN,
    API_TOKEN_TYPE,
    GENERATED_API_TOKEN_HASH,
    GENERATED_API_TOKEN_KEY,
    SECURITY_LEVELS_TYPE,
    RecordedGateway,
)

BUNDLE_VERSION = "0.6.0"
SOURCE_REVISION = "c" * 40
PROJECT = "ignition_runtime_credentials"
SERVER_CONFIG = "phase4-credentials"
PROFILE = "readonly"
LEVEL = f"IgnitionMcpRuntime{PROFILE.capitalize()}"
LEVEL_PATH = f"Authenticated/{LEVEL}"
LEVEL_DESCRIPTION = f"ignition-mcp Runtime MCP Security Level for the {PROFILE} profile (deployment-owned)."
TOOLS = ["bundle_info", "tag_read"]
SECRET = f"{SERVER_CONFIG}:{GENERATED_API_TOKEN_KEY}"
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


# ------------------------------------------------------------------------- fixtures


def project_archive(version: str = BUNDLE_VERSION) -> bytes:
    description = (
        "Runtime MCP Bundle.\n"
        f"ignition-mcp-managed: product=ignition-runtime-bundle; bundle={version}"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", json.dumps({
            "title": "Runtime MCP Bundle", "description": description, "enabled": True, "inheritable": False,
        }))
        archive.writestr("com.inductiveautomation.mcp/tools/bundle_info/resource.json", b"{}")
    return buffer.getvalue()


def make_manifest(payload: bytes, version: str = BUNDLE_VERSION) -> dict[str, Any]:
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
        "resources": [],
        "prompts": [],
        "toolRequirements": {
            name: {"budgetClass": "FAST", "permissionClass": "READ", "nativeRequirements": ["system.tag.readBlocking"]}
            for name in TOOLS
        },
        "profileInventories": {
            "readonly": profile(["READ"], sorted(TOOLS), []),
            "operator": profile(["CONTROL", "READ"], sorted(TOOLS), []),
            "configurator": profile(["CONFIG", "READ"], sorted(TOOLS), []),
            "full": profile(["CONFIG", "CONTROL", "READ"], sorted(TOOLS), []),
        },
        "testedTuples": [],
    }


class Workdir:
    """The operator-side artifacts one run needs, including where a secret may land."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        payload = project_archive()
        self.manifest = tmp_path / "bundle.manifest.json"
        self.manifest.write_text(json.dumps(make_manifest(payload), sort_keys=True), encoding="utf-8")
        self.archive = tmp_path / "bundle.zip"
        self.archive.write_bytes(payload)
        self.policy = tmp_path / "policy.json"
        self.policy.write_text(json.dumps(POLICY, sort_keys=True), encoding="utf-8")
        self.permissions = tmp_path / "permissions.json"
        self.permissions.write_text(json.dumps(PERMISSIONS, sort_keys=True), encoding="utf-8")
        self.token = tmp_path / "gateway.token"
        self.token.write_text(API_TOKEN + "\n", encoding="utf-8")
        self.token.chmod(0o600)
        self.secret = tmp_path / "runtime.token"

    def argv(
        self,
        command: str,
        base: str,
        *,
        levels: bool = True,
        token: bool = True,
        token_file: Path | None = None,
        extra: tuple[str, ...] = (),
    ) -> list[str]:
        args = [
            "setup-native", command,
            "--bundle-manifest", str(self.manifest),
            "--bundle-zip", str(self.archive),
            "--gateway-url", base,
            "--gateway-token-file", str(self.token),
            "--mcp-token-file", str(self.token),
            "--profile", PROFILE,
            "--bundle-project", PROJECT,
            "--server-config-name", SERVER_CONFIG,
            "--policy-file", str(self.policy),
            "--server-config-permissions-file", str(self.permissions),
        ]
        if levels:
            args += ["--provision-security-levels"]
        if token:
            args += ["--create-runtime-token", "--runtime-token-file", str(token_file or self.secret)]
        return args + list(extra)

    def flags(self, command: str, base: str, **kwargs: Any) -> list[str]:
        """The same argv without the ``setup-native <command>`` words, for ``load_inputs``."""

        return self.argv(command, base, **kwargs)[2:]


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main.main(argv)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def gateway() -> Iterator[RecordedGateway]:
    with RecordedGateway(
        runtime_tools=tuple(TOOLS),
        runtime_resources=(),
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


def token_writes(gateway: RecordedGateway) -> list[dict[str, Any]]:
    return [
        dict(json.loads(request["body"])[0])
        for request in gateway.requests
        if request["method"] == "POST" and str(request["path"]) == f"/data/api/v1/resources/{API_TOKEN_TYPE}"
    ]


def token_body(gateway: RecordedGateway) -> dict[str, Any]:
    bodies = token_writes(gateway)
    assert bodies, "no API token write reached the Gateway"
    return bodies[0]


def level_tree(gateway: RecordedGateway) -> list[dict[str, Any]]:
    tree = gateway.security_levels()
    assert tree is not None, "the fixture serves no security tree"
    return tree


def find(tree: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for node in tree:
        if isinstance(node, dict) and node.get("name") == name:
            return node
    return None


def seeded_level_tree(*children: dict[str, Any]) -> dict[str, Any]:
    return {"securityLevels": [{"name": "Authenticated", "children": list(children)}]}


# ------------------------------------------------------------- no flags: unchanged


def test_without_the_flags_the_plan_detects_only_and_writes_nothing(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    code, out, err = run_cli(workdir.argv("plan", gateway.base_url, levels=False, token=False))
    assert code == 0 and err == ""
    assert "NO CHANGE security-level Gateway security level: detect-only, provisioning is apply-phase" in out
    assert "NO CHANGE runtime-token Ignition API token for the MCP service user" in out
    assert plan_module.PLAN_SENTINEL in out
    assert writes(gateway) == []
    assert not workdir.secret.exists()
    # Nothing in this run reasons about the security planes: not even a read of them.
    assert not any(
        str(request["path"]).startswith("/data/api/v1/resources/singleton/") for request in gateway.requests
    )
    assert not any(
        str(request["path"]) == "/data/api/v1/api-token/generate" for request in gateway.requests
    )


def test_with_the_flags_the_plan_shows_the_two_lines_and_still_writes_nothing(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    code, out, err = run_cli(workdir.argv("plan", gateway.base_url))
    assert code == 0 and err == ""
    assert f"CREATE security-level {LEVEL_PATH}: add the dedicated Runtime level under Authenticated" in out
    assert f"CREATE runtime-token {SERVER_CONFIG}: create API token granted {LEVEL_PATH}" in out
    assert "mode 0600 and never reported" in out
    assert plan_module.PLAN_SENTINEL in out
    assert writes(gateway) == []
    assert not workdir.secret.exists()


# ------------------------------------------------------------------- the happy path


def test_apply_creates_the_level_the_token_and_the_secret(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    code, out, err = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 0, out + err
    assert f"WRITE security-level {LEVEL_PATH} [CREATE]" in out
    assert f"WRITE runtime-token {SERVER_CONFIG} [CREATE]" in out
    assert "apply: wrote=5 skipped=0 failed=0 => exit 0" in out

    # D20's order: the level, then the credential, and only then the deployment.
    assert writes(gateway)[:3] == [
        ("PUT", f"/data/api/v1/resources/{SECURITY_LEVELS_TYPE}"),
        ("POST", "/data/api/v1/api-token/generate"),
        ("POST", f"/data/api/v1/resources/{API_TOKEN_TYPE}"),
    ]

    # The dedicated level is a leaf under Authenticated; the sibling level survives.
    authenticated = find(level_tree(gateway), "Authenticated")
    assert authenticated is not None
    assert [node["name"] for node in authenticated["children"]] == ["Roles", LEVEL]
    assert find(authenticated["children"], LEVEL) == {
        "name": LEVEL, "description": LEVEL_DESCRIPTION, "children": [],
    }

    # The token is granted exactly that level, carries the Gateway's own hash, and
    # requires a secure channel unless the operator said this is a plain-HTTP lab.
    item = token_body(gateway)
    assert item["name"] == SERVER_CONFIG and item["enabled"] is True
    assert item["config"]["profile"]["type"] == "basic-token"
    assert item["config"]["profile"]["secureChannelRequired"] is True
    assert isinstance(item["config"]["profile"]["timestamp"], int)
    grant = item["config"]["profile"]["securityLevels"]
    assert [node["name"] for node in grant] == ["Authenticated"]
    assert [node["name"] for node in grant[0]["children"]] == [LEVEL]
    assert grant[0]["children"][0]["children"] == []
    assert item["config"]["settings"]["tokenHash"] == GENERATED_API_TOKEN_HASH

    # The secret exists once, mode 0600, and is the credential the operator will send.
    assert workdir.secret.read_text(encoding="utf-8") == SECRET + "\n"
    assert os.stat(workdir.secret).st_mode & 0o777 == 0o600


def test_the_secret_never_reaches_any_output(gateway: RecordedGateway, workdir: Workdir) -> None:
    """D20: the secret is written to the operator's file and nowhere else."""

    code, out, err = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 0
    assert GENERATED_API_TOKEN_KEY not in out + err
    assert SECRET not in out
    assert str(workdir.secret) in out  # the location is reported; the credential is not.

    code, out, err = run_cli(workdir.argv("apply", gateway.base_url, extra=("--json",)))
    assert code == 0
    report = json.loads(out.split(plan_module.PLAN_SENTINEL)[0])
    assert GENERATED_API_TOKEN_KEY not in out + err
    assert report["securityLevel"] == LEVEL_PATH
    assert report["runtimeToken"] == {
        "name": SERVER_CONFIG,
        "secretFile": str(workdir.secret),
        "secretFileMode": "0600",
        "secureChannelRequired": True,
    }
    assert [
        write["action"] for write in report["writes"] if write["kind"] in ("security-level", "runtime-token")
    ] == ["NO CHANGE", "NO CHANGE"]


# ------------------------------------------------------------------------ idempotency


def test_a_second_run_is_no_change_and_leaves_the_secret_alone(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    assert run_cli(workdir.argv("apply", gateway.base_url))[0] == 0
    before_writes = writes(gateway)
    secret = workdir.secret.read_bytes()
    inode = os.stat(workdir.secret).st_ino

    code, out, err = run_cli(workdir.argv("plan", gateway.base_url))
    assert code == 0 and err == ""
    assert f"NO CHANGE security-level {LEVEL_PATH}: the dedicated Runtime level is already present" in out
    assert f"NO CHANGE runtime-token {SERVER_CONFIG}: the Runtime API token already exists and matches" in out

    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 0
    assert "apply: wrote=0 skipped=5 failed=0 => exit 0" in out
    assert writes(gateway) == before_writes
    assert workdir.secret.read_bytes() == secret
    assert os.stat(workdir.secret).st_ino == inode


# ------------------------------------------------------------------- never overwrite


def test_an_existing_token_whose_secret_is_unknown_is_blocked(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(
        API_TOKEN_TYPE, SERVER_CONFIG,
        config={"profile": {"type": "basic-token", "secureChannelRequired": True,
                            "securityLevels": [{"name": "Authenticated"}], "timestamp": 1},
                "settings": {"tokenHash": "somebody-elses-hash"}},
    )
    code, out, err = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 3 and err == ""
    assert "BLOCKED runtime-token" in out and "refusing to overwrite it" in out
    assert writes(gateway) == []
    assert not workdir.secret.exists()


def test_an_existing_token_whose_stored_hash_is_unreadable_is_blocked(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(API_TOKEN_TYPE, SERVER_CONFIG, config={"profile": {"type": "basic-token"}})
    code, out, _ = run_cli(workdir.argv("plan", gateway.base_url))
    assert code == 3
    assert "already exists and the Gateway serves no readable token hash" in out
    assert writes(gateway) == []


def test_an_existing_token_matching_the_secret_file_is_left_unchanged(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(
        API_TOKEN_TYPE, SERVER_CONFIG,
        config={"profile": {"type": "basic-token", "secureChannelRequired": True,
                            "securityLevels": [{"name": "Authenticated"}], "timestamp": 1},
                "settings": {"tokenHash": GENERATED_API_TOKEN_HASH}},
    )
    workdir.secret.write_text(SECRET + "\n", encoding="utf-8")
    workdir.secret.chmod(0o600)

    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 0, out
    assert "the Runtime API token already exists and matches" in out
    assert token_writes(gateway) == []
    assert workdir.secret.read_text(encoding="utf-8") == SECRET + "\n"


def test_a_secret_file_holding_another_credential_is_blocked(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(
        API_TOKEN_TYPE, SERVER_CONFIG,
        config={"profile": {"type": "basic-token"}, "settings": {"tokenHash": GENERATED_API_TOKEN_HASH}},
    )
    workdir.secret.write_text(f"another-token:{GENERATED_API_TOKEN_KEY}\n", encoding="utf-8")
    workdir.secret.chmod(0o600)
    code, out, _ = run_cli(workdir.argv("plan", gateway.base_url))
    assert code == 3
    assert "holds the credential 'another-token'" in out
    assert writes(gateway) == []


def test_an_existing_credential_file_without_a_token_is_never_overwritten(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    workdir.secret.write_text("stale:AAAA\n", encoding="utf-8")
    workdir.secret.chmod(0o600)
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 3
    assert "--runtime-token-file already holds a credential ('stale')" in out
    assert workdir.secret.read_text(encoding="utf-8") == "stale:AAAA\n"
    # The level line itself is a CREATE, but apply writes nothing while one line is BLOCKED.
    assert writes(gateway) == []


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o666])
def test_a_group_or_world_readable_credential_file_is_blocked(
    gateway: RecordedGateway, workdir: Workdir, mode: int
) -> None:
    workdir.secret.write_text(SECRET + "\n", encoding="utf-8")
    workdir.secret.chmod(mode)
    code, out, _ = run_cli(workdir.argv("plan", gateway.base_url))
    assert code == 3
    assert "accessible to group or others" in out
    assert writes(gateway) == []


def test_a_missing_secret_directory_is_blocked_before_any_write(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    missing = workdir.root / "nope" / "runtime.token"
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url, token_file=missing))
    assert code == 3
    assert "is not a directory" in out
    assert writes(gateway) == []
    assert not missing.exists()


# -------------------------------------------------------------- existing levels


def test_an_existing_level_of_another_shape_is_never_modified(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(
        SECURITY_LEVELS_TYPE, "security-levels",
        config=seeded_level_tree({"name": LEVEL, "children": [{"name": "Operator made", "children": []}]}),
    )
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 3
    assert "already exists but it carries 1 child level(s)" in out
    assert "this CLI never modifies an existing Security Level" in out
    assert writes(gateway) == []
    assert not workdir.secret.exists()


def test_a_level_of_our_name_elsewhere_in_the_tree_is_refused(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(
        SECURITY_LEVELS_TYPE, "security-levels",
        config={"securityLevels": [
            {"name": "Authenticated", "children": [{"name": "Roles", "children": []}]},
            {"name": LEVEL, "children": []},
        ]},
    )
    code, out, _ = run_cli(workdir.argv("plan", gateway.base_url))
    assert code == 3
    assert f"a level named {LEVEL} already exists at {LEVEL}" in out
    assert writes(gateway) == []


def test_a_tree_without_the_authenticated_parent_is_refused(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(
        SECURITY_LEVELS_TYPE, "security-levels",
        config={"securityLevels": [{"name": "Public", "children": []}]},
    )
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 3
    assert "security tree has no Authenticated level" in out
    assert "granted the dedicated Security Level" in out
    assert writes(gateway) == []
    assert not workdir.secret.exists()


def test_an_unreadable_security_singleton_is_blocked(gateway: RecordedGateway, workdir: Workdir) -> None:
    gateway.seed_resource(SECURITY_LEVELS_TYPE, "security-levels", config={"securityLevels": "nonsense"})
    code, out, _ = run_cli(workdir.argv("plan", gateway.base_url))
    assert code == 3
    assert "carries no readable securityLevels tree" in out
    assert writes(gateway) == []


def test_a_token_without_the_provisioning_flag_needs_the_level_to_exist(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url, levels=False))
    assert code == 3
    assert "does not exist and --provision-security-levels was not passed" in out
    assert writes(gateway) == []
    assert not workdir.secret.exists()


def test_a_token_can_be_granted_a_level_that_already_exists(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.seed_resource(
        SECURITY_LEVELS_TYPE, "security-levels",
        config=seeded_level_tree({"name": "Roles", "children": []}, {"name": LEVEL, "children": []}),
    )
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url, levels=False))
    assert code == 0, out
    assert "WRITE runtime-token" in out
    assert not any(
        request["method"] == "PUT" and str(request["path"]) == f"/data/api/v1/resources/{SECURITY_LEVELS_TYPE}"
        for request in gateway.requests
    )
    assert workdir.secret.read_text(encoding="utf-8") == SECRET + "\n"
    assert [node["name"] for node in token_body(gateway)["config"]["profile"]["securityLevels"][0]["children"]] == [LEVEL]


# ---------------------------------------------------------------- failure handling


def test_a_stale_security_tree_is_refused_by_the_precondition(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    """Another writer changes the tree between the read and the write (D20's precondition)."""

    gateway.race_write_with("update", config={"securityLevels": [{"name": "Authenticated", "children": []}]})
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 1
    assert "FAILED security-level" in out and "signature" in out.lower()
    assert token_writes(gateway) == []
    assert not workdir.secret.exists()


def test_an_inconsistent_generated_pair_is_never_stored(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.answer_api_token_generation_with(
        {"key": GENERATED_API_TOKEN_KEY, "hash": GENERATED_API_TOKEN_HASH[:-1] + "X"},
    )
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 1
    assert "disagrees with the documented derivation" in out
    assert "FAILED runtime-token" in out
    assert token_writes(gateway) == []
    assert not workdir.secret.exists()


def test_a_garbage_generated_body_is_never_stored(gateway: RecordedGateway, workdir: Workdir) -> None:
    gateway.answer_api_token_generation_with({"key": "not base64url!", "hash": "x"})
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 1
    assert "FAILED runtime-token" in out
    assert not workdir.secret.exists()


def test_a_gateway_refusal_stops_before_the_secret_is_stored(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    gateway.refuse_writes_with("create", "the API token could not be written")
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url))
    assert code == 1
    assert f"WRITE security-level {LEVEL_PATH} [CREATE]" in out
    assert "FAILED runtime-token" in out and "refused by the Gateway" in out
    assert not workdir.secret.exists()
    # The level stayed written (D20: no rollback), and the run still verified.
    authenticated = find(level_tree(gateway), "Authenticated")
    assert authenticated is not None and authenticated["children"][-1]["name"] == LEVEL
    assert "verify: verified=" in out


# ------------------------------------------------------------------------ validation


def test_the_opt_in_flags_are_validated(workdir: Workdir, gateway: RecordedGateway) -> None:
    with pytest.raises(UsageError, match="--create-runtime-token needs --runtime-token-file"):
        load_inputs(workdir.flags("apply", gateway.base_url, levels=False, token=False,
                                  extra=("--create-runtime-token",)), "apply")
    with pytest.raises(UsageError, match="no effect without --create-runtime-token"):
        load_inputs(workdir.flags("apply", gateway.base_url, levels=False, token=False,
                                  extra=("--runtime-token-file", str(workdir.secret))), "apply")
    with pytest.raises(UsageError, match="--security-level-name has no effect"):
        load_inputs(workdir.flags("apply", gateway.base_url, levels=False, token=False,
                                  extra=("--security-level-name", "OperatorMade")), "apply")


def test_a_runtime_token_needs_a_name(workdir: Workdir, gateway: RecordedGateway) -> None:
    args = [
        item for item in workdir.flags("apply", gateway.base_url, levels=False, token=False)
        if item not in ("--server-config-name", SERVER_CONFIG)
    ]
    args += ["--create-runtime-token", "--runtime-token-file", str(workdir.secret)]
    with pytest.raises(UsageError, match="--runtime-token-name"):
        load_inputs(args, "apply")


def test_a_named_token_and_an_insecure_channel_are_honoured(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    other = workdir.root / "other.token"
    code, out, _ = run_cli(workdir.argv("apply", gateway.base_url, token_file=other, extra=(
        "--runtime-token-name", "ignition-mcp-lab",
        "--runtime-token-insecure-channel",
    )))
    assert code == 0, out
    item = token_body(gateway)
    assert item["name"] == "ignition-mcp-lab"
    assert item["config"]["profile"]["secureChannelRequired"] is False
    assert other.read_text(encoding="utf-8") == f"ignition-mcp-lab:{GENERATED_API_TOKEN_KEY}\n"
    assert not workdir.secret.exists()


def test_a_custom_level_name_is_honoured(gateway: RecordedGateway, workdir: Workdir) -> None:
    custom = "IgnitionMcpRuntimeLab"
    code, out, _ = run_cli(workdir.argv("plan", gateway.base_url, extra=("--security-level-name", custom)))
    assert code == 0
    assert f"CREATE security-level Authenticated/{custom}" in out
    assert f"granted Authenticated/{custom}" in out


# --------------------------------------------------------------- document helpers


def test_the_managed_level_shape_and_the_grant_are_minimal(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    """The pure pieces D20 leans on: insertion, path finding, the granted tree."""

    inputs = load_inputs(workdir.flags("plan", gateway.base_url), "plan")
    assert isinstance(inputs, Inputs)
    tree: list[dict[str, Any]] = [{
        "name": "Authenticated",
        "description": "authenticated",
        "children": [{"name": "Roles", "children": []}],
    }]
    merged, error = security_module.with_managed_level(tree, inputs)
    assert error == "" and merged is not None
    assert merged[0]["children"][-1] == security_module.desired_level(inputs)
    # The observed tree is never mutated in place.
    assert len(tree[0]["children"]) == 1

    grant = security_module.token_grant(inputs, tree, creating=True)
    assert grant == [{
        "name": "Authenticated",
        "description": "authenticated",
        "children": [{"name": LEVEL, "description": LEVEL_DESCRIPTION, "children": []}],
    }]

    # A tree without the parent, and one with the parent twice, are both refusals.
    assert security_module.with_managed_level([{"name": "Public"}], inputs)[1].startswith(
        "the Gateway's security tree has no Authenticated level"
    )
    doubled = [*tree, {"name": "Authenticated", "children": []}]
    assert "2 top-level Authenticated levels" in security_module.with_managed_level(doubled, inputs)[1]


def test_the_readback_verification_reports_what_a_write_dropped(
    gateway: RecordedGateway, workdir: Workdir
) -> None:
    """D20's structural verification: the level, its path, and every level that survived."""

    inputs = load_inputs(workdir.flags("plan", gateway.base_url), "plan")
    before: list[dict[str, Any]] = [{
        "name": "Authenticated",
        "children": [{"name": "Roles", "children": []}, {"name": LEVEL, "children": []}],
    }]
    document = {"config": {"securityLevels": before}}
    assert security_module.verify_readback(document, before, inputs) == ""

    assert "is not in the served tree" in security_module.verify_readback(
        {"config": {"securityLevels": [{"name": "Authenticated", "children": []}]}}, before, inputs,
    )
    dropped = security_module.verify_readback(
        {"config": {"securityLevels": [{"name": "Authenticated", "children": [{"name": LEVEL, "children": []}]}]}},
        before, inputs,
    )
    assert dropped == "the write dropped existing levels: Authenticated.Roles"
    assert security_module.verify_readback(None, before, inputs).startswith("the security-levels singleton")


def test_the_token_hash_derivation_matches_the_live_rule() -> None:
    """G0's recorded pair: 32 key bytes, unpadded Base64URL SHA-256 of the decoded key."""

    assert security_module.token_hash(GENERATED_API_TOKEN_KEY) == GENERATED_API_TOKEN_HASH
    assert security_module.token_hash("not base64url!") == ""
    assert security_module.token_secret("svc", GENERATED_API_TOKEN_KEY) == f"svc:{GENERATED_API_TOKEN_KEY}"
    with pytest.raises(security_module.CredentialError):
        security_module.credential({"key": GENERATED_API_TOKEN_KEY, "hash": "wrong"})
    with pytest.raises(security_module.CredentialError):
        security_module.credential({})


def test_the_secret_file_is_created_with_0600_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "credential.token"
    old_umask = os.umask(0o777)
    try:
        security_module.write_secret_file(path, SECRET)
    finally:
        os.umask(old_umask)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert path.read_text(encoding="utf-8") == SECRET + "\n"
    with pytest.raises(security_module.FileError, match="refusing to overwrite"):
        security_module.write_secret_file(path, "other:value")
    assert path.read_text(encoding="utf-8") == SECRET + "\n"
    assert security_module.check_secret_file_target(path).endswith("refusing to overwrite a credential file")


def test_the_writer_refuses_an_unusable_token_document() -> None:
    endpoint = Endpoint(url="http://127.0.0.1:1", scheme="http", host="127.0.0.1", port=1)
    good: dict[str, Any] = {
        "profile": {
            "type": "basic-token", "secureChannelRequired": True,
            "securityLevels": [{"name": "Authenticated", "children": []}], "timestamp": 1,
        },
        "settings": {"tokenHash": GENERATED_API_TOKEN_HASH},
    }

    async def exercise() -> None:
        async with writer_module.GatewayWriter(endpoint, "token") as writer:
            item = writer._api_token_change("svc", good, "a description")
            assert item["name"] == "svc" and item["description"] == "a description"
            assert item["config"]["profile"]["type"] == "basic-token"
            for mutate, expected in (
                (lambda doc: doc["profile"].update({"type": "jwt"}), "basic-token profile"),
                (lambda doc: doc["profile"].update({"securityLevels": []}),
                 "granted an explicit security level"),
                (lambda doc: doc.update({"settings": {}}), "tokenHash"),
                (lambda doc: doc["profile"].pop("secureChannelRequired"), "secureChannelRequired"),
                (lambda doc: doc["profile"].pop("timestamp"), "timestamp"),
            ):
                document = json.loads(json.dumps(good))
                mutate(document)
                with pytest.raises(writer_module.WriteError, match=expected):
                    writer._api_token_change("svc", document, "a description")

    asyncio.run(exercise())
