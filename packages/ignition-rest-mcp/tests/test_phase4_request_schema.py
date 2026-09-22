"""Phase 4 milestone 4c (tickets #14 and #15): D03 request-schema validation.

D03 requires the OpenAPI operation and schema to be validated for a generic config
write, so a Mutation may only send what the target Gateway documents. The snapshot
keeps a *self-contained* schema per write route (bundled at refresh time): the change
item of a collection ``POST``/``PUT`` and the request body of the rename route. A
route whose schema is unusable is withheld, so the Tool refuses that resource type
rather than sending an unvalidated body. These tests hold the bundler, the committed
specification and the snapshot together.
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
from ignition_rest_mcp.capabilities.request_schema import (
    bundle_collection_item_schema,
    bundle_operation_body_schema,
)
from ignition_rest_mcp.client.gateway import GatewayClient

ROOT = Path(__file__).resolve().parents[3]
COMMITTED_DOCUMENT = ROOT / "docs/ignition-8.3.8-openapi/openapi.min.json"
COLLECTION_PREFIX = "/data/api/v1/resources/"
WRITE_METHODS = ("put", "post")


def _document() -> dict[str, Any]:
    return json.loads(COMMITTED_DOCUMENT.read_text(encoding="utf-8"))


def _collection_routes(document: dict[str, Any], method: str) -> list[str]:
    """Every resource type the document gives a ``method`` collection route."""

    found = []
    for path, operation in document["paths"].items():
        if not path.startswith(COLLECTION_PREFIX) or not isinstance(operation, dict):
            continue
        remainder = path[len(COLLECTION_PREFIX):]
        if len(remainder.split("/")) != 2 or method not in operation:
            continue
        found.append(remainder)
    return sorted(found)


def update_routes(document: dict[str, Any]) -> list[str]:
    """Every resource type the document gives a PUT collection route."""

    return _collection_routes(document, "put")


def create_routes(document: dict[str, Any]) -> list[str]:
    """Every resource type the document gives a POST collection route."""

    return _collection_routes(document, "post")


def rename_routes(document: dict[str, Any]) -> list[str]:
    """Every resource type the document gives a rename route."""

    found = []
    for path, operation in document["paths"].items():
        if not path.startswith(f"{COLLECTION_PREFIX}rename/") or not isinstance(operation, dict):
            continue
        remainder = path[len(f"{COLLECTION_PREFIX}rename/"):]
        resource_type, _, name = remainder.rpartition("/")
        if not resource_type or name != "{name}" or "post" not in operation:
            continue
        found.append(resource_type)
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


def test_the_committed_document_still_documents_write_routes() -> None:
    """The rest of this module is meaningless if the fixture document changed."""

    document = _document()
    assert len(update_routes(document)) >= 50
    assert len(create_routes(document)) >= 50
    assert len(rename_routes(document)) >= 30


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_every_documented_collection_route_yields_a_change_item_schema(method: str) -> None:
    document = _document()
    missing = [
        resource_type for resource_type in _collection_routes(document, method)
        if bundle_collection_item_schema(document, resource_type, method) is None
    ]

    assert missing == [], f"these types document {method} without a usable schema: {missing}"


def test_every_documented_rename_route_yields_a_body_schema() -> None:
    """The rename body is an object rather than an array of change items, and it is
    still validated against what the Gateway documents (D03)."""

    document = _document()
    missing = [
        resource_type for resource_type in rename_routes(document)
        if bundle_operation_body_schema(
            document, f"{COLLECTION_PREFIX}rename/{resource_type}/{{name}}", "post",
        ) is None
    ]

    assert missing == [], f"these types document a rename without a usable schema: {missing}"


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_a_bundled_schema_references_only_its_own_defs(method: str) -> None:
    """Bundling is what lets validation run without the OpenAPI document, so no
    reference may survive outside the schema's own ``$defs`` — otherwise a call
    could fail on an unresolvable reference instead of on the caller's input."""

    document = _document()
    offenders: list[str] = []
    for resource_type in _collection_routes(document, method):
        schema = bundle_collection_item_schema(document, resource_type, method)
        assert schema is not None
        defs = schema.get("$defs", {})
        assert isinstance(defs, Mapping)
        for reference in references(schema):
            name = reference[len("#/$defs/"):] if reference.startswith("#/$defs/") else None
            if name is None or name not in defs:
                offenders.append(f"{resource_type}: {reference}")

    assert offenders == [], f"references surviving outside $defs: {offenders}"


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_a_bundled_schema_validates_a_minimal_item(method: str) -> None:
    """A schema that cannot validate at all is worse than no schema: the caller
    would see an internal error instead of their own validation failure. The item is
    the smallest one the Tool can send, which always names the core collection (D30
    owner ruling 5), so the shipped document must accept that value too."""

    document = _document()
    for resource_type in _collection_routes(document, method):
        schema = bundle_collection_item_schema(document, resource_type, method)
        assert schema is not None
        item: dict[str, Any] = {"collection": "core"}
        if method == "put":
            item["signature"] = "sig-1"
        if "name" in schema.get("properties", {}):
            item["name"] = "example"
        try:
            Draft202012Validator(schema).validate(item)
        except ValidationError as error:  # pragma: no cover - failure detail
            raise AssertionError(f"{resource_type}: minimal item rejected: {error.message}") from error


