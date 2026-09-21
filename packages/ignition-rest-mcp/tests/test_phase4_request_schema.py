"""Phase 4 milestone 4c (ticket #14): D03 request-schema validation for writes.

D03 requires the OpenAPI operation and schema to be validated for a generic config
write, so a Mutation may only send what the target Gateway documents. The snapshot
keeps a *self-contained* change-item schema per resource type (bundled at refresh
time), and a type whose schema is unusable gets no update route at all. These tests
hold the bundler, the committed specification and the snapshot together.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.capabilities.request_schema import bundle_update_item_schema
from ignition_rest_mcp.client.gateway import GatewayClient

ROOT = Path(__file__).resolve().parents[3]
COMMITTED_DOCUMENT = ROOT / "docs/ignition-8.3.8-openapi/openapi.min.json"
COLLECTION_PREFIX = "/data/api/v1/resources/"


def _document() -> dict[str, Any]:
    return json.loads(COMMITTED_DOCUMENT.read_text(encoding="utf-8"))


def update_routes(document: dict[str, Any]) -> list[str]:
    """Every resource type the document gives a PUT collection route."""

    found = []
    for path, operation in document["paths"].items():
        if not path.startswith(COLLECTION_PREFIX) or not isinstance(operation, dict):
            continue
        remainder = path[len(COLLECTION_PREFIX):]
        if len(remainder.split("/")) != 2 or "put" not in operation:
            continue
        found.append(remainder)
    return sorted(found)


def references(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [value for key, value in node.items() if key == "$ref" and isinstance(value, str)]
        for value in node.values():
            found.extend(references(value))
        return found
    if isinstance(node, list):
        return [item for value in node for item in references(value)]
    return []


def test_the_committed_document_still_documents_update_routes() -> None:
    """The rest of this module is meaningless if the fixture document changed."""

    assert len(update_routes(_document())) >= 50


def test_every_documented_update_route_yields_a_change_item_schema() -> None:
    document = _document()
    missing = [
        resource_type for resource_type in update_routes(document)
        if bundle_update_item_schema(document, resource_type) is None
    ]

    assert missing == [], f"these resource types document an update without a usable schema: {missing}"


def test_a_bundled_schema_references_only_its_own_defs() -> None:
    """Bundling is what lets validation run without the OpenAPI document, so no
    reference may survive outside the schema's own ``$defs`` — otherwise a call
    could fail on an unresolvable reference instead of on the caller's input."""

    document = _document()
    offenders: list[str] = []
    for resource_type in update_routes(document):
        schema = bundle_update_item_schema(document, resource_type)
        assert schema is not None
        defs = schema.get("$defs", {})
        assert isinstance(defs, Mapping)
        for reference in references(schema):
            name = reference[len("#/$defs/"):] if reference.startswith("#/$defs/") else None
            if name is None or name not in defs:
                offenders.append(f"{resource_type}: {reference}")

    assert offenders == [], f"references surviving outside $defs: {offenders}"


def test_a_bundled_schema_validates_a_minimal_item() -> None:
    """A schema that cannot validate at all is worse than no schema: the caller
    would see an internal error instead of their own validation failure."""

    document = _document()
    for resource_type in update_routes(document):
        schema = bundle_update_item_schema(document, resource_type)
        assert schema is not None
        item: dict[str, Any] = {"signature": "sig-1"}
        if "name" in schema.get("properties", {}):
            item["name"] = "example"
        try:
            Draft202012Validator(schema).validate(item)
        except ValidationError as error:  # pragma: no cover - failure detail
            raise AssertionError(f"{resource_type}: minimal item rejected: {error.message}") from error


def test_a_change_item_declares_a_name_exactly_when_the_type_is_not_a_singleton() -> None:
    """The Tool builds its change item from the documented schema, so the schema and
    the type's own read route must agree about whether a name identifies it."""

    document = _document()
    for resource_type in update_routes(document):
        schema = bundle_update_item_schema(document, resource_type)
        assert schema is not None
        declared = "name" in schema.get("properties", {})
        singleton = f"{COLLECTION_PREFIX}singleton/{resource_type}" in document["paths"]
        assert declared is not singleton, resource_type


