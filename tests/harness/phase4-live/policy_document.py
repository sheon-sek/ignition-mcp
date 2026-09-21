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

# --------------------------------------------------------------------------- #
# Ticket #7 (`tag_write`) live fixtures
#
# The ticket #6 policy above is the characterization fixture for the storage
# question, so its bytes stay frozen. The tag_write stage installs its own
# document over the same reserved provider: the same shape plus the audit profile
# the D18 `best_effort`/`required` modes use, and (as a second state) an explicit
# `*` for the reserved-provider refusal proof.
# --------------------------------------------------------------------------- #

#: The Tag targets the probe project's `tag_fixture_probe` Tool creates. The
#: prefix is the entry the ticket #6 fixture already carries for `tag_write`, and
#: `IgnitionMCP_CI2` is its segment-boundary sibling, which must never match.
TAG_FIXTURE_PROVIDER = "default"
TAG_FIXTURE_ROOT = "IgnitionMCP_CI"
TAG_FIXTURE_SIBLING_ROOT = "IgnitionMCP_CI2"
TAG_FIXTURE_PATH = f"[{TAG_FIXTURE_PROVIDER}]{TAG_FIXTURE_ROOT}/WriteTarget"
TAG_FIXTURE_SIBLING_PATH = f"[{TAG_FIXTURE_PROVIDER}]{TAG_FIXTURE_SIBLING_ROOT}/WriteTarget"
TAG_WRITE_ALLOWLIST = (f"[{TAG_FIXTURE_PROVIDER}]{TAG_FIXTURE_ROOT}",)
WILDCARD_ALLOWLIST = ("*",)
#: A path inside the allowed prefix that does not exist, so the item's Native
#: outcome is a Bad QualityCode rather than a Tool failure (D11).
TAG_FIXTURE_MISSING_PATH = f"[{TAG_FIXTURE_PROVIDER}]{TAG_FIXTURE_ROOT}/Missing"

#: Disposable local audit profile the tag_write stage provisions through Native
#: REST and names in the policy, so the Runtime audit row can be read back.
AUDIT_PROFILE_NAME = "MCP_CI_AUDIT"
#: The Service identity the policy carries; the Runtime audit actor (CONTEXT.md).
SERVICE_IDENTITY = str(POLICY["serviceIdentity"])



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
    return {"tags": policy_tags(policy_json()) + [
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
    ]}


def policy_tags(text: str) -> list[dict[str, Any]]:
    """The policy Tag and its companion declared-length Tag for one document."""
    return [
        {
            "name": POLICY_TAG_NAME,
            "tagType": "AtomicTag",
            "valueSource": "memory",
            "dataType": "String",
            "value": text,
            "enabled": True,
        },
        {
            "name": POLICY_LENGTH_TAG_NAME,
            "tagType": "AtomicTag",
            "valueSource": "memory",
            "dataType": "Int4",
            "value": len(text.encode("utf-8")),
            "enabled": True,
        },
    ]


def tag_write_policy(*, allowlist: tuple[str, ...] = TAG_WRITE_ALLOWLIST, audit_mode: str = "best_effort") -> dict[str, Any]:
    """The policy the `tag_write` live cases run against (ticket #7).

    Shape-identical to :data:`POLICY` plus the D18 audit profile, so the shipped
    reader's schema and the D30 fields are exercised by the same document.
    """
    document = json.loads(json.dumps(POLICY))
    document["allowlists"]["tag_write"] = list(allowlist)
    document["auditMode"] = audit_mode
    document["auditProfile"] = AUDIT_PROFILE_NAME
    return document


def tag_write_policy_json(**overrides: Any) -> str:
    return json.dumps(tag_write_policy(**overrides), sort_keys=True, separators=(",", ":"))


def tag_write_policy_sha256(**overrides: Any) -> str:
    return hashlib.sha256(tag_write_policy_json(**overrides).encode("utf-8")).hexdigest()


def tag_write_policy_byte_length(**overrides: Any) -> int:
    return len(tag_write_policy_json(**overrides).encode("utf-8"))


def tag_write_tag_document_bytes(*, allowlist: tuple[str, ...] = TAG_WRITE_ALLOWLIST, audit_mode: str = "best_effort") -> bytes:
    """Tag import document that updates the policy and its length companion."""
    document = {"tags": policy_tags(tag_write_policy_json(allowlist=allowlist, audit_mode=audit_mode))}
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def audit_profile_resource() -> dict[str, Any]:
    """Native REST body for the disposable local audit profile (D18 read-back)."""
    return {
        "name": AUDIT_PROFILE_NAME,
        "enabled": True,
        "description": "Disposable Phase 4 CI local audit profile for Runtime audit evidence",
        "config": {"profile": {"type": "local"}, "settings": {}},
    }


# --------------------------------------------------------------------------- #
# Ticket #8 (`alarm_shelve`/`alarm_unshelve`) live fixtures
#
# The Alarm stage runs after the tag_write stage, so it installs its own policy
# over the same reserved provider: the alarm allowlists, the audit profile the
# D18 modes name, and a shelve cap below the D12 24 h hard maximum so the cap
# refusal is provable on a live Gateway. The ticket #6 fixture above keeps its
# frozen bytes.
# --------------------------------------------------------------------------- #

#: D12 Phase 4 amendment: a deployment may lower the 24 h maximum and never raise
#: it. The alarm stage runs against this cap and asks for twice it.
ALARM_SHELVE_CAP_SECONDS = 3600


