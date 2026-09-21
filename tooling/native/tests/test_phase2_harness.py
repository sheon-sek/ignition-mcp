from __future__ import annotations

import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "tests/harness/phase2-live/docker-compose.yml"
PROVISION = ROOT / "tests/harness/phase2-live/provision.py"
WORKFLOW = ROOT / ".github/workflows/phase2-live-g2.yml"
SERVER_RESOURCE = (
    ROOT
    / "tests/harness/phase2-live/gateway-config/com.inductiveautomation.mcp"
    / "server-config/phase2-runtime/resource.json"
)

EXPECTED_MODULES = {
    "com.inductiveautomation.mcp",
    "com.inductiveautomation.historian",
    "com.inductiveautomation.alarm-notification",
    "com.inductiveautomation.jdbc.mariadb",
}


def test_phase2_gateway_module_whitelist_is_exact_and_certificate_scope_is_third_party() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    prefix = '      GATEWAY_MODULES_ENABLED: "'
    line = next(line for line in text.splitlines() if line.startswith(prefix))
    modules = set(line.removeprefix(prefix).removesuffix('"').split(","))
    assert modules == EXPECTED_MODULES
    assert '      ACCEPT_MODULE_CERTS: "com.inductiveautomation.mcp"' in text


def test_phase2_server_config_has_stable_uuid() -> None:
    payload = json.loads(SERVER_RESOURCE.read_text(encoding="utf-8"))
    assert payload["attributes"]["uuid"] == "24c38cf5-1080-567c-bbf3-74916fe4bea4"


def test_phase2_provisioning_uses_openapi_readiness_not_certificate_healing() -> None:
    source = PROVISION.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "_REQUIRED_OPENAPI_ENDPOINTS" in source
    assert "_await_required_openapi_endpoints" in source
    assert '"/openapi.json"' in source
    assert "_accept_quarantined_certificates" not in source
    assert "--stage heal" not in workflow
    assert "Heal quarantined bundled modules" not in workflow


def test_phase2_tag_import_uses_export_document_shape_and_accepts_observed_success_wire() -> None:
    namespace = runpy.run_path(str(PROVISION))
    document = json.loads(namespace["_tag_document"](10))
    assert set(document) == {"tags"}
    assert isinstance(document["tags"], list)
    assert {tag["name"] for tag in document["tags"]} == {"McpCiType", "_mcp_ci"}

    failure_detail = namespace["_tag_import_failure_detail"]
    assert failure_detail([]) is None
    assert failure_detail({"successCount": 2, "failureCount": 0, "failures": []}) is None
    assert failure_detail({
        "successCount": 1,
        "failureCount": 1,
        "failures": [{"quality": "Error"}],
    }) is not None
