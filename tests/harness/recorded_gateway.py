"""Recorded Ignition Gateway fake shared by local live-harness rehearsals.

The public interface is ``RecordedGateway``. It starts one ephemeral HTTP
listener for Native REST and module-hosted MCP, then exposes the two URLs and
captured requests. Wire bodies come from ``tests/fixtures/recorded``.
"""
from __future__ import annotations

import http.server
import io
import json
from pathlib import Path
import threading
import time
from typing import Any
import urllib.parse
import re
import zipfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/recorded/gateway-8.3"
API_TOKEN = "recorded:AAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_OPENAPI_OPERATIONS = (
    ("get", "/data/api/v1/gateway-info"),
    ("get", "/data/api/v1/projects/list"),
    ("get", "/data/api/v1/projects/export/{name}"),
    ("get", "/data/api/v1/projects/find/{name}"),
    ("post", "/data/api/v1/projects/import/{name}"),
    ("get", "/data/api/v1/designers"),
    ("get", "/data/api/v1/tags/export"),
    ("post", "/data/api/v1/tags/import"),
    ("get", "/data/api/v1/audit/log/{name}"),
    ("get", "/data/alarm-notification/api/v1/pipelines"),
    ("get", "/data/alarm-notification/api/v1/pipeline"),
    # Phase 4 ticket #18: the pipeline cancel surface. The route is a DELETE that
    # takes its two facts in a JSON body, and the status route above is the bounded
    # read its verification uses.
    ("delete", "/data/alarm-notification/api/v1/pipeline"),
    ("get", "/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/{name}"),
    # Ticket #21: the Server Config create and modify routes `setup-native apply`
    # writes through (never `config_resource_*`, which refuses the type by design).
    ("post", "/data/api/v1/resources/com.inductiveautomation.mcp/server-config"),
    ("put", "/data/api/v1/resources/com.inductiveautomation.mcp/server-config"),
    ("get", "/data/api/v1/resources/type/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/names/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/list/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/find/ignition.gateway/idp-links/{name}"),
    ("get", "/data/api/v1/resources/singleton/ignition/security-levels"),
    ("post", "/data/api/v1/resources/ignition/api-token"),
    # Ticket #22: D20's opt-in provisioning writes — the Security Levels singleton
    # (modify, with the Resource signature as the optimistic precondition) and the
    # Gateway's own API-token key/hash generator.
    ("put", "/data/api/v1/resources/ignition/security-levels"),
    ("post", "/data/api/v1/api-token/generate"),
    ("post", "/data/api/v1/resources/com.inductiveautomation.historian/historian-provider"),
    ("post", "/data/api/v1/resources/ignition/audit-profile"),
    ("post", "/data/api/v1/resources/ignition/database-connection"),
    # Phase 4 ticket #14: the config-resource update surface. The Gateway documents
    # one collection path per resource type for POST (create) and PUT (modify).
    ("get", "/data/api/v1/resources/type/ignition/audit-profile"),
    ("get", "/data/api/v1/resources/names/ignition/audit-profile"),
    ("get", "/data/api/v1/resources/list/ignition/audit-profile"),
    ("get", "/data/api/v1/resources/find/ignition/audit-profile/{name}"),
    ("put", "/data/api/v1/resources/ignition/audit-profile"),
    ("get", "/data/api/v1/resources/type/ignition/api-token"),
    ("get", "/data/api/v1/resources/names/ignition/api-token"),
    ("get", "/data/api/v1/resources/list/ignition/api-token"),
    ("get", "/data/api/v1/resources/find/ignition/api-token/{name}"),
    ("put", "/data/api/v1/resources/ignition/api-token"),
    # An *allowed* singleton type (D30 §5 refuses security-levels): its documented
    # PUT item schema requires only `signature`, so its change item carries no name.
    ("get", "/data/api/v1/resources/type/ignition/cobranding"),
    ("get", "/data/api/v1/resources/singleton/ignition/cobranding"),
    ("put", "/data/api/v1/resources/ignition/cobranding"),
    # Phase 4 ticket #15: the create, delete and rename surface. Every type gets the
    # POST collection route (create) and the DELETE route whose *path* carries the
    # Resource signature (`/{name}/{signature}`, or `/{signature}` for a singleton);
    # a non-singleton also gets the POST rename route, which takes no signature.
    ("post", "/data/api/v1/resources/ignition/cobranding"),
    ("delete", "/data/api/v1/resources/ignition/cobranding/{signature}"),
    ("delete", "/data/api/v1/resources/ignition/audit-profile/{name}/{signature}"),
    ("post", "/data/api/v1/resources/rename/ignition/audit-profile/{name}"),
    ("delete", "/data/api/v1/resources/ignition/api-token/{name}/{signature}"),
    ("post", "/data/api/v1/resources/rename/ignition/api-token/{name}"),
    # Phase 4 ticket #6 characterization: the Native REST write path for the
    # deployment-owned Runtime Target Policy provider and its Tags.
    ("post", "/data/api/v1/resources/ignition/tag-provider"),
    ("get", "/data/api/v1/resources/find/ignition/tag-provider/{name}"),
    ("get", "/data/api/v1/resources/type/ignition/tag-provider"),
    # Phase 4 ticket #7: the audit profile the Runtime audit mode names, and the
    # audit log the recorded attempt/result rows are read back from.
    ("get", "/data/api/v1/resources/find/ignition/audit-profile/{name}"),
    ("get", "/data/api/v1/audit/log/{name}"),
    # Phase 4 ticket #36: the same provider type through the generic config Mutations.
    # D30 owner ruling 4 refuses the resource named `IgnitionMCPPolicy` by name, and
    # the cases that prove the *rest* of the type stays manageable need its update,
    # delete and rename routes to exist - a type without them has no such Tool at all
    # (the capability snapshot withholds it), so the refusal being asserted could
    # never be reached.
    ("put", "/data/api/v1/resources/ignition/tag-provider"),
    ("delete", "/data/api/v1/resources/ignition/tag-provider/{name}/{signature}"),
    ("post", "/data/api/v1/resources/rename/ignition/tag-provider/{name}"),
)

#: Path segments under ``/data/api/v1/resources/`` that name an operation rather
#: than a resource type, so a collection path can never be confused with them.
_RESERVED_RESOURCE_SEGMENTS = frozenset({
    "copy", "delete", "move", "rename", "datafile", "names", "list", "find", "singleton", "type",
})

#: The documented rename route: ``/data/api/v1/resources/rename/<type>/{name}``.
_RENAME_ROUTE_PREFIX = "/data/api/v1/resources/rename/"

#: The Gateway's default configuration collection. A read or a change that names no
#: collection lands in this one, which is what the documented ``collection`` query
#: parameter's own example says (``core``). D30's owner ruling 5 pins generic config
#: Mutations to it explicitly, so every request a Mutation makes names it.
DEFAULT_COLLECTION = "core"

#: The D20 ownership marker a managed Runtime Bundle Project carries in the last line
#: of its description. Ticket #21 models what the Module reports: a Project that was
#: *imported* carries its own bundle version, so `bundle_info` follows it.
_MANAGED_MARKER_RE = re.compile(r"^ignition-mcp-managed:\s*product=(\S+)\s*;\s*bundle=(\S+)\s*$")
MANAGED_PRODUCT = "ignition-runtime-bundle"

#: The MCP Module's own Server Config resource type. ``setup-native apply`` writes it
#: through the type's collection routes; D30 §5 refuses it to the generic config
#: Mutations, which is exactly why apply has its own curated path.
SERVER_CONFIG_TYPE = "com.inductiveautomation.mcp/server-config"

#: Ticket #22: the resource types D20's opt-in provisioning writes — the Gateway's
#: Security Levels singleton and an API token. D30 §5 refuses both to the generic
#: config Mutations, so ``setup-native apply`` owns them through its curated path.
SECURITY_LEVELS_TYPE = "ignition/security-levels"
API_TOKEN_TYPE = "ignition/api-token"

#: The security tree the fake serves. Modelled from the live G0 evidence
#: (``tests/compatibility/evidence/g0-8.3.8-mcp-2026021307/ci-security.json`` records
#: the CI level as an "Authenticated child; sibling of Authenticated/Roles"), so a
#: plan reads the same shape the disposable Gateway serves and has a real sibling to
#: preserve.
AUTHENTICATED_DESCRIPTION = "Represents a user who has been authenticated by the system."
STOCK_SECURITY_LEVELS: list[dict[str, Any]] = [
    {
        "name": "Authenticated",
        "description": AUTHENTICATED_DESCRIPTION,
        "children": [{"name": "Roles", "children": []}],
    },
]

#: The Gateway-generated ``key``/``hash`` pair the token routes answer with. Recorded,
#: not modelled: the key is the harness's own disposable CI credential (a value that
#: exists nowhere but an ephemeral CI Gateway, and that the job destroys), and the hash
#: is the one the live G0 run recorded for it in the evidence file above. The pair
#: follows the documented derivation — the hash is the unpadded Base64URL SHA-256
#: digest of the decoded key bytes.
GENERATED_API_TOKEN_KEY = "zG48znDwfapnZCJA_d7THMrQJpejwONfXMFZ5oBYn0I"
GENERATED_API_TOKEN_HASH = "QaH9skRX4DQggE1M8oVuwZSNIU9tOzkt9TKzKNPt53M"


def _resource_type_segment(path: str) -> str | None:
    """The exact ``<module>/<typeId>`` a collection path addresses, or ``None``."""

    prefix = "/data/api/v1/resources/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix):]
    parts = remainder.split("/")
    if len(parts) != 2 or not all(parts) or parts[0] in _RESERVED_RESOURCE_SEGMENTS:
        return None
    return remainder


def _delete_target(path: str) -> tuple[str | None, str, str]:
    """Split a documented DELETE path into ``(resourceType, name, signature)``.

    ``/<type>/{name}/{signature}`` addresses a named resource and
    ``/<type>/{signature}`` the type's singleton; anything else is not a route this
    fixture serves (and ``resourceType`` is ``None``).
    """

    prefix = "/data/api/v1/resources/"
    if not path.startswith(prefix):
        return None, "", ""
    parts = path[len(prefix):].split("/")
    if len(parts) == 4 and all(parts):
        resource_type = f"{parts[0]}/{parts[1]}"
        return resource_type, parts[2], parts[3]
    if len(parts) == 3 and all(parts):
        return f"{parts[0]}/{parts[1]}", "", parts[2]
    return None, "", ""


#: The repository's committed 8.3.8 specification. The recorded Gateway advertises
#: the *real* request body for every route the server's D03 validation reads: a
#: route without the request schema the capability snapshot needs is withheld, so a
#: schema-less stub would silently disable the Tool under test instead of
#: exercising it.
COMMITTED_OPENAPI = ROOT / "docs/ignition-8.3.8-openapi/openapi.min.json"
#: Operation paths that name an action rather than a resource type, so their
#: request bodies are also part of the recorded document.
_ACTION_PREFIXES = ("/data/api/v1/resources/rename/",)
_committed_openapi: dict[str, Any] | None = None


def _committed_document() -> dict[str, Any]:
    global _committed_openapi
    if _committed_openapi is None:
        _committed_openapi = json.loads(COMMITTED_OPENAPI.read_text(encoding="utf-8"))
    return _committed_openapi


def _committed_request_body(method: str, operation_path: str) -> dict[str, Any] | None:
    """The committed request body of one documented collection or action route."""

    if method not in {"put", "post"}:
        return None
    if _resource_type_segment(operation_path) is None and not operation_path.startswith(
        _ACTION_PREFIXES,
    ):
        return None
    operation = (_committed_document().get("paths") or {}).get(operation_path)
    body = ((operation or {}).get(method) or {}).get("requestBody")
    return body if isinstance(body, dict) else None


def _recorded_operation(method: str, operation_path: str) -> dict[str, Any]:
    body = _committed_request_body(method, operation_path)
    return {"requestBody": body} if body else {}


def _committed_components() -> dict[str, Any]:
    """The committed component schemas the injected request bodies reference.

    A real request body refers to the specification's ``#/components/schemas/...``
    entries, so the recorded OpenAPI has to carry them or the reference is
    unresolvable — which is exactly the failure mode a Gateway with an unusable
    request schema must produce. Only the reachable closure is injected, so the
    recorded document stays small.
    """

    schemas: dict[str, Any] = {}
    pending: list[Any] = []
    for method, operation_path in _OPENAPI_OPERATIONS:
        operation = _recorded_operation(method, operation_path)
        if operation:
            pending.append(operation)
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                name = reference[len("#/components/schemas/"):]
                if name not in schemas:
                    target = _committed_pointer(reference)
                    schemas[name] = target
                    pending.append(target)
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return {"schemas": schemas}