def test_a_rename_body_validates_and_always_carries_abort() -> None:
    """The one body the rename Tool ever sends must satisfy every documented rename
    schema, and it must carry the fixed D30 §4 `references` value; a body that omits
    the new name is still rejected."""

    document = _document()
    for resource_type in rename_routes(document):
        schema = bundle_operation_body_schema(
            document, f"{COLLECTION_PREFIX}rename/{resource_type}/{{name}}", "post",
        )
        assert schema is not None
        validator = Draft202012Validator(schema)
        assert list(validator.iter_errors({"name": "new", "references": "ABORT"})) == []
        missing = list(validator.iter_errors({"references": "ABORT"}))
        assert [error.validator for error in missing] == ["required"]
        assert "name" in missing[0].message


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_a_change_item_declares_a_name_exactly_when_the_type_is_not_a_singleton(method: str) -> None:
    """The Tool builds its item from the documented schema, so the schema and the
    type's own read route must agree about whether a name identifies it."""

    document = _document()
    for resource_type in _collection_routes(document, method):
        schema = bundle_collection_item_schema(document, resource_type, method)
        assert schema is not None
        declared = "name" in schema.get("properties", {})
        singleton = f"{COLLECTION_PREFIX}singleton/{resource_type}" in document["paths"]
        assert declared is not singleton, resource_type


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_a_change_item_declares_the_collection(method: str) -> None:
    """D30 owner ruling 5: a collection route documents no collection query parameter,
    so its change item is the only place a write can name the collection. Every type
    the shipped document gives a write route to must therefore declare the field —
    the Tool refuses a type whose item cannot name `core`, because the Gateway's own
    default would otherwise decide which collection the write lands in."""

    document = _document()
    for resource_type in _collection_routes(document, method):
        schema = bundle_collection_item_schema(document, resource_type, method)
        assert schema is not None
        assert "collection" in schema.get("properties", {}), resource_type


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
    schema = bundle_collection_item_schema(document, "example/type", "put")
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
    schema = bundle_collection_item_schema(document, "example/type", "put")
    assert schema is not None

    with pytest.raises(TypeError):
        schema["type"] = "string"  # type: ignore[index]
    with pytest.raises(TypeError):
        schema["properties"]["signature"] = {}  # type: ignore[index]
    with pytest.raises(AttributeError):
        schema["required"].append("more")  # type: ignore[union-attr]


def test_a_bundled_rename_body_is_deeply_immutable() -> None:
    """The rename body schema enters the snapshot through its own entry point, so it
    is frozen for the same reason the change-item schema is."""

    document = {
        "paths": {
            f"{COLLECTION_PREFIX}rename/example/type/{{name}}": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}, "references": {"type": "string"}},
                    }}}},
                },
            },
        },
    }
    schema = bundle_operation_body_schema(
        document, f"{COLLECTION_PREFIX}rename/example/type/{{name}}", "post",
    )
    assert schema is not None

    with pytest.raises(TypeError):
        schema["type"] = "array"  # type: ignore[index]
    with pytest.raises(AttributeError):
        schema["required"].append("references")  # type: ignore[union-attr]


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

    assert bundle_collection_item_schema(document, "example/type", "put") is None


def test_a_route_without_a_documented_schema_yields_nothing() -> None:
    assert bundle_collection_item_schema(
        {"paths": {f"{COLLECTION_PREFIX}example/type": {"put": {"requestBody": None}}}},
        "example/type", "put",
    ) is None
    assert bundle_collection_item_schema(
        {"paths": {f"{COLLECTION_PREFIX}example/type": {"post": {"requestBody": None}}}},
        "example/type", "post",
    ) is None
    assert bundle_operation_body_schema(
        {"paths": {f"{COLLECTION_PREFIX}rename/example/type/{{name}}": {"post": {}}}},
        f"{COLLECTION_PREFIX}rename/example/type/{{name}}", "post",
    ) is None


