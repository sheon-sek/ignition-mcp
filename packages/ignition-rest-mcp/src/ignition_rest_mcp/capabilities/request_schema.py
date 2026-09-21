"""D03 request-schema validation for generic config writes.

A generic config write may only send what the target Gateway documents. The D04
capability snapshot therefore keeps, for every resource type with an update route,
a **self-contained** JSON Schema for one PUT change item: the type's documented
item schema with every internal ``$ref`` bundled into ``$defs``. Bundling at
refresh time keeps the snapshot small, needs no document registry at call time and
holds no reference to the (multi-megabyte) OpenAPI document, while a self-reference
stays a reference to ``$defs`` and so remains resolvable.

A type whose request schema cannot be bundled gets no update route in the snapshot:
without the schema there is nothing to validate the write against, and sending an
unvalidated change is exactly what D03 forbids. Validation of a bundled schema
cannot hit an unresolvable reference, and a test asserts that for every committed
document.
"""

from __future__ import annotations

from typing import Any

COLLECTION_PREFIX = "/data/api/v1/resources/"
JSON_CONTENT_TYPE = "application/json"
DEFS_KEY = "$defs"

#: Guard against a pathological document: bundling is bounded work, and a schema
#: that exceeds the bound is treated as unusable (the type then has no update route).
MAX_BUNDLED_NODES = 200_000


def bundle_update_item_schema(
    document: dict[str, Any], resource_type: str,
) -> dict[str, Any] | None:
    """The self-contained PUT change-item schema for ``resource_type``, or ``None``.

    ``None`` means "no usable schema": the route is absent, the request body is not
    documented as JSON, or a reference could not be resolved inside the document.
    """

    item = _documented_item_schema(document, resource_type)
    if item is None:
        return None
    bundler = _Bundler(document)
    try:
        bundled = bundler.bundle(item)
    except (_UnresolvableReference, _TooLarge):
        return None
    if not isinstance(bundled, dict):
        return None
    result = {str(key): value for key, value in bundled.items()}
    if bundler.defs:
        result[DEFS_KEY] = bundler.defs
    return result


def _documented_item_schema(document: dict[str, Any], resource_type: str) -> dict[str, Any] | None:
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return None
    operation = paths.get(f"{COLLECTION_PREFIX}{resource_type}")
    if not isinstance(operation, dict):
        return None
    put = operation.get("put")
    if not isinstance(put, dict):
        return None
    content = ((put.get("requestBody") or {}).get("content") or {})
    if not isinstance(content, dict):
        return None
    media = content.get(JSON_CONTENT_TYPE)
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    if not isinstance(schema, dict) or schema.get("type") != "array":
        return None
    item = schema.get("items")
    if not isinstance(item, dict):
        return None
    return {str(key): value for key, value in item.items()}


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
            return {"$ref": f"#/{DEFS_KEY}/{self._keys[reference]}"}
        target = _resolve_pointer(self._document, reference)
        if target is None:
            raise _UnresolvableReference(reference)
        key = self._key(reference)
        # Record the key before walking the target, so a self-referential schema
        # terminates as a reference to its own $defs entry.
        self._keys[reference] = key
        self.defs[key] = self.bundle(target)
        merged = {"$ref": f"#/{DEFS_KEY}/{key}"}
        # OpenAPI allows $ref next to sibling keywords (JSON Schema 2020-12); the
        # siblings are validated as well, so they are kept.
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
