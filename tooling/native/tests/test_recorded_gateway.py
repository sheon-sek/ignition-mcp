from __future__ import annotations

import asyncio
import io
from pathlib import Path
import runpy
import sys
import tempfile
import types
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))
sys.path.insert(0, str(ROOT / "tests/harness/phase3-live"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

from ignition_rest_mcp.cli.gateway_ops.inputs import Endpoint  # noqa: E402
from ignition_rest_mcp.cli.gateway_ops.mcp_http import (  # noqa: E402
    McpHttpClient,
    McpMethodNotFound,
)

MCP_HTTP = ROOT / "packages/ignition-rest-mcp/src/ignition_rest_mcp/cli/gateway_ops/mcp_http.py"
PHASE3_DRIVER = ROOT / "tests/harness/phase3-live/driver.py"
PHASE2_PROVISION = ROOT / "tests/harness/phase2-live/provision.py"


def _load_temp_module(path: Path, replacements: dict[str, str], name: str) -> types.ModuleType:
    source = path.read_text(encoding="utf-8")
    for current, reverted in replacements.items():
        assert current in source
        source = source.replace(current, reverted)
    with tempfile.TemporaryDirectory(prefix="recorded-gateway-revert-") as directory:
        temp_path = Path(directory) / path.relative_to(ROOT)
        temp_path.parent.mkdir(parents=True)
        temp_path.write_text(source, encoding="utf-8")
        module = types.ModuleType(name)
        module.__file__ = str(temp_path)
        sys.modules[name] = module
        exec(compile(source, str(temp_path), "exec"), module.__dict__)
    return module


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


def _endpoint(url: str) -> Endpoint:
    parsed = urlsplit(url)
    assert parsed.hostname is not None and parsed.port is not None
    return Endpoint(url=url, scheme=parsed.scheme, host=parsed.hostname, port=parsed.port)


def _zip_of(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def _gateway_request(url: str, method: str, body: bytes | None = None) -> bytes:
    request = Request(
        url,
        data=body,
        method=method,
        headers={"X-Ignition-API-Token": API_TOKEN, "Content-Type": "application/zip"},
    )
    with urlopen(request, timeout=2.0) as response:
        return response.read()


def test_ff7d610_api_token_header_is_required_by_recorded_module() -> None:
    async def exercise(client_type: type[Any], endpoint: Endpoint) -> dict[str, Any]:
        async with client_type(endpoint=endpoint, token=API_TOKEN, timeout_seconds=2.0) as client:
            return await client.initialize()

    with RecordedGateway() as gateway:
        endpoint = _endpoint(gateway.mcp_url)
        result = _run(exercise(McpHttpClient, endpoint))
        assert result["protocolVersion"] == "2025-06-18"

        reverted = _load_temp_module(
            MCP_HTTP,
            {
                "headers.update(self._auth_headers())": (
                    'if self.token:\n            headers["Authorization"] = f"Bearer {self.token}"'
                )
            },
            "reverted_ff7d610_mcp_http",
        )
        with pytest.raises(reverted.McpProbeError, match="HTTP 403"):
            _run(exercise(reverted.McpHttpClient, endpoint))


def test_399b399_get_probe_accepts_recorded_415_as_reachable() -> None:
    async def exercise(client_type: type[Any], endpoint: Endpoint) -> str:
        async with client_type(endpoint=endpoint, token=API_TOKEN, timeout_seconds=2.0) as client:
            return await client.reachability()

    with RecordedGateway() as gateway:
        endpoint = _endpoint(gateway.mcp_url)
        assert _run(exercise(McpHttpClient, endpoint)) == "HTTP 415"

        reverted = _load_temp_module(
            MCP_HTTP,
            {"status in (404, 405, 406, 415)": "status in (404, 405, 406)"},
            "reverted_399b399_mcp_http",
        )
        with pytest.raises(reverted.McpProbeError, match="HTTP 415"):
            _run(exercise(reverted.McpHttpClient, endpoint))


def test_3e9e81f_unadvertised_prompts_list_tolerates_recorded_invalid_request() -> None:
    async def exercise(client_type: type[Any], endpoint: Endpoint) -> None:
        async with client_type(endpoint=endpoint, token=API_TOKEN, timeout_seconds=2.0) as client:
            await client.initialize()
            await client.prompts_list()

    with RecordedGateway() as gateway:
        endpoint = _endpoint(gateway.mcp_url)
        with pytest.raises(McpMethodNotFound, match="prompts/list"):
            _run(exercise(McpHttpClient, endpoint))

        reverted = _load_temp_module(
            MCP_HTTP,
            {
                "code == METHOD_NOT_FOUND or (code == INVALID_REQUEST and method in LIST_METHODS)": (
                    "code == METHOD_NOT_FOUND"
                )
            },
            "reverted_3e9e81f_mcp_http",
        )
        with pytest.raises(reverted.McpProbeError, match="prompts/list failed") as caught:
            _run(exercise(reverted.McpHttpClient, endpoint))
        assert not isinstance(caught.value, reverted.McpMethodNotFound)


def test_418f2e7_candidate_uses_payload_that_survives_gateway_reserialization() -> None:
    driver = runpy.run_path(str(PHASE3_DRIVER))
    original = _zip_of(ROOT / "tests/harness/phase3-live/fixture-project")
    source_project = driver["_read_entries"](original)["project.json"]

    def round_trip(edit_candidate: Any) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        with RecordedGateway(projects={"recorded": original}) as gateway:
            url = gateway.base_url + "/data/api/v1/projects/export/recorded"
            baseline = _gateway_request(url, "GET")
            assert driver["_read_entries"](baseline)["project.json"] != source_project
            candidate = driver["_drained_zip"](
                edit_candidate(driver["_read_entries"](baseline), "a1b2c3d4")
            )
            _gateway_request(
                gateway.base_url + "/data/api/v1/projects/import/recorded?overwrite=true",
                "POST",
                candidate,
            )
            exported = _gateway_request(url, "GET")
            _gateway_request(
                gateway.base_url + "/data/api/v1/projects/import/recorded?overwrite=true",
                "POST",
                exported,
            )
            stable = _gateway_request(url, "GET")
            return (
                driver["_entry_digests"](candidate),
                driver["_entry_digests"](exported),
                driver["_entry_digests"](stable),
            )

    candidate_entries, exported_entries, stable_entries = round_trip(driver["_edit_candidate"])
    assert candidate_entries == exported_entries
    assert exported_entries == stable_entries
    assert any(
        name.endswith("query.sql") and digest != driver["_entry_digests"](original).get(name)
        for name, digest in candidate_entries.items()
    )

    reverted = _load_temp_module(
        PHASE3_DRIVER,
        {'re.compile(r"^ignition/named-query/.+/query\\.sql$")': 're.compile(r"a^")'},
        "reverted_418f2e7_driver",
    )
    reverted_candidate, reverted_export, reverted_stable = round_trip(reverted._edit_candidate)
    assert reverted_candidate != reverted_export
    assert reverted_export == reverted_stable
    assert {
        name for name in reverted_candidate if reverted_candidate[name] != reverted_export[name]
    } == {"project.json"}


def test_830b354_phase2_provision_discovers_recorded_mariadb_identity() -> None:
    provision = runpy.run_path(str(PHASE2_PROVISION))
    provision["time"].sleep = lambda _seconds: None
    with RecordedGateway() as gateway:
        result = provision["provision"](gateway.base_url, API_TOKEN)
    assert result["databaseDriver"] == "MariaDB"
    assert result["databaseTranslator"] == "MYSQL"

    reverted = _load_temp_module(
        PHASE2_PROVISION,
        {
            "driver, translator, connect_url = _select_database_identity_when_ready(base_url, token)": (
                'driver, translator, connect_url = "PostgreSQL", "POSTGRESQL", '
                '"jdbc:postgresql://postgres:5432/ignition_mcp_ci"'
            )
        },
        "reverted_830b354_provision",
    )
    reverted.time.sleep = lambda _seconds: None
    with RecordedGateway() as gateway:
        with pytest.raises(reverted.ProvisionError, match="Invalid reference"):
            reverted.provision(gateway.base_url, API_TOKEN)


def test_3b6cd393_phase2_omits_string_for_recorded_credential_object() -> None:
    provision = runpy.run_path(str(PHASE2_PROVISION))
    provision["time"].sleep = lambda _seconds: None
    with RecordedGateway() as gateway:
        result = provision["provision"](gateway.base_url, API_TOKEN)
    assert result["resources"]["ignition/database-connection"] == 200

    reverted = _load_temp_module(
        PHASE2_PROVISION,
        {
            '                    "username": "ignition_mcp_ci",': (
                '                    "username": "ignition_mcp_ci",\n'
                '                    "password": "phase2-ci-only-not-a-production-secret",'
            )
        },
        "reverted_3b6cd393_provision",
    )
    reverted.time.sleep = lambda _seconds: None
    with RecordedGateway() as gateway:
        with pytest.raises(reverted.ProvisionError, match="required property.*type"):
            reverted.provision(gateway.base_url, API_TOKEN)


def test_cc74bd9_phase2_uses_recorded_canonical_tag_export_document() -> None:
    provision = runpy.run_path(str(PHASE2_PROVISION))
    with RecordedGateway() as gateway:
        assert provision["_import_tags"](gateway.base_url, API_TOKEN, 10) == 200

    reverted = _load_temp_module(
        PHASE2_PROVISION,
        {
            '    document = {"tags": [': "    document = [",
            "    ]}\n    return json.dumps(document": "    ]\n    return json.dumps(document",
        },
        "reverted_cc74bd9_provision",
    )
    with RecordedGateway() as gateway:
        with pytest.raises(reverted.ProvisionError, match="non-Good QualityCodes"):
            reverted._import_tags(gateway.base_url, API_TOKEN, 10)


def test_35ac63d_phase2_waits_for_recorded_openapi_capabilities() -> None:
    provision = runpy.run_path(str(PHASE2_PROVISION))
    provision["time"].sleep = lambda _seconds: None
    with RecordedGateway(openapi_missing_responses=1) as gateway:
        result = provision["provision"](gateway.base_url, API_TOKEN)
        paths = [request["path"] for request in gateway.requests]
    assert result["openapiReadiness"]["requiredEndpoints"]
    assert paths.count("/openapi.json") == 2
    assert paths.index("/openapi.json") < paths.index(
        "/data/api/v1/resources/com.inductiveautomation.historian/historian-provider"
    )

    reverted = _load_temp_module(
        PHASE2_PROVISION,
        {
            "openapi_readiness = _await_required_openapi_endpoints(base_url, token)": (
                'openapi_readiness = {"endpointCount": 0, "requiredEndpoints": []}'
            )
        },
        "reverted_35ac63d_provision",
    )
    reverted.time.sleep = lambda _seconds: None
    with RecordedGateway(openapi_missing_responses=1) as gateway:
        with pytest.raises(reverted.ProvisionError, match="No route match"):
            reverted.provision(gateway.base_url, API_TOKEN)


def test_recorded_gateway_fake_requires_restart_after_certificate_acceptance() -> None:
    """Test fake state only; production replaced the historical healing flow in 35ac63d."""
    provision = runpy.run_path(str(PHASE2_PROVISION))
    modules = (
        "com.inductiveautomation.historian",
        "com.inductiveautomation.alarm-notification",
    )
    resource_path = "/data/api/v1/resources/com.inductiveautomation.historian/historian-provider"
    with RecordedGateway(quarantined_modules=modules) as gateway:
        with pytest.raises(provision["ProvisionError"], match="No route match"):
            provision["_request"](gateway.base_url, API_TOKEN, "POST", resource_path, body=b"[]")
        status, _ = provision["_request"](
            gateway.base_url,
            API_TOKEN,
            "POST",
            "/data/api/v1/modules/certificate",
            query={"moduleId": modules[0]},
        )
        assert status == 200
        status, _ = provision["_request"](
            gateway.base_url,
            API_TOKEN,
            "POST",
            resource_path,
            body=b"[]",
            allowed_error_statuses=frozenset({404}),
        )
        assert status == 404
        gateway.restart()
        status, _ = provision["_request"](gateway.base_url, API_TOKEN, "POST", resource_path, body=b"[]")
        assert status == 200


def test_recorded_gateway_fake_shares_certificate_acceptance_across_modules() -> None:
    """Test fake state only; production no longer accepts bundled-module certificates."""
    provision = runpy.run_path(str(PHASE2_PROVISION))
    modules = (
        "com.inductiveautomation.historian",
        "com.inductiveautomation.alarm-notification",
    )
    with RecordedGateway(quarantined_modules=modules) as gateway:
        first, _ = provision["_request"](
            gateway.base_url,
            API_TOKEN,
            "POST",
            "/data/api/v1/modules/certificate",
            query={"moduleId": modules[0]},
        )
        second, _ = provision["_request"](
            gateway.base_url,
            API_TOKEN,
            "POST",
            "/data/api/v1/modules/certificate",
            query={"moduleId": modules[1]},
            allowed_error_statuses=frozenset({409}),
        )
    assert (first, second) == (200, 409)
