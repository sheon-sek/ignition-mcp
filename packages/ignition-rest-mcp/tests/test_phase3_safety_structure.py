"""Slice 6 (Phase 3 / G3): static structural invariants of the D08 safety chain.

These scans prove the *shape* of the mutation boundary in production code only:
one write primitive, one auth-minted principal, one guarded executor, destructive
registrations that match their contracts and a zero-mutation effective Tool
inventory in every gate configuration.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.client.gateway import GatewayClient
from test_config import _settings

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "ignition_rest_mcp"

# The only two production modules allowed to touch the write transport.
WRITE_BOUNDARY_FILES = frozenset({"client/gateway.py", "safety/executor.py"})

WRITE_METHOD_CALLS = frozenset({"post", "put", "patch", "delete", "request"})

#: The setup-native CLI is code-separated from the server (D25): it never imports
#: the Gateway transport and only probes documented read-only REST routes plus the
#: Runtime MCP endpoint (whose JSON-RPC wire requires POST). The name-based write
#: scan below would flag the CLI's own ``request`` RPC helper, so the subtree is
#: excluded here and pinned GET-only by test_cli_gateway_probes_are_get_only.
SCAN_EXCLUDED_PREFIXES = ("cli/",)

READ_TOOLS = frozenset({
    "gateway_info",
    "gateway_diagnose",
    "project_list",
    "config_resource_search",
    "config_resource_describe",
    "config_resource_names",
    "config_resource_list",
    "config_resource_get",
    "audit_query",
    "alarm_pipeline_list",
    "alarm_pipeline_status",
    "project_export",
    "tag_config_export",
    "artifact_list",
    "artifact_info",
    "operation_diagnose",
    "perspective_view_list",
    "perspective_view_get",
    "perspective_view_validate",
    "perspective_page_config_get",
    "perspective_session_props_get",
})

MUTATION_TOOLS = frozenset({
    "project_import",
    "tag_config_import",
    "artifact_delete",
    "config_resource_create",
    "config_resource_update",
    "config_resource_delete",
    "config_resource_rename",
    "alarm_pipeline_cancel",
    "perspective_view_upsert",
    "perspective_view_delete",
    "perspective_page_config_update",
    "perspective_session_props_update",
})


def _production_files() -> list[Path]:
    files = sorted(p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, "the production source tree must be present for structural scans"
    return files


def _relative(path: Path) -> str:
    return path.relative_to(SRC_ROOT).as_posix()


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ------------------------------------------------------------------ 1. write primitive

def test_dispatch_write_is_referenced_only_by_gateway_and_executor() -> None:
    offenders: list[str] = []
    for path in _production_files():
        rel = _relative(path)
        if rel in WRITE_BOUNDARY_FILES:
            continue
        for node in ast.walk(_parse(path)):
            if (
                (isinstance(node, ast.Name) and node.id == "dispatch_write")
                or (isinstance(node, ast.Attribute) and node.attr == "dispatch_write")
                or (isinstance(node, ast.alias) and node.name == "dispatch_write")
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"dispatch_write leaked outside the write boundary: {offenders}"


# ------------------------------------------------------------------ 2. mintable principal

def _is_principal_construction(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "VerifiedPrincipal":
        return True
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "VerifiedPrincipal"
    )


def test_verified_principal_is_constructed_only_inside_auth() -> None:
    offenders: list[str] = []
    for path in _production_files():
        rel = _relative(path)
        if rel == "auth.py":
            continue
        for node in ast.walk(_parse(path)):
            if _is_principal_construction(node):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"VerifiedPrincipal was constructed outside auth.py: {offenders}"


def test_principal_token_is_confined_to_auth() -> None:
    offenders: list[str] = []
    for path in _production_files():
        rel = _relative(path)
        if rel == "auth.py":
            continue
        for node in ast.walk(_parse(path)):
            if (
                (isinstance(node, ast.Name) and node.id == "_PRINCIPAL_TOKEN")
                or (isinstance(node, ast.alias) and node.name == "_PRINCIPAL_TOKEN")
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"_PRINCIPAL_TOKEN leaked outside auth.py: {offenders}"


# ------------------------------------------------------------------ 3. transport boundary

def test_no_httpx_write_method_calls_outside_the_write_boundary() -> None:
    offenders: list[str] = []
    for path in _production_files():
        rel = _relative(path)
        if rel in WRITE_BOUNDARY_FILES or rel.startswith(SCAN_EXCLUDED_PREFIXES):
            continue
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in WRITE_METHOD_CALLS
            ):
                offenders.append(f"{rel}:{node.lineno}:{node.func.attr}")
    assert offenders == [], f"direct HTTP write call sites outside the boundary: {offenders}"


def test_cli_gateway_probes_are_get_only_and_post_targets_the_mcp_endpoint() -> None:
    """Compensating pin for the D25 CLI exclusion: the only write-shaped traffic
    in ``cli/`` is the MCP JSON-RPC POST, and it can target nothing but the
    operator-supplied Runtime MCP endpoint URL."""

    cli_dir = SRC_ROOT / "cli" / "setup_native"
    assert cli_dir.is_dir(), "the setup-native CLI package must exist"
    for path in sorted(cli_dir.glob("*.py")):
        for node in ast.walk(_parse(path)):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"post", "put", "patch", "delete", "stream"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.upper() != "GET"
            ):
                continue
            assert path.name == "mcp_http.py" and str(node.args[0].value).upper() == "POST", (
                f"{path.name}:{node.lineno} issues {node.args[0].value} outside the MCP JSON-RPC client"
            )
            url = node.args[1] if len(node.args) > 1 else None
            target = url.value if isinstance(url, ast.Attribute) else None
            assert isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
            assert target.value.id == "self" and target.attr == "endpoint", (
                f"{path.name}:{node.lineno} POSTs somewhere other than its own MCP endpoint"
            )
    # GatewayRest's transport chokepoint is _request; every call site must be a literal GET.
    offenders: list[int] = []
    for node in ast.walk(_parse(cli_dir / "gateway.py")):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_request"
        ):
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Constant) and first.value == "GET"):
                offenders.append(node.lineno)
    assert offenders == [], f"gateway.py _request call sites with a non-GET method: {offenders}"


def test_cli_write_routes_are_the_curated_set_of_ticket_21() -> None:
    """Ticket #21: ``apply`` writes through one guarded module with named routes.

    The CLI is excluded from the write-transport scan above (D25 code separation),
    so its write half is pinned here instead: the route constants are exactly the
    documented operations ``apply`` needs, every write dispatch goes through the
    single ``_write`` chokepoint, and no other module in the package issues a
    non-GET transport call. The only GET-shaped transport call in ``writer.py`` is
    the bounded Project export.
    """

    cli_dir = SRC_ROOT / "cli" / "setup_native"
    writer = cli_dir / "writer.py"
    tree = _parse(writer)

    routes: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith("_PATH"):
                    routes[target.id] = str(node.value.value)
    assert routes == {
        "PROJECT_IMPORT_PATH": "/data/api/v1/projects/import/{name}",
        "PROJECT_EXPORT_PATH": "/data/api/v1/projects/export/{name}",
        "RESOURCE_COLLECTION_PATH": "/data/api/v1/resources/{resource_type}",
        "TAG_IMPORT_PATH": "/data/api/v1/tags/import",
        # Ticket #22's opt-in credential: the Gateway's own key/hash generator.
        "API_TOKEN_GENERATE_PATH": "/data/api/v1/api-token/generate",
    }, sorted(routes)

    chokepoints = {"_write": {"POST", "PUT"}, "_archive": {"GET"}}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in chokepoints):
            continue
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            if call.func.attr not in WRITE_METHOD_CALLS | {"stream"}:
                continue
            method = call.args[0] if call.args else None
            if isinstance(method, ast.Constant):
                allowed = chokepoints[node.name]
                assert method.value in allowed, f"{node.name} issues {method.value}"
            else:
                # A non-literal method may only be the parameter the chokepoint received.
                assert isinstance(method, ast.Name) and method.id == "method", (
                    f"{node.name}:{call.lineno} dispatches an unvetted method"
                )

    for path in sorted(cli_dir.glob("*.py")):
        if path.name in ("writer.py", "mcp_http.py"):
            continue
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in WRITE_METHOD_CALLS
            for node in ast.walk(_parse(path))
        ), f"{path.name} issues a write-shaped transport call"


# ------------------------------------------------------------------ 4. destructive declarations

def test_destructive_registrations_match_the_tool_contracts() -> None:
    """A Tool's ``_invoke`` declaration must be the one its contract publishes.

    Phase 3 registered no destructive Tool at all; Phase 4 adds
    ``config_resource_delete`` and ``project_import`` (D26). Rather than weakening that
    invariant, the scan ties every registration to the contract it ships with, so a
    destructive Tool can never be registered as harmless — or the reverse — anywhere.
    """

    repo_root = SRC_ROOT.parents[3]
    contracts = {
        path.name[: -len(".contract.json")]: json.loads(
            path.read_text(encoding="utf-8"),
        ).get("destructive", False)
        for path in sorted((repo_root / "contracts" / "tools" / "rest").glob("*.contract.json"))
    }
    tree = _parse(SRC_ROOT / "server.py")
    create = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "create_server"
    )
    registered: dict[str, bool] = {}
    for node in ast.walk(create):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_invoke"):
            continue
        name = node.args[0] if node.args else None
        assert isinstance(name, ast.Constant) and isinstance(name.value, str), (
            "every _invoke call must name its Tool with a literal"
        )
        declared = next(
            (keyword.value.value for keyword in node.keywords if keyword.arg == "destructive"),
            False,
        )
        assert isinstance(declared, bool)
        registered[name.value] = declared

    assert registered, "_invoke call sites must be discoverable in create_server"
    mismatched = {
        name: declared for name, declared in registered.items()
        if contracts.get(name) is not declared
    }
    assert mismatched == {}, f"registrations disagree with their contracts: {mismatched}"
    assert [name for name, declared in registered.items() if declared] == [
        "config_resource_delete", "project_import", "alarm_pipeline_cancel", "artifact_delete",
        "perspective_view_delete",
    ]


def test_only_the_local_artifact_mutation_declares_itself_not_gateway_backed() -> None:
    """D08's capability layer must keep asking the D04 registry for every Gateway
    write. Exactly one production operation may turn that layer into a local
    subsystem: `artifact_delete`, whose HTTP route D30 drops, so it has no route to
    check and dispatches nothing (the write boundary scan above keeps that true).
    """

    local_operations: list[str] = []
    for path in _production_files():
        rel = _relative(path)
        for node in ast.walk(_parse(path)):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "MutationOperation"
            ):
                continue
            declared = [
                keyword.value.value for keyword in node.keywords
                if keyword.arg == "gateway_backed" and isinstance(keyword.value, ast.Constant)
            ]
            if declared == [False]:
                local_operations.append(f"{rel}:{node.lineno}")

    assert [site.split(":")[0] for site in local_operations] == ["services/artifact_delete.py"], (
        f"only the D30 routeless artifact Mutation may skip the Gateway capability layer: "
        f"{local_operations}"
    )


# ------------------------------------------------------------------ 5. zero-mutation inventory

def _full_read_openapi() -> bytes:
    return json.dumps({"paths": {
        "/data/api/v1/gateway-info": {"get": {}},
        "/data/api/v1/projects/list": {"get": {}},
        "/data/api/v1/audit/log/{name}": {"get": {}},
        "/data/alarm-notification/api/v1/pipelines": {"get": {}},
        "/data/alarm-notification/api/v1/pipeline": {"get": {}},
        "/data/api/v1/projects/export/{name}": {"get": {}},
        "/data/api/v1/tags/export": {"get": {}},
        "/data/api/v1/resources/type/tag/Tag": {"get": {}},
        "/data/api/v1/resources/names/tag/Tag": {"get": {}},
        "/data/api/v1/resources/list/tag/Tag": {"get": {}},
        "/data/api/v1/resources/find/tag/Tag/{name}": {"get": {}},
    }}).encode()


@pytest.fixture
def stub_full_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    async def info(self: GatewayClient, context: Any = None) -> dict[str, str]:
        return {"ignitionVersion": "8.3.8"}

    async def modules(self: GatewayClient, context: Any = None) -> dict[str, list[Any]]:
        return {"items": []}

    async def openapi(self: GatewayClient) -> bytes:
        return _full_read_openapi()

    monkeypatch.setattr(GatewayClient, "gateway_info", info)
    monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
    monkeypatch.setattr(GatewayClient, "openapi", openapi)


def test_effective_tool_inventory_has_zero_mutation_tools_in_every_gate_configuration(
    tmp_path: Path, stub_full_gateway: None,
) -> None:
    async def listed(settings: Any) -> set[str]:
        server = server_module.create_server(settings)
        async with Client(server) as client:
            first = {tool.name for tool in await client.list_tools()}
            second = {tool.name for tool in await client.list_tools()}
        assert first == second, "the effective inventory must be stable across listings"
        return first

    def visible(**overrides: Any) -> set[str]:
        settings = _settings(data_dir=str(tmp_path), **overrides)
        return asyncio.run(listed(settings))

    for gates_on in (False, True):
        names = visible(sensitive_exports_enabled=gates_on)
        assert names <= READ_TOOLS, f"unexpected Tools in inventory: {sorted(names - READ_TOOLS)}"
        assert not (names & MUTATION_TOOLS)
        if gates_on:
            # The stub advertises every read capability: the inventory must actually be
            # populated, so the subset assertion above is not passing on an empty list.
            assert names == READ_TOOLS


def test_mutation_policy_inventory_stays_fully_gated_off_by_default() -> None:
    default = _settings()
    assert default.config_mutation_enabled is False
    assert default.control_mutation_enabled is False
    assert default.admin_mutation_enabled is False
    assert default.mutation_operations == ()
    assert default.mutation_targets == {}
