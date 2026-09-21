"""Deterministic Runtime Target Policy documents used by the Phase 4 live harness.

The Phase 4 research ticket (#6) characterizes *where* a deployment-owned
Runtime Target Policy can live and *how* a Runtime Tool handler reads it. The
harness therefore needs one policy document that is byte-identical every run,
plus the Native REST Tag documents that write it to a disposable Gateway.

The shape below is a characterization fixture, not the final D30 document
schema: ticket #2 owns the shipped policy reader and its validation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Repo-defined provider that owns the Runtime Target Policy on a deployment.
#: D30 requires the document to live outside the Runtime Bundle, to be readable
#: by a handler at bounded cost and to be unreachable by the Runtime plane.
POLICY_PROVIDER = "IgnitionMCPPolicy"
POLICY_TAG_NAME = "RuntimeTargetPolicy"
POLICY_TAG_PATH = f"[{POLICY_PROVIDER}]{POLICY_TAG_NAME}"
#: Second Tag in the same provider: proves a handler can write inside the policy
#: provider at all, so "the Runtime server cannot write it" is an in-product rule.
WRITE_PROBE_TAG_NAME = "WriteProbe"
WRITE_PROBE_PATH = f"[{POLICY_PROVIDER}]{WRITE_PROBE_TAG_NAME}"
WRITE_PROBE_INITIAL_VALUE = "phase4-unset"

#: Provider resource body for `POST /data/api/v1/resources/ignition/tag-provider`.
PROVIDER_DESCRIPTION = "ignition-mcp Runtime Target Policy provider (deployment-owned)"

#: Canonical policy payload. Keys are sorted at serialization time so its SHA-256
#: is stable across runs and can be compared between REST and a live handler read.
POLICY: dict[str, Any] = {
    "alarmShelveMaxSeconds": 3600,
    "allowlists": {
        "alarm_acknowledge": ["prov:default:/tag:IgnitionMCP_CI/*"],
        "alarm_shelve": ["prov:default:/tag:IgnitionMCP_CI/*"],
        "tag_write": ["[default]IgnitionMCP_CI"],
    },
    "auditMode": "best_effort",
    "schemaVersion": 1,
    "serviceIdentity": "ignition-mcp-service",
}


def policy_json() -> str:
    """The canonical policy text a handler must read back byte for byte."""
    return json.dumps(POLICY, sort_keys=True, separators=(",", ":"))


def policy_sha256() -> str:
    return hashlib.sha256(policy_json().encode("utf-8")).hexdigest()


def provider_resource() -> dict[str, Any]:
    """Native REST body for creating the dedicated policy Tag provider."""
    return {
        "name": POLICY_PROVIDER,
        "description": PROVIDER_DESCRIPTION,
        "enabled": True,
        "config": {"profile": {"type": "STANDARD"}, "settings": {}},
    }


def tag_document() -> dict[str, Any]:
    """Native REST Tag import document (Designer export shape) for the policy."""
    return {
        "tags": [
            {
                "name": POLICY_TAG_NAME,
                "tagType": "AtomicTag",
                "valueSource": "memory",
                "dataType": "String",
                "value": policy_json(),
                "enabled": True,
            },
            {
                "name": WRITE_PROBE_TAG_NAME,
                "tagType": "AtomicTag",
                "valueSource": "memory",
                "dataType": "String",
                "value": WRITE_PROBE_INITIAL_VALUE,
                "enabled": True,
            },
        ],
    }


def tag_document_bytes() -> bytes:
    return json.dumps(tag_document(), separators=(",", ":")).encode("utf-8")


def find_tag(exported: Any, name: str) -> dict[str, Any] | None:
    """Depth-first search of an exported Tag tree for `name`."""
    if isinstance(exported, dict):
        if exported.get("name") == name or exported.get("path") == name:
            return exported
        for key in ("tags", "children"):
            found = find_tag(exported.get(key), name)
            if found is not None:
                return found
        return None
    if isinstance(exported, list):
        for item in exported:
            found = find_tag(item, name)
            if found is not None:
                return found
    return None
