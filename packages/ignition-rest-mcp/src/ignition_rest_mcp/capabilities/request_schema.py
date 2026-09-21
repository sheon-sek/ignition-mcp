"""D03 request-schema validation for generic config writes.

A generic config write may only send what the target Gateway documents. The D04
capability snapshot therefore keeps, for every resource type with a collection write
route (``POST`` to create, ``PUT`` to modify) and for the rename route, a
**self-contained** JSON Schema: the type's documented change-item schema (or an
operation's request body) with every internal ``$ref`` bundled into ``$defs``.
Bundling at refresh time keeps the snapshot small, needs no document registry at
call time and holds no reference to the (multi-megabyte) OpenAPI document, while a
self-reference stays a reference to ``$defs`` and so remains resolvable.

A route whose request schema cannot be bundled is withheld from the snapshot:
without the schema there is nothing to validate the write against, and sending an
unvalidated change is exactly what D03 forbids. Validation of a bundled schema
cannot hit an unresolvable reference, and a test asserts that for every committed
document.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

COLLECTION_PREFIX = "/data/api/v1/resources/"
JSON_CONTENT_TYPE = "application/json"
DEFS_KEY = "$defs"

#: The collection write methods, each of which documents an array of change items.
COLLECTION_WRITE_METHODS = ("post", "put")

#: Guard against a pathological document: bundling is bounded work, and a schema
#: that exceeds the bound is treated as unusable (the type then has no write route).
MAX_BUNDLED_NODES = 200_000


def bundle_collection_item_schema(
    document: dict[str, Any], resource_type: str, method: str,
) -> Mapping[str, Any] | None:
    """The self-contained, **deeply immutable** change-item schema of one collection
    route (``method`` is ``post`` to create or ``put`` to modify), or ``None``.

    ``None`` means "no usable schema": the route is absent, the request body is not
    documented as JSON, or a reference could not be resolved inside the document.
    """

    if method not in COLLECTION_WRITE_METHODS:
        raise ValueError(f"not a collection write method: {method}")
    return _bundle(document, _documented_item_schema(document, resource_type, method))


def bundle_operation_body_schema(
    document: dict[str, Any], operation_path: str, method: str,
) -> Mapping[str, Any] | None:
    """The self-contained, **deeply immutable** request-body schema of one documented
    operation, or ``None``. Used for the rename route, whose body is an object rather
    than an array of change items."""

    return _bundle(document, _documented_body_schema(document, operation_path, method))


def _bundle(document: dict[str, Any], node: dict[str, Any] | None) -> Mapping[str, Any] | None:
    """Bundle one documented schema into a frozen, self-contained one.

    The result is frozen (mappings become read-only views, arrays become tuples)
    because it lives inside the D04 snapshot: a caller holding the snapshot must not
    be able to change the rules a later write is validated against.
    """

    if node is None:
        return None
    bundler = _Bundler(document)
    try:
        bundled = bundler.bundle(node)
    except (_UnresolvableReference, _TooLarge):
        return None
    if not isinstance(bundled, dict):
        return None
    result = {str(key): value for key, value in bundled.items()}
    if bundler.defs:
        result[DEFS_KEY] = bundler.defs
    frozen = _freeze(result)
    assert isinstance(frozen, Mapping)  # a bundled schema is an object
    return frozen


def _freeze(node: Any) -> Any:
    """Recursively read-only view of a bundled schema (D04 snapshot immutability)."""

    if isinstance(node, Mapping):
        return MappingProxyType({str(key): _freeze(value) for key, value in node.items()})
    if isinstance(node, (list, tuple)):
        return tuple(_freeze(value) for value in node)
    return node


def _documented_item_schema(
    document: dict[str, Any], resource_type: str, method: str,
) -> dict[str, Any] | None:
    """One change item of a resource type's documented collection write body."""

    operation = _documented_operation(document, f"{COLLECTION_PREFIX}{resource_type}", method)
    if operation is None:
        return None
    schema = _documented_schema(document, operation)
    if not isinstance(schema, dict) or schema.get("type") != "array":
        return None
    item = schema.get("items")
    if not isinstance(item, dict):
        return None
    return {str(key): value for key, value in item.items()}


def _documented_body_schema(
    document: dict[str, Any], operation_path: str, method: str,
) -> dict[str, Any] | None:
    """The documented JSON request body of one operation, if it declares one."""

    operation = _documented_operation(document, operation_path, method)
    if operation is None:
        return None
    schema = _documented_schema(document, operation)
    return {str(key): value for key, value in schema.items()} if isinstance(schema, dict) else None


def _documented_operation(
    document: dict[str, Any], operation_path: str, method: str,
) -> dict[str, Any] | None:
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return None
    operation = paths.get(operation_path)
    if not isinstance(operation, dict):
        return None
    entry = operation.get(method)
    return entry if isinstance(entry, dict) else None


def _documented_schema(document: dict[str, Any], operation: dict[str, Any]) -> Any:
    content = ((operation.get("requestBody") or {}).get("content") or {})
    if not isinstance(content, dict):
        return None
    media = content.get(JSON_CONTENT_TYPE)
    if not isinstance(media, dict):
        return None
    return media.get("schema")


class _UnresolvableReference(Exception):
    """A ``$ref`` this document cannot resolve: the schema is unusable."""


class _TooLarge(Exception):
    """Bundling exceeded :data:`MAX_BUNDLED_NODES`: the schema is unusable."""


class _Bundler:
    """Rewrite internal ``$ref``\\ s into ``$defs`` entries, once per target."""

    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document
        self._keys: dict[str, str] = {}
        self.defs: dict[str, Any] = {}
        self._nodes = 0

    def bundle(self, node: Any) -> Any:
        self._nodes += 1
        if self._nodes > MAX_BUNDLED_NODES:
            raise _TooLarge
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str):
                return self._reference(reference, node)
            return {key: self.bundle(value) for key, value in node.items()}
        if isinstance(node, list):
            return [self.bundle(value) for value in node]
        return node

    def _reference(self, reference: str, node: dict[str, Any]) -> dict[str, Any]:
        if not reference.startswith("#/"):
            # An external document would need a registry to resolve; refusing the
            # schema is fail-closed, and D03 wants it validated, not guessed.
            raise _UnresolvableReference(reference)
        if reference in self._keys:
            target_reference = f"#/{DEFS_KEY}/{self._keys[reference]}"
        else:
            target = _resolve_pointer(self._document, reference)
            if target is None:
                raise _UnresolvableReference(reference)
            key = self._key(reference)
            # Record the key before walking the target, so a self-referential schema
            # terminates as a reference to its own $defs entry.
            self._keys[reference] = key
            self.defs[key] = self.bundle(target)
            target_reference = f"#/{DEFS_KEY}/{key}"
        merged = {"$ref": target_reference}
        # OpenAPI 3.1 allows keywords beside a $ref (JSON Schema 2020-12 applies
        # both), so *every* occurrence keeps its siblings — including an occurrence
        # whose target was already bundled, which would otherwise silently lose the
        # extra constraint that occurrence added.
        for name, value in node.items():
            if name != "$ref":
                merged[name] = self.bundle(value)
        return merged

    def _key(self, reference: str) -> str:
        key = reference.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
        if not key:
            key = "target"
        unique = key
        taken = set(self.defs)
        suffix = 2
        while unique in taken or unique in self._keys.values():
            unique = f"{key}_{suffix}"
            suffix += 1
        return unique


def _resolve_pointer(document: dict[str, Any], pointer: str) -> Any | None:
    node: Any = document
    for part in pointer[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
