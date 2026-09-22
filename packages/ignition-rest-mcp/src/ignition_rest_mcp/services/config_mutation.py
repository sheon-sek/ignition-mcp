"""D30 config-resource Mutations on the Native REST plane (Phase 4).

The Phase 4 REST Mutation Tools. Everything each one does goes through the D08
chain: the caller supplies the Resource signature ``config_resource_get`` gave it
(where the operation has one), the deployment policy decides whether the operation
and every Target it changes are allowed, a Refused resource type is denied whatever
the allowlist says, and the write is dispatched once by the guarded executor — never
replayed.

Three rules decide the wire item:

- **D03 schema validation.** The change item is validated against the target
  Gateway's own documented request schema, taken from the D04 snapshot, before
  anything is dispatched. A Gateway that documents a write route without a usable
  schema exposes no such write at all (the capability is withheld), so no change is
  ever sent unvalidated.
- **D30 §2 Precondition.** A Resource signature is sent to the Gateway natively
  where its route can carry one (``PUT``, ``DELETE``) *and* compared against a
  bounded read immediately before dispatch, so a stale token fails with ``conflict``
  without a change byte being built, and a Gateway that reports no signature can
  never be preconditioned. The rename endpoint takes no signature, so its token is a
  server-side read-compare only: the Gateway's check stays the authority for an
  update and a delete, and a refusal it reports itself is mapped to the caller's
  error as well. A read-compare narrows the race window but does not remove it.
- **Target identity.** A Target is the exact ``<resourceType>/<name>`` in the
  ``core`` configuration collection (D30 owner ruling 5), and every Gateway read and
  write this module makes names that collection: the reads send
  ``collection=core`` as a query parameter, a change item always carries it as the
  field its documented request schema declares, and the ``DELETE`` and rename routes
  send it as a query parameter. A type whose documented item schema cannot carry that
  field, or cannot accept ``core``, has no way to address a Target here and is refused
  before anything is dispatched — an item is never sent for the Gateway's own default
  to place. A caller-supplied collection is accepted only when it *is* ``core``;
  anything else is refused before anything is read or dispatched, because the Target
  allowlist names a resource and not a collection. A rename changes its source *and*
  produces a resource at its destination, so both are Targets and both must be
  allowlisted (D30 §3).

Because an explicit Gateway rejection is final for these Tools (D30 §2), every
operation is declared ``rejection_is_final``: the caller either gets the Gateway's
own refusal (``conflict`` for a signature mismatch or a collision) or the
confirmation of a claim the Gateway itself made. ``recovered_success`` is unreachable
here, and an ambiguous dispatch whose read-back looks right is ``outcome_unknown``
rather than a success.
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
from ignition_rest_mcp.models import (
    ConfigResourceCreateResult,
    ConfigResourceDeleteResult,
    ConfigResourceRenameResult,
    ConfigResourceUpdateResult,
)
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.safety.executor import (
    MutationRequest,
    VerificationOutcome,
    execute_mutation,
    mutation_failure,
)
from ignition_rest_mcp.safety.policy import CONFIG_MUTATION, MutationOperation
from ignition_rest_mcp.safety.refused_resource_types import refuse_resource_type_decision
from ignition_rest_mcp.safety.verification import verdict as _verdict
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
    #: D30 §2: an explicit Gateway rejection is this Tool's result. Neither a stale
    #: signature nor any other refusal may be reported as a success.
    rejection_is_final=True,
)

CONFIG_RESOURCE_CREATE = MutationOperation(
    op_id="config_resource_create",
    mutation_class=CONFIG_MUTATION,
    capability="config_resource_create",
    destructive=False,
    target_denial_code="permission_denied",
    #: Create takes no Precondition token (D30 §2), so nothing but the Gateway's own
    #: answer can decide the outcome: an explicit refusal is final, and an ambiguous
    #: dispatch whose read-back shows the resource is still outcome_unknown.
    rejection_is_final=True,
)

CONFIG_RESOURCE_DELETE = MutationOperation(
    op_id="config_resource_delete",
    mutation_class=CONFIG_MUTATION,
    capability="config_resource_delete",
    destructive=True,
    target_denial_code="permission_denied",
    rejection_is_final=True,
)

CONFIG_RESOURCE_RENAME = MutationOperation(
    op_id="config_resource_rename",
    mutation_class=CONFIG_MUTATION,
    capability="config_resource_rename",
    destructive=False,
    target_denial_code="permission_denied",
    rejection_is_final=True,
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

#: D30 §4: the caller can never choose these knobs.
ALLOW_INVALID_REFERENCES = "false"
REFERENCES_ABORT = "ABORT"

#: D30 owner ruling 5: generic config Mutations always target the ``core`` collection,
#: and the Gateway is told so on every read and every write.
CORE_COLLECTION = "core"


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

    capability = _write_capability(registry, CONFIG_RESOURCE_UPDATE, resource_type)
    if capability.update_path is None:
        raise GatewayError(
            "unsupported_capability",
            "this resourceType has no documented update route on the connected Gateway",
        )
    name = _requested_name(capability, name)
    collection = _core_collection(collection)
    expected_signature = _required_signature(expected_signature)
    fields = _change_fields(config, enabled, description, required=True)
    item = _write_item(
        capability, capability.update_request_schema, name, expected_signature, fields,
    )
    body = _wire_body(item)

    #: Filled by the pre-dispatch read; the Gateway's own refusal is what a stale
    #: token produces when the resource changes inside the race window.
    read_state: dict[str, Any] = {}

    async def precondition() -> None:
        read_state["resource"] = await _preconditioned_read(
            client, context, capability, name, expected_signature,
        )

    async def verify(dispatch: WriteDispatchResult) -> VerificationOutcome:
        current = await _read_resource(client, context, capability, name)
        before = read_state["resource"]
        # D30 §6 (Observed state): the bounded read-back is reported as data and
        # decides success by answering "is the intended change visible?".
        read_state["observed"] = current
        return _verdict(
            claimed=_is_claimed_success(dispatch),
            intended=_matches_intended_state(current, fields),
            pre_state=_pre_state_intact(current, before, fields),
            # A change whose values were already in place has no observable effect of
            # its own: nothing it observes can be attributed to it.
            no_effect=_already_satisfied(fields, before),
        )

    mutation = await execute_mutation(
        client=client, registry=registry, settings=settings, context=context,
        request=MutationRequest(
            operation=CONFIG_RESOURCE_UPDATE, principal=principal,
            target_id=resource_target_id(capability, name),
            request_path=capability.update_path,
            method="PUT",
            # D30 owner ruling 5: this route documents no collection query parameter, so
            # the collection travels in the change item, which names `core`.
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


async def config_resource_create(
    client: GatewayClient,
    registry: CapabilityRegistry,
    settings: Settings,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    resource_type: str,
    name: str,
    collection: str,
    config: dict[str, Any] | None,
    enabled: bool | None,
    description: str | None,
) -> ConfigResourceCreateResult:
    """Create one configuration resource.

    D30 §2: create takes no Precondition token, so there is nothing to compare before
    dispatch; the collision policy decides the refusals instead. D11 says an existing
    target fails with ``conflict``, which is checked against the Gateway before
    anything is sent *and* reported by the Gateway itself if the target appears in the
    race window.
    """

    capability = _write_capability(registry, CONFIG_RESOURCE_CREATE, resource_type)
    if capability.create_path is None:
        raise GatewayError(
            "unsupported_capability",
            "this resourceType has no documented create route on the connected Gateway",
        )
    name = _requested_name(capability, name)
    collection = _core_collection(collection)
    fields = _change_fields(config, enabled, description, required=False)
    item = _write_item(capability, capability.create_request_schema, name, None, fields)
    body = _wire_body(item)

    read_state: dict[str, Any] = {}

    async def precondition() -> None:
        if await _probe_resource(client, context, capability, name) is not None:
            # D11 collision policy: the target exists, so this call has nothing to
            # create. Nothing was dispatched and the caller keeps its read.
            raise GatewayError(
                "conflict",
                "the target already exists, so there is nothing to create; nothing was dispatched",
            )

    async def verify(dispatch: WriteDispatchResult) -> VerificationOutcome:
        current = await _probe_resource(client, context, capability, name)
        read_state["observed"] = current
        return _verdict(
            claimed=_is_claimed_success(dispatch),
            intended=current is not None and _matches_intended_state(current, fields),
            # The pre-state of a create is the target's absence.
            pre_state=current is None,
        )

    mutation = await execute_mutation(
        client=client, registry=registry, settings=settings, context=context,
        request=MutationRequest(
            operation=CONFIG_RESOURCE_CREATE, principal=principal,
            target_id=resource_target_id(capability, name),
            request_path=capability.create_path,
            method="POST",
            # D30 owner ruling 5: this route documents no collection query parameter, so
            # the collection travels in the change item, which names `core`.
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
    return ConfigResourceCreateResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        name=name,
        collection=collection,
        signature=resource_signature(observed),
        observedState=redact(observed),
    )


async def config_resource_delete(
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
) -> ConfigResourceDeleteResult:
    """Delete one configuration resource.

    D30 §2 puts the Resource signature in the ``DELETE`` path, so the Gateway enforces
    the token itself; a bounded read-compare immediately before dispatch still turns a
    stale token into ``conflict`` without touching the resource. The caller cannot
    authorize a cascading delete: the route's ``confirm`` flag is never sent, so a
    delete the Gateway wants confirmed is a refusal like any other.
    """

    capability = _write_capability(registry, CONFIG_RESOURCE_DELETE, resource_type)
    template = capability.delete_path_template
    if template is None:
        raise GatewayError(
            "unsupported_capability",
            "this resourceType has no documented delete route on the connected Gateway",
        )
    name = _requested_name(capability, name)
    collection = _core_collection(collection)
    expected_signature = _required_signature(expected_signature)
    path = _delete_path(template, name, expected_signature)

    read_state: dict[str, Any] = {}

    async def precondition() -> None:
        read_state["resource"] = await _preconditioned_read(
            client, context, capability, name, expected_signature,
        )

    async def verify(dispatch: WriteDispatchResult) -> VerificationOutcome:
        current = await _probe_resource(client, context, capability, name)
        read_state["observed"] = current
        return _verdict(
            claimed=_is_claimed_success(dispatch),
            # The intended post-state of a delete is the Target's absence.
            intended=current is None,
            pre_state=_pre_state_intact(current, read_state["resource"]),
        )

    mutation = await execute_mutation(
        client=client, registry=registry, settings=settings, context=context,
        request=MutationRequest(
            operation=CONFIG_RESOURCE_DELETE, principal=principal,
            target_id=resource_target_id(capability, name),
            request_path=path,
            method="DELETE",
            # D30 owner ruling 5: the documented DELETE route takes the collection as a
            # query parameter, and the change is always made in `core`.
            params={"collection": CORE_COLLECTION},
            body_chunks=_no_body(),
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
    return ConfigResourceDeleteResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        name=name,
        collection=collection,
        # The bounded Observed state a delete leaves: the Target's absence, which the
        # verification had to observe for the call to be reported as a success.
        present=read_state["observed"] is not None,
    )


async def config_resource_rename(
    client: GatewayClient,
    registry: CapabilityRegistry,
    settings: Settings,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    resource_type: str,
    name: str,
    new_name: str,
    collection: str,
    expected_signature: str,
) -> ConfigResourceRenameResult:
    """Rename one configuration resource.

    D30 §2: the rename endpoint takes no signature, so the Precondition token is a
    server-side read-compare only — the race window between that read and the dispatch
    remains, and no atomicity is claimed. Both the resource being renamed and the one
    the rename produces are Targets (D30 §3), so the deployment checks and allowlists
    both before anything executes. The destination must not exist (D11 collision
    policy), and ``references=ABORT`` is always sent (D30 §4).
    """

    capability = _write_capability(registry, CONFIG_RESOURCE_RENAME, resource_type)
    template = capability.rename_path_template
    if template is None:
        raise GatewayError(
            "unsupported_capability",
            "this resourceType has no documented rename route on the connected Gateway",
        )
    name = _requested_name(capability, name)
    collection = _core_collection(collection)
    new_name = _new_name(name, new_name)
    expected_signature = _required_signature(expected_signature)
    body = _rename_body(capability, new_name)
    path = template.replace("{name}", quote(name, safe=""))

    read_state: dict[str, Any] = {}

    async def precondition() -> None:
        read_state["resource"] = await _preconditioned_read(
            client, context, capability, name, expected_signature,
        )
        if await _probe_resource(client, context, capability, new_name) is not None:
            # D11 collision policy: the rename destination is taken, so nothing may be
            # dispatched, and the caller's signature is not consumed.
            raise GatewayError(
                "conflict",
                "a resource already exists at the rename destination; nothing was dispatched",
            )

    async def verify(dispatch: WriteDispatchResult) -> VerificationOutcome:
        renamed = await _probe_resource(client, context, capability, new_name)
        source = await _probe_resource(client, context, capability, name)
        read_state["observed"] = renamed
        return _verdict(
            claimed=_is_claimed_success(dispatch),
            intended=renamed is not None and source is None,
            pre_state=renamed is None and _pre_state_intact(source, read_state["resource"]),
        )

    mutation = await execute_mutation(
        client=client, registry=registry, settings=settings, context=context,
        request=MutationRequest(
            operation=CONFIG_RESOURCE_RENAME, principal=principal,
            target_id=resource_target_id(capability, name),
            # D30 §3: the rename produces a resource at its destination, so that
            # resource is a Target of this call too and must be allowlisted.
            additional_target_ids=(resource_target_id(capability, new_name),),
            request_path=path,
            method="POST",
            # D30 owner ruling 5: the documented rename route takes the collection as a
            # query parameter (`references` travels in the body).
            params={"collection": CORE_COLLECTION},
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
    return ConfigResourceRenameResult(
        correlationId=context.correlation_id,
        resourceType=capability.resource_type,
        previousName=name,
        name=new_name,
        collection=collection,
        signature=resource_signature(observed),
        observedState=redact(observed),
    )


# ------------------------------------------------------------------ input


def _write_capability(
    registry: CapabilityRegistry, operation: MutationOperation, resource_type: str,
) -> ConfigResourceCapability:
    """The catalogued resource type a write addresses, or a refusal.

    The operation's semantic capability must be in the current snapshot before any
    per-type route is considered: a Gateway whose document has no write route at all
    exposes no such Tool.
    """

    if not registry.supports(operation.capability):
        raise GatewayError(
            "unsupported_capability",
            f"{operation.op_id} is not present in the current Gateway capability snapshot",
        )
    return catalog_resource_type(registry, resource_type)


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
    config: dict[str, Any] | None,
    enabled: bool | None,
    description: str | None,
    *,
    required: bool,
) -> dict[str, Any]:
    """The validated part of one write that does not identify its Target (D30 §3).

    ``required`` is the update's rule, not a universal one: a change that supplies
    nothing would consume the caller's Resource signature without changing anything,
    while a create is fully described by the name it is asked to publish.
    """

    if required and config is None and enabled is None and description is None:
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


def _new_name(current: str, value: str) -> str:
    """The rename destination (D30 §3: validated before anything executes)."""

    new_name = bounded_text(value, "newName", MAX_NAME_LENGTH, allow_empty=False)
    if new_name == current:
        raise GatewayError(
            "invalid_argument",
            "newName is the resource's current name, so the rename would consume the "
            "Resource signature without changing anything",
        )
    return new_name


def _core_collection(value: str) -> str:
    """The one collection a config Mutation may address (D30 owner ruling 5).

    The Gateway selects the resource a read or a change applies to by collection as
    well as by name, so a Target allowlist entry for ``<resourceType>/<name>`` would
    otherwise authorize the same name in every collection. The ruling therefore pins
    every config Mutation to ``core``: an omitted (or empty) collection *means*
    ``core`` and is sent explicitly, an explicit ``core`` is accepted, and any other
    value is refused before anything is read or dispatched.
    """

    collection = bounded_text(value, "collection", 128, allow_empty=True)
    if collection and collection != CORE_COLLECTION:
        raise GatewayError(
            "invalid_argument",
            "collection must be core: a Target is the exact <resourceType>/<name> in the "
            "core collection, which is the only collection this Tool addresses",
        )
    return CORE_COLLECTION


def _core_collection_refusal() -> GatewayError:
    """D30 owner ruling 5: a write that cannot name the core collection has no Target.

    The collection routes document no collection query parameter, so a change item is
    the only place a ``PUT``/``POST`` can carry the collection. A type whose documented
    item schema cannot carry the field — or cannot accept ``core`` — is a type this Tool
    cannot address at all, and no change may be sent for it: the Gateway's own default
    would otherwise choose the collection the write lands in.
    """

    return GatewayError(
        "unsupported_capability",
        "this resourceType's documented change item cannot name the core collection, which "
        "is the only collection this Tool addresses; nothing was dispatched",
    )


def _write_item(
    capability: ConfigResourceCapability,
    schema: Mapping[str, Any] | None,
    name: str,
    expected_signature: str | None,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """The complete Gateway-shaped change item, validated against the snapshot (D03).

    The Resource signature is included when the operation's route carries one, the name
    exactly when the documented item schema declares one, and the collection *always*:
    a singleton's update item requires only ``signature``, a create item takes no
    signature at all, and sending an undeclared field is not what the Gateway documents.
    The collection is always ``core`` (D30 owner ruling 5), and the item always names it
    — where the documented item schema cannot carry that field the write has no way to
    address the Target, so it is refused here instead of being dispatched for the
    Gateway's own default to place.
    """

    if not _schema_declares(schema, "collection"):
        raise _core_collection_refusal()
    item: dict[str, Any] = {}
    if expected_signature is not None:
        item["signature"] = expected_signature
    if _schema_declares(schema, "name"):
        item["name"] = name
    item["collection"] = CORE_COLLECTION
    item.update(fields)
    _validate_against_gateway(capability, schema, item)
    return item


def _rename_body(capability: ConfigResourceCapability, new_name: str) -> bytes:
    """The bounded rename body, validated against the documented request schema (D03).

    D30 §4 fixes ``references`` to ``ABORT``: the caller cannot choose a value that
    would let the rename change resources outside its Targets.
    """

    payload: dict[str, Any] = {"name": new_name, "references": REFERENCES_ABORT}
    _validate_against_gateway(capability, capability.rename_request_schema, payload)
    return _bounded_body(payload)


def _schema_declares(schema: Mapping[str, Any] | None, field: str) -> bool:
    if not isinstance(schema, Mapping):
        return False
    properties = schema.get("properties")
    return isinstance(properties, Mapping) and field in properties


def _delete_path(template: str, name: str, expected_signature: str) -> str:
    """The documented ``DELETE`` route with both path parameters percent-encoded.

    The signature of a DELETE is part of the path, so it is encoded segment by
    segment: a token containing a separator must not become a second path segment.
    """

    return template.replace("{name}", quote(name, safe="")).replace(
        "{signature}", quote(expected_signature, safe=""),
    )


def _validate_against_gateway(
    capability: ConfigResourceCapability,
    schema: Mapping[str, Any] | None,
    payload: dict[str, Any],
) -> None:
    """D03: the body must satisfy the Gateway's own documented request schema.

    The snapshot's schema is self-contained (bundled at refresh time), so this cannot
    hit an unresolvable reference. The failure message names the failing location and
    keyword, never the caller's value: the D06 envelope is not a place to echo a
    configuration document, which may hold embedded secrets.
    """

    if not isinstance(schema, Mapping):
        raise GatewayError(
            "unsupported_capability",
            "this resourceType has no documented request schema for this route on the "
            "connected Gateway",
        )
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=_error_order)
    if not errors:
        return
    first = errors[0]
    if first.absolute_path and str(first.absolute_path[0]) == "collection":
        # D30 owner ruling 5: `core` is the only value this module ever puts in the
        # field, so a schema that refuses it describes a type this Tool cannot address
        # — not anything the caller supplied, which is why this is not an
        # `invalid_argument`.
        raise _core_collection_refusal()
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

    return _bounded_body([item])


def _bounded_body(payload: Any) -> bytes:
    """One bounded JSON request body (D10: oversize input fails explicitly)."""

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
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
) -> dict[str, Any]:
    """The bounded read that supplies the Precondition token and, for a singleton,
    the wire identity.

    The read names the ``core`` collection explicitly (D30 owner ruling 5), as every
    read and write of a config Mutation does.
    """

    params: dict[str, Any] = {"collection": CORE_COLLECTION}
    if capability.singleton:
        path = capability.singleton_path
    else:
        template = capability.find_path_template
        path = template.replace("{name}", quote(name, safe="")) if template else None
    if path is None:
        raise GatewayError("unsupported_capability", "This resourceType has no exact lookup route")
    return await client.get_json(path, params=params, context=context)


async def _probe_resource(
    client: GatewayClient,
    context: OperationContext,
    capability: ConfigResourceCapability,
    name: str,
) -> dict[str, Any] | None:
    """Whether one Target exists, as its document (``None`` = absent).

    Only the Gateway's own 404 means absent: a permission, transport or schema
    failure propagates, so no failure is ever mistaken for a missing resource.
    """

    try:
        return await _read_resource(client, context, capability, name)
    except GatewayError as error:
        if error.code == "not_found":
            return None
        raise


async def _preconditioned_read(
    client: GatewayClient,
    context: OperationContext,
    capability: ConfigResourceCapability,
    name: str,
    expected_signature: str,
) -> dict[str, Any]:
    """The bounded read that enforces the Precondition token before dispatch (D30 §2).

    A Gateway that reports no signature leaves nothing to compare, and a signature
    that differs means the resource changed after the caller read it: both are
    ``conflict`` with nothing dispatched. The returned document is the pre-state the
    later verification compares against.
    """

    current = await _read_resource(client, context, capability, name)
    signature = resource_signature(current)
    if signature is None:
        raise GatewayError(
            "conflict",
            "the Gateway reports no Resource signature for this resource, so the change "
            "cannot be preconditioned and was not dispatched",
        )
    if signature != expected_signature:
        raise GatewayError(
            "conflict",
            "the resource changed after it was read (expected signature "
            f"{_display(expected_signature)}, the Gateway now reports {_display(signature)}); "
            "nothing was dispatched",
        )
    return current


async def _chunked(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def _no_body() -> AsyncIterator[bytes]:
    """A ``DELETE`` route documents no request body, so none is sent."""

    return
    yield b""  # pragma: no cover - unreachable, and what makes this an async generator


def _pre_state_intact(
    current: dict[str, Any] | None, before: dict[str, Any], fields: dict[str, Any] | None = None,
) -> bool:
    """Whether the Target still shows the state the caller preconditioned on.

    Compared on the Resource signature and, when the operation names fields, on those
    fields too, so an unrelated Gateway-side field cannot turn "nothing happened" into
    an ambiguous outcome. A Target that is gone is the pre-state of nothing.
    """

    if current is None or resource_signature(current) != resource_signature(before):
        return False
    for key in set(fields or ()) & {"config", "enabled", "description"}:
        if current.get(key) != before.get(key):
            return False
    return True


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
