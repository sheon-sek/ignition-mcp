"""The two operator-supplied documents ``plan`` and ``apply`` reconcile (D20, D30 §1).

* the **Runtime Target Policy**: a deployment-owned JSON document stored as a
  String Tag in the reserved Tag provider ``IgnitionMCPPolicy``, together with a
  companion ``Int4`` Tag that declares its byte length. The storage location, the
  read gate and the product-enforced 32 KiB cap come from the ticket #6 research
  note (``docs/research/runtime-target-policy-storage-and-alarm-query-bound.md``)
  and D30's owner ruling 1. A Runtime Mutation fails closed when the document is
  missing, unreadable, over the cap or malformed, so ``apply`` refuses an over-cap
  or malformed document before it writes anything.
* the **Server Config permissions tree**. ``apply`` never invents one: D20 keeps
  Security Level provisioning opt-in (ticket #22), so a Server Config that does
  not exist yet is only created when the operator supplies the permissions it
  should carry.

Nothing here dispatches a request: the document contracts are pure, and the one
read (``observe_policy``) is an injected read-only Gateway client.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native.inputs import Inputs, UsageError

# --------------------------------------------------------------------------- policy

PROVIDER = "IgnitionMCPPolicy"
TAG_NAME = "RuntimeTargetPolicy"
LENGTH_TAG_NAME = "RuntimeTargetPolicyLength"
#: ``[provider]name`` of the document and of its declared-length companion.
POLICY_PATH = f"[{PROVIDER}]{TAG_NAME}"
LENGTH_PATH = f"[{PROVIDER}]{LENGTH_TAG_NAME}"
#: The product-enforced cap (D30 owner ruling 1). The Gateway has no native size
#: limit on a Tag value, so the writer carries the cap and writes the companion
#: Tag the reader gates on.
MAX_BYTES = 32768
TAG_PROVIDER_TYPE = "ignition/tag-provider"
PROVIDER_DESCRIPTION = "ignition-mcp Runtime Target Policy provider (deployment-owned)"

AUDIT_MODES = frozenset({"best_effort", "required", "off"})
#: Per-Tool D10 item ceilings the document may carry, and their hard maximum.
ITEM_CEILING_FIELDS = ("tagUpdateMaxItems", "tagCreateMaxItems", "tagCopyMaxItems")
HARD_MAX_ITEMS = 100
#: The document's required fields, exactly the shared schema's ``required`` list.
REQUIRED_FIELDS = ("schemaVersion", "allowlists", "serviceIdentity", "auditMode")


def canonical_text(document: dict[str, Any]) -> str:
    """The canonical policy text: sorted keys, compact separators.

    The reader decodes the Tag value and validates it; the canonical form is what
    makes the document's byte length (and therefore the declared-length companion)
    stable across runs, so ``apply`` can compare its own bytes with a read-back.
    """

    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def validate_policy(document: Any, source: str) -> dict[str, Any]:
    """Shape check mirroring ``contracts/shared/runtime-target-policy.schema.json``."""

    def fail(message: str) -> NoReturn:
        raise UsageError(f"{source}: {message}")

    if not isinstance(document, dict):
        fail("must be a JSON object")
    for key in REQUIRED_FIELDS:
        if key not in document:
            fail(f"is missing the required field {key!r}")
    if document["schemaVersion"] != 1:
        fail("schemaVersion must be 1")
    allowlists = document["allowlists"]
    if not isinstance(allowlists, dict):
        fail("allowlists must be an object keyed by Tool name")
    for tool, entries in allowlists.items():
        if not isinstance(entries, list) or any(
            not isinstance(entry, str) or not entry for entry in entries
        ):
            fail(f"allowlists.{tool} must be a list of non-empty strings")
    identity = document["serviceIdentity"]
    if not isinstance(identity, str) or not identity:
        fail("serviceIdentity must be a non-empty string")
    mode = document["auditMode"]
    if not isinstance(mode, str) or mode not in AUDIT_MODES:
        fail(f"auditMode must be one of {', '.join(sorted(AUDIT_MODES))}")
    profile = document.get("auditProfile")
    if profile is not None and (not isinstance(profile, str) or not profile):
        fail("auditProfile must be a non-empty string when present")
    cap = document.get("alarmShelveMaxSeconds")
    if cap is not None and (not _is_int(cap) or cap < 1):
        fail("alarmShelveMaxSeconds must be a positive integer")
    for field in ITEM_CEILING_FIELDS:
        if field in document:
            value = document[field]
            if not _is_int(value) or not 1 <= value <= HARD_MAX_ITEMS:
                fail(f"{field} must be an integer in 1..{HARD_MAX_ITEMS}")
    return document


def load_policy(path: Path) -> str:
    """Read, validate and canonicalize the policy document; enforces the 32 KiB cap."""

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise UsageError(f"--policy-file: cannot read {path}: {type(error).__name__}") from error
    except UnicodeDecodeError as error:
        raise UsageError(f"--policy-file: {path} is not UTF-8 text") from error
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise UsageError(f"--policy-file: {path} is not valid JSON ({error.args[0]})") from error
    validate_policy(document, "--policy-file")
    text = canonical_text(document)
    size = byte_length(text)
    if size > MAX_BYTES:
        raise UsageError(
            f"--policy-file: the document is {size} bytes; the enforced cap is {MAX_BYTES} bytes "
            "(the reader gates on a declared length, so apply never writes an over-cap document)"
        )
    return text


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def provider_resource() -> dict[str, Any]:
    """The Native REST body that creates the reserved policy Tag provider."""

    return {
        "name": PROVIDER,
        "description": PROVIDER_DESCRIPTION,
        "enabled": True,
        "config": {"profile": {"type": "STANDARD"}, "settings": {}},
    }


def policy_tags(text: str) -> list[dict[str, Any]]:
    """The policy Tag and its declared-length companion, as one import document."""

    return [
        {
            "name": TAG_NAME,
            "tagType": "AtomicTag",
            "valueSource": "memory",
            "dataType": "String",
            "value": text,
            "enabled": True,
        },
        {
            "name": LENGTH_TAG_NAME,
            "tagType": "AtomicTag",
            "valueSource": "memory",
            "dataType": "Int4",
            "value": byte_length(text),
            "enabled": True,
        },
    ]


def tag_document(text: str) -> bytes:
    """The ``POST /data/api/v1/tags/import`` body that writes the policy document."""

    return json.dumps({"tags": policy_tags(text)}).encode("utf-8")


@dataclass(frozen=True, slots=True)
class PolicyObservation:
    """What the reserved provider currently serves."""

    provider_present: bool
    error: str = ""
    policy_text: str | None = None
    declared_length: int | None = None

    def matches(self, desired_text: str) -> bool:
        """Whether the served document is the desired one, declared length included."""

        return (
            self.policy_text == desired_text
            and self.declared_length == byte_length(desired_text)
        )


def find_tag(document: Any, name: str) -> dict[str, Any] | None:
    """The Tag node called ``name`` in a provider export (depth-first)."""

    stack = [document]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        children = node.get("tags")
        if not isinstance(children, list):
            continue
        for child in children:
            if isinstance(child, dict):
                if child.get("name") == name:
                    return child
                stack.append(child)
    return None


async def observe_policy(client: gw.GatewayRest) -> PolicyObservation:
    """Read the reserved provider: presence, then the policy and declared-length Tags."""

    try:
        provider = await client.resource_document(TAG_PROVIDER_TYPE, PROVIDER)
    except gw.GatewayProbeError as error:
        return PolicyObservation(provider_present=False, error=f"provider state unknown ({error})")
    if provider is None:
        return PolicyObservation(provider_present=False)
    try:
        document = await client.export_tags(PROVIDER)
    except gw.GatewayProbeError as error:
        return PolicyObservation(provider_present=True, error=f"policy export failed ({error})")
    policy = find_tag(document, TAG_NAME)
    length = find_tag(document, LENGTH_TAG_NAME)
    value = policy.get("value") if isinstance(policy, dict) else None
    declared = length.get("value") if isinstance(length, dict) else None
    return PolicyObservation(
        provider_present=True,
        policy_text=value if isinstance(value, str) else None,
        declared_length=declared if _is_int(declared) else None,
    )


# ------------------------------------------------------------------- Server Config

#: Depth and node ceilings for an operator-supplied permissions tree; the tree is
#: a structural document, so a bound keeps a malformed one from being echoed.
MAX_PERMISSION_DEPTH = 8
MAX_PERMISSION_NODES = 1000


def load_permissions(path: Path) -> dict[str, Any]:
    """Read and structurally validate a Server Config permissions tree."""

    source = "--server-config-permissions-file"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise UsageError(f"{source}: cannot read {path}: {type(error).__name__}") from error
    except UnicodeDecodeError as error:
        raise UsageError(f"{source}: {path} is not UTF-8 text") from error
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise UsageError(f"{source}: {path} is not valid JSON ({error.args[0]})") from error
    if not isinstance(document, dict):
        raise UsageError(f"{source}: the permissions tree must be a JSON object")
    if document.get("type") not in ("AllOf", "AnyOf"):
        raise UsageError(f"{source}: type must be AllOf or AnyOf")
    levels = document.get("securityLevels")
    if not isinstance(levels, list) or not levels:
        raise UsageError(f"{source}: securityLevels must be a non-empty list")
    nodes = _count_levels(levels, source)
    if nodes > MAX_PERMISSION_NODES:
        raise UsageError(f"{source}: securityLevels holds {nodes} nodes; the ceiling is {MAX_PERMISSION_NODES}")
    return document


def _count_levels(levels: list[Any], source: str, depth: int = 1) -> int:
    if depth > MAX_PERMISSION_DEPTH:
        raise UsageError(f"{source}: securityLevels nests deeper than {MAX_PERMISSION_DEPTH}")
    total = 0
    for level in levels:
        if not isinstance(level, dict):
            raise UsageError(f"{source}: every security level must be an object")
        name = level.get("name")
        if not isinstance(name, str) or not name.strip():
            raise UsageError(f"{source}: every security level needs a non-empty name")
        children = level.get("children")
        if children is None:
            continue
        if not isinstance(children, list):
            raise UsageError(f"{source}: children of {name!r} must be a list")
        total += _count_levels(children, source, depth + 1)
    return total + len(levels)


def observed_tools(document: dict[str, Any], project: str) -> tuple[list[str] | None, str]:
    """The Tool list a Server Config document serves for ``project``, and how it reads.

    ``(list, "")`` when the document names an explicit list, ``(None, reason)``
    otherwise: a wildcard, no entry for the project, or a document whose ``config``
    cannot be read at all.
    """

    config = document.get("config")
    if not isinstance(config, dict):
        return None, "the resource document carries no readable config"
    tools = config.get("tools")
    if not isinstance(tools, dict):
        return None, "the config carries no tools mapping"
    entry = tools.get(f"project/{project}")
    if isinstance(entry, list) and all(isinstance(name, str) for name in entry):
        return [str(name) for name in entry], ""
    if isinstance(entry, str) and entry.strip() == "*":
        return None, "the config selects every Tool with a wildcard"
    if entry is None:
        return None, f"the config holds no entry for project/{project}"
    return None, "the config's Tool entry is neither a list nor a wildcard"


def desired_server_config(
    inputs: Inputs, observed: dict[str, Any] | None, permissions: dict[str, Any]
) -> dict[str, Any]:
    """The Server Config document ``apply`` writes for the selected profile.

    Everything the operator already had is preserved; what ``apply`` owns is the
    bundle's version, the permissions tree it was given, and the explicit Tool list
    of the selected profile — never ``*`` (D09/D20). Resources and Prompts keep the
    deployment-owned wildcard the live harness uses, because the Module exposes no
    Resource/Prompt authorization to write: their inventories are verified exactly
    by ``verify`` instead (D09), so nothing unverified is claimed here.
    """

    config: dict[str, Any] = dict(observed) if isinstance(observed, dict) else {}
    config["title"] = str(config.get("title") or inputs.server_config_name or "")
    config["version"] = inputs.bundle_version
    config["permissions"] = permissions
    key = f"project/{inputs.bundle_project}"
    for field in ("tools", "resources", "prompts"):
        held = config.get(field)
        mapping = dict(held) if isinstance(held, dict) else {}
        # Only this bundle's entry is the CLI's to write; another project's mapping,
        # if the operator keeps one, is preserved verbatim (minimum change).
        mapping[key] = list(inputs.profile_inventory("tools")) if field == "tools" else "*"
        config[field] = mapping
    return config


@dataclass(frozen=True, slots=True)
class Documents:
    """The operator-supplied documents one run reasons over (both optional).

    ``plan`` may run without either; ``apply`` requires the policy document and
    needs the permissions tree only when it has to create a Server Config.
    """

    policy_text: str | None = None
    permissions: dict[str, Any] | None = None


def load(inputs: Inputs) -> Documents:
    """Read and validate both documents; raises :class:`UsageError` (exit 2) on either."""

    return Documents(
        policy_text=None if inputs.policy_file is None else load_policy(inputs.policy_file),
        permissions=None if inputs.permissions_file is None else load_permissions(inputs.permissions_file),
    )