def alarm_paths(root_name: str, provider: str = "default") -> dict[str, str]:
    """The exact Alarm paths the ticket #8 cases use, under one run-unique root.

    `sibling` shares a string prefix with the allowlisted root without sharing a
    path segment, so it is the segment-boundary refusal case; `wildcard` is the
    target form D12 forbids.
    """
    root = "prov:" + provider + ":/tag:" + root_name
    return {
        "root": root,
        "exact": root + "/Exact:/alm:ProbeHi",
        "nested": root + "/Fold/ChildA:/alm:ProbeHi",
        "sibling": root + "_sibling/Exact:/alm:ProbeHi",
        "wildcard": root + "/*",
    }


def alarm_allowlist(root_name: str, provider: str = "default") -> tuple[str, ...]:
    """The one Alarm path prefix the ticket #8 allowlist carries."""
    return ("prov:" + provider + ":/tag:" + root_name,)


def alarm_policy(
    *, allowlist: tuple[str, ...], shelve_cap: int = ALARM_SHELVE_CAP_SECONDS,
    audit_mode: str = "best_effort",
) -> dict[str, Any]:
    """The policy the ticket #8 live cases run against."""
    return {
        "schemaVersion": 1,
        "allowlists": {"alarm_shelve": list(allowlist), "alarm_unshelve": list(allowlist)},
        "serviceIdentity": SERVICE_IDENTITY,
        "auditMode": audit_mode,
        "auditProfile": AUDIT_PROFILE_NAME,
        "alarmShelveMaxSeconds": shelve_cap,
    }


def alarm_policy_json(**overrides: Any) -> str:
    return json.dumps(alarm_policy(**overrides), sort_keys=True, separators=(",", ":"))


def alarm_policy_sha256(**overrides: Any) -> str:
    return hashlib.sha256(alarm_policy_json(**overrides).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Ticket #10 (`tag_update`) live fixtures
#
# The Tag CONFIG Mutation runs on the `configurator` profile, so it installs its
# own document over the same reserved provider: the Tag allowlist the ticket #7
# fixture Tags already live under, the audit profile the D18 modes name, and — as
# a second state — an explicit `_types_` entry for the UDT-definition case.
# --------------------------------------------------------------------------- #

#: The Tag the positive update merges into, and the paths its refusals target.
TAG_UPDATE_TARGET = TAG_FIXTURE_PATH
TAG_UPDATE_TEXT_TARGET = f"[{TAG_FIXTURE_PROVIDER}]{TAG_FIXTURE_ROOT}/TextTarget"
#: A UDT definition path. D30 6 lets a Tag CONFIG Mutation reach it only through an
#: explicit `_types_` entry, and the refusals happen before any read, so the
#: definition itself never has to exist for the live cases to be conclusive.
UDT_NAMESPACE = "_types_"
TAG_UPDATE_UDT_TARGET = f"[{TAG_FIXTURE_PROVIDER}]{UDT_NAMESPACE}/{TAG_FIXTURE_ROOT}/ProbeType"
#: The properties the positive update merges: a description and engineering units
#: are configuration, not a value write, and both must show in the re-read.
TAG_UPDATE_CONFIG = {"documentation": "phase4-updated", "engUnits": "kPa"}
TAG_UPDATE_ALLOWLIST = (f"[{TAG_FIXTURE_PROVIDER}]{TAG_FIXTURE_ROOT}",)
TAG_UPDATE_TYPES_ALLOWLIST = (
    f"[{TAG_FIXTURE_PROVIDER}]{TAG_FIXTURE_ROOT}",
    f"[{TAG_FIXTURE_PROVIDER}]_types_/{TAG_FIXTURE_ROOT}",
)


def tag_update_policy(
    *, allowlist: tuple[str, ...] = TAG_UPDATE_ALLOWLIST, audit_mode: str = "best_effort",
) -> dict[str, Any]:
    """The policy the ticket #10 live cases run against.

    Shape-identical to the ticket #7 document with this Tool's own allowlist key,
    so the shipped reader validates one document shape for every Tag Mutation.
    """
    document = json.loads(json.dumps(POLICY))
    document["allowlists"]["tag_update"] = list(allowlist)
    document["auditMode"] = audit_mode
    document["auditProfile"] = AUDIT_PROFILE_NAME
    return document


def tag_update_policy_json(**overrides: Any) -> str:
    return json.dumps(tag_update_policy(**overrides), sort_keys=True, separators=(",", ":"))


def tag_update_policy_sha256(**overrides: Any) -> str:
    return hashlib.sha256(tag_update_policy_json(**overrides).encode("utf-8")).hexdigest()


def tag_update_tag_document_bytes(**overrides: Any) -> bytes:
    """Tag import document that updates the policy and its length companion."""
    document = {"tags": policy_tags(tag_update_policy_json(**overrides))}
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def alarm_policy_byte_length(**overrides: Any) -> int:
    return len(alarm_policy_json(**overrides).encode("utf-8"))


def tag_update_policy_byte_length(**overrides: Any) -> int:
    return len(tag_update_policy_json(**overrides).encode("utf-8"))


def alarm_policy_tag_document_bytes(**overrides: Any) -> bytes:
    """Tag import document that updates the policy and its length companion."""
    document = {"tags": policy_tags(alarm_policy_json(**overrides))}
    return json.dumps(document, separators=(",", ":")).encode("utf-8")



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