def test_a_repeated_reference_keeps_every_occurrences_sibling_keywords() -> None:
    """OpenAPI 3.1 allows keywords beside a ``$ref``. Two fields that reference one
    definition and each tighten it must both stay tightened: an already-bundled
    reference must not drop the siblings of the occurrence that used it."""

    document = {
        "components": {"schemas": {"Name": {"type": "string"}}},
        "paths": {
            f"{COLLECTION_PREFIX}example/type": {
                "put": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "first": {"$ref": "#/components/schemas/Name", "minLength": 5},
                                    "second": {"$ref": "#/components/schemas/Name", "minLength": 5},
                                },
                            },
                        }}},
                    },
                },
            },
        },
    }
    schema = bundle_update_item_schema(document, "example/type")
    assert schema is not None
    assert schema["properties"]["second"]["minLength"] == 5
    validator = Draft202012Validator(schema)

    assert [list(error.absolute_path) for error in validator.iter_errors({"second": "x"})] == [["second"]]
    assert [list(error.absolute_path) for error in validator.iter_errors(
        {"first": "x", "second": "abcde"},
    )] == [["first"]]
    assert list(validator.iter_errors({"first": "abcde", "second": "abcde"})) == []


def test_the_bundled_schema_is_deeply_immutable() -> None:
    """The snapshot may not be mutable: a caller holding ``registry.snapshot`` could
    otherwise change the rules a later write is validated against, with no refresh
    and no generation change."""

    document = {
        "paths": {
            f"{COLLECTION_PREFIX}example/type": {
                "put": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["signature"],
                                "properties": {"signature": {"type": "string"}},
                            },
                        }}},
                    },
                },
            },
        },
    }
    schema = bundle_update_item_schema(document, "example/type")
    assert schema is not None

    with pytest.raises(TypeError):
        schema["type"] = "string"  # type: ignore[index]
    with pytest.raises(TypeError):
        schema["properties"]["signature"] = {}  # type: ignore[index]
    with pytest.raises(AttributeError):
        schema["required"].append("more")  # type: ignore[union-attr]


def test_an_unresolvable_reference_makes_the_schema_unusable() -> None:
    document = {
        "paths": {
            f"{COLLECTION_PREFIX}example/type": {
                "put": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Missing"},
                        }}},
                    },
                },
            },
        },
    }

    assert bundle_update_item_schema(document, "example/type") is None


def test_a_route_without_a_documented_schema_yields_nothing() -> None:
    document = {
        "paths": {f"{COLLECTION_PREFIX}example/type": {"put": {"requestBody": None}}},
    }

    assert bundle_update_item_schema(document, "example/type") is None


# ------------------------------------------------------------------ snapshot


def _stub_openapi(monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]) -> None:
    async def info(self: GatewayClient, context: Any = None) -> dict[str, str]:
        return {"ignitionVersion": "8.3.8"}

    async def modules(self: GatewayClient, context: Any = None) -> dict[str, list[Any]]:
        return {"items": []}

    async def openapi(self: GatewayClient) -> bytes:
        return json.dumps(document).encode("utf-8")

    monkeypatch.setattr(GatewayClient, "gateway_info", info)
    monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
    monkeypatch.setattr(GatewayClient, "openapi", openapi)


def _capability(document: dict[str, Any], monkeypatch: pytest.MonkeyPatch, resource_type: str) -> Any:
    _stub_openapi(monkeypatch, document)

    async def scenario() -> Any:
        client = GatewayClient(base_url="http://gateway", api_token="ci:key", timeout_seconds=10)
        registry = CapabilityRegistry(client)
        try:
            snapshot = await registry.refresh()
            return snapshot.resource_types[resource_type], frozenset(snapshot.semantic_capabilities)
        finally:
            await registry.aclose()
            await client.aclose()

    return asyncio.run(scenario())


def _route_document(with_schema: bool) -> dict[str, Any]:
    operation: dict[str, Any] = {}
    if with_schema:
        operation["requestBody"] = {
            "content": {"application/json": {"schema": {
                "type": "array",
                "items": {"type": "object", "properties": {"name": {"type": "string"}}},
            }}},
        }
    return {
        "paths": {
            f"{COLLECTION_PREFIX}type/example/thing": {"get": {}},
            f"{COLLECTION_PREFIX}names/example/thing": {"get": {}},
            f"{COLLECTION_PREFIX}find/example/thing/{{name}}": {"get": {}},
            f"{COLLECTION_PREFIX}example/thing": {"put": operation},
        },
    }


def test_a_documented_update_route_carries_its_schema_in_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability, capabilities = _capability(_route_document(True), monkeypatch, "example/thing")

    assert capability.update_path == f"{COLLECTION_PREFIX}example/thing"
    assert capability.update_request_schema is not None
    assert "config_resource_update" in capabilities
    with pytest.raises(TypeError):
        capability.update_request_schema["type"] = "string"  # type: ignore[index]


def test_an_update_route_without_a_usable_schema_is_withheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: without the schema there is nothing to validate the write
    against, so the route — and with it the capability — does not exist."""

    capability, capabilities = _capability(_route_document(False), monkeypatch, "example/thing")

    assert capability.update_path is None
    assert capability.update_request_schema is None
    assert "config_resource_update" not in capabilities
