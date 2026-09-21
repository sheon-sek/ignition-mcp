"""D30 config-resource Mutations on the Native REST plane (Phase 4).

The first REST Mutation Tool. Everything it does goes through the D08 chain: the
caller supplies the Resource signature ``config_resource_get`` gave it, the
deployment policy decides whether the operation and Target are allowed, a Refused
resource type is denied whatever the allowlist says, and the write is dispatched
once by the guarded executor — never replayed.

Three rules decide the wire item:

- **D03 schema validation.** The change item is validated against the target
  Gateway's own documented PUT request schema, taken from the D04 snapshot, before
  anything is dispatched. A Gateway that documents an update route without a usable
  schema exposes no update at all (the capability is withheld), so no change is ever
  sent unvalidated.
- **D30 §2 Precondition.** The signature is sent to the Gateway natively *and*
  compared against a bounded read immediately before dispatch, so a stale token fails
  with ``conflict`` without a change byte being built, and a Gateway that reports no
  signature can never be preconditioned. The read-compare narrows the race window but
  does not remove it: the Gateway's check stays the authority, and a refusal it
  reports itself is mapped to the caller's error as well.
- **Target identity.** A Target is the exact ``<resourceType>/<name>`` in the
  default configuration collection. A caller-supplied collection is refused, because
  the Target allowlist cannot name one unambiguously; see
  ``resource_target_id`` and the runbook's open question.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote

# jsonschema ships no type stubs and needs none here: the one call site validates a
# bundled schema and reads only ``validator``/``absolute_path`` from the errors.
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry, ConfigResourceCapability
from ignition_rest_mcp.client.gateway import DispatchOutcome, GatewayClient, WriteDispatchResult
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import ConfigResourceUpdateResult
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.safety.executor import (
    MutationRequest,
    VerificationOutcome,
    execute_mutation,
    mutation_failure,
)
from ignition_rest_mcp.safety.policy import CONFIG_MUTATION, MutationOperation
from ignition_rest_mcp.safety.refused_resource_types import refuse_resource_type_decision
from ignition_rest_mcp.services.config_resources import (
    bounded_text,
    catalog_resource_type,
    redact,
    resource_signature,
    resource_target_id,
)

CONFIG_RESOURCE_UPDATE = MutationOperation(
    op_id="config_resource_update",
    mutation_class=CONFIG_MUTATION,
    capability="config_resource_update",
    destructive=False,
    #: D30 §7: a Phase 4 Mutation answers `permission_denied` for a Target outside
    #: its Target allowlist.
    target_denial_code="permission_denied",
)

UPDATE_OPERATION_ID = CONFIG_RESOURCE_UPDATE.op_id

#: D10: the change is bounded and an oversize change fails explicitly. The ceiling
#: matches the deployment's default structured-output budget instead of inventing a
#: second number for the same "one bounded payload" rule.
MAX_CHANGE_BYTES = 262_144
MAX_NAME_LENGTH = 512
MAX_SIGNATURE_LENGTH = 512
MAX_DESCRIPTION_LENGTH = 1024
SIGNATURE_DISPLAY_LENGTH = 64

#: D30 §4: the caller can never choose this knob.
ALLOW_INVALID_REFERENCES = "false"


async def config_resource_update(
    client: GatewayClient,
    registry: CapabilityRegistry,
    settings: Settings,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    resource_type: str,
    name: str,
    collection: str,
    expected_signature: str,
    config: dict[str, Any] | None,
    enabled: bool | None,
    description: str | None,
) -> ConfigResourceUpdateResult:
    """Change one configuration resource, preconditioned on its Resource signature."""

    if not registry.supports(CONFIG_RESOURCE_UPDATE.capability):
        raise GatewayError(
            "unsupported_capability",
            "config_resource_update is not present in the current Gateway capability snapshot",
        )
    capability = catalog_resource_type(registry, resource_type)
    if capability.update_path is None:
        raise GatewayError(
            "unsupported_capability",
            "this resourceType has no documented update route on the connected Gateway",
        )
    name = _requested_name(capability, name)
    collection = _default_collection(collection)
    expected_signature = _required_signature(expected_signature)
    fields = _change_fields(capability, config, enabled, description)
    item = _change_item(capability, name, expected_signature, fields)
    body = _wire_body(item)

    #: Filled by the pre-dispatch read; the Gateway's own refusal is what a stale
    #: token produces when the resource changes inside the race window.
    read_state: dict[str, Any] = {}

    async def precondition() -> None:
        current = await _read_resource(client, context, capability, name, collection)
        read_state["resource"] = current
        signature = resource_signature(current)
        if signature is None:
            raise GatewayError(
                "conflict",
                "the Gateway reports no Resource signature for this resource, so the change "
                "cannot be preconditioned and was not dispatched",
            )
        if signature != expected_signature:
            # The resource changed after the caller read it. Nothing was dispatched.
            raise GatewayError(
                "conflict",
                "the resource changed after it was read (expected signature "
                f"{_display(expected_signature)}, the Gateway now reports {_display(signature)}); "
                "nothing was dispatched",
            )

    async def verify(dispatch: WriteDispatchResult) -> VerificationOutcome:
        current = await _read_resource(client, context, capability, name, collection)
        before = read_state["resource"]
        # D30 §6 (Observed state): the bounded read-back is reported as data and
        # decides success by answering "is the intended change visible?".
        read_state["observed"] = current
        unchanged = _still_pre_state(current, before, fields)
        if _is_claimed_success(dispatch):
            if _matches_intended_state(current, fields):
                return VerificationOutcome.CONFIRMED
            return VerificationOutcome.UNCHANGED if unchanged else VerificationOutcome.MISMATCH
        # A refused or ambiguous dispatch is only a recovered success if the resource
        # actually moved *in the direction this call asked for*. Two things make that
        # unattributable: the resource still shows the pre-state, or the pre-state
        # already satisfied everything this call requested — a request whose values
        # were already in place cannot be credited with any observation, however the
        # resource moved afterwards. Both return UNCHANGED, which the executor maps to
        # a rejection (keeping an explicit Gateway error authoritative) or to
        # not-applied, never to a success.
        if unchanged or _already_satisfied(fields, before):
            return VerificationOutcome.UNCHANGED
        if _matches_intended_state(current, fields):
            return VerificationOutcome.CONFIRMED
        return VerificationOutcome.MISMATCH

    mutation = await execute_mutation(
        client=client, registry=registry, settings=settings, context=context,
        request=MutationRequest(
            operation=CONFIG_RESOURCE_UPDATE, principal=principal,
            target_id=resource_target_id(capability, name),
            request_path=capability.update_path,
            method="PUT",
            params={"allowInvalidReferences": ALLOW_INVALID_REFERENCES},
            body_chunks=_chunked(body),
            content_type="application/json",
            dispatch_deadline_seconds=settings.budget_deadline_seconds("FAST"),
            verification_deadline_seconds=settings.budget_deadline_seconds("FAST"),
            verify=verify,
            precondition=precondition,
            rejection=_gateway_rejection,
            target_policy=_target_policy(capability),
            audit_fields={
                "resourceType": capability.resource_type, "name": name, "collection": collection,
            },
            target_type="config-resource",
        ),
    )
    failure = mutation_failure(mutation)
    if failure is not None:
        raise failure
    observed = read_state["observed"]
    return ConfigResourceUpdateResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        name=name,
        collection=collection,
        signature=resource_signature(observed),
        observedState=redact(observed),
    )


# ------------------------------------------------------------------ input


def _requested_name(capability: ConfigResourceCapability, value: str) -> str:
    if capability.singleton:
        if value:
            raise GatewayError("invalid_argument", "name must be empty for a singleton resourceType")
        return ""
    return bounded_text(value, "name", MAX_NAME_LENGTH, allow_empty=False)


def _required_signature(value: str) -> str:
    signature = bounded_text(value, "expectedSignature", MAX_SIGNATURE_LENGTH, allow_empty=False)
    if any(character.isspace() for character in signature):
        raise GatewayError("invalid_argument", "expectedSignature must not contain whitespace")
    return signature


def _change_fields(
    capability: ConfigResourceCapability,
    config: dict[str, Any] | None,
    enabled: bool | None,
    description: str | None,
) -> dict[str, Any]:
    """The validated, name-independent part of one change item (D30 §3 Preflight)."""

    if config is None and enabled is None and description is None:
        raise GatewayError(
            "invalid_argument",
            "the change is empty: supply config, enabled or description, or the call would "
            "consume the Resource signature without changing anything",
        )
    if description is not None and len(description) > MAX_DESCRIPTION_LENGTH:
        raise GatewayError(
            "limit_exceeded",
            f"description exceeds the {MAX_DESCRIPTION_LENGTH}-character limit",
        )
    fields: dict[str, Any] = {}
    if config is not None:
        fields["config"] = config
    if enabled is not None:
        fields["enabled"] = enabled
    if description is not None:
        fields["description"] = description
    return fields


def _default_collection(value: str) -> str:
    """D30: a Target is a resource in the *default* collection.

    The Gateway selects the resource a read or a change applies to by collection as
    well as by name, so a Target allowlist entry for ``<resourceType>/<name>`` would
    otherwise authorize the same name in every collection. Until the Target policy can
    name a collection unambiguously, a caller-supplied collection is refused
    (fail closed) and only the default collection is addressable.
    """

    collection = bounded_text(value, "collection", 128, allow_empty=True)
    if collection:
        raise GatewayError(
            "invalid_argument",
            "collection is not supported: a Target is the exact <resourceType>/<name> in the "
            "default collection, and this deployment's Target allowlist cannot name a "
            "collection, so a collection-qualified change is refused",
        )
    return collection


def _change_item(
    capability: ConfigResourceCapability,
    name: str,
    expected_signature: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """The complete Gateway-shaped change item, validated against the snapshot (D03).

    The name is included exactly when the Gateway's documented item schema declares
    one: a singleton's item schema requires only ``signature``, and sending an
    undeclared field is not what the Gateway documents.
    """

    item: dict[str, Any] = {"signature": expected_signature}
    if _item_schema_declares_name(capability):
        item["name"] = name
    item.update(fields)
    _validate_change_item(capability, item)
    return item


def _item_schema_declares_name(capability: ConfigResourceCapability) -> bool:
    schema = capability.update_request_schema
    if not isinstance(schema, Mapping):
        return False
    properties = schema.get("properties")
    return isinstance(properties, Mapping) and "name" in properties


def _validate_change_item(capability: ConfigResourceCapability, item: dict[str, Any]) -> None:
    """D03: the change must satisfy the Gateway's own documented request schema.

    The snapshot's schema is self-contained (bundled at refresh time), so this cannot
    hit an unresolvable reference. The failure message names the failing location and
    keyword, never the caller's value: the D06 envelope is not a place to echo a
    configuration document, which may hold embedded secrets.
    """

    schema = capability.update_request_schema
    if not isinstance(schema, Mapping):
        raise GatewayError(
            "unsupported_capability",
            "this resourceType has no documented update request schema on the connected Gateway",
        )
    errors = sorted(Draft202012Validator(schema).iter_errors(item), key=_error_order)
    if not errors:
        return
    first = errors[0]
    location = "/" + "/".join(str(part) for part in first.absolute_path) if first.absolute_path else "/"
    raise GatewayError(
        "invalid_argument",
        f"the change does not match the Gateway's documented request schema at {location}: "
        f"{first.validator} constraint failed",
    )


def _error_order(error: Any) -> tuple[int, str]:
    path = [str(part) for part in error.absolute_path]
    return (len(path), "/".join(path))


def _wire_body(item: dict[str, Any]) -> bytes:
    """The bounded Gateway body: one JSON array holding the single change item."""

    raw = json.dumps([item], separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_CHANGE_BYTES:
        raise GatewayError(
            "limit_exceeded",
            f"the change requires {len(raw)} bytes; the limit is {MAX_CHANGE_BYTES} bytes",
        )
    return raw


def _target_policy(capability: ConfigResourceCapability) -> Callable[[], Any]:
    def policy() -> Any:
        return refuse_resource_type_decision(capability.resource_type)

    return policy


def _gateway_rejection(dispatch: WriteDispatchResult) -> GatewayError | None:
    """The Gateway's own refusal inside a 2xx resource response (D30 §4/§7).

    Ignition reports a change it refused as ``{"success": false, "problem": {...}}``
    with HTTP 200. The Gateway's problem text never reaches the caller — only the
    fixed safe message does, exactly as the HTTP status mapping behaves.
    """

    body = dispatch.body
    if not isinstance(body, dict):
        return None
    if body.get("success") is not False and not isinstance(body.get("problem"), dict):
        return None
    if _mentions_signature(body):
        return GatewayError(
            "conflict",
            "Ignition refused the change because the Resource signature is stale; "
            "nothing was replayed",
        )
    return GatewayError("invalid_argument", "Ignition refused the change; nothing was replayed")


def _mentions_signature(body: dict[str, Any]) -> bool:
    problem = body.get("problem")
    message = problem.get("message") if isinstance(problem, dict) else None
    return isinstance(message, str) and "signature" in message.lower()


# ------------------------------------------------------------------ gateway


async def _read_resource(
    client: GatewayClient,
    context: OperationContext,
    capability: ConfigResourceCapability,
    name: str,
    collection: str,
) -> dict[str, Any]:
    """The bounded read that supplies the Precondition token and, for a singleton,
    the wire identity."""

    params: dict[str, Any] = {"collection": collection} if collection else {}
    if capability.singleton:
        path = capability.singleton_path
    else:
        template = capability.find_path_template
        path = template.replace("{name}", quote(name, safe="")) if template else None
    if path is None:
        raise GatewayError("unsupported_capability", "This resourceType has no exact lookup route")
    return await client.get_json(path, params=params or None, context=context)


async def _chunked(body: bytes) -> AsyncIterator[bytes]:
    yield body


def _is_claimed_success(dispatch: WriteDispatchResult) -> bool:
    """Whether the Gateway answered with a success status and no refusal of its own."""

    return (
        dispatch.outcome is DispatchOutcome.RESPONDED
        and dispatch.status is not None
        and 200 <= dispatch.status < 300
        and _gateway_rejection(dispatch) is None
    )


def _already_satisfied(fields: dict[str, Any], before: dict[str, Any]) -> bool:
    """Whether the pre-state already showed every value this call requested.

    Such a call has no observable effect of its own, so nothing it observes can be
    attributed to it.
    """

    return _matches_intended_state(before, fields)


def _still_pre_state(
    current: dict[str, Any], before: dict[str, Any], fields: dict[str, Any],
) -> bool:
    """Whether the resource still shows the state the caller preconditioned on.

    Compared on the supplied fields plus the Resource signature, so an unrelated
    Gateway-side field cannot turn "nothing happened" into an ambiguous outcome.
    """

    if resource_signature(current) != resource_signature(before):
        return False
    keys = set(fields) & {"config", "enabled", "description"}
    for key in keys:
        if current.get(key) != before.get(key):
            return False
    return True


def _matches_intended_state(current: dict[str, Any], fields: dict[str, Any]) -> bool:
    """Whether the observed resource shows the intended change.

    Only the fields the caller supplied are compared, so Gateway-added defaults do
    not read as a mismatch, and ``config`` is compared key by key rather than as a
    whole document for the same reason.
    """

    for key in ("enabled", "description"):
        if key in fields and current.get(key) != fields[key]:
            return False
    if "config" in fields:
        observed_config = current.get("config")
        if not isinstance(observed_config, dict):
            return False
        for key, value in fields["config"].items():
            if observed_config.get(key) != value:
                return False
    return True


def _display(signature: str) -> str:
    return signature[:SIGNATURE_DISPLAY_LENGTH]
