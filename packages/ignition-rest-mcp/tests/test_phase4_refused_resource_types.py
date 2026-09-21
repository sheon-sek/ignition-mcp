"""Phase 4 milestone 4c (ticket #14): D30 §5 Refused resource types.

D30 requires every resource type in a supported OpenAPI document to be classified
as allowed or refused, refuses anything unclassified, and fails a test when a
Gateway version adds a type the contract does not know. These tests hold the
contract, the shipped classification data and the committed OpenAPI documents
together.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ignition_rest_mcp.safety.refused_resource_types import (
    ALLOWED_RESOURCE_TYPES,
    REFUSED_RESOURCE_TYPES,
    is_refused_resource_type,
    refuse_resource_type_decision,
)

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contracts/shared/refused-resource-types.json"
RESOURCE_TYPE_PREFIX = "/data/api/v1/resources/type/"

#: D30 §5 families that must always be refused, named as the decision names them.
D30_REFUSED = {
    "ignition/api-token": "API tokens",
    "ignition/security-levels": "security levels",
    "ignition/security-properties": "security properties",
    "ignition/security-zone": "security zones",
    "ignition/user-source": "user sources",
    "ignition/identity-provider": "identity providers",
    "ignition/oauth2-client": "OAuth2 clients",
    "ignition/secret-provider": "secret providers",
    "ignition/system-properties": "system properties",
    "ignition/local-system-properties": "local system properties",
    "com.inductiveautomation.eam/hw-license-management": "EAM licenses",
    "com.inductiveautomation.eam/leased-license-management": "EAM licenses",
    "com.inductiveautomation.eam/module-certificates": "module administration",
    "com.inductiveautomation.eam/module-eulas": "module administration",
    "com.inductiveautomation.eam/module-settings": "module administration",
    "com.inductiveautomation.mcp/server-config": "the MCP Module's own Tool inventory",
}


def _contract() -> dict[str, object]:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _documented_resource_types(document: dict[str, object]) -> set[str]:
    paths = document["paths"]
    assert isinstance(paths, dict)
    found: set[str] = set()
    for path, item in paths.items():
        if not isinstance(path, str) or not path.startswith(RESOURCE_TYPE_PREFIX):
            continue
        if not isinstance(item, dict) or "get" not in item:
            continue
        resource_type = path[len(RESOURCE_TYPE_PREFIX):]
        assert resource_type.count("/") == 1 and all(resource_type.split("/")), path
        found.add(resource_type)
    return found


def committed_documents() -> list[Path]:
    """Every OpenAPI document the repository ships as a supported-version anchor.

    Discovery is by path, so a new ``docs/ignition-<version>-openapi`` export is
    classified by this test the moment it is committed.
    """

    return sorted((ROOT / "docs").glob("ignition-*-openapi/openapi.min.json"))


def committed_inventories() -> list[Path]:
    """The resource-type inventory of a version whose full document is not committed.

    A candidate Gateway's document is large and module-dependent, so its derived
    inventory (``docs/ignition-<version>-openapi/resource-types.json``, carrying the
    source document's SHA-256 and the run that captured it) is what has to be
    classified in place of the whole document. Discovery is by path, so adding an
    inventory for a new version is enforced the moment it is committed.
    """

    return sorted((ROOT / "docs").glob("ignition-*-openapi/resource-types.json"))


def unclassified(document: dict[str, object]) -> set[str]:
    return {
        resource_type for resource_type in _documented_resource_types(document)
        if resource_type not in ALLOWED_RESOURCE_TYPES and resource_type not in REFUSED_RESOURCE_TYPES
    }


def test_the_committed_classification_matches_the_contract() -> None:
    contract = _contract()
    refused = contract["refused"]
    assert isinstance(refused, list)
    assert set(contract["allowed"]) == set(ALLOWED_RESOURCE_TYPES)  # type: ignore[arg-type]
    assert {item["resourceType"] for item in refused} == set(REFUSED_RESOURCE_TYPES)
    assert not (ALLOWED_RESOURCE_TYPES & REFUSED_RESOURCE_TYPES)


def test_the_contract_carries_the_d30_categories() -> None:
    categories = {item["resourceType"]: item["category"] for item in _contract()["refused"]}
    for resource_type, category in D30_REFUSED.items():
        assert resource_type in REFUSED_RESOURCE_TYPES, resource_type
        assert categories[resource_type] == category, resource_type


@pytest.mark.parametrize("document_path", committed_documents(), ids=lambda path: path.parts[-2])
def test_every_resource_type_in_a_supported_openapi_document_is_classified(
    document_path: Path,
) -> None:
    document = json.loads(document_path.read_text(encoding="utf-8"))
    found = _documented_resource_types(document)
    assert found, f"{document_path} documents no resource types"
    assert unclassified(document) == set(), (
        f"{document_path}: unclassified resource types are refused at runtime, "
        "and must be added to contracts/shared/refused-resource-types.json"
    )


def test_an_unclassified_resource_type_fails_the_classification_check() -> None:
    """The guard above is only worth its line if a new type trips it."""

    document = json.loads(committed_documents()[0].read_text(encoding="utf-8"))
    document["paths"][f"{RESOURCE_TYPE_PREFIX}com.example/new-module-type"] = {"get": {}}
    assert unclassified(document) == {"com.example/new-module-type"}


@pytest.mark.parametrize(
    "inventory_path", committed_inventories(), ids=lambda path: path.parts[-2],
)
def test_every_resource_type_of_a_captured_candidate_inventory_is_classified(
    inventory_path: Path,
) -> None:
    """A candidate version whose full document is not committed still has to be
    classified: the live capture's inventory is the anchor, and it fails closed."""

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    resource_types = inventory["resourceTypes"]
    assert isinstance(resource_types, list) and resource_types
    assert inventory["sourceDocumentSha256"], inventory_path
    assert inventory["gatewayVersion"] and inventory["sourceRunId"], inventory_path
    unclassified_types = sorted(
        resource_type for resource_type in resource_types
        if resource_type not in ALLOWED_RESOURCE_TYPES and resource_type not in REFUSED_RESOURCE_TYPES
    )
    assert unclassified_types == [], (
        f"{inventory_path}: unclassified resource types are refused at runtime, "
        "and must be added to contracts/shared/refused-resource-types.json"
    )


@pytest.mark.parametrize(
    ("resource_type", "refused"),
    [
        ("com.inductiveautomation.mcp/server-config", True),
        ("ignition/api-token", True),
        ("ignition/security-levels", True),
        ("com.example/no-openapi-says-so", True),
        ("ignition/audit-profile", False),
        ("ignition/database-connection", False),
        ("com.inductiveautomation.eam/agent-group", False),
        ("com.inductiveautomation.mcp/anything-else", True),
    ],
)
def test_unclassified_types_are_refused_and_classified_types_follow_the_contract(
    resource_type: str, refused: bool,
) -> None:
    assert is_refused_resource_type(resource_type) is refused


def test_a_refused_type_denies_with_permission_denied() -> None:
    decision = refuse_resource_type_decision("com.inductiveautomation.mcp/server-config")
    assert decision.allowed is False
    assert decision.error_code == "permission_denied"
    assert decision.layer == "target-class"
    assert decision.reason.endswith("com.inductiveautomation.mcp/server-config")
    assert refuse_resource_type_decision("ignition/audit-profile").allowed is True