def test_a_collection_method_that_is_not_a_write_is_refused() -> None:
    """Only the two documented collection write methods have change items; asking for
    anything else is a programming error, not an empty schema."""

    with pytest.raises(ValueError):
        bundle_collection_item_schema({"paths": {}}, "example/type", "delete")


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


def _route_document(*, with_body: bool, method: str = "put") -> dict[str, Any]:
    operation: dict[str, Any] = {}
    if with_body:
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
            f"{COLLECTION_PREFIX}example/thing": {method: operation},
        },
    }


def test_a_documented_update_route_carries_its_schema_in_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability, capabilities = _capability(_route_document(with_body=True), monkeypatch, "example/thing")

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

    capability, capabilities = _capability(_route_document(with_body=False), monkeypatch, "example/thing")

    assert capability.update_path is None
    assert capability.update_request_schema is None
    assert "config_resource_update" not in capabilities


def test_a_documented_create_route_carries_its_schema_in_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability, capabilities = _capability(
        _route_document(with_body=True, method="post"), monkeypatch, "example/thing",
    )

    assert capability.create_path == f"{COLLECTION_PREFIX}example/thing"
    assert capability.create_request_schema is not None
    assert "config_resource_create" in capabilities


def test_a_create_route_without_a_usable_schema_is_withheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability, capabilities = _capability(
        _route_document(with_body=False, method="post"), monkeypatch, "example/thing",
    )

    assert capability.create_path is None
    assert capability.create_request_schema is None
    assert "config_resource_create" not in capabilities


def test_the_delete_route_template_follows_the_documented_path_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signature travels in the DELETE path, so the template must be the exact
    documented route: ``/{name}/{signature}`` for a named type and ``/{signature}``
    for a singleton."""

    named = _route_document(with_body=True)
    named["paths"][f"{COLLECTION_PREFIX}example/thing/{{name}}/{{signature}}"] = {"delete": {}}
    capability, capabilities = _capability(named, monkeypatch, "example/thing")
    assert capability.delete_path_template == f"{COLLECTION_PREFIX}example/thing/{{name}}/{{signature}}"
    assert "config_resource_delete" in capabilities

    singleton = _route_document(with_body=True)
    singleton["paths"][f"{COLLECTION_PREFIX}singleton/example/thing"] = {"get": {}}
    singleton["paths"][f"{COLLECTION_PREFIX}example/thing/{{signature}}"] = {"delete": {}}
    capability, _ = _capability(singleton, monkeypatch, "example/thing")
    assert capability.delete_path_template == f"{COLLECTION_PREFIX}example/thing/{{signature}}"


def test_a_write_route_without_a_lookup_route_is_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    """D30 §2: a Precondition token comes from the caller's own read, so a Gateway
    that cannot read the resource back exactly can never precondition a change to it."""

    document = _route_document(with_body=True)
    document["paths"].pop(f"{COLLECTION_PREFIX}find/example/thing/{{name}}")
    document["paths"][f"{COLLECTION_PREFIX}example/thing/{{name}}/{{signature}}"] = {"delete": {}}

    capability, capabilities = _capability(document, monkeypatch, "example/thing")

    assert capability.update_path is None
    assert capability.create_path is None
    assert capability.delete_path_template is None
    assert {"config_resource_update", "config_resource_create", "config_resource_delete"}.isdisjoint(
        capabilities,
    )


def _rename_route(document: dict[str, Any], request_body: dict[str, Any] | None) -> None:
    """Add the rename route for ``example/thing`` to a document, with or without a
    documented request body."""

    operation: dict[str, Any] = {} if request_body is None else {"requestBody": request_body}
    document["paths"][f"{COLLECTION_PREFIX}rename/example/thing/{{name}}"] = {"post": operation}


_RENAME_BODY_SCHEMA: dict[str, Any] = {
    "content": {"application/json": {"schema": {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}, "references": {"type": "string"}},
    }}},
}


def test_a_documented_rename_route_carries_its_schema_in_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _route_document(with_body=True)
    _rename_route(document, _RENAME_BODY_SCHEMA)

    capability, capabilities = _capability(document, monkeypatch, "example/thing")

    assert capability.rename_path_template == f"{COLLECTION_PREFIX}rename/example/thing/{{name}}"
    assert capability.rename_request_schema is not None
    assert "config_resource_rename" in capabilities
    with pytest.raises(TypeError):
        capability.rename_request_schema["type"] = "array"  # type: ignore[index]


def test_a_rename_route_without_a_usable_schema_is_withheld(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _route_document(with_body=True)
    _rename_route(document, None)

    capability, capabilities = _capability(document, monkeypatch, "example/thing")

    assert capability.rename_path_template is None
    assert capability.rename_request_schema is None
    assert "config_resource_rename" not in capabilities
