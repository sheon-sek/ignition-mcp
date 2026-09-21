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
#: A deployment-enforced maximum for the policy document. The Gateway has no
#: native size cap on a Tag value and `system.tag.readBlocking` has no size
#: option, so the cap has to be carried by the *writer* and checked by the
#: reader before the value is materialized (see the note).
POLICY_MAX_BYTES = 32768
#: Companion Tag holding the document's byte length. The handler reads this
#: small Int4 first and refuses an over-cap policy without ever reading it.
POLICY_LENGTH_TAG_NAME = "RuntimeTargetPolicyLength"
POLICY_LENGTH_TAG_PATH = f"[{POLICY_PROVIDER}]{POLICY_LENGTH_TAG_NAME}"
#: Harness-only oversize pair: the live proof that the gate skips a value that
#: is over the cap instead of materializing it.
OVERSIZE_POLICY_TAG_NAME = "OversizePolicyProbe"
OVERSIZE_POLICY_TAG_PATH = f"[{POLICY_PROVIDER}]{OVERSIZE_POLICY_TAG_NAME}"
OVERSIZE_LENGTH_TAG_NAME = "OversizePolicyProbeLength"
OVERSIZE_LENGTH_TAG_PATH = f"[{POLICY_PROVIDER}]{OVERSIZE_LENGTH_TAG_NAME}"
OVERSIZE_POLICY_BYTES = 40000
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


#: Every Runtime Tag Mutation that can reach a Tag, and what it must refuse
#: inside the reserved policy provider (D30 §1, research note §1). The driver's
#: evidence verdict is generated from this list so the recorded sentence cannot
#: drift from the documented rule.
RESERVED_PROVIDER_REFUSALS: tuple[tuple[str, str], ...] = (
    ("tag_write", "any target inside the reserved provider"),
    ("tag_update", "any target inside the reserved provider"),
    ("tag_delete", "any target inside the reserved provider"),
    ("tag_create", "a Tag that would be created inside the reserved provider"),
    ("tag_move", "a source or destination inside the reserved provider"),
    ("tag_rename", "a source or destination inside the reserved provider"),
    ("tag_copy", "a source or destination inside the reserved provider"),
)


def reserved_provider_rule(provider: str) -> str:
    """The product rule that makes the reserved provider unreachable from the Runtime plane."""
    refused = "; ".join(f"{name} refuses {refusal}" for name, refusal in RESERVED_PROVIDER_REFUSALS)
    return (
        "product rule: before Preflight executes, " + refused + ", whatever the Target allowlist "
        "says, including an explicit *; the refusal is by provider, so it covers every Tag in "
        "[" + provider + "] (the policy Tag, its companion length Tag and anything beside them)"
    )


def policy_json() -> str:
    """The canonical policy text a handler must read back byte for byte."""
    return json.dumps(POLICY, sort_keys=True, separators=(",", ":"))


def policy_sha256() -> str:
    return hashlib.sha256(policy_json().encode("utf-8")).hexdigest()


def oversize_policy_json() -> str:
    """A deterministic document larger than `POLICY_MAX_BYTES`, for the gate proof."""
    filler = "x" * (OVERSIZE_POLICY_BYTES - 64)
    return json.dumps({"schemaVersion": 1, "filler": filler}, sort_keys=True, separators=(",", ":"))


def policy_byte_length() -> int:
    return len(policy_json().encode("utf-8"))


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
                "name": POLICY_LENGTH_TAG_NAME,
                "tagType": "AtomicTag",
                "valueSource": "memory",
                "dataType": "Int4",
                "value": policy_byte_length(),
                "enabled": True,
            },
            {
                "name": OVERSIZE_POLICY_TAG_NAME,
                "tagType": "AtomicTag",
                "valueSource": "memory",
                "dataType": "String",
                "value": oversize_policy_json(),
                "enabled": True,
            },
            {
                "name": OVERSIZE_LENGTH_TAG_NAME,
                "tagType": "AtomicTag",
                "valueSource": "memory",
                "dataType": "Int4",
                "value": len(oversize_policy_json().encode("utf-8")),
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