def _committed_pointer(pointer: str) -> Any:
    node: Any = _committed_document()
    for part in pointer[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def _fixture(path: str) -> Any:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def _alarm_body(server: Any, path: str) -> dict[str, Any]:
    """One recorded Alarm body with the run-unique Alarm root substituted in.

    The Alarm fixture is run-unique on a live Gateway, so a recorded body carries
    either the ``__ALARM_ROOT__`` placeholder (the replayed Tool results) or the
    root of the run it was recorded from (the probe report, whose many nested
    patterns all name that root).
    """
    text = (FIXTURES / path).read_text(encoding="utf-8")
    if "__ALARM_ROOT__" in text:
        return json.loads(text.replace("__ALARM_ROOT__", server.alarm_root or "__ALARM_ROOT__"))
    document = json.loads(text)
    recorded_root = document.get("rootName") if isinstance(document, dict) else None
    if server.alarm_root and isinstance(recorded_root, str) and recorded_root and recorded_root != server.alarm_root:
        text = text.replace(recorded_root, server.alarm_root)
        document = json.loads(text)
    return document


#: The run-scoped values a recorded ticket #10 refusal body carries. The refusal
#: bodies come from a live run (or are modelled until the first one), and the paths
#: and fingerprints are the run's own, so they are templated.
_TAG_UPDATE_TEMPLATES = (
    ("__TARGET__", "writeTarget"),
    ("__TEXT_TARGET__", "textTarget"),
    ("__FOLDER__", "nestedFolder"),
    ("__RESERVED__", "writeProbe"),
    ("__SIBLING__", "siblingTarget"),
    ("__MISSING__", "missingTarget"),
    ("__UDT__", "udtTarget"),
)


def _tag_update_body(server: Any, arguments: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Substitute the run-scoped values a recorded `tag_update` refusal carries."""
    from tooling.contracts.lint import encode_nulls, tag_config_fingerprint

    text = json.dumps(body)
    for placeholder, key in _TAG_UPDATE_TEMPLATES:
        text = text.replace(placeholder, server.tag_update_paths.get(key, ""))
    paths = _tag_update_paths(arguments)
    expected = ""
    for item in arguments.get("items") or []:
        if isinstance(item, dict) and item.get("path") == (paths[0] if paths else None):
            expected = str(item.get("expectedFingerprint", ""))
    observed = tag_config_fingerprint(encode_nulls(server.tag_config.get(paths[0], []))) if paths else ""
    text = text.replace("__OBSERVED_FINGERPRINT__", observed).replace("__EXPECTED_FINGERPRINT__", expected)
    # A refusal carries a correlation ID like any other Tool Error; a replay has no
    # run of its own, so it answers with the same fixed one the other replayed Tools do.
    text = text.replace("__CORRELATION__", "recorded-replay")
    return json.loads(text)


#: The run-scoped values a ticket #11 refusal body carries. The paths are the run's
#: own; so are the item ceiling the served document names, the item count the call
#: carried and the bounded echo of an over-budget path, because the handler derives
#: all three from the request rather than from a constant.
_TAG_CREATE_TEMPLATES = (
    ("__EXISTING__", "existingTarget"),
    ("__RESERVED__", "writeProbe"),
    ("__SIBLING__", "siblingTarget"),
    ("__TARGET__", "createTarget"),
    ("__UDT__", "udtTarget"),
)
_TAG_COPY_TEMPLATES = (
    ("__DESTINATION__", "destination"),
    ("__MISSING_DESTINATION__", "missingSourceDestination"),
    ("__MISSING_SOURCE__", "missingSource"),
    ("__RESERVED_DESTINATION__", "reservedDestination"),
    ("__RESERVED_SOURCE_DESTINATION__", "reservedSourceDestination"),
    ("__RESERVED_SOURCE__", "reservedSource"),
    ("__SIBLING__", "siblingDestination"),
    ("__SOURCE__", "source"),
    ("__UDT__", "udtDestination"),
)

#: The D10 budgets a request crosses with no native call at all (the item count and the
#: path length), the project default an absent policy field falls back to, and the
#: length the handler bounds an echoed path to. They are the shipped constants, restated
#: here so the rehearsal selects a case the way the handler decides one.
HARD_MAX_ITEMS = 100
PATH_MAX_BYTES = 2048
DEFAULT_MAX_ITEMS = 20
ECHOED_PATH_MAX_CHARS = 256


def _target_leaf(path: str) -> str:
    """The node a config path names, with the provider bracket excluded.

    A path whose body has no ``/`` names a node at the provider root, where a `str.rsplit`
    on ``/`` would answer with the whole path — the handler's own segment rule is what the
    leaf comparisons and the written `name` have to agree with.
    """
    closing = path.find("]")
    segments = [segment for segment in path[closing + 1:].split("/") if segment] if closing > 0 else []
    return segments[-1] if segments else ""


def _tag_path_candidates(arguments: dict[str, Any]) -> list[str]:
    """Every path one ticket #11 call names, whichever key its item carries it in.

    A create names one path per item and a copy names two, and the D10 path ceiling
    is quoted with the offending path only.
    """
    found: list[str] = []
    for item in arguments.get("items") or []:
        if not isinstance(item, dict):
            continue
        found.extend(
            str(value) for key, value in item.items() if key == "path" or str(key).endswith("Path")
        )
    return found


def _requested_fingerprints(server: Any, arguments: dict[str, Any]) -> tuple[str, str]:
    """The token a call carried and the fingerprint of the state it was compared against.

    A fingerprint refusal names both, and neither is a run constant: the expected half
    is the caller's own token and the observed half is the fingerprint the served state
    holds for that item's target, which is what the shipped handler compared.
    """
    for item in arguments.get("items") or []:
        if not isinstance(item, dict):
            continue
        expected = str(item.get("expectedFingerprint", ""))
        path = str(item.get("path") or item.get("sourcePath") or "")
        if expected.startswith("tcf1:") and path:
            return expected, _tag_config_fingerprint(server.tag_config.get(path, []))
    return "", ""


def _render_tag_error_body(
    server: Any, body: dict[str, Any], templates: tuple[tuple[str, str], ...], values: dict[str, str],
    arguments: dict[str, Any], *, limit: int,
) -> dict[str, Any]:
    """Fill one recorded ticket #11 refusal with the values of this run's call.

    The numbers are substituted bare, so a body that quotes a ceiling stays valid
    JSON whichever ceiling the served document names.
    """
    text = json.dumps(body)
    for placeholder, key in sorted(templates, key=lambda pair: -len(pair[0])):
        text = text.replace(placeholder, values.get(key, ""))
    # The handler bounds every free-text value it echoes, so the replayed refusal
    # quotes the same bounded prefix a live call reads.
    long_path = next(
        (path for path in _tag_path_candidates(arguments)
         if len(path.encode("utf-8")) > PATH_MAX_BYTES),
        "",
    )
    path_bytes = len(long_path.encode("utf-8"))
    quoted = long_path
    if len(long_path) > ECHOED_PATH_MAX_CHARS:
        quoted = long_path[:ECHOED_PATH_MAX_CHARS] + "..."
    items = [item for item in (arguments.get("items") or []) if isinstance(item, dict)]
    # A replay has no run of its own, so it answers with the same fixed correlation
    # ID the other replayed Tools do.
    text = text.replace("__CORRELATION__", "recorded-replay")
    # A fingerprint refusal quotes the token the call carried and the fingerprint the
    # served state holds for that target.
    expected_fingerprint, observed_fingerprint = _requested_fingerprints(server, arguments)
    text = text.replace("__EXPECTED_FINGERPRINT__", expected_fingerprint)
    text = text.replace("__OBSERVED_FINGERPRINT__", observed_fingerprint)
    # A leaf refusal quotes the destination the caller actually named, which is the
    # one path in that case which is not a run constant.
    text = text.replace(
        "__REQUESTED_DESTINATION__", str((items[0] if items else {}).get("destinationPath", "")),
    )
    # The serialized body escapes the quotes around a recorded value, so a number is
    # substituted with them and then bare, whichever form the fixture used.
    for placeholder, number in (("__POLICY_LIMIT__", limit), ("__ITEM_COUNT__", len(items)),
                                ("__PATH_BYTES__", path_bytes)):
        text = text.replace(f'\\"{placeholder}\\"', str(number))
        text = text.replace(placeholder, str(number))
    text = text.replace("__LONG_PATH__", quoted)
    return json.loads(text)


#: The encoded Good QualityCode a dispatched mutation item reports. D28 escapes the
#: absent diagnostic, so the replay publishes exactly what the handler publishes.
GOOD_OUTCOME = {
    "code": 192, "name": "Good", "level": "Good", "good": True,
    "diagnosticMessage": {"$ignition": "null"},
}


def _tag_create_body(
    server: Any, arguments: dict[str, Any], body: dict[str, Any],
) -> dict[str, Any]:
    """Substitute the run-scoped values a recorded `tag_create` refusal carries."""
    return _render_tag_error_body(
        server, body, _TAG_CREATE_TEMPLATES, server.tag_create_paths, arguments,
        limit=_served_item_limit(server, "tagCreateMaxItems"),
    )


def _tag_copy_body(
    server: Any, arguments: dict[str, Any], body: dict[str, Any],
) -> dict[str, Any]:
    """Substitute the run-scoped values a recorded `tag_copy` refusal carries."""
    return _render_tag_error_body(
        server, body, _TAG_COPY_TEMPLATES, server.tag_copy_paths, arguments,
        limit=_served_item_limit(server, "tagCopyMaxItems"),
    )


#: The run-scoped values a ticket #12 refusal body carries, one map per Tool.
_TAG_DELETE_TEMPLATES = (
    ("__FOLDER_CHILD__", "folderChild"),
    ("__FOLDER__", "folder"),
    ("__MISSING__", "missingTarget"),
    ("__RESERVED__", "reservedTarget"),
    ("__SIBLING__", "siblingTarget"),
    ("__STALE__", "staleTarget"),
    ("__TARGET__", "target"),
    ("__UDT__", "udtTarget"),
)
_TAG_MOVE_TEMPLATES = (
    ("__OCCUPIED_DESTINATION__", "occupiedDestination"),
    ("__OCCUPIED_SOURCE__", "writeTarget"),
    ("__STALE_SOURCE__", "writeTarget"),
    ("__LEAF_MISMATCH_DESTINATION__", "leafMismatchDestination"),
    ("__MISSING_DESTINATION__", "missingDestination"),
    ("__MISSING_SOURCE__", "missingSource"),
    ("__RESERVED_DESTINATION__", "reservedDestination"),
    ("__RESERVED_SOURCE_DESTINATION__", "reservedSourceDestination"),
    ("__RESERVED_SOURCE__", "reservedSource"),
    ("__SIBLING_DESTINATION__", "siblingDestination"),
    ("__SIBLING_SOURCE__", "siblingSource"),
    ("__SOURCE__", "source"),
    ("__DESTINATION__", "destination"),
    ("__UDT_DESTINATION__", "udtDestination"),
    ("__UDT_SOURCE__", "udtSource"),
)
_TAG_RENAME_TEMPLATES = (
    ("__MULTI_SEGMENT_NAME__", "multiSegmentName"),
    ("__OCCUPIED_SOURCE__", "occupiedSource"),
    ("__OCCUPIED_NEW_PATH__", "targetNewPath"),
    ("__STALE_SOURCE__", "staleSource"),
    ("__STALE_NEW_NAME__", "staleNewName"),
    ("__MISSING_TARGET__", "missingTarget"),
    ("__MISSING_NEW_NAME__", "missingNewName"),
    ("__SIBLING_TARGET__", "siblingTarget"),
    ("__SIBLING_NEW_NAME__", "siblingNewName"),
    ("__SIBLING_NEW_PATH__", "siblingNewPath"),
    ("__RESERVED_TARGET__", "reservedTarget"),
    ("__RESERVED_NEW_NAME__", "reservedNewName"),
    ("__RESERVED_NEW_PATH__", "reservedNewPath"),
    ("__UDT_TARGET__", "udtTarget"),
    ("__UDT_NEW_NAME__", "udtNewName"),
    ("__UDT_NEW_PATH__", "udtNewPath"),
    ("__TARGET_NEW_PATH__", "targetNewPath"),
    ("__TARGET_NEW_NAME__", "targetNewName"),
    ("__TARGET__", "target"),
)

#: The Bad QualityCode a `system.tag.deleteTags` answers for a path that is gone.
#: The shape is the recorded one from the ticket #7 `tag_write` evidence (code 260,
#: `Bad_NotFound`, level `Error`); a live Gateway's own answer is what the live
#: stage records, and this models it for the rehearsal.
BAD_NOT_FOUND_OUTCOME = {
    "code": 260, "name": "Bad_NotFound", "level": "Error", "good": False,
    "diagnosticMessage": "The path does not exist.",
}


def _tag_delete_body(
    server: Any, arguments: dict[str, Any], body: dict[str, Any],
) -> dict[str, Any]:
    """Substitute the run-scoped values a recorded `tag_delete` refusal carries."""
    return _render_tag_error_body(
        server, body, _TAG_DELETE_TEMPLATES, server.tag_delete_paths, arguments,
        limit=_served_item_limit(server, "tagDeleteMaxItems"),
    )


def _tag_move_body(
    server: Any, arguments: dict[str, Any], body: dict[str, Any],
) -> dict[str, Any]:
    """Substitute the run-scoped values a recorded `tag_move` refusal carries."""
    return _render_tag_error_body(
        server, body, _TAG_MOVE_TEMPLATES, server.tag_move_paths, arguments,
        limit=_served_item_limit(server, "tagMoveMaxItems"),
    )


def _tag_rename_body(
    server: Any, arguments: dict[str, Any], body: dict[str, Any],
) -> dict[str, Any]:
    """Substitute the run-scoped values a recorded `tag_rename` refusal carries."""
    return _render_tag_error_body(
        server, body, _TAG_RENAME_TEMPLATES, server.tag_rename_paths, arguments,
        limit=_served_item_limit(server, "tagRenameMaxItems"),
    )


def _zip_entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {entry.filename: archive.read(entry.filename) for entry in archive.infolist()}


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return output.getvalue()


def _gateway_export(project: bytes) -> bytes:
    """Apply the deterministic project.json serialization recorded on export."""
    entries = _zip_entries(project)
    entries["project.json"] = json.dumps(
        json.loads(entries["project.json"]), sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return _zip_bytes(entries)


def _gateway_import(current: bytes | None, incoming: bytes) -> bytes:
    """Model the recorded project.json-only import transition and fixed point."""
    if current is None:
        return _gateway_export(incoming)
    current_entries = _zip_entries(current)
    incoming_entries = _zip_entries(incoming)
    current_project = json.loads(current_entries["project.json"])
    incoming_project = json.loads(incoming_entries["project.json"])
    if incoming_project != current_project:
        incoming_entries["project.json"] = json.dumps(
            incoming_project, sort_keys=True,
        ).encode("utf-8")
    else:
        incoming_entries["project.json"] = current_entries["project.json"]
    return _zip_bytes(incoming_entries)


GOOD_QUALITY = {
    "code": 192, "name": "Good", "level": "Good", "good": True, "diagnosticMessage": None,
}


def _policy_probe_report(server: Any, report: dict[str, Any]) -> dict[str, Any]:
    """Apply the modelled provider state to one recorded `policy_probe` report.

    `policy_provider_unready_reads` counts down per probe: while it is positive
    (or `-1`, which never becomes ready) the provider answers handler reads with
    `Error_Configuration`. That is the state the live 8.3.9 run of
    `Phase 4 Live Gateway G4a` 35654626095 recorded: `/resources/find` and
    `/tags/export` answered 200 for a provider whose Tag actors had not started.
    """

    remaining = server.policy_provider_unready_reads
    if remaining == 0:
        return report
    if remaining > 0:
        server.policy_provider_unready_reads = remaining - 1
    document = json.loads(json.dumps(report))
    for entry in document.get("measurements") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        if name.startswith("tag.readBlocking."):
            for item in entry.get("items") or []:
                if isinstance(item, dict):
                    item["quality"] = 'Error_Configuration("The Tag provider is not serving tags.")'
            entry["jsonKind"] = ""
            entry["jsonKeys"] = []
        elif name.startswith("tag.gatedRead."):
            entry["gate"] = "blocked"
            entry["reason"] = "the running provider is not serving tags"
            entry["materialized"] = False
    return document


def _record_tag_import(server: Any, body: bytes) -> None:
    """Track the policy document an accepted import leaves in the provider.

    `setup-native apply` (and the ticket #7 harness) verifies the *served*
    document through a handler-scope read, so the fake has to remember what it
    was asked to write instead of only replaying the response body.
    """
    try:
        document = json.loads(body)
    except (ValueError, TypeError):
        return
    if not isinstance(document, dict):
        return
    entries = [entry for entry in document.get("tags") or [] if isinstance(entry, dict)]
    for entry in entries:
        value = entry.get("value")
        if entry.get("name") == "RuntimeTargetPolicy" and isinstance(value, str):
            server.policy_value = value
        if isinstance(value, str) and "WriteProbe" == entry.get("name"):
            server.write_probe_value = value
    if entries:
        # Ticket #21: what the provider *serves* is what was imported, so
        # `setup-native apply`'s read-back (and its declared-length companion) can be
        # compared with the document it wrote. Before the first import the recorded
        # provider export fixture answers instead.
        server.served_policy_tags = entries


def _tag_read_replay(server: Any, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The recorded `tag_read` domain for a path the fake models, else None.

    The fake answers an invalid path with the recorded Tool Error the Phase 3
    rehearsal relies on, so only modeled paths take this branch.
    """
    paths = [str(path) for path in arguments.get("tagPaths") or []]
    if not paths:
        return None
    items = []
    for path in paths:
        if path == "[IgnitionMCPPolicy]RuntimeTargetPolicy" and server.policy_value:
            items.append({"path": path, "status": "ok", "value": server.policy_value,
                          "quality": GOOD_QUALITY, "timestamp": "2026-09-22T00:00:00Z"})
            continue
        if path == "[IgnitionMCPPolicy]WriteProbe" and server.write_probe_value:
            items.append({"path": path, "status": "ok", "value": server.write_probe_value,
                          "quality": GOOD_QUALITY, "timestamp": "2026-09-22T00:00:00Z"})
            continue
        if path in server.tag_state:
            items.append({"path": path, "status": "ok", "value": server.tag_state[path],
                          "quality": GOOD_QUALITY, "timestamp": "2026-09-22T00:00:00Z"})
            continue
        return None
    return {
        "items": items,
        "summary": {"requested": len(paths), "succeeded": len(items), "failed": 0},
        "meta": {"correlationId": "recorded-replay"},
    }


def _published_configuration(configuration: list[Any]) -> list[Any]:
    """The D28-encoded form a Tool publishes, which is also what it fingerprints.

    The fake stores the native configuration it serves; a handler publishes the
    encoded form and fingerprints exactly that, so the fake does the same and the
    live driver's check (which hashes the published value as it stands) holds.
    """
    from tooling.contracts.lint import encode_nulls

    return encode_nulls(configuration)


def _tag_config_fingerprint(configuration: list[Any]) -> str:
    from tooling.contracts.lint import tag_config_fingerprint

    return tag_config_fingerprint(_published_configuration(configuration))


#: The node a Gateway answers for a configuration read of a path that is not
#: there, recorded from phase4-live-g4b run 35668653064 on both rows: the same
#: node and the same fingerprint on 8.3.8 and 8.3.9. The `path` comes back as a
#: native `BasicTagPath` object, which is why the recorded body carries the
#: handler's own native-object form of it.
def _synthesized_node(path: str) -> dict[str, Any]:
    """The node a Gateway answers for a configuration read of a path that is not there.

    The body is the recorded one (`phase4/tag-get-config-missing-template.json`), with
    the requested path and its leaf substituted.
    """
    template = _fixture("phase4/tag-get-config-missing-template.json")["configuration"][0]
    body = json.dumps(template).replace("__PATH__", path).replace("__NAME__", path.rsplit("/", 1)[-1])
    return json.loads(body)


def _tag_config_domain(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """The recorded `tag_get_config` domain for a path the fake models.

    A path the fake has no configuration for answers the synthesized node a live
    Gateway answers (recorded), which is what makes the missing-target case model
    the Gateway rather than the rehearsal's convenience.
    """
    path = arguments.get("path")
    if not isinstance(path, str):
        return None
    if path not in server.tag_config:
        configuration = _published_configuration([_synthesized_node(path)])
        return {
            "path": path,
            "recursive": bool(arguments.get("recursive", False)),
            "overridesOnly": bool(arguments.get("overridesOnly", False)),
            "fingerprint": _tag_config_fingerprint(configuration),
            "configuration": configuration,
            "summary": {"returned": 1, "limit": 50},
            "meta": {"correlationId": "recorded-replay"},
        }
    configuration = _published_configuration(server.tag_config[path])
    recursive = bool(arguments.get("recursive", False))
    overrides_only = bool(arguments.get("overridesOnly", False))
    limit = arguments.get("maxResults")
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = 50
    return {
        "path": path,
        "recursive": recursive,
        "overridesOnly": overrides_only,
        "fingerprint": _tag_config_fingerprint(configuration),
        "configuration": configuration,
        "summary": {"returned": len(configuration) if recursive else 1, "limit": limit},
        "meta": {"correlationId": "recorded-replay"},
    }


def _provider_component(path: str) -> str:
    """The bracketed provider of a Tag path, which is what the reserved rule names.

    A prefix or substring test would also refuse a provider whose name merely
    starts with the reserved one, so the comparison is on the component itself.
    """
    if not path.startswith("["):
        return ""
    closing = path.find("]")
    if closing <= 1:
        return ""
    return path[1:closing]


def _tag_update_paths(arguments: dict[str, Any]) -> list[str]:
    return [
        str(item.get("path", ""))
        for item in (arguments.get("items") or [])
        if isinstance(item, dict)
    ]


def _served_item_limit(server: Any, field: str) -> int:
    """The item ceiling one Tool's policy field carries, defaulting to D10's 20.

    Each CONFIG Mutation has its own field, so a refusal quotes the number the
    *served* document names for that Tool, not a constant the fake shares with it.
    """
    try:
        document = json.loads(server.policy_value)
        limit = document.get(field)
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_MAX_ITEMS
    return int(limit) if isinstance(limit, int) and not isinstance(limit, bool) else DEFAULT_MAX_ITEMS


def _served_allowlist(server: Any, tool: str) -> list[str]:
    """The allowlist the policy Tag currently serves for one Tool, if any."""
    try:
        document = json.loads(server.policy_value)
        entries = (document.get("allowlists") or {}).get(tool)
    except (AttributeError, TypeError, ValueError):
        return []
    return [str(entry) for entry in entries] if isinstance(entries, list) else []


def _served_audit_mode(server: Any) -> str:
    """The Runtime audit mode the served policy carries; D18's three states."""
    try:
        document = json.loads(server.policy_value)
        mode = document.get("auditMode")
    except (AttributeError, TypeError, ValueError):
        return "best_effort"
    return mode if mode in {"best_effort", "required", "off"} else "best_effort"


def _allowlisted(path: str, entries: list[str]) -> bool:
    """D30 1: an entry is a provider-qualified prefix, matched at segment boundaries.

    A substring test would let `[default]IgnitionMCP_CI` cover its `IgnitionMCP_CI2`
    sibling, which is the exact regression the segment-boundary case exists to catch.
    """
    return any(entry == "*" or path == entry or path.startswith(entry + "/") for entry in entries)


def _names_udt_namespace(path: str) -> bool:
    """Whether the path itself sits inside a provider's `_types_` namespace.

    D30 §6 names `[provider]_types_/...`, so the rule is positional: only the first
    post-provider segment selects the definition namespace, and a folder that merely
    happens to be called `_types_` deeper in the path is an ordinary target. The
    shipped handlers implement exactly this, and the fake has to as well or a
    rehearsal would answer a path the Gateway does not.
    """
    closing = path.find("]")
    if closing <= 0:
        return False
    segments = [segment for segment in path[closing + 1:].split("/") if segment]
    return bool(segments) and segments[0] == "_types_"


def _udt_allowlisted(path: str, entries: list[str]) -> bool:
    """D30 6: only an entry that itself names `_types_` lets a definition through."""
    return any(
        entry != "*" and _names_udt_namespace(entry) and _allowlisted(path, [entry])
        for entry in entries
    )


def _tag_create_paths(arguments: dict[str, Any]) -> list[str]:
    return [str(item.get("path", "")) for item in (arguments.get("items") or []) if isinstance(item, dict)]


def _tag_copy_pairs(arguments: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (str(item.get("sourcePath", "")), str(item.get("destinationPath", "")))
        for item in (arguments.get("items") or []) if isinstance(item, dict)
    ]


def _tag_create_case(server: Any, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded `tag_create` refusal, or the modelled create, these arguments ask for.

    The selection mirrors the shipped handler's own order: the two D10 ceilings a
    request crosses with no native call at all, then the policy gate, then the
    deployment's item ceiling, then the reserved provider, the allowlist with D30 6's
    `_types_` rule, and last `system.tag.exists` — which for a create has to answer
    *absent*, because an existing target is the collision the caller is told about
    instead of an overwrite.
    """
    items = [item for item in (arguments.get("items") or []) if isinstance(item, dict)]
    paths = [str(item.get("path", "")) for item in items]
    if len(items) > HARD_MAX_ITEMS:
        return "items-over-hard-limit", paths
    if any(len(path.encode("utf-8")) > PATH_MAX_BYTES for path in paths):
        return "path-over-length", paths
    if not items or not server.policy_provider_created or not server.policy_value:
        return "no-policy", paths
    if len(items) > _served_item_limit(server, "tagCreateMaxItems"):
        return "over-policy-limit", paths
    entries = _served_allowlist(server, "tag_create")
    refused: list[tuple[int, str]] = []
    for index, path in enumerate(paths):
        if _provider_component(path) == "IgnitionMCPPolicy":
            refused.append((index, "reserved-provider-refusal"))
        elif _names_udt_namespace(path) and not _udt_allowlisted(path, entries):
            refused.append((index, "udt-not-allowlisted"))
        elif not _allowlisted(path, entries):
            refused.append((index, "sibling-denial"))
    if refused:
        # A refused Preflight lists every failing item and executes none, so a batch
        # whose allowed item would have created something replays the body that names
        # only the refused end.
        return (refused[0][1] if len(refused) == len(items) else "preflight-refusal"), paths
    if any(path in server.tag_config for path in paths):
        return "target-exists", paths
    return "created", paths


def _apply_tag_create(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Model one `system.tag.configure(base, [config + name], "Abort")` per item.

    The created node is the item's own configuration plus the target's leaf as its
    name, which is what the target path — not the configuration — decides. Storing it
    in the served configuration is what lets the driver's independent `tag_get_config`
    re-read and the provider export see the node the create promised to build.
    """
    items = [item for item in (arguments.get("items") or []) if isinstance(item, dict)]
    server.tag_create_correlation_id = "recorded-replay"
    results = []
    observed = []
    for item in items:
        path = str(item.get("path", ""))
        node = {str(key): value for key, value in (item.get("config") or {}).items()}
        node["name"] = _target_leaf(path)
        node.setdefault("tagType", "AtomicTag")
        node["path"] = path
        server.tag_config[path] = [node]
        results.append({
            "path": path,
            "status": "executed",
            "nativeOutcome": dict(GOOD_OUTCOME),
        })
        observed.append({
            "path": path,
            "status": "ok",
            "fingerprint": _tag_config_fingerprint(server.tag_config[path]),
            "configuration": _published_configuration(server.tag_config[path]),
        })
    return _tag_mutation_result(
        server, items=len(items), results=results, observed=observed,
    )


def _tag_copy_case(server: Any, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded `tag_copy` refusal, or the modelled copy, these arguments ask for.

    Same order as the shipped handler, with the two differences the contract names:
    the destination's leaf is an *input* rule (one native call lands each source
    under its own name), and only the destination is measured against the allowlist
    and D30 6's `_types_` rule, while the reserved provider bounds both ends because a
    copy out of the policy provider would publish the document elsewhere. Preflight
    then reads the endpoints: the source has to exist and answer a configuration read,
    the destination has to be free.
    """
    items = [item for item in (arguments.get("items") or []) if isinstance(item, dict)]
    pairs = _tag_copy_pairs(arguments)
    paths = [destination for _source, destination in pairs]
    if len(items) > HARD_MAX_ITEMS:
        return "items-over-hard-limit", paths
    # The handler refuses a destination whose leaf differs inside its input pass, and
    # measures the D10 ceilings only after every item passed it, so a request with both
    # problems reports the leaf. The fake has to take the same order or a rehearsal
    # would pick the ceiling body where a Gateway answers the leaf one.
    if any(_target_leaf(source) != _target_leaf(destination) for source, destination in pairs):
        return "destination-leaf-mismatch", paths
    if any(
        len(value.encode("utf-8")) > PATH_MAX_BYTES
        for source, destination in pairs for value in (source, destination)
    ):
        return "path-over-length", paths
    if not items or not server.policy_provider_created or not server.policy_value:
        return "no-policy", paths
    if len(items) > _served_item_limit(server, "tagCopyMaxItems"):
        return "over-policy-limit", paths
    entries = _served_allowlist(server, "tag_copy")
    refused: list[tuple[int, str]] = []
    for index, (source, destination) in enumerate(pairs):
        if _provider_component(source) == "IgnitionMCPPolicy":
            refused.append((index, "reserved-source-refusal"))
        elif _provider_component(destination) == "IgnitionMCPPolicy":
            refused.append((index, "reserved-destination-refusal"))
        elif _names_udt_namespace(destination) and not _udt_allowlisted(destination, entries):
            refused.append((index, "udt-not-allowlisted"))
        elif not _allowlisted(destination, entries):
            refused.append((index, "sibling-denial"))
    if refused:
        return (refused[0][1] if len(refused) == len(items) else "preflight-refusal"), paths
    for source, destination in pairs:
        if source not in server.tag_config:
            return "source-missing", paths
        if destination in server.tag_config:
            return "destination-exists", paths
    return "copied", paths


def _apply_tag_copy(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Model one `system.tag.copy([source], targetBase(destination), "Abort")` per item.

    The copied node carries the source's configuration and its own destination path,
    and the source stays exactly where it was: a copy is not a move, and the driver
    proves that by re-reading both ends.
    """
    pairs = _tag_copy_pairs(arguments)
    server.tag_copy_correlation_id = "recorded-replay"
    results = []
    observed = []
    for source, destination in pairs:
        node = json.loads(json.dumps((server.tag_config.get(source) or [{}])[0]))
        node["name"] = _target_leaf(destination)
        if isinstance(node.get("path"), dict):
            node["path"] = dict(node["path"], text=destination)
        else:
            node["path"] = destination
        server.tag_config[destination] = [node]
        results.append({
            "sourcePath": source,
            "destinationPath": destination,
            "status": "executed",
            "nativeOutcome": dict(GOOD_OUTCOME),
        })
        observed.append({
            "path": destination,
            "status": "ok",
            "fingerprint": _tag_config_fingerprint(server.tag_config[destination]),
            "configuration": _published_configuration(server.tag_config[destination]),
        })
    return _tag_mutation_result(
        server, items=len(pairs), results=results, observed=observed,
    )


def _tag_delete_paths(arguments: dict[str, Any]) -> list[str]:
    return [str(item.get("path", "")) for item in (arguments.get("items") or []) if isinstance(item, dict)]


def _tag_move_pairs(arguments: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (
            str(item.get("sourcePath", "")),
            str(item.get("destinationPath", "")),
            str(item.get("expectedFingerprint", "")),
        )
        for item in (arguments.get("items") or []) if isinstance(item, dict)
    ]


def _tag_rename_items(arguments: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [
        (
            str(item.get("path", "")),
            str(item.get("newName", "")),
            str(item.get("expectedFingerprint", "")),
        )
        for item in (arguments.get("items") or []) if isinstance(item, dict)
    ]


def _renamed_path(path: str, new_name: str) -> str:
    """The new path a rename makes: the target's own parent plus the new name.

    `system.tag.rename` takes a name, never a path, so the parent survives — which is
    what D30 §6 measures against the allowlist. A target at the provider root has the
    bracket as its base, so the name joins it directly.
    """
    index = path.rfind("/")
    base = path[0:index] if index > path.find("]") else path[0:path.find("]") + 1]
    return base + new_name if base.endswith("]") else base + "/" + new_name


def _tag_delete_case(server: Any, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded `tag_delete` refusal, or the modelled delete, these arguments ask for.

    The selection mirrors the shipped handler's order: the input pass, the two D10
    ceilings a request crosses with no native call at all, the policy gate, the
    deployment's item ceiling, the reserved provider, the allowlist with D30 §6's
    `_types_` rule, then `system.tag.exists` and the Precondition token.
    """
    raw_items = (arguments.get("items") or [])
    items = [item for item in raw_items if isinstance(item, dict)]
    paths = _tag_delete_paths(arguments)
    if any(set(item) != {"path", "expectedFingerprint"} for item in items) or len(items) != len(raw_items):
        return "item-keys", paths
    if len(items) > HARD_MAX_ITEMS:
        return "items-over-hard-limit", paths
    if any(len(path.encode("utf-8")) > PATH_MAX_BYTES for path in paths):
        return "path-over-length", paths
    if not items or not server.policy_provider_created or not server.policy_value:
        return "no-policy", paths
    if len(items) > _served_item_limit(server, "tagDeleteMaxItems"):
        return "over-policy-limit", paths
    entries = _served_allowlist(server, "tag_delete")
    refused: list[tuple[int, str]] = []
    for index, path in enumerate(paths):
        if _provider_component(path) == "IgnitionMCPPolicy":
            refused.append((index, "reserved-provider-refusal"))
        elif _names_udt_namespace(path) and not _udt_allowlisted(path, entries):
            refused.append((index, "udt-not-allowlisted"))
        elif not _allowlisted(path, entries):
            refused.append((index, "sibling-denial"))
    if refused:
        return (refused[0][1] if len(refused) == len(items) else "preflight-refusal"), paths
    for path in paths:
        if path not in server.tag_config:
            return "missing-target", paths
    for item, path in zip(items, paths, strict=True):
        if item.get("expectedFingerprint") != _tag_config_fingerprint(server.tag_config.get(path, [])):
            return "stale-fingerprint", paths
    return "deleted", paths


def _apply_tag_delete(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Model one `system.tag.deleteTags([path])` per item.

    A Folder takes everything beneath it, so the modelled delete drops the node and
    every path under it — which is what makes a batch that names a folder and a Tag
    inside it a real partial failure: the second item's own call answers Bad, nothing
    is retried, and no rollback undoes the first item.
    """
    items = [item for item in (arguments.get("items") or []) if isinstance(item, dict)]
    server.tag_delete_correlation_id = "recorded-replay"
    results = []
    observed = []
    succeeded = 0
    failed = 0
    for item in items:
        path = str(item.get("path", ""))
        if path in server.tag_config:
            for key in [key for key in list(server.tag_config) if key == path or key.startswith(path + "/")]:
                del server.tag_config[key]
            results.append({"path": path, "status": "executed", "nativeOutcome": dict(GOOD_OUTCOME)})
            succeeded += 1
        else:
            # The item passed Preflight and the path is gone at dispatch: the Batch
            # reports that item's own Bad outcome and nothing is rolled back.
            outcome = dict(BAD_NOT_FOUND_OUTCOME)
            outcome["diagnosticMessage"] = "Path '" + path + "' not found."
            results.append({"path": path, "status": "executed", "nativeOutcome": outcome})
            failed += 1
        observed.append({"path": path, "status": "ok", "absent": True})
    server.tag_delete_targets = ",".join(item.get("path", "") for item in items)
    return _tag_mutation_result(
        server, items=len(items), results=results, observed=observed,
        succeeded=succeeded, failed=failed,
    )


def _tag_move_case(server: Any, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded `tag_move` refusal, or the modelled move, these arguments ask for.

    Same order as the shipped handler: the input pass (the destination's leaf is an
    input rule, because one `system.tag.move` call lands each source under its own
    name), the D10 ceilings, the policy gate, the deployment's item ceiling, both
    ends' reserved-provider and allowlist rules, then the source's existence and
    Precondition token and the destination's absence.
    """
    raw_items = (arguments.get("items") or [])
    items = [item for item in raw_items if isinstance(item, dict)]
    pairs = _tag_move_pairs(arguments)
    paths = [destination for _source, destination, _fingerprint in pairs]
    if any(set(item) != {"sourcePath", "destinationPath", "expectedFingerprint"} for item in items) \
            or len(items) != len(raw_items):
        return "item-keys", paths
    if any(_target_leaf(source) != _target_leaf(destination) for source, destination, _f in pairs):
        return "destination-leaf-mismatch", paths
    if len(items) > HARD_MAX_ITEMS:
        return "items-over-hard-limit", paths
    if any(
        len(value.encode("utf-8")) > PATH_MAX_BYTES
        for source, destination, _fingerprint in pairs for value in (source, destination)
    ):
        return "path-over-length", paths
    if not items or not server.policy_provider_created or not server.policy_value:
        return "no-policy", paths
    if len(items) > _served_item_limit(server, "tagMoveMaxItems"):
        return "over-policy-limit", paths
    entries = _served_allowlist(server, "tag_move")
    refused: list[tuple[int, str]] = []
    for index, (source, destination, _fingerprint) in enumerate(pairs):
        if _provider_component(source) == "IgnitionMCPPolicy":
            refused.append((index, "reserved-source-refusal"))
        elif _provider_component(destination) == "IgnitionMCPPolicy":
            refused.append((index, "reserved-destination-refusal"))
        elif _names_udt_namespace(source) and not _udt_allowlisted(source, entries):
            refused.append((index, "udt-source-not-allowlisted"))
        elif not _allowlisted(source, entries):
            refused.append((index, "source-not-allowlisted"))
        elif _names_udt_namespace(destination) and not _udt_allowlisted(destination, entries):
            refused.append((index, "udt-not-allowlisted"))
        elif not _allowlisted(destination, entries):
            refused.append((index, "destination-not-allowlisted"))
    if refused:
        return (refused[0][1] if len(refused) == len(items) else "preflight-refusal"), paths
    for source, destination, fingerprint in pairs:
        if source not in server.tag_config:
            return "source-missing", paths
        if fingerprint != _tag_config_fingerprint(server.tag_config.get(source, [])):
            return "stale-fingerprint", paths
        if destination in server.tag_config:
            return "destination-exists", paths
    return "moved", paths


def _apply_tag_move(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Model one `system.tag.move([source], destinationParent, "Abort")` per item.

    The moved node keeps its configuration and gains the destination path, the source
    is gone, and the Observed state reports both ends in that order.
    """
    pairs = _tag_move_pairs(arguments)
    server.tag_move_correlation_id = "recorded-replay"
    results = []
    observed = []
    for source, destination, _fingerprint in pairs:
        if source in server.tag_config:
            for key in [key for key in list(server.tag_config) if key == source or key.startswith(source + "/")]:
                node = json.loads(json.dumps(server.tag_config.pop(key)))
                moved = key.replace(source, destination, 1)
                if isinstance(node[0].get("path"), dict):
                    node[0]["path"] = dict(node[0]["path"], text=moved)
                else:
                    node[0]["path"] = moved
                server.tag_config[moved] = node
        results.append({
            "sourcePath": source,
            "destinationPath": destination,
            "status": "executed",
            "nativeOutcome": dict(GOOD_OUTCOME),
        })
        observed.append({
            "path": destination,
            "status": "ok",
            "absent": False,
            "fingerprint": _tag_config_fingerprint(server.tag_config.get(destination, [])),
            "configuration": _published_configuration(server.tag_config.get(destination, [])),
        })
        observed.append({"path": source, "status": "ok", "absent": True})
    server.tag_move_targets = ",".join(
        path for pair in pairs for path in (pair[1], pair[0])
    )
    return _tag_mutation_result(
        server, items=len(pairs), results=results, observed=observed,
    )


def _tag_rename_case(server: Any, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded `tag_rename` refusal, or the modelled rename, these arguments ask for.

    Same order as the shipped handler: the input pass (a new name is one path segment,
    and the new path is the target's own parent plus that name), the D10 ceilings, the
    policy gate, the deployment's item ceiling, the reserved provider and the new
    path's allowlist, then the target's existence and token and the new path's
    absence.
    """
    raw_items = (arguments.get("items") or [])
    items = [item for item in raw_items if isinstance(item, dict)]
    pairs = _tag_rename_items(arguments)
    paths = [_renamed_path(path, new_name) for path, new_name, _fingerprint in pairs]
    if any(set(item) != {"path", "newName", "expectedFingerprint"} for item in items) \
            or len(items) != len(raw_items):
        return "item-keys", paths
    if any(not _one_segment(new_name) for _path, new_name, _fingerprint in pairs):
        return "new-name-not-a-segment", paths
    if len(items) > HARD_MAX_ITEMS:
        return "items-over-hard-limit", paths
    if any(
        len(value.encode("utf-8")) > PATH_MAX_BYTES
        for path, _new_name, _fingerprint in pairs for value in (path, _renamed_path(path, _new_name))
    ):
        return "path-over-length", paths
    if not items or not server.policy_provider_created or not server.policy_value:
        return "no-policy", paths
    if len(items) > _served_item_limit(server, "tagRenameMaxItems"):
        return "over-policy-limit", paths
    entries = _served_allowlist(server, "tag_rename")
    refused: list[tuple[int, str]] = []
    for index, (path, new_name, _fingerprint) in enumerate(pairs):
        new_path = _renamed_path(path, new_name)
        if _provider_component(path) == "IgnitionMCPPolicy" \
                or _provider_component(new_path) == "IgnitionMCPPolicy":
            refused.append((index, "reserved-provider-refusal"))
        elif _names_udt_namespace(new_path) and not _udt_allowlisted(new_path, entries):
            refused.append((index, "udt-not-allowlisted"))
        elif not _allowlisted(new_path, entries):
            refused.append((index, "target-not-allowlisted"))
    if refused:
        return (refused[0][1] if len(refused) == len(items) else "preflight-refusal"), paths
    for (path, _new_name, fingerprint), new_path in zip(pairs, paths, strict=True):
        if path not in server.tag_config:
            return "missing-target", paths
        if fingerprint != _tag_config_fingerprint(server.tag_config.get(path, [])):
            return "stale-fingerprint", paths
        if new_path in server.tag_config:
            return "new-path-exists", paths
    return "renamed", paths


def _apply_tag_rename(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Model one `system.tag.rename(target, newName, "Abort")` per item.

    The node stays in its own parent and takes the new name, the old path is gone, and
    the Observed state reports the new path first and the old path second.
    """
    entries = _tag_rename_items(arguments)
    server.tag_rename_correlation_id = "recorded-replay"
    results = []
    observed = []
    for path, new_name, _fingerprint in entries:
        new_path = _renamed_path(path, new_name)
        if path in server.tag_config:
            for key in [key for key in list(server.tag_config) if key == path or key.startswith(path + "/")]:
                node = json.loads(json.dumps(server.tag_config.pop(key)))
                renamed = new_path + key[len(path):]
                if isinstance(node[0].get("path"), dict):
                    node[0]["path"] = dict(node[0]["path"], text=renamed)
                else:
                    node[0]["path"] = renamed
                node[0]["name"] = new_name if key == path else node[0].get("name")
                server.tag_config[renamed] = node
        results.append({
            "path": path,
            "newPath": new_path,
            "status": "executed",
            "nativeOutcome": dict(GOOD_OUTCOME),
        })
        observed.append({
            "path": new_path,
            "status": "ok",
            "absent": False,
            "fingerprint": _tag_config_fingerprint(server.tag_config.get(new_path, [])),
            "configuration": _published_configuration(server.tag_config.get(new_path, [])),
        })
        observed.append({"path": path, "status": "ok", "absent": True})
    server.tag_rename_targets = ",".join(
        path for pair in entries for path in (_renamed_path(pair[0], pair[1]), pair[0])
    )
    return _tag_mutation_result(
        server, items=len(entries), results=results, observed=observed,
    )


def _one_segment(value: str) -> bool:
    """Whether a new name is one path segment, as the shipped rename rule decides."""
    if not value or value != value.strip():
        return False
    return not any(character in value for character in ("/", ".", "[", "]", "*", "?", ":"))


def _tag_mutation_result(
    server: Any, *, items: int, results: list[dict[str, Any]], observed: list[dict[str, Any]],
    succeeded: int | None = None, failed: int = 0,
) -> dict[str, Any]:
    """The structuredContent a dispatched batch publishes.

    The default counts are the driver's own arithmetic on `items`, because every item
    a batch case models dispatches and succeeds; a Tool whose dispatch can answer a
    per-item Bad outcome passes the counts its own applier observed on the served
    state. The audit mode is the served document's.
    """
    return {
        "content": [{"type": "text", "text": "recorded replay"}],
        "isError": False,
        "structuredContent": {
            "items": results,
            "observed": observed,
            "summary": {
                "requested": items,
                "succeeded": len(results) if succeeded is None else succeeded,
                "failed": failed,
                "outcomeUnknown": 0,
                "notExecuted": 0, "auditMode": _served_audit_mode(server), "auditRecorded": True,
            },
            "meta": {"correlationId": "recorded-replay"},
        },
    }


def _tag_update_case(server: Any, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded `tag_update` refusal, or the modelled merge, these arguments ask for.

    The selection mirrors the shipped handler's own order — policy gate, input,
    reserved provider, allowlist (with D30 6's `_types_` rule), existence, then the
    Precondition token — so the rehearsal exercises the branches the live Gateway
    answers in the same order.
    """
    items = [item for item in (arguments.get("items") or []) if isinstance(item, dict)]
    paths = [str(item.get("path", "")) for item in items]
    if not items or not server.policy_provider_created or not server.policy_value:
        return "no-policy", paths
    # D10: the deployment's item ceiling is checked right after the Policy read,
    # before any Target or Precondition check.
    if len(items) > _served_item_limit(server, "tagUpdateMaxItems"):
        return "over-policy-limit", paths
    if any(_provider_component(path) == "IgnitionMCPPolicy" for path in paths):
        return "reserved-provider-refusal", paths
    entries = _served_allowlist(server, "tag_update")
    for path in paths:
        if "_types_" in path and not any("_types_" in entry for entry in entries):
            return "udt-not-allowlisted", paths
    if any("IgnitionMCP_CI2" in path for path in paths):
        return "sibling-denial", paths
    if any(path not in server.tag_config for path in paths):
        return "missing-target", paths
    for item in items:
        path = str(item.get("path", ""))
        actual = _tag_config_fingerprint(server.tag_config.get(path, []))
        if item.get("expectedFingerprint") != actual:
            return "stale-fingerprint", paths
    return "allowlisted", paths


def _apply_tag_update(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Merge the requested properties the way MergeOverwrite leaves them.

    Only the driver's own merge is modelled: the item's properties are merged into
    the node's top-level configuration, a property the read did not carry is added,
    and the observed half of the result is the post-merge state.
    """
    items = [item for item in (arguments.get("items") or []) if isinstance(item, dict)]
    server.tag_update_correlation_id = "recorded-replay"
    results = []
    observed = []
    succeeded = 0
    for item in items:
        path = str(item.get("path", ""))
        node = dict((server.tag_config.get(path) or [{}])[0])
        for key, value in (item.get("config") or {}).items():
            node[str(key)] = value
        node["name"] = path.rsplit("/", 1)[-1]
        server.tag_config[path] = [node]
        results.append({
            "path": path,
            "status": "executed",
            "nativeOutcome": {
                "code": 192, "name": "Good", "level": "Good", "good": True,
                "diagnosticMessage": {"$ignition": "null"},
            },
        })
        observed.append({
            "path": path,
            "status": "ok",
            "fingerprint": _tag_config_fingerprint(server.tag_config[path]),
            "configuration": _published_configuration(server.tag_config[path]),
        })
        succeeded += 1
    return {
        "content": [{"type": "text", "text": "recorded replay"}],
        "isError": False,
        "structuredContent": {
            "items": results,
            "observed": observed,
            "summary": {
                "requested": len(items), "succeeded": succeeded, "failed": 0, "outcomeUnknown": 0,
                "notExecuted": 0, "auditMode": "best_effort", "auditRecorded": True,
            },
            "meta": {"correlationId": "recorded-replay"},
        },
    }


def _tag_write_case(server: Any, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded `tag_write` body the fake replays for these arguments."""
    writes = arguments.get("writes")
    paths = [
        str(item.get("path", ""))
        for item in (writes or []) if isinstance(item, dict)
    ]
    if not server.policy_provider_created:
        return "no-policy", paths
    if any(path.startswith("[IgnitionMCPPolicy]") for path in paths):
        return "reserved-provider-refusal", paths
    if any("IgnitionMCP_CI2" in path for path in paths):
        return ("preflight-refusal" if len(paths) > 1 else "sibling-denial"), paths
    return "allowlisted-batch", paths


def _installed_shelve_cap(server: Any) -> int:
    """The shelve cap the policy Tag currently served carries, defaulting to the D12 hard max."""
    try:
        document = json.loads(server.policy_value)
        cap = document.get("alarmShelveMaxSeconds")
    except (TypeError, ValueError):
        return 86400
    return int(cap) if isinstance(cap, int) and not isinstance(cap, bool) else 86400


def _alarm_mutation_paths(arguments: dict[str, Any]) -> list[str]:
    return [str(path) for path in arguments.get("paths") or []]


def _alarm_mutation_case(server: Any, tool: str, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded Alarm Mutation body the fake replays for these arguments.

    The case selection mirrors the shipped Tool's own refusal order, so the
    rehearsal exercises the same branches the live Gateway answers: the policy
    gate, the input grammar, the allowlist and the (shelve-only) duration bounds.
    """
    paths = _alarm_mutation_paths(arguments)
    if not server.policy_provider_created:
        return "no-policy", paths
    if any("*" in path for path in paths):
        return "wildcard-refusal", paths
    if any("_sibling" in path for path in paths):
        return ("preflight-refusal" if len(paths) > 1 else "sibling-denial"), paths
    if tool == "alarm_shelve":
        seconds = arguments.get("timeoutSeconds")
        if seconds == 86401:
            return "hard-max-refusal", paths
        if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > _installed_shelve_cap(server):
            return "cap-refusal", paths
    return "allowlisted", paths


def _apply_alarm_mutation_case(server: Any, tool: str, case: str, body: dict[str, Any]) -> None:
    """Model the shelving state an executed Alarm Mutation leaves behind."""
    structured = body.get("structuredContent") or {}
    correlation = str((structured.get("meta") or {}).get("correlationId", ""))
    if correlation:
        server.alarm_correlation_id = correlation
    if case != "allowlisted":
        return
    for item in structured.get("observed") or []:
        if not isinstance(item, dict) or item.get("status") != "ok":
            continue
        path = str(item.get("path", ""))
        if not path:
            continue
        if tool == "alarm_shelve" and item.get("shelved") is True:
            server.shelved_paths[path] = {
                "user": item.get("user"),
                "expiration": item.get("expiration"),
                "expired": bool(item.get("expired")),
            }
        if tool == "alarm_unshelve":
            server.shelved_paths.pop(path, None)


def _alarm_shelved_list_result(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """The `alarm_shelved_list` domain for the shelving state the fake models."""
    limit = arguments.get("maxResults")
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = 100
    items = [
        {"path": path, "user": entry.get("user"), "expiration": entry.get("expiration"),
         "expired": bool(entry.get("expired"))}
        for path, entry in sorted(server.shelved_paths.items())
    ]
    return {
        "items": items,
        "summary": {"returned": len(items), "limit": limit},
        "meta": {"correlationId": "recorded-replay"},
    }


def _apply_tag_write_case(server: Any, case: str, body: dict[str, Any]) -> None:
    if case != "allowlisted-batch":
        return
    structured = body.get("structuredContent") or {}
    server.tag_write_correlation_id = str((structured.get("meta") or {}).get("correlationId", ""))
    for item in structured.get("observed") or []:
        if isinstance(item, dict) and item.get("status") == "ok" and item.get("path") in server.tag_state:
            server.tag_state[str(item["path"])] = item.get("value")


def _uploaded_module_identity(archive: bytes) -> dict[str, Any] | None:
    """The identity the uploaded ``.modl`` declares, parsed from its own module.xml.

    The install flow serves the module back under the id and the display version the
    archive carries, so the rehearsal drives the real bytes of the pinned fixture and
    the fake comes back with exactly the build that file declares.
    """

    if not archive.startswith(b"PK"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zp:
            document = zp.read("module.xml")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    try:
        root = ElementTree.fromstring(document.decode("utf-8"))
    except (ElementTree.ParseError, UnicodeDecodeError):
        return None
    node = root if root.tag == "module" else root.find("module")
    if node is None:
        return None
    module_id = (node.findtext("id") or "").strip()
    raw_version = (node.findtext("version") or "").strip()
    if not module_id or not raw_version:
        return None
    logical, build = raw_version, ""
    parts = raw_version.split(".")
    if len(parts) > 3:
        candidate = parts[3].split("-")[0]
        if len(candidate) == 10 and candidate.isdigit():
            build = candidate
            logical = ".".join(parts[:3]) + parts[3][len(candidate):]
    return {"id": module_id, "version": logical, "build": build}


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, status: int, body: bytes = b"", content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value, separators=(",", ":")).encode("utf-8"))

    def _read_body(self) -> bytes:
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            body = bytearray()
            while True:
                size = int(self.rfile.readline().strip().split(b";", 1)[0], 16)
                if size == 0:
                    self.rfile.readline()
                    return bytes(body)
                body.extend(self.rfile.read(size))
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self) -> None:  # noqa: N802
        server: Any = self.server
        server.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers), "body": b""})
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path.startswith("/data/mcp/"):
            self._json(415, _fixture("http/unsupported-media-type.json"))
            return
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            self._json(403, _fixture("http/forbidden.json"))
            return
        if path.startswith("/data/api/v1/projects/export/"):
            name = path.rsplit("/", 1)[-1]
            project = server.observe_export(name)
            if project is None:
                self._json(404, {"message": "No project", "status": "404"})
                return
            self._send(200, project, "application/zip")
            return
        if path == "/data/api/v1/gateway-info":
            self._json(200, {
                "name": "recorded-gateway",
                "edition": "standard",
                "ignitionVersion": "8.3.8 (b2026071409)",
            })
            return
        if path == "/openapi.json":
            if server.openapi_missing_responses > 0:
                server.openapi_missing_responses -= 1
                self._json(200, {"openapi": "3.0.1", "paths": {}})
            else:
                server.modules_active = True
                document = _fixture("phase2/openapi-required.json")
                document["components"] = _committed_components()
                paths: dict[str, dict[str, object]] = document["paths"]
                for method, operation_path in _OPENAPI_OPERATIONS:
                    paths.setdefault(operation_path, {})[method] = _recorded_operation(method, operation_path)
                self._json(200, document)
            return
        if path == "/data/api/v1/resources/names/ignition/database-driver":
            self._json(200, _fixture("phase2/database-drivers.json"))
            return
        if path == "/data/api/v1/resources/names/ignition/database-translator":
            self._json(200, _fixture("phase2/database-translators.json"))
            return
        if path == "/data/api/v1/modules/quarantined":
            payload = _fixture("phase2/modules-quarantined.json")
            payload["items"] = [
                item for item in payload["items"] if item["id"] in server.quarantined_modules
            ]
            self._json(200, payload)
            return
        if path == "/data/api/v1/modules/healthy":
            if server.module_install_flow:
                # Ticket #56: the stateful install flow. No module until the operator
                # installed one and the Gateway came back from its restart.
                items = []
                uploaded = server.uploaded_module
                if uploaded and server.module_installed and server.module_restarted:
                    items = [{
                        "id": uploaded["id"],
                        "name": uploaded["id"],
                        "version": f"{uploaded['version']} (b{uploaded['build']})",
                        "installed": True,
                        "healthy": True,
                    }]
                self._json(200, {"items": items})
                return
            fixture = (
                "phase2/modules-healthy-after-restart.json"
                if server.modules_active
                else "phase2/modules-healthy-before-restart.json"
            )
            self._json(200, _fixture(fixture))
            return
        if path == "/data/alarm-notification/api/v1/pipeline":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            payload = server.read_pipeline(_first(query, "path", ""), query)
            if payload is None:
                self._json(404, {"message": "No such pipeline", "status": "404"})
                return
            self._json(200, payload)
            return
        if path == "/data/api/v1/projects/list":
            names = sorted(server.projects)
            self._json(200, {
                "items": [{"name": name} for name in names],
                "metadata": {
                    "total": float(len(names)),
                    "matching": float(len(names)),
                    "limit": 500,
                    "offset": 0,
                },
            })
            return
        if path.startswith("/data/api/v1/projects/find/"):
            name = path.rsplit("/", 1)[-1]
            project = server.projects.get(name)
            if project is None:
                self._json(404, {"message": "No project", "status": "404"})
                return
            document = json.loads(_zip_entries(project)["project.json"])
            self._json(200, {
                "name": name,
                "title": document.get("title", name),
                "description": document.get("description", ""),
                "inheritable": bool(document.get("inheritable", False)),
            })
            return
        if path.startswith("/data/api/v1/audit/log/"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            action_filter = (query.get("actionFilter") or [""])[0]
            if action_filter.startswith("ignition-mcp.alarm") and server.alarm_correlation_id:
                tool = action_filter.rsplit(".", 1)[-1]
                document = json.loads(json.dumps(_fixture("phase4/audit-log-alarm.json")))
                for row in document["items"]:
                    row["action"] = str(row["action"]).replace("__ACTION__", action_filter)
                    row["actionValue"] = str(row["actionValue"]).replace("__TOOL__", tool).replace(
                        "__CORRELATION__", server.alarm_correlation_id,
                    )
                self._json(200, document)
                return
            if action_filter.startswith("ignition-mcp.tag_update") and server.tag_update_correlation_id:
                document = json.loads(json.dumps(_fixture("phase4/audit-log-config.json")))
                for row in document["items"]:
                    row["actionTarget"] = str(row["actionTarget"]).replace(
                        "__TARGET__", server.tag_update_paths.get("writeTarget", ""),
                    )
                    row["actionValue"] = str(row["actionValue"]).replace(
                        "__CORRELATION__", server.tag_update_correlation_id,
                    )
                self._json(200, document)
                return
            if action_filter in (
                "ignition-mcp.tag_create", "ignition-mcp.tag_copy",
                "ignition-mcp.tag_delete", "ignition-mcp.tag_move", "ignition-mcp.tag_rename",
            ):
                # Ticket #11 and #12: each CONFIG Mutation audits under its own action,
                # so the driver's read-back can only match the rows of the Tool it
                # called.
                tool = action_filter.rsplit(".", 1)[-1]
                correlation = getattr(server, tool + "_correlation_id", "")
                if not correlation:
                    self._json(200, {
                        "items": [],
                        "metadata": {"total": 0.0, "matching": 0.0, "limit": 100, "offset": 0},
                    })
                    return
                document = json.loads(json.dumps(_fixture("phase4/audit-log-tag-config.json")))
                if tool in ("tag_delete", "tag_move", "tag_rename"):
                    # Ticket #12: the action target is the paths the call's Observed
                    # state reports, which the applier recorded for this batch.
                    target = getattr(server, tool + "_targets", "")
                else:
                    paths = server.tag_create_paths if tool == "tag_create" else server.tag_copy_paths
                    target = paths.get("createTarget" if tool == "tag_create" else "destination", "")
                for row in document["items"]:
                    row["action"] = str(row["action"]).replace("__ACTION__", action_filter)
                    row["actionTarget"] = str(row["actionTarget"]).replace("__TARGET__", target)
                    row["actionValue"] = str(row["actionValue"]).replace("__TOOL__", tool).replace(
                        "__CORRELATION__", correlation,
                    )
                self._json(200, document)
                return
            if server.tag_write_correlation_id:
                document = json.loads(json.dumps(_fixture("phase4/audit-log.json")))
                for row in document["items"]:
                    row["actionValue"] = str(row["actionValue"]).replace("__CORRELATION__", server.tag_write_correlation_id)
                self._json(200, document)
                return
            self._json(200, {"items": [], "metadata": {"total": 0.0, "matching": 0.0, "limit": 100, "offset": 0}})
            return
        if path.startswith("/data/api/v1/resources/find/ignition/tag-provider/"):
            name = path.rsplit("/", 1)[-1]
            # Ticket #6's recorded provider document, for the harness that provisions the
            # Runtime Target Policy through this route. Every other name — and the same
            # name before that harness has created it — is the generic recorded behaviour
            # below, so a test that seeds its own Tag-provider resources (ticket #36's
            # by-name refusal) reads them back like any other resource.
            if name == server.policy_provider and server.policy_provider_created:
                self._json(200, _fixture("phase4/tag-provider-find.json"))
                return
        # Ticket #21: the Server Config find answers from the modelled resource state
        # (``server.resources``), exactly as every other config-resource read does, so
        # a config apply created is readable and a config that is not there is a 404.
        if path.startswith("/data/api/v1/resources/"):
            payload = server.read_resource(
                path,
                urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query),
            )
            if payload is None:
                self._json(404, {"message": "No recorded resource", "status": "404"})
                return
            self._json(200, payload)
            return
        if path == "/data/api/v1/tags/export":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            provider = query.get("provider", [""])[0]
            if provider == server.policy_provider:
                if not server.policy_provider_created:
                    self._json(404, {"message": "No tag provider", "status": "404"})
                    return
                served = server.served_policy_tags
                document = (
                    {"name": "", "tagType": "Provider", "tags": served}
                    if served else _fixture("phase4/tag-export.json")
                )
                self._send(200, json.dumps(document, separators=(",", ":")).encode("utf-8"),
                           "application/octet-stream")
                return
            if provider in server.tags:
                # Phase 4 ticket #17: a modelled provider exports its own state, so an
                # import the Tool dispatched is visible to the bounded re-export that
                # verifies it.
                document = server.export_tags(
                    provider,
                    _first(query, "path", ""),
                    recursive=_first(query, "recursive", "true") != "false",
                    include_udts=_first(query, "includeUdts", "true") != "false",
                )
                if document is None:
                    self._json(404, {"message": "No such tag path", "status": "404"})
                    return
                self._send(200, json.dumps(document, separators=(",", ":")).encode("utf-8"),
                           "application/octet-stream")
                return
            if provider == server.tag_state_provider:
                # Ticket #10: the disposable Tag provider the Tag CONFIG Mutation
                # cases run against. The fake answers from the configuration it
                # serves, so an export that does not carry a path is evidence the
                # Tool never created it.
                self._json(200, {
                    "path": "",
                    "tags": [
                        {"name": path.rsplit("/", 1)[-1], "path": path, "tagType": "AtomicTag"}
                        for path in sorted(server.tag_config)
                        if path.startswith("[" + provider + "]")
                    ],
                })
                return
            self._json(200, {"path": "", "tags": [{"name": "Status", "tagType": "Boolean"}]})
            return
        if path == "/data/api/v1/designers":
            self._json(200, {
                "items": [],
                "metadata": {"total": 0.0, "matching": 0.0, "limit": 100, "offset": 0},
            })
            return
        if path == "/data/api/v1/modules/certificate":
            module_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("moduleId", [""])[0]
            uploaded = server.uploaded_module
            if not server.module_install_flow or uploaded is None or module_id != uploaded["id"]:
                self._json(404, {"message": "Module not uploaded", "status": "404"})
                return
            # Modelled, not recorded: the operator-facing fields the 8.3.8 OpenAPI
            # documents for the certificate view. The rehearsal only needs the CLI to
            # see a certificate it must accept; the live run records the real fields.
            self._json(200, {
                "subjectName": f"CN={uploaded['id']}",
                "issuerName": f"CN={uploaded['id']}",
                "notValidBefore": "2026-01-01T00:00:00Z",
                "notValidAfter": "2036-01-01T00:00:00Z",
                "selfSigned": True,
            })
            return
        if path == "/data/api/v1/modules/eula":
            module_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("moduleId", [""])[0]
            uploaded = server.uploaded_module
            if not server.module_install_flow or uploaded is None or module_id != uploaded["id"]:
                self._json(404, {"message": "No EULA found for module", "status": "404"})
                return
            self._send(200, b"<html><body>modelled module EULA</body></html>", "text/html")
            return
        self._json(404, {"message": "No recorded response", "status": "404"})

    def do_POST(self) -> None:  # noqa: N802
        server: Any = self.server
        body = self._read_body()
        server.requests.append({"method": "POST", "path": self.path, "headers": dict(self.headers), "body": body})
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path.startswith("/data/mcp/"):
            if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
                self._json(403, _fixture("http/forbidden.json"))
                return
            payload = json.loads(body)
            method = payload.get("method")
            name = path.rsplit("/", 1)[-1]
            if server.server_config_is_stale(name):
                # The Module's server for this Server Config was built before the
                # Project's provider registered: it answers ``initialize`` with no
                # capability at all, and every primitive method is Invalid Request.
                if method == "initialize":
                    config = (server.server_config_named(name) or {}).get("config") or {}
                    self._json(200, {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "serverInfo": {
                                "name": name,
                                "title": str(config.get("title") or name),
                                "version": str(config.get("version") or ""),
                            },
                        },
                    })
                    return
                if method == "notifications/initialized":
                    self._send(202)
                    return
                response = _fixture("mcp/prompts-list-invalid-request.json")
                response["id"] = payload.get("id")
                self._json(200, response)
                return
            if method == "initialize":
                response = _fixture("mcp/initialize.json")
                response["id"] = payload.get("id")
                response["result"]["serverInfo"] = {
                    "name": "phase3-runtime",
                    "title": "Phase 3 Runtime Readonly",
                    "version": server.bundle_version,
                }
                self._json(200, response)
                return
            if method == "notifications/initialized":
                self._send(202)
                return
            if method == "prompts/list":
                response = _fixture("mcp/prompts-list-invalid-request.json")
                response["id"] = payload.get("id")
                self._json(200, response)
                return
            if method == "tools/list":
                if str(path).endswith("phase4-operator"):
                    operator_tools = [
                        str(item["name"]) for item in _fixture("phase4/tools-list-operator.json")["tools"]
                    ]
                elif str(path).endswith("phase4-configurator"):
                    # Read from the contract, exactly as the live workflow's Server
                    # Config is written from it (D09's explicit lists).
                    profile = json.loads(
                        (ROOT / "contracts/profiles/configurator.yaml").read_text(encoding="utf-8")
                    )
                    operator_tools = [str(name) for name in profile["tools"]]
                else:
                    operator_tools = list(server.runtime_tools)
                self._json(200, {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"tools": [
                        {"name": name, "description": f"{name} recorded replay"}
                        for name in operator_tools
                    ]},
                })
                return
            if method == "resources/list":
                self._json(200, {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"resources": [
                        {"uri": uri, "name": uri.rsplit("/", 1)[-1]}
                        for uri in server.runtime_resources
                    ]},
                })
                return
            if method == "resources/read":
                uri = str((payload.get("params") or {}).get("uri"))
                self._json(200, {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"contents": [{
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps({"schemaVersion": 1, "recordedReplay": True}),
                    }]},
                })
                return
            if method == "tools/call":
                params = payload.get("params") or {}
                tool = str(params.get("name"))
                tool_arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                if tool == "bundle_info":
                    result = {
                        "content": [{"type": "text", "text": "recorded replay"}],
                        "isError": False,
                        "structuredContent": {
                            "bundleVersion": server.deployed_bundle_version(),
                            "bundleSourceRevision": server.source_revision,
                            "gatewayVersion": "8.3.8 (b2026071409)",
                            "mcpModuleVersion": "1.3.5-SNAPSHOT",
                            "compatibilityStatus": "UNKNOWN",
                        },
                    }
                elif tool == "tag_read":
                    domain = _tag_read_replay(server, tool_arguments)
                    if domain is None:
                        result = {
                            "content": [{"type": "text", "text": "tag path is not valid"}],
                            "isError": True,
                        }
                    else:
                        result = {
                            "content": [{"type": "text", "text": "recorded replay"}],
                            "isError": False,
                            "structuredContent": domain,
                        }
                elif tool == "tag_fixture_probe":
                    report = json.loads(json.dumps(_fixture("phase4/tag-fixture-probe.json")))
                    # Ticket #10: the fake serves the fixture Tags' configuration
                    # from the recorded body, so `tag_get_config` and `tag_update`
                    # then agree on one state exactly as the live Gateway does.
                    seeded = json.loads(json.dumps(_fixture("phase4/tag-config.json")))
                    for path, configuration in seeded.items():
                        server.tag_config.setdefault(path, configuration)
                    for entry in report.get("initialValues") or []:
                        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                            raw = str(entry.get("value"))
                            server.tag_state[entry["path"]] = int(raw) if raw.lstrip("-").isdigit() else raw
                    server.tag_state.setdefault("[default]IgnitionMCP_CI/Nested/Inner", 0)
                    result = {
                        "content": [{"type": "text", "text": "recorded replay"}],
                        "isError": False,
                        "structuredContent": report,
                    }
                elif tool == "tag_get_config":
                    domain = _tag_config_domain(server, tool_arguments)
                    if domain is None:  # pragma: no cover - the fake models every valid path
                        result = {
                            "content": [{"type": "text", "text": json.dumps({
                                "code": "not_found",
                                "message": "The Tag configuration read found no such path.",
                                "correlationId": "recorded-replay",
                            })},
                            ],
                            "isError": True,
                        }
                    else:
                        result = {
                            "content": [{"type": "text", "text": "recorded replay"}],
                            "isError": False,
                            "structuredContent": domain,
                        }
                elif tool == "tag_update":
                    case, _paths = _tag_update_case(server, tool_arguments)
                    if case == "allowlisted":
                        result = _apply_tag_update(server, tool_arguments)
                    else:
                        body = json.loads(json.dumps(_fixture(f"phase4/tag-update-{case}.json")))
                        result = _tag_update_body(server, tool_arguments, body)
                elif tool == "tag_create":
                    case, _paths = _tag_create_case(server, tool_arguments)
                    if case == "created":
                        result = _apply_tag_create(server, tool_arguments)
                    else:
                        body = json.loads(json.dumps(_fixture(f"phase4/tag-create-{case}.json")))
                        result = _tag_create_body(server, tool_arguments, body)
                elif tool == "tag_copy":
                    case, _paths = _tag_copy_case(server, tool_arguments)
                    if case == "copied":
                        result = _apply_tag_copy(server, tool_arguments)
                    else:
                        body = json.loads(json.dumps(_fixture(f"phase4/tag-copy-{case}.json")))
                        result = _tag_copy_body(server, tool_arguments, body)
                elif tool == "tag_delete":
                    case, _paths = _tag_delete_case(server, tool_arguments)
                    if case == "deleted":
                        result = _apply_tag_delete(server, tool_arguments)
                    else:
                        body = json.loads(json.dumps(_fixture(f"phase4/tag-delete-{case}.json")))
                        result = _tag_delete_body(server, tool_arguments, body)
                elif tool == "tag_move":
                    case, _paths = _tag_move_case(server, tool_arguments)
                    if case == "moved":
                        result = _apply_tag_move(server, tool_arguments)
                    else:
                        body = json.loads(json.dumps(_fixture(f"phase4/tag-move-{case}.json")))
                        result = _tag_move_body(server, tool_arguments, body)
                elif tool == "tag_rename":
                    case, _paths = _tag_rename_case(server, tool_arguments)
                    if case == "renamed":
                        result = _apply_tag_rename(server, tool_arguments)
                    else:
                        body = json.loads(json.dumps(_fixture(f"phase4/tag-rename-{case}.json")))
                        result = _tag_rename_body(server, tool_arguments, body)
                elif tool == "tag_write":
                    case, _paths = _tag_write_case(server, tool_arguments)
                    body = json.loads(json.dumps(_fixture(f"phase4/tag-write-{case}.json")))
                    _apply_tag_write_case(server, case, body)
                    result = body
                elif tool in {"alarm_shelve", "alarm_unshelve"}:
                    case, _paths = _alarm_mutation_case(server, tool, tool_arguments)
                    body = _alarm_body(server, f"phase4/{tool.replace('_', '-')}-{case}.json")
                    _apply_alarm_mutation_case(server, tool, case, body)
                    result = body
                elif tool == "alarm_shelved_list":
                    result = {
                        "content": [{"type": "text", "text": "recorded replay"}],
                        "isError": False,
                        "structuredContent": _alarm_shelved_list_result(server, tool_arguments),
                    }
                elif tool in {"policy_probe", "alarm_probe"}:
                    fixture = "phase4/policy-probe.json" if tool == "policy_probe" else "phase4/alarm-probe.json"
                    report = _fixture(fixture) if tool == "policy_probe" else _alarm_body(server, fixture)
                    if tool == "policy_probe":
                        report = _policy_probe_report(server, report)
                    result = {
                        "content": [{"type": "text", "text": "recorded replay"}],
                        "isError": False,
                        "structuredContent": report,
                    }
                else:
                    self._json(200, {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "error": {"code": -32602, "message": f"unknown tool {tool}"},
                    })
                    return
                self._json(200, {"jsonrpc": "2.0", "id": payload.get("id"), "result": result})
                return
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            self._json(403, _fixture("http/forbidden.json"))
            return
        # Phase 4 ticket #15: a type the harness has published resource state for
        # answers the write surface from that state, so create and rename behave as
        # the real Gateway does. A type with no published state keeps the recorded
        # Phase 2/3 provisioning behaviour of its own exact-path branch below.
        if path.startswith(_RENAME_ROUTE_PREFIX):
            resource_type, _, name = path[len(_RENAME_ROUTE_PREFIX):].rpartition("/")
            # D30 owner ruling 5: the documented rename route takes the collection as a
            # query parameter, and that is the collection the rename applies to.
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            status, payload = server.apply_resource_rename(
                resource_type, name, body, _first(query, "collection", DEFAULT_COLLECTION),
            )
            self._json(status, payload)
            return
        resource_type = _resource_type_segment(path)
        if resource_type is not None and resource_type in server.resources:
            status, payload = server.apply_resource_create(resource_type, body)
            self._json(status, payload)
            return
        if path.startswith("/data/api/v1/projects/import/"):
            name = path.rsplit("/", 1)[-1]
            status, payload = server.apply_project_import(name, body)
            self._json(status, payload)
            return
        if path == "/data/api/v1/resources/ignition/database-connection":
            resources = json.loads(body)
            config = resources[0].get("config", {}) if isinstance(resources, list) and resources else {}
            if config.get("driver") == "PostgreSQL":
                self._json(422, _fixture("phase2/postgresql-missing.json"))
                return
            if isinstance(config.get("password"), str):
                self._json(422, _fixture("phase2/credential-object-required.json"))
                return
            self._json(200, {})
            return
        if path == "/data/api/v1/resources/com.inductiveautomation.historian/historian-provider":
            if not server.modules_active:
                self._json(404, _fixture("phase2/no-route.json"))
                return
            self._json(200, {})
            return
        if path == "/data/api/v1/resources/ignition/audit-profile":
            self._json(200, {})
            return
        if path == "/data/api/v1/api-token/generate":
            # Ticket #22: the Gateway's own key/hash generator. It persists nothing;
            # the pair is what the token create below stores.
            self._json(200, server.generated_token_pair)
            return
        if path == "/data/api/v1/resources/ignition/api-token":
            # Ticket #22: the Runtime API token an opt-in apply creates. The type has
            # no published state until a token is seeded or created, so this branch
            # publishes it on demand instead of at startup, which would change what the
            # ticket #15/#36 cases read back from an unseeded Gateway.
            server.resources.setdefault(API_TOKEN_TYPE, {})
            status, payload = server.apply_resource_create(API_TOKEN_TYPE, body)
            self._json(status, payload)
            return
        if path == "/data/api/v1/resources/ignition/tag-provider":
            resources = json.loads(body)
            names = [item.get("name") for item in resources] if isinstance(resources, list) else []
            if server.policy_provider not in names:
                self._json(422, {"message": "Unexpected provider name", "status": "422"})
                return
            server.policy_provider_created = True
            self._json(200, _fixture("phase4/tag-provider-create.json"))
            return
        if path == "/data/api/v1/tags/import":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            provider = query.get("provider", [""])[0]
            collision_policy = query.get("collisionPolicy", [""])[0]
            _record_tag_import(server, body)
            if provider == server.policy_provider:
                if not server.policy_provider_created:
                    self._json(404, _fixture("phase2/no-route.json"))
                    return
                if server.policy_provider_unready_reads != 0:
                    # A provider that has not finished starting applies the import to
                    # its config while the Tag's actor never starts — the recorded
                    # `Bad 776 ... cleanPath is null` hazard, which a handler read
                    # then answers with Error_Configuration forever.
                    self._json(200, _fixture("phase4/tag-import-provider-not-ready.json"))
                    return
                if not server.policy_tags_imported:
                    if not server.policy_import_flaked:
                        # The recorded 8.3.8 run answered the first import while
                        # the provider was still starting; apply must retry.
                        server.policy_import_flaked = True
                        self._json(200, _fixture("phase4/tag-import-provider-not-ready.json"))
                        return
                    server.policy_tags_imported = True
                    self._json(200, _fixture("phase4/tag-import-success.json"))
                    return
                if collision_policy == "Abort":
                    self._json(200, _fixture("phase4/tag-import-abort.json"))
                    return
                self._json(200, _fixture("phase4/tag-import-merge.json"))
                return
            if provider in server.tags:
                # Phase 4 ticket #17: a modelled provider applies the documented
                # transition against its own state, so the Tool's collision check and
                # its bounded re-export both see the same Tags the Gateway serves.
                status, payload = server.apply_tag_import(
                    provider,
                    _first(query, "path", ""),
                    body,
                    collision_policy=collision_policy,
                )
                self._json(status, payload)
                return
            document = json.loads(body)
            if isinstance(document, list):
                self._json(200, _fixture("phase2/tag-import-bare-array.json"))
            else:
                # The green provision recording retained the 200 status, not a response body.
                self._send(200)
            return
        if path == "/data/api/v1/modules/certificate":
            module_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("moduleId", [""])[0]
            if server.module_install_flow:
                uploaded = server.uploaded_module
                if uploaded is None or module_id != uploaded["id"]:
                    self._json(404, {"message": "Module not uploaded", "status": "404"})
                    return
                if server.module_certificate_accepted:
                    self._json(409, _fixture("phase2/certificate-already-accepted.json"))
                    return
                server.module_certificate_accepted = True
                self._json(200, {"success": True, "message": "Module certificate accepted."})
                return
            if module_id not in server.quarantined_modules:
                self._json(404, {"message": "Module not quarantined", "status": "404"})
                return
            if server.certificate_accepted:
                self._json(409, _fixture("phase2/certificate-already-accepted.json"))
                return
            server.certificate_accepted = True
            self._json(200, {})
            return
        if path == "/data/api/v1/modules/eula":
            module_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("moduleId", [""])[0]
            uploaded = server.uploaded_module
            if not server.module_install_flow or uploaded is None or module_id != uploaded["id"]:
                self._json(404, {"message": "Module not uploaded", "status": "404"})
                return
            if server.module_eula_accepted:
                self._json(409, {"message": "Module EULA already accepted.", "url": self.path, "status": "409"})
                return
            server.module_eula_accepted = True
            self._json(200, {"success": True, "message": "EULA accepted successfully"})
            return
        if path == "/data/api/v1/modules/upload":
            if not server.module_install_flow:
                self._json(404, {"message": "No recorded response", "status": "404"})
                return
            file_name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("fileName", [""])[0]
            uploaded = _uploaded_module_identity(body)
            if uploaded is None or not uploaded["build"]:
                # Not a .modl, or one without a comparable build: a Gateway would
                # store it and quarantine it; the install flow has no use for it.
                self._json(400, {"message": "Not a usable module archive", "status": "400"})
                return
            server.uploaded_module = uploaded
            server.upload_name = file_name
            self._json(200, {"moduleId": uploaded["id"], "licenseAccepted": False})
            return
        if path == "/data/api/v1/modules/install":
            module_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("moduleId", [""])[0]
            uploaded = server.uploaded_module
            if not server.module_install_flow or uploaded is None or module_id != uploaded["id"]:
                self._json(404, {"message": "Module not uploaded", "status": "404"})
                return
            if not (server.module_certificate_accepted and server.module_eula_accepted):
                self._json(400, {"message": "Accept the module's certificate and EULA first", "status": "400"})
                return
            server.module_installed = True
            self._json(200, {
                "filename": server.upload_name,
                "onStartup": "true",
                "upgradeVersion": uploaded["version"],
            })
            return
        if path == "/data/api/v1/restart-tasks/restart":
            confirm = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("confirm", [""])[0]
            if not server.module_install_flow:
                self._json(404, {"message": "No recorded response", "status": "404"})
                return
            if confirm != "true":
                self._json(400, {"message": "Please confirm restart through a 'confirm' query parameter.", "status": "400"})
                return
            # A Gateway can drop the connection on the way down; a 200 with no body is
            # the acknowledgement, and the module comes back only after the restart.
            if server.module_installed:
                server.module_restarted = True
            self._send(200)
            return
        self._json(404, {"message": "No recorded response", "status": "404"})

    def do_PUT(self) -> None:  # noqa: N802
        server: Any = self.server
        body = self._read_body()
        server.requests.append({"method": "PUT", "path": self.path, "headers": dict(self.headers), "body": body})
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            self._json(403, _fixture("http/forbidden.json"))
            return
        resource_type = _resource_type_segment(path)
        if resource_type is None or resource_type not in server.resources:
            self._json(404, {"message": "No recorded response", "status": "404"})
            return
        status, payload = server.apply_resource_update(resource_type, body)
        self._json(status, payload)

    def do_DELETE(self) -> None:  # noqa: N802
        server: Any = self.server
        body = self._read_body()
        server.requests.append({
            "method": "DELETE", "path": self.path, "headers": dict(self.headers), "body": body,
        })
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            self._json(403, _fixture("http/forbidden.json"))
            return
        # Phase 4 ticket #18: the documented cancel route takes its two facts in the
        # request body (`DELETE` with a JSON body), unlike the resource deletes below,
        # whose signature travels in the path.
        if path == "/data/alarm-notification/api/v1/pipeline":
            status, payload = server.apply_pipeline_cancel(body)
            self._json(status, payload)
            return
        # The signature travels in the path, exactly as the documented route has it:
        # `/<type>/{name}/{signature}`, or `/<type>/{signature}` for a singleton.
        resource_type, name, signature = _delete_target(path)
        if resource_type is None:
            self._json(404, {"message": "No recorded response", "status": "404"})
            return
        # D30 owner ruling 5: the documented DELETE route takes the collection as a
        # query parameter, and that is the collection the delete applies to.
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        status, payload = server.apply_resource_delete(
            resource_type, name, signature, _first(query, "collection", DEFAULT_COLLECTION),
        )
        self._json(status, payload)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        projects: dict[str, bytes],
        openapi_missing_responses: int,
        quarantined_modules: tuple[str, ...],
        runtime_tools: tuple[str, ...],
        runtime_resources: tuple[str, ...],
        source_revision: str,
        bundle_version: str,
        policy_provider: str = "",
        port: int = 0,
        audit_profile: str = "",
        alarm_root: str = "",
        tag_update_paths: dict[str, str] | None = None,
        tag_create_paths: dict[str, str] | None = None,
        tag_copy_paths: dict[str, str] | None = None,
        tag_delete_paths: dict[str, str] | None = None,
        tag_move_paths: dict[str, str] | None = None,
        tag_rename_paths: dict[str, str] | None = None,
        primitive_pickup_delay: float = 0.0,
        module_install_flow: bool = False,
    ) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.requests: list[dict[str, Any]] = []
        self.projects = {name: _gateway_export(project) for name, project in projects.items()}
        self.imports: list[str] = []
        # Ticket #21's live-reload hazard, modelled: the Module resolves a Server
        # Config's Tool list against its provider registry at the moment the resource
        # is written, and a Project's provider is registered by the Project
        # collection's own notification thread. ``primitive_pickup_delay`` is that
        # thread's turn: a Project imported now has its primitives available that many
        # seconds later, and a Server Config written before then serves *no* primitives
        # (``capabilities={}``, every list ``-32600``) until the resource is written
        # again. ``0.0`` (the default) means a deployment the fake was started with.
        self.primitive_pickup_delay = primitive_pickup_delay
        self.project_imported_at: float | None = None
        self.server_config_stale: set[str] = set()
        self.openapi_missing_responses = openapi_missing_responses
        self.quarantined_modules = set(quarantined_modules)
        self.certificate_accepted = False
        self.modules_active = openapi_missing_responses == 0 and not quarantined_modules
        self.runtime_tools = runtime_tools
        self.runtime_resources = runtime_resources
        self.source_revision = source_revision
        self.bundle_version = bundle_version
        self.policy_provider = policy_provider
        self.policy_provider_created = False
        self.policy_tags_imported = False
        self.policy_import_flaked = False
        #: The second recorded provider-startup hazard: a freshly created provider
        #: answers handler reads of its Tags with `Error_Configuration` until it has
        #: finished loading, and an import applied in that window leaves a Tag whose
        #: actor never starts. A positive count models a provider that becomes
        #: ready after that many handler reads, `-1` models one that never does.
        self.policy_provider_unready_reads = 0
        #: Modelled Tag state, keyed by provider: the provider root node in the
        #: recorded JSON export shape (``{"name": "", "tagType": "Provider", "tags": [...]}``).
        #: Seed it through :meth:`RecordedGateway.seed_tags`; the tag routes answer
        #: from it exactly as the recorded Gateway answers from its own Tag state.
        self.tags: dict[str, dict[str, Any]] = {}
        #: Tag imports the fixture actually applied (provider, path, Tag names), the
        #: observation a test asserts alongside the recorded requests.
        self.tag_imports: list[dict[str, Any]] = []
        #: Which wire shape the import route answers a reported failure with: the
        #: summary object live 8.3.8/8.3.9 return, or the list of non-Good QualityCodes
        #: the committed OpenAPI documents.
        self.tag_import_wire_shape = "summary"
        #: When set, the import route applies only the first N nodes of the document
        #: and reports the rest as failures — a partial application.
        self.tag_import_partial: int | None = None
        #: Modelled, not recorded: the import route reports a clean success and creates
        #: nothing, so only the bounded re-export can tell the difference.
        self.tag_import_lies = False
        #: Modelled, not recorded: the 2xx body the import route answers with whatever
        #: the transition did, for a case that needs a response this server cannot
        #: interpret (a negative count, a count of the wrong type, ``null``). Set it
        #: through :meth:`answer_tag_import_with`.
        self.tag_import_body: Any = _UNSET
        # Ticket #7: the fake models Tag values so a `tag_read` after a Mutation
        # reports what that Mutation recorded, and the audit log can answer with
        # the correlation ID the recorded `tag_write` result carried.
        self.audit_profile = audit_profile
        self.tag_state: dict[str, Any] = {}
        self.tag_write_correlation_id = ""
        #: Ticket #8: the shelving state a replayed Alarm Mutation leaves behind,
        #: plus the correlation ID the recorded result carried so the audit log can
        #: answer for it. `alarm_root` is the run-unique Alarm root the driver used,
        #: substituted for `__ALARM_ROOT__` in the recorded Alarm bodies.
        self.shelved_paths: dict[str, dict[str, Any]] = {}
        self.alarm_correlation_id = ""
        self.alarm_root = alarm_root
        #: The Tag CONFIG Mutation paths of a ticket #10 run. The driver passes them
        #: through `RecordedGateway(tag_update_paths=...)`, and a recorded refusal
        #: body templates them because they are the run's own. Ticket #11's two Tools
        #: carry their own path sets for the same reason: a `tag_copy` refusal names the
        #: refused *end*, which is not a path any ticket #10 run had a name for.
        self.tag_update_paths: dict[str, str] = dict(tag_update_paths or {})
        self.tag_create_paths: dict[str, str] = dict(tag_create_paths or {})
        self.tag_copy_paths: dict[str, str] = dict(tag_copy_paths or {})
        #: Ticket #12's three Tools carry their own path sets for the same reason:
        #: a `tag_move` or `tag_rename` refusal names an endpoint pair, and a
        #: `tag_delete` refusal names the target, none of which is a path any earlier
        #: ticket's run had a name for.
        self.tag_delete_paths: dict[str, str] = dict(tag_delete_paths or {})
        self.tag_move_paths: dict[str, str] = dict(tag_move_paths or {})
        self.tag_rename_paths: dict[str, str] = dict(tag_rename_paths or {})
        #: The correlation ID the modelled Mutation result carried, so the audit log can
        #: answer for it the way the recorded `tag_write` rows do.
        self.tag_update_correlation_id = ""
        self.tag_create_correlation_id = ""
        self.tag_copy_correlation_id = ""
        self.tag_delete_correlation_id = ""
        self.tag_move_correlation_id = ""
        self.tag_rename_correlation_id = ""
        #: The action target the audit rows of the last modelled batch carry: the
        #: paths that batch's Observed state reports, in the handler's own order.
        self.tag_delete_targets = ""
        self.tag_move_targets = ""
        self.tag_rename_targets = ""
        #: The provider the ticket #10 Tag fixture lives in; its export is modelled
        #: from the configuration the fake serves.
        self.tag_state_provider = "default"
        self.policy_value = ""
        #: Ticket #21: the Tags the reserved provider currently serves (the last
        #: imported document), so an apply read-back sees what it wrote.
        self.served_policy_tags: list[dict[str, Any]] = []
        # Ticket #56: the module-install flow. A server started with
        # ``module_install_flow`` has NO MCP Module: ``modules/healthy`` serves an
        # empty list until the operator uploads, accepts and installs one through the
        # documented module routes and restarts the Gateway, exactly the state
        # ``setup-native install-module`` drives. The build the module comes back
        # with is parsed from the uploaded archive's own module.xml.
        self.module_install_flow = module_install_flow
        self.uploaded_module: dict[str, Any] | None = None
        self.upload_name = ""
        self.module_certificate_accepted = False
        self.module_eula_accepted = False
        self.module_installed = False
        self.module_restarted = False
        self.write_probe_value = "phase4-write-probe-value"
        #: Ticket #10: the Tag configuration the fake serves, keyed by exact path.
        #: `tag_get_config` answers from it and `tag_update` merges into it, so a
        #: local rehearsal can follow a change from one Tool to the other. The
        #: fingerprints are derived with the contracts linter's own copy of the D30
        #: rule (`tooling.contracts.lint`), which is the definition the shipped
        #: handler implements; a rehearsal is never evidence, and the live run
        #: re-reads everything through a real Gateway.
        self.tag_config: dict[str, list[Any]] = {}
        #: config resource state: resource type -> (name, collection) -> document.
        #: Keying by collection as well as name is what makes the fixture able to
        #: tell two resources with one name in different collections apart. Seed it
        #: through :meth:`RecordedGateway.seed_resource`.
        self.resources: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        #: Modelled Alarm Notification Pipeline runtime state, keyed by the fully
        #: qualified pipeline path: the instances the status route serves. Seed it
        #: through :meth:`RecordedGateway.seed_pipeline`; a path with no state is a
        #: 404, exactly as a Gateway with no such pipeline answers.
        self.pipelines: dict[str, list[dict[str, Any]]] = {}
        #: The pipeline cancels the fixture actually applied, the observation a test
        #: asserts alongside the recorded requests.
        self.pipeline_cancels: list[dict[str, Any]] = []
        #: Modelled (not recorded) cancel behaviour: a refusal the Gateway reports
        #: inside a 200 (`pipeline_cancel_refusal`, its message), a 2xx that claims
        #: nothing (`pipeline_cancel_unclaimed`), an ambiguous status that applies
        #: nothing (`pipeline_cancel_status`), and a competing cancel at dispatch time
        #: (`pipeline_cancel_race`).
        self.pipeline_cancel_refusal: str | None = None
        self.pipeline_cancel_unclaimed = False
        self.pipeline_cancel_lies = False
        self.pipeline_cancel_status: int | None = None
        self.pipeline_cancel_race: dict[str, str] | None = None
        self.signature_serial = 0
        #: Ticket #22: the API-token key/hash pair the generate route answers with
        #: (recorded, see ``GENERATED_API_TOKEN_KEY``), and the security tree the
        #: Security Levels singleton serves. ``answer_api_token_generation_with``
        #: replaces the pair for the case where a Gateway answers an inconsistent one.
        self.generated_token_pair: Any = {
            "key": GENERATED_API_TOKEN_KEY,
            "hash": GENERATED_API_TOKEN_HASH,
        }
        #: Modelled (not recorded) per-operation write behaviour, keyed by the
        #: operation the Tool performs ("update", "create", "delete", "rename"):
        #: a Gateway that refuses a change inside a 200 (`write_problem`), one that
        #: answers an ambiguous status without applying anything (`write_status`),
        #: and a competing writer that acts at dispatch time (`write_race`).
        self.write_problem: dict[str, str] = {}
        self.write_status: dict[str, int] = {}
        self.write_race: dict[str, dict[str, Any]] = {}
        #: Exports served, and one scheduled external Project change that lands once
        #: that many exports have been served (models a writer acting between baseline
        #: A and the D16 pre-import re-export A').
        self.exports_served = 0
        self.project_change_after: tuple[str, int, dict[str, bytes]] | None = None
        self._seed_server_config(runtime_tools, bundle_version)
        self._seed_security_levels()

    def _seed_security_levels(self) -> None:
        """Publish the Security Levels singleton ``setup-native apply`` reconciles.

        Ticket #22: the stock tree (modelled from the live G0 evidence) with the
        ``Authenticated`` level a dedicated Runtime level hangs under, so a plan has a
        real tree to preserve and a real signature to precondition a write with.
        """

        levels = json.loads(json.dumps(STOCK_SECURITY_LEVELS))
        self.resources[SECURITY_LEVELS_TYPE] = {
            ("security-levels", DEFAULT_COLLECTION): {
                "type": "security-levels",
                "name": "security-levels",
                "enabled": True,
                "description": "",
                "collection": DEFAULT_COLLECTION,
                "signature": self.next_signature(),
                "config": {"securityLevels": levels},
            },
        }

    def deployed_bundle_version(self) -> str:
        """The bundle version the served Project reports, as the Module's handler reads it.

        Ticket #21: ``setup-native apply`` imports the Project, so the bundle a
        deployment now serves is the imported one. A Project that was seeded without
        an import (the harness deploys by copying) keeps the configured version.
        """

        for name in reversed(self.imports):
            archive = self.projects.get(name)
            if archive is None:
                continue
            try:
                document = json.loads(_zip_entries(archive)["project.json"])
            except (KeyError, ValueError, zipfile.BadZipFile):
                continue
            description = document.get("description")
            if not isinstance(description, str) or not description.strip():
                continue
            match = _MANAGED_MARKER_RE.fullmatch(description.rstrip().splitlines()[-1].strip())
            if match is not None and match.group(1) == MANAGED_PRODUCT:
                return match.group(2)
        return self.bundle_version

    def _seed_server_config(self, runtime_tools: tuple[str, ...], bundle_version: str) -> None:
        """Publish the deployed MCP Server Config the harness workflows deploy by hand.

        Ticket #21: an explicit Tool list and a permissions tree, so ``setup-native
        plan``/``apply`` read and reconcile the same document shape the live harness
        copies onto the Gateway. A config apply creates lands in the same state
        through the modelled collection routes.
        """

        self.resources[SERVER_CONFIG_TYPE] = {
            ("phase3-runtime", DEFAULT_COLLECTION): {
                "type": "server-config",
                "name": "phase3-runtime",
                "enabled": True,
                "description": "",
                "collection": DEFAULT_COLLECTION,
                "signature": self.next_signature(),
                "config": {
                    "title": "Phase 3 Runtime Readonly",
                    "version": bundle_version,
                    "permissions": {
                        "type": "AllOf",
                        "securityLevels": [{
                            "name": "Authenticated",
                            "children": [{"name": "IgnitionMcpCi", "children": []}],
                        }],
                    },
                    "tools": {"project/ignition_runtime": list(runtime_tools)},
                    "resources": {"project/ignition_runtime": "*"},
                    "prompts": {"project/ignition_runtime": "*"},
                },
            },
        }

    # ------------------------------------------------------- projects

    def observe_export(self, name: str) -> bytes | None:
        """Serve one Project export, applying a scheduled external change first.

        The change lands *after* the scheduled number of exports have been served, so
        the export that follows it is the first to see it.
        """

        pending = self.project_change_after
        if pending is not None and pending[0] == name and self.exports_served >= pending[1]:
            self.project_change_after = None
            self.change_project(pending[0], pending[2])
        self.exports_served += 1
        return self.projects.get(name)

    def change_project(self, name: str, entries: dict[str, bytes]) -> None:
        """Change a Project without the MCP server, as another operator would.

        The change goes through the recorded import transition, so the Project stays a
        valid archive while a fingerprint taken before it is stale.
        """

        self.projects[name] = _gateway_import(self.projects.get(name), _zip_bytes(entries))

    def apply_project_import(self, name: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway Project import.

        The transition itself is the recorded one (:func:`_gateway_import`); the write
        hooks the config-resource routes use model the boundaries around it, keyed by
        ``"import"``: a competing writer at dispatch time (``write_race``), an
        ambiguous status that applies nothing (``write_status``), and a refusal carried
        inside a 200 (``write_problem``).
        """

        if race := self.write_race.pop("import", None):
            self.change_project(name, {key: value for key, value in race.items()})
        if (status := self.write_status.get("import")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if (problem := self.write_problem.get("import")) is not None:
            return 200, _refused(problem)
        self.projects[name] = _gateway_import(self.projects.get(name), body)
        self.imports.append(name)
        # The Project's provider now has to be registered by the Module's own thread
        # before a Server Config can resolve any of its primitives.
        self.project_imported_at = time.monotonic()
        return 200, {"message": f"Project {name} imported"}

    # ---------------------------------------------------- Module primitive pickup

    def primitives_ready(self) -> bool:
        """Whether the Module has registered the Project's provider yet."""

        if self.project_imported_at is None:
            return True
        return time.monotonic() - self.project_imported_at >= self.primitive_pickup_delay

    def note_server_config_write(self, name: str) -> None:
        """Record what one Server Config write left the Module's server built from.

        The Module resolves a Server Config's Tool list from its provider registry at
        the moment the resource is written, so a write that lands before the Project's
        provider is registered leaves an endpoint that serves nothing at all. A write
        after that point rebuilds the server and it serves the profile's inventory.
        """

        if self.primitives_ready():
            self.server_config_stale.discard(name)
        else:
            self.server_config_stale.add(name)

    def server_config_is_stale(self, name: str) -> bool:
        """Whether the Module's server for ``name`` was built before the pickup landed."""

        return name in self.server_config_stale

    def server_config_named(self, name: str) -> dict[str, Any] | None:
        for (held, _collection), document in (self.resources.get(SERVER_CONFIG_TYPE) or {}).items():
            if held == name:
                return document
        return None

    # ---------------------------------------------------------------- tags

    def seed_tags(self, provider: str, tags: list[dict[str, Any]]) -> dict[str, Any]:
        """Publish one modelled provider's top-level Tag nodes.

        The document is the recorded JSON export shape, so an export of the provider
        at an empty path answers exactly what ``seed_tags`` published.
        """

        root: dict[str, Any] = {"name": "", "tagType": "Provider", "tags": _tag_nodes(tags)}
        self.tags[provider] = root
        return root

    def tag_node(self, provider: str, path: str) -> dict[str, Any] | None:
        """The node one modelled provider holds at ``path`` (``None`` = no such path)."""

        root = self.tags.get(provider)
        if root is None:
            return None
        node = root
        for segment in _tag_segments(path):
            child = _tag_child(node, segment)
            if child is None:
                return None
            node = child
        return node

    def tag_folder(self, provider: str, path: str) -> dict[str, Any]:
        """The node at ``path``, creating the folders along it as an import would."""

        root = self.tags.setdefault(provider, {"name": "", "tagType": "Provider", "tags": []})
        node = root
        for segment in _tag_segments(path):
            child = _tag_child(node, segment)
            if child is None:
                child = {"name": segment, "tagType": "Folder"}
                node.setdefault("tags", []).append(child)
            node = child
        return node

    def apply_tag_write(self, provider: str, path: str, tags: list[dict[str, Any]]) -> None:
        """Create or replace Tags without any Gateway dispatch, as another writer would."""

        target = self.tag_folder(provider, path)
        for node in tags:
            existing = _tag_child(target, str(node.get("name")))
            if existing is None:
                target.setdefault("tags", []).append(_tag_copy(node))
            else:
                existing.clear()
                existing.update(_tag_copy(node))

    def export_tags(
        self, provider: str, path: str, *, recursive: bool, include_udts: bool,
    ) -> dict[str, Any] | None:
        """One modelled provider's JSON export at ``path`` (``None`` = no such path).

        ``recursive=False`` keeps the immediate children and drops their own children;
        ``include_udts=False`` drops the ``_types_`` node, which is the folder the
        recorded export carries the provider's UDT definitions under.
        """

        node = self.tag_node(provider, path)
        if node is None:
            return None
        exported = _tag_copy(node)
        if not recursive:
            for child in exported.get("tags") or []:
                if isinstance(child, dict):
                    child.pop("tags", None)
        if not include_udts:
            _drop_tag(exported, "_types_")
        return exported

    def apply_tag_import(
        self, provider: str, path: str, body: bytes, *, collision_policy: str,
    ) -> tuple[int, Any]:
        """Apply one recorded Tag import and answer with the route's own report.

        The transition is the recorded one; when a case has substituted a response body
        (:meth:`answer_tag_import_with`) the transition still happens, and only the body
        the route reports it with changes — the state is real while the response is
        unreadable, which is exactly the case a verification-based Tool must not read as
        a success.
        """

        status, payload = self._tag_import_transition(provider, path, body, collision_policy)
        if self.tag_import_body is not _UNSET:
            return 200, self.tag_import_body
        return status, payload

    def _tag_import_transition(
        self, provider: str, path: str, body: bytes, collision_policy: str,
    ) -> tuple[int, Any]:
        """The recorded Tag import transition against a modelled provider's state.

        The document's Tag nodes are created under ``path``, and ``Abort`` refuses the
        whole import when any of them already exists there (D30 §4). The write hooks
        model the same boundaries the config-resource routes model, keyed by
        ``"tag_import"``: a competing writer at dispatch time (``write_race``), an
        ambiguous status that applies nothing (``write_status``), and a reported failure
        inside a 200 (``write_problem``).
        """

        if race := self.write_race.pop("tag_import", None):
            self.apply_tag_write(race["provider"], race["path"], race["tags"])
        if (status := self.write_status.get("tag_import")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if (problem := self.write_problem.get("tag_import")) is not None:
            return 200, _tag_import_failures([problem], self.tag_import_wire_shape)
        document = _decode_tag_document(body)
        if document is None:
            return 400, _INVALID_BODY
        incoming = _incoming_tag_nodes(document)
        if self.tag_import_lies:
            #: Modelled, not recorded: a Gateway that reports a clean import and
            #: creates nothing. The bounded re-export is what has to catch it.
            return 200, _tag_import_success(len(incoming), self.tag_import_wire_shape)
        target = self.tag_folder(provider, path)
        collisions = [
            node for node in incoming
            if _tag_child(target, str(node.get("name"))) is not None
        ]
        if collisions and collision_policy == "Abort":
            messages = [
                f"Tag '[{provider}]{_join_tag_path(path, str(node.get('name')))}' already exists,"
                " and 'abort' collision policy has been specified"
                for node in collisions
            ]
            return 200, _tag_import_failures(
                messages, self.tag_import_wire_shape, sub_code=527,
            )
        applied: list[str] = []
        for index, node in enumerate(incoming):
            if self.tag_import_partial is not None and index >= self.tag_import_partial:
                break
            target.setdefault("tags", []).append(_tag_copy(node))
            applied.append(str(node.get("name")))
        if applied:
            self.tag_imports.append({"provider": provider, "path": path, "names": applied})
        if len(applied) < len(incoming):
            messages = [
                f"Tag '[{provider}]{_join_tag_path(path, str(node.get('name')))}' was not imported"
                for node in incoming[len(applied):]
            ]
            return 200, _tag_import_failures(
                messages, self.tag_import_wire_shape, success_count=len(applied),
            )
        return 200, _tag_import_success(len(incoming), self.tag_import_wire_shape)

    # ------------------------------------------------------- alarm pipelines

    def seed_pipeline(self, path: str, instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Publish the running instances of one modelled pipeline path."""

        published = [_pipeline_instance(path, instance) for instance in instances]
        self.pipelines[path] = published
        return published

    def pipeline_instances(self, path: str) -> list[dict[str, Any]] | None:
        """The instances one modelled pipeline path serves (``None`` = no such path)."""

        instances = self.pipelines.get(path)
        if instances is None:
            return None
        return [dict(instance) for instance in instances]

    def read_pipeline(self, path: str, query: dict[str, list[str]]) -> dict[str, Any] | None:
        """The recorded read behavior of the status route (``None`` = 404/path absent)."""

        instances = self.pipelines.get(path)
        if instances is None:
            return None
        limit = int(_first(query, "limit", "100"))
        offset = int(_first(query, "offset", "0"))
        window = instances[offset:offset + limit]
        return {
            "items": [dict(instance) for instance in window],
            "metadata": {
                "total": float(len(instances)), "matching": float(len(instances)),
                "limit": limit, "offset": offset,
            },
        }

    def cancel_pipeline_instance(self, path: str, alarm_event_id: str) -> bool:
        """Cancel one pipeline run without any Gateway dispatch, as an operator would."""

        instances = self.pipelines.get(path)
        if instances is None:
            return False
        remaining = [
            instance for instance in instances
            if instance.get("alarmEventId") != alarm_event_id
        ]
        if len(remaining) == len(instances):
            return False
        self.pipelines[path] = remaining
        return True

    def apply_pipeline_cancel(self, body: bytes) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway pipeline cancel against a modelled pipeline.

        The recorded transition is the documented one: the route removes the run the
        ``(path, alarmEventId)`` pair names and answers ``{"success": true,
        "alarmEventId": ...}``; a pair the pipeline does not hold is refused inside the
        200 with ``success: false``. The write hooks model the boundaries around it: a
        competing cancel at dispatch time (``pipeline_cancel_race``), an ambiguous
        status that applies nothing (``pipeline_cancel_status``), and a 2xx that claims
        nothing (``pipeline_cancel_unclaimed``).
        """

        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, _INVALID_BODY
        if not isinstance(document, dict):
            return 400, _INVALID_BODY
        path, alarm_event_id = document.get("path"), document.get("alarmEventId")
        if not isinstance(path, str) or not path or not isinstance(alarm_event_id, str) or not alarm_event_id:
            return 400, _INVALID_BODY
        if (race := self.pipeline_cancel_race) is not None:
            self.pipeline_cancel_race = None
            self.cancel_pipeline_instance(race["path"], race["alarmEventId"])
        if (status := self.pipeline_cancel_status) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if (problem := self.pipeline_cancel_refusal) is not None:
            return 200, {
                "success": False,
                "alarmEventId": alarm_event_id,
                "problem": {"message": problem, "stacktrace": []},
            }
        if self.pipeline_cancel_lies:
            #: Modelled, not recorded: a Gateway that reports a clean cancel and leaves
            #: the run in place. The bounded re-read is what has to catch it.
            return 200, {"success": True, "alarmEventId": alarm_event_id}
        applied = self.cancel_pipeline_instance(path, alarm_event_id)
        if applied:
            self.pipeline_cancels.append({"path": path, "alarmEventId": alarm_event_id})
        if self.pipeline_cancel_unclaimed:
            return 200, {}
        return 200, {"success": applied, "alarmEventId": alarm_event_id}

    # ------------------------------------------------------- config resources

    def next_signature(self) -> str:
        self.signature_serial += 1
        return f"sig-{self.signature_serial}"

    def _resource(self, resource_type: str, name: str, collection: str) -> dict[str, Any] | None:
        return (self.resources.get(resource_type) or {}).get((name, collection))

    def _singleton(self, resource_type: str) -> dict[str, Any] | None:
        entries = self.resources.get(resource_type) or {}
        if len(entries) != 1:
            return None
        return next(iter(entries.values()))

    def read_resource(self, path: str, query: dict[str, list[str]]) -> dict[str, Any] | None:
        """The recorded read behavior for one config-resource path (``None`` = 404)."""

        for verb in ("find", "singleton"):
            prefix = f"/data/api/v1/resources/{verb}/"
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix):]
            if verb == "singleton":
                return self._singleton(remainder)
            resource_type, _, name = remainder.rpartition("/")
            if not resource_type or not name:
                return None
            return self._resource(resource_type, name, _first(query, "collection", DEFAULT_COLLECTION))
        for verb in ("names", "list"):
            prefix = f"/data/api/v1/resources/{verb}/"
            if not path.startswith(prefix):
                continue
            entries = self.resources.get(path[len(prefix):])
            if entries is None:
                return None
            return _resource_collection(entries, query, names_only=verb == "names")
        return None

    def apply_resource_update(self, resource_type: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway resource PUT: validate all, then change all."""

        changes = _decode_changes(body)
        if changes is None:
            return 400, _INVALID_BODY
        entries = self.resources[resource_type]
        targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for change in changes:
            current = self._change_target(resource_type, entries, change)
            if current is None:
                return 404, _NO_SUCH_RESOURCE
            targets.append((change, current))
        if self.write_race.get("update"):
            fields = self.write_race.pop("update")
            for _change, current in targets:
                current.update(fields)
                current["signature"] = self.next_signature()
        if (status := self.write_status.get("update")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        for change, current in targets:
            if change.get("signature") != current.get("signature"):
                return 409, _SIGNATURE_MISMATCH
        if (problem := self.write_problem.get("update")) is not None:
            return 200, _refused(problem)
        applied: list[dict[str, Any]] = []
        for change, current in targets:
            for key in ("config", "enabled", "description"):
                if key in change:
                    current[key] = change[key]
            current["signature"] = self.next_signature()
            if resource_type == SERVER_CONFIG_TYPE:
                self.note_server_config_write(str(current.get("name") or ""))
            applied.append(self._change_notice(resource_type, current))
        return 200, {"success": True, "changes": applied}

    def apply_resource_create(self, resource_type: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway resource POST: validate all, then create all.

        A create takes no Resource signature; an existing target is the Gateway's
        own D11 collision refusal (409), which the Tool must report as ``conflict``.
        """

        changes = _decode_changes(body)
        if changes is None:
            return 400, _INVALID_BODY
        entries = self.resources[resource_type]
        targets: list[tuple[dict[str, Any], str, str]] = []
        for change in changes:
            name, collection = self._create_identity(resource_type, entries, change)
            if name is None:
                return 400, _INVALID_BODY
            targets.append((change, name, collection))
        if (race := self.write_race.get("create")) is not None:
            self.write_race.pop("create")
            for _change, name, collection in targets:
                entries[(name, collection)] = self._document(
                    resource_type, name, collection, race,
                )
        if (status := self.write_status.get("create")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        for _change, name, collection in targets:
            if (name, collection) in entries:
                return 409, {
                    "message": f"A resource named {name} already exists", "status": "409",
                }
        if (problem := self.write_problem.get("create")) is not None:
            return 200, _refused(problem)
        applied: list[dict[str, Any]] = []
        for change, name, collection in targets:
            document = self._document(resource_type, name, collection, change)
            entries[(name, collection)] = document
            if resource_type == SERVER_CONFIG_TYPE:
                self.note_server_config_write(name)
            applied.append(self._change_notice(resource_type, document))
        return 200, {"success": True, "changes": applied}

    def apply_resource_delete(
        self, resource_type: str, name: str, signature: str, collection: str,
    ) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway resource DELETE.

        The route carries the signature in its path, so the Gateway itself refuses a
        stale one (modelled as the same 409 the update route answers; the live Gateway
        cannot be reached from this fixture, and D30 §2 maps a stale token to
        ``conflict``).
        """

        entries = self.resources.get(resource_type)
        if entries is None:
            return 404, _NO_SUCH_RESOURCE
        current = (
            self._singleton(resource_type) if not name else entries.get((name, collection))
        )
        if current is None:
            return 404, _NO_SUCH_RESOURCE
        race = self.write_race.pop("delete", None)
        if race is not None:
            if race.get("deleted"):
                del entries[(str(current.get("name", name)), str(current.get("collection", "")))]
                current = None
            else:
                current.update(race)
                current["signature"] = self.next_signature()
        if (status := self.write_status.get("delete")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if current is None:
            return 404, _NO_SUCH_RESOURCE
        if current.get("signature") != signature:
            return 409, _SIGNATURE_MISMATCH
        if (problem := self.write_problem.get("delete")) is not None:
            return 200, _refused(problem)
        del entries[(str(current.get("name", name)), str(current.get("collection", "")))]
        return 200, {"success": True, "changes": [
            {"name": current.get("name", name), "type": resource_type.rsplit("/", 1)[-1],
             "collection": current.get("collection", "")},
        ]}

    def apply_resource_rename(
        self, resource_type: str, name: str, body: bytes, collection: str,
    ) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway resource rename POST.

        The endpoint takes no Resource signature (D30 §2), so a competing writer can
        change the resource inside the window between the caller's read-compare and
        this dispatch: the documented race, modelled by ``write_race``.
        """

        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, _INVALID_BODY
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            return 400, _INVALID_BODY
        if payload.get("references") != "ABORT":
            return 400, {
                "message": "references must be ABORT", "status": "400",
            }
        entries = self.resources.get(resource_type)
        if entries is None:
            return 404, _NO_SUCH_RESOURCE
        current = entries.get((name, collection))
        if current is None:
            return 404, _NO_SUCH_RESOURCE
        new_name = str(payload["name"])
        if (new_name, collection) in entries:
            return 409, {"message": f"A resource named {new_name} already exists", "status": "409"}
        race = self.write_race.pop("rename", None)
        if race is not None:
            if race.get("deleted"):
                del entries[(name, collection)]
                current = None
            else:
                current.update(race)
                current["signature"] = self.next_signature()
        if (status := self.write_status.get("rename")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if current is None:
            return 404, _NO_SUCH_RESOURCE
        if (problem := self.write_problem.get("rename")) is not None:
            return 200, _refused(problem)
        del entries[(name, collection)]
        current["name"] = new_name
        current["signature"] = self.next_signature()
        entries[(new_name, collection)] = current
        return 200, {"success": True, "changes": [self._change_notice(resource_type, current)]}

    def _document(
        self, resource_type: str, name: str, collection: str, fields: dict[str, Any],
    ) -> dict[str, Any]:
        """One stored resource document, as creation leaves it."""

        document: dict[str, Any] = {
            "type": resource_type.rsplit("/", 1)[-1],
            "name": name,
            "enabled": bool(fields.get("enabled", True)),
            "description": str(fields.get("description", "")),
            "collection": collection,
            "signature": self.next_signature(),
            "config": fields.get("config", {}),
        }
        return document

    def _change_notice(self, resource_type: str, document: dict[str, Any]) -> dict[str, Any]:
        """One entry of the recorded ``changes`` list a write answers with."""

        return {
            "name": document.get("name", ""),
            "type": resource_type.rsplit("/", 1)[-1],
            "collection": document.get("collection", ""),
            "newSignature": document.get("signature"),
        }

    def _create_identity(
        self,
        resource_type: str,
        entries: dict[tuple[str, str], dict[str, Any]],
        change: dict[str, Any],
    ) -> tuple[str | None, str]:
        """The resource a create item names, or ``(None, "")`` for an invalid item.

        A non-singleton create item names its resource (its documented schema requires
        ``name``); a singleton's item declares no name and addresses the type's single
        resource, whose replacement identity is described by its own state.
        """

        collection = change.get("collection", DEFAULT_COLLECTION)
        if not isinstance(collection, str):
            return None, ""
        name = change.get("name")
        if name is None:
            existing = self._singleton(resource_type)
            name = str(existing.get("name", resource_type)) if existing else resource_type
        if not isinstance(name, str) or not name.strip():
            return None, ""
        return name, collection

    def _change_target(
        self,
        resource_type: str,
        entries: dict[tuple[str, str], dict[str, Any]],
        change: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The resource one PUT change item addresses.

        A change item names its resource, or — for a singleton, whose documented
        item schema requires only ``signature`` — addresses the type's single
        resource. The collection selects between same-named resources.
        """

        name = change.get("name")
        collection = change.get("collection", DEFAULT_COLLECTION)
        if not isinstance(collection, str):
            return None
        if name is None:
            return self._singleton(resource_type)
        if not isinstance(name, str) or not name.strip():
            return None
        return entries.get((name, collection))


#: The response bodies the modelled Gateway answers with. They are modelled, not
#: recorded: no live Gateway is reachable from this fixture, and each shape is the
#: documented or decided one (409 for a signature mismatch and a collision, a 200
#: carrying ``success=false`` with a ``problem`` for a refusal).
_INVALID_BODY: dict[str, Any] = {"message": "Invalid request body", "status": "400"}
_NO_SUCH_RESOURCE: dict[str, Any] = {"message": "No such resource", "status": "404"}


#: Sentinels for a modelled route override that has not been set. ``object()`` rather
#: than ``None`` because ``None`` is itself a modelled body (an empty or JSON-null 2xx).
_UNSET: Any = object()
_SIGNATURE_MISMATCH: dict[str, Any] = {
    "message": "Signature mismatch: the resource changed after it was read",
    "status": "409",
}


def _refused(problem: str) -> dict[str, Any]:
    """A refusal the Gateway reports inside a 2xx resource response."""

    return {"success": False, "problem": {"message": problem, "stacktrace": []}}


def _decode_changes(body: bytes) -> list[dict[str, Any]] | None:
    """The change items of one resource write, or ``None`` for an invalid body."""

    try:
        changes = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(changes, list) or not changes:
        return None
    if not all(isinstance(change, dict) for change in changes):
        return None
    return [dict(change) for change in changes]


def _resource_collection(
    entries: dict[tuple[str, str], dict[str, Any]],
    query: dict[str, list[str]],
    *,
    names_only: bool,
) -> dict[str, Any]:
    limit = int(_first(query, "limit", "100"))
    offset = int(_first(query, "offset", "0"))
    search = _first(query, "search", "").lower()
    collection = _first(query, "collection", "")
    selected = {
        name: document for (name, item_collection), document in entries.items()
        if item_collection == collection
    }
    matching = sorted(name for name in selected if not search or search in name.lower())
    window = matching[offset:offset + limit]
    items = (
        [{"name": name, "enabled": bool(selected[name].get("enabled", True))} for name in window]
        if names_only
        else [selected[name] for name in window]
    )
    return {
        "items": items,
        "metadata": {
            "total": float(len(selected)),
            "matching": float(len(matching)),
            "limit": limit,
            "offset": offset,
        },
    }


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default


def _pipeline_instance(path: str, instance: dict[str, Any]) -> dict[str, Any]:
    """One modelled Alarm Notification Pipeline run, as the status route serves it.

    ``alarmEventId`` is the identity the cancel route addresses a run by, so it is
    required; the other fields default to what a healthy run reports.
    """

    alarm_event_id = instance.get("alarmEventId")
    if not isinstance(alarm_event_id, str) or not alarm_event_id:
        raise ValueError("a modelled pipeline instance needs a non-empty alarmEventId")
    return {
        "pipelinePath": instance.get("pipelinePath", path),
        "source": instance.get("source", ""),
        "displayPath": instance.get("displayPath", ""),
        "blockName": instance.get("blockName", ""),
        "status": instance.get("status", "Running"),
        "millis": instance.get("millis", 0),
        "alarmEventId": alarm_event_id,
    }


def _tag_copy(node: dict[str, Any]) -> dict[str, Any]:
    """A deep copy of one Tag node, so a caller cannot mutate the fixture's state."""

    return json.loads(json.dumps(node))


def _tag_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_tag_copy(node) for node in nodes]


def _tag_segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _join_tag_path(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _tag_child(node: dict[str, Any], name: str) -> dict[str, Any] | None:
    children = node.get("tags")
    if not isinstance(children, list):
        return None
    for child in children:
        if isinstance(child, dict) and child.get("name") == name:
            return child
    return None


def _drop_tag(node: dict[str, Any], name: str) -> None:
    children = node.get("tags")
    if isinstance(children, list):
        node["tags"] = [
            child for child in children
            if not (isinstance(child, dict) and child.get("name") == name)
        ]


def _decode_tag_document(body: bytes) -> dict[str, Any] | None:
    """The Tag document one import body carries, or ``None`` for an unusable body."""

    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    if "tags" in document and not isinstance(document["tags"], list):
        return None
    return dict(document)


def _incoming_tag_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The Tag nodes one import document creates under the target path.

    A document that names its own root imports that root (the same reading the export
    of a sub-path produces, ``{"name": "source", "tagType": "Folder", ...}``); one that
    does not — a provider-root export — contributes its ``tags`` entries, which is the
    shape the Runtime plane imports live.
    """

    name = document.get("name")
    if isinstance(name, str) and name:
        return [document]
    children = document.get("tags")
    if isinstance(children, list) and children:
        return [
            child for child in children
            if isinstance(child, dict) and isinstance(child.get("name"), str) and child.get("name")
        ]
    return []


def _tag_import_success(count: int, shape: str) -> Any:
    """A successful import in one of the two observed wire shapes."""

    if shape == "list":
        return []
    return {"failureCount": 0, "failures": [], "successCount": count}


def _tag_import_failures(
    messages: list[str], shape: str, *, success_count: int = 0, sub_code: int = 776,
) -> Any:
    """A reported import failure in one of the two observed wire shapes.

    The summary object is what live 8.3.8/8.3.9 answer (recorded in
    ``phase4/tag-import-abort.json`` for the ``Abort`` collision refusal, whose
    ``qualitySubCode`` is 527, and in ``phase4/tag-import-provider-not-ready.json``
    for a provider that is still starting, whose sub-code is 776); the list of non-Good
    QualityCodes is what the committed 8.3.8 OpenAPI documents.
    """

    if shape == "list":
        return [
            {"level": "Bad", "userCode": sub_code, "diagnosticMessage": message}
            for message in messages
        ]
    return {
        "failureCount": len(messages),
        "failures": [
            {"diagnosticMessage": message, "quality": "Bad", "qualitySubCode": sub_code}
            for message in messages
        ],
        "successCount": success_count,
    }


class RecordedGateway:
    """Context-managed replay of recorded Gateway behavior on an ephemeral port."""

    def __init__(
        self,
        *,
        projects: dict[str, bytes] | None = None,
        openapi_missing_responses: int = 0,
        quarantined_modules: tuple[str, ...] = (),
        runtime_tools: tuple[str, ...] = (),
        runtime_resources: tuple[str, ...] = (),
        source_revision: str = "UNSTAMPED",
        bundle_version: str = "0.2.0",
        policy_provider: str = "",
        port: int = 0,
        audit_profile: str = "",
        alarm_root: str = "",
        policy_provider_unready_reads: int = 0,
        tag_update_paths: dict[str, str] | None = None,
        tag_create_paths: dict[str, str] | None = None,
        tag_copy_paths: dict[str, str] | None = None,
        tag_delete_paths: dict[str, str] | None = None,
        tag_move_paths: dict[str, str] | None = None,
        tag_rename_paths: dict[str, str] | None = None,
        primitive_pickup_delay: float = 0.0,
        module_install_flow: bool = False,
        module_installed: bool = False,
    ) -> None:
        self._server = _Server(
            projects or {},
            openapi_missing_responses,
            quarantined_modules,
            runtime_tools,
            runtime_resources,
            source_revision,
            bundle_version,
            policy_provider,
            port,
            audit_profile,
            alarm_root,
            tag_update_paths,
            tag_create_paths,
            tag_copy_paths,
            tag_delete_paths,
            tag_move_paths,
            tag_rename_paths,
            primitive_pickup_delay,
            module_install_flow,
        )
        if module_installed:
            # Ticket #56: a fake that models the Gateway AFTER the install-only phase:
            # the pinned Module is installed, the restart happened, and the CLI's
            # second install-module run must answer NO CHANGE against it.
            fixture = next((ROOT / "tests/fixtures/modules").glob("MCP-module-*.modl"))
            installed = _uploaded_module_identity(fixture.read_bytes())
            if installed is None:  # pragma: no cover - the pinned fixture is a module
                raise ValueError(f"{fixture}: not a module archive")
            self._server.uploaded_module = installed
            self._server.upload_name = "MCP-module.modl"
            self._server.module_certificate_accepted = True
            self._server.module_eula_accepted = True
            self._server.module_installed = True
            self._server.module_restarted = True
        self._server.policy_provider_unready_reads = policy_provider_unready_reads
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        if audit_profile:
            # The Phase 4 policy names an audit profile the harness creates
            # through Native REST; publish it as a config resource so the fake
            # answers the same find the live Gateway does.
            self.seed_resource(
                "ignition/audit-profile", audit_profile,
                config={"profile": {"type": "local"}, "settings": {}},
                enabled=True, description="Disposable Phase 4 CI local audit profile",
            )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def mcp_url(self) -> str:
        return self.base_url + "/data/mcp/phase3-runtime"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self._server.requests

    @property
    def imports(self) -> list[str]:
        return self._server.imports

    # ------------------------------------------------------------- projects

    def project(self, name: str) -> bytes | None:
        """The Project archive the Gateway currently serves for ``name``."""

        return self._server.projects.get(name)

    def served_policy_tags(self) -> list[dict[str, Any]]:
        """The Tags the reserved policy provider currently serves.

        Ticket #21: the provider's own state after the last import, so a case can
        assert what ``setup-native apply`` left in it without a second HTTP read.
        """

        return list(self._server.served_policy_tags)

    def change_project_out_of_band(self, name: str, entries: dict[str, bytes]) -> None:
        """Change a Project without the MCP server, as another operator would; a
        fingerprint read before the change is now stale."""

        self._server.change_project(name, entries)

    def change_project_after_exports(
        self, name: str, exports: int, entries: dict[str, bytes],
    ) -> None:
        """Schedule an external Project change that lands once ``exports`` exports have
        been served.

        With the baseline export being the first one, ``exports=1`` models a writer that
        changes the Project between baseline A and the D16 mandatory pre-import
        re-export A'.
        """

        self._server.project_change_after = (name, exports, dict(entries))

    def fail_imports_with(self, status: int = 500) -> None:
        """Model a Gateway that answers the Project import with ``status`` and applies
        nothing — the ambiguous dispatch boundary of D08."""

        self._server.write_status["import"] = status

    def refuse_imports_with(self, problem: str | None) -> None:
        """Model a Gateway that refuses the Project import inside a 200 response."""

        self._server.write_problem["import"] = problem

    def race_import_with(self, entries: dict[str, bytes]) -> None:
        """Model another writer importing ``entries`` at dispatch time (D30 §2's race)."""

        self._server.write_race["import"] = dict(entries)

    # ------------------------------------------------------------------ tags

    @property
    def tag_imports(self) -> list[dict[str, Any]]:
        """The Tag imports the fixture actually applied (provider, path, Tag names)."""

        return self._server.tag_imports

    def seed_tags(self, provider: str, tags: list[dict[str, Any]]) -> None:
        """Publish a Tag provider holding ``tags`` as its top-level nodes."""

        self._server.seed_tags(provider, tags)

    def tags(self, provider: str, path: str = "") -> dict[str, Any] | None:
        """The Tag node the fixture holds at ``path`` (``None`` = absent)."""

        return self._server.tag_node(provider, path)

    def change_tags_out_of_band(self, provider: str, path: str, tags: list[dict[str, Any]]) -> None:
        """Create Tags without the MCP server, as another operator would; the Tool's
        collision check and its re-export both see them."""

        self._server.apply_tag_write(provider, path, _tag_nodes(tags))

    def fail_tag_imports_with(self, status: int = 500) -> None:
        """Model a Gateway that answers the Tag import with ``status`` and applies
        nothing — the ambiguous dispatch boundary of D08."""

        self._server.write_status["tag_import"] = status

    def refuse_tag_imports_with(self, problem: str | None = None) -> None:
        """Model a Gateway that reports a failed import inside a 200 response."""

        self._server.write_problem["tag_import"] = (
            problem if problem is not None else "the import was refused"
        )

    def partial_tag_imports_with(self, applied: int) -> None:
        """Model a Gateway that imports ``applied`` nodes of the document and reports
        the rest as failures — the partial application D30 §2 does not call a claim."""

        self._server.tag_import_partial = applied

    def claim_tag_imports_without_applying(self) -> None:
        """Model a Gateway that answers a clean success and creates nothing.

        Modelled, not recorded: it is the case D30 §2's verification exists for, where
        the claim stands and the observed state does not.
        """

        self._server.tag_import_lies = True

    def race_tag_import_with(self, provider: str, path: str, tags: list[dict[str, Any]]) -> None:
        """Model another writer creating ``tags`` under ``path`` at dispatch time."""

        self._server.write_race["tag_import"] = {
            "provider": provider, "path": path, "tags": _tag_nodes(tags),
        }

    def answer_tag_import_failures_with(self, shape: str) -> None:
        """Which wire shape the import route reports failures with: the summary object
        live 8.3.8/8.3.9 answer, or the QualityCode list the 8.3.8 OpenAPI documents."""

        if shape not in {"summary", "list"}:
            raise ValueError("shape must be 'summary' or 'list'")
        self._server.tag_import_wire_shape = shape

    def answer_tag_import_with(self, body: Any) -> None:
        """Answer the Tag import with ``body`` inside a 200, whatever it did.

        Modelled, not recorded: the case is a Gateway whose 2xx report this server cannot
        interpret (a negative or non-numeric count, a count that disagrees with its
        failure list, a ``null`` body). The import itself still applies, so a Tool that
        treats the unreadable report as a success would return one here.
        """

        self._server.tag_import_body = body

    # ------------------------------------------------------- alarm pipelines

    @property
    def pipeline_cancels(self) -> list[dict[str, Any]]:
        """The pipeline cancels the fixture actually applied (path, alarm event)."""

        return self._server.pipeline_cancels

    def seed_pipeline(self, path: str, instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Publish one modelled pipeline path holding ``instances`` runs.

        Each instance needs a non-empty ``alarmEventId``; ``status``, ``source``,
        ``displayPath``, ``blockName`` and ``millis`` are optional, and
        ``pipelinePath`` defaults to ``path``. A path with no published state answers
        exactly as a Gateway with no such pipeline does: ``not_found``.
        """

        return self._server.seed_pipeline(path, instances)

    def pipeline(self, path: str) -> list[dict[str, Any]] | None:
        """The runs the fixture holds for ``path`` (``None`` = no such path)."""

        return self._server.pipeline_instances(path)

    def cancel_pipeline_out_of_band(self, path: str, alarm_event_id: str) -> bool:
        """Cancel one pipeline run without the MCP server, as an operator would.

        The Tool's verification re-reads afterwards, so a run removed here and a run
        this server cancelled are indistinguishable in the observed state — exactly
        the attribution boundary D30 §2 addresses.
        """

        return self._server.cancel_pipeline_instance(path, alarm_event_id)

    def refuse_cancels_with(self, problem: str | None = None) -> None:
        """Model a Gateway that refuses a pipeline cancel inside a 200 response.

        The documented route answers ``{"success": bool, "alarmEventId": str}``; a pair
        the pipeline does not hold is refused with ``success: false``. The shape is
        modelled, not recorded, like every other refusal this fixture carries.
        """

        self._server.pipeline_cancel_refusal = (
            problem if problem is not None else "No such alarm event is running on this pipeline"
        )

    def answer_cancels_without_claiming(self) -> None:
        """Model a Gateway that answers 200 with no ``success`` field at all.

        Modelled, not recorded: the route's answer is readable as neither a claim nor a
        refusal, which is exactly the case D30 §2 must not report as a success. The
        cancel is applied, so only the claim is missing.
        """

        self._server.pipeline_cancel_unclaimed = True

    def claim_cancels_without_applying(self) -> None:
        """Model a Gateway that claims a clean cancel and leaves the run in place.

        Modelled, not recorded: it is the case D30 §2's verification exists for, where
        the claim stands and the observed state does not.
        """

        self._server.pipeline_cancel_lies = True

    def fail_cancels_with(self, status: int = 500) -> None:
        """Model a Gateway that answers a cancel with ``status`` and applies nothing —
        the ambiguous dispatch boundary of D08."""

        self._server.pipeline_cancel_status = status

    def race_cancel_with(self, path: str, alarm_event_id: str) -> None:
        """Model another operator cancelling the same run at dispatch time (D30 §2)."""

        self._server.pipeline_cancel_race = {"path": path, "alarmEventId": alarm_event_id}

    # ------------------------------------------------------- config resources

    def seed_resource(
        self,
        resource_type: str,
        name: str,
        *,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
        description: str = "",
        collection: str = DEFAULT_COLLECTION,
    ) -> dict[str, Any]:
        """Publish one config resource, returning the stored document.

        The collection defaults to the Gateway's own default (``core``); a case that
        needs a look-alike in another collection names it.
        """

        document: dict[str, Any] = {
            "type": resource_type.rsplit("/", 1)[-1],
            "name": name,
            "enabled": enabled,
            "description": description,
            "collection": collection,
            "signature": self._server.next_signature(),
            "config": config if config is not None else {},
        }
        self._server.resources.setdefault(resource_type, {})[(name, collection)] = document
        return document

    def resource(
        self, resource_type: str, name: str, collection: str = DEFAULT_COLLECTION,
    ) -> dict[str, Any]:
        return self._server.resources[resource_type][(name, collection)]

    def signature(self, resource_type: str, name: str, collection: str = DEFAULT_COLLECTION) -> str:
        return str(self.resource(resource_type, name, collection)["signature"])

    def security_levels(self) -> list[dict[str, Any]] | None:
        """The security tree the fake currently serves (``None`` = no singleton)."""

        document = self._server._singleton(SECURITY_LEVELS_TYPE)
        tree = document.get("config", {}).get("securityLevels") if isinstance(document, dict) else None
        return tree if isinstance(tree, list) else None

    def answer_api_token_generation_with(self, payload: Any) -> None:
        """Answer the API-token generate route with ``payload`` whatever it is.

        Modelled, not recorded: the case is a 2xx body this CLI cannot turn into a
        credential — a key/hash pair that disagrees, or a body that is not a pair.
        """

        self._server.generated_token_pair = payload

    def refuse_writes_with(self, operation: str, problem: str | None) -> None:
        """Model a Gateway that answers 200 with ``success=false`` for one operation.

        ``operation`` is the write the Tool performs: ``update`` (PUT), ``create``
        (POST on the collection route), ``delete`` (DELETE) or ``rename`` (POST on
        the rename route).
        """

        self._server.write_problem[operation] = problem

    def fail_writes_with(self, operation: str, status: int = 500) -> None:
        """Model a Gateway that answers one write operation with ``status``.

        Nothing is applied, and the caller cannot tell whether its write landed before
        the failure — the ambiguous dispatch boundary of D08.
        """

        self._server.write_status[operation] = status

    def race_write_with(self, operation: str, **fields: Any) -> None:
        """Model another writer acting on the target at dispatch time.

        The competing change lands after this harness's pre-dispatch read and before
        the Gateway's own check, which is the deterministic race each operation's
        Precondition token is meant to narrow (D30 §2). Any field models the writer
        changing the resource; ``deleted=True`` models it *removing* the target, which
        is what a delete or a rename cannot tell apart from its own work.
        """

        self._server.write_race[operation] = dict(fields)

    # The update shorthand the reviewed #14 cases call; the generic methods above are
    # the same mechanism, named for the operation each one models.
    def race_update_with(self, **fields: Any) -> None:
        self.race_write_with("update", **fields)

    def fail_updates_with(self, status: int = 500) -> None:
        self.fail_writes_with("update", status)

    def change_resource_out_of_band(
        self, resource_type: str, name: str, collection: str = DEFAULT_COLLECTION, **fields: Any,
    ) -> str:
        """Change a resource without the MCP server, as another operator would; the
        stored signature moves, so a token read before the change is now stale."""

        document = self.resource(resource_type, name, collection)
        document.update(fields)
        document["signature"] = self._server.next_signature()
        return str(document["signature"])

    def refuse_updates_with(self, problem: str | None) -> None:
        """Model a Gateway that answers 200 with ``success=false`` for every PUT."""

        self.refuse_writes_with("update", problem)

    def restart(self) -> None:
        """Apply an accepted shared certificate as the recorded Gateway restart did."""
        if self._server.certificate_accepted:
            self._server.modules_active = True
            self._server.quarantined_modules.clear()

    def __enter__(self) -> RecordedGateway:
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
