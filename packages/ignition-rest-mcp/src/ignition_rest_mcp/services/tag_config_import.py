"""D11/D30 Tag configuration import on the Native REST plane (Phase 4, milestone 4c).

``tag_config_import`` creates the Tags one caller-supplied JSON Tag export declares
under a provider-qualified path, and does nothing else — D11's Phase 4 amendment:
"it creates Tags only".

Where each rule lands:

- **D30 §4 fixed knob.** ``collisionPolicy=Abort`` is always sent and the caller cannot
  choose it. An import therefore only ever creates Tags, and a destination that already
  holds one of the document's Tags refuses the whole import instead of overwriting it.
- **D30 §3 Preflight and Target.** The D08 chain — verified principal, authorization
  scope, deployment class, operation allowlist, the operation's Target-class rule and
  the Target allowlist — runs inside the guarded executor before anything is dispatched.
  The Target is the ``[provider]path`` the import creates Tags under, and D30 §1/D08
  match a Target allowlist entry as a provider-qualified path prefix at a segment
  boundary: `[default]CI/Imports` authorizes `[default]CI/Imports` and everything below
  it, never `[default]CI/Imports2`, and `*` must be written explicitly. A Target the
  deployment does not name answers ``permission_denied`` (D30 §7) with nothing sent.
- **D30 §1 reserved provider.** The Runtime Target Policy lives in the reserved
  ``IgnitionMCPPolicy`` provider (#6), and D30 §1 requires that the Runtime plane cannot
  write it. The refusal is a product rule: any Tag Mutation addressed to that provider is
  refused by provider *before* the Target allowlist, with ``permission_denied``, so
  neither `*` nor an entry naming the provider can change or extend the policy document.
- **D30 §2 no Precondition token.** A Tag import carries no token: ``Abort`` *is* its
  concurrency rule. It is checked against the Gateway before dispatch (D11 collision
  policy), so a destination that already holds a declared Tag is a ``conflict`` that
  dispatches nothing. An explicit Gateway rejection is final for this Tool
  (``rejection_is_final``), so no refusal can be reconciled into a success, and an
  ambiguous dispatch — possibly sent with no response, or a 5xx — is
  ``outcome_unknown`` or ``not_applied``: the bounded re-export can show the intended
  Tags without proving *this* call created them, so it never confirms one on its own.
- **D17/D30 §6 artifact input.** The document is a READY ``tag_config_export``
  artifact visible to the same Mutation principal; anything else answers ``not_found``
  (no existence oracle), a different kind is ``invalid_argument``, and the body is read
  under an explicit byte cap and parsed before any dispatch, so an oversize or
  unparseable document fails with nothing sent.
- **Verification (Observed state).** A bounded re-export of the same provider and path
  is compared with the Tag paths the document declares; the comparison is returned as
  Observed state. A Gateway claim is confirmed only when it shows every declared Tag —
  a claim that does not is ``recovery_required``, never a success.

The document shape is the Gateway's own JSON Tag export: ``{"name": ..., "tagType":
..., "tags": [...]}``, where a node's non-empty name is a path segment and ``tags``
its children (recorded in ``tests/fixtures/recorded/gateway-8.3/phase4/tag-export.json``,
and live-proven by the Runtime plane importing a ``{"tags": [...]}`` document).
"""

from __future__ import annotations

import json
from functools import partial
from typing import Any, AsyncIterator, Callable, TypeGuard

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, validate_artifact_id
from ignition_rest_mcp.artifacts.model import Artifact
from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import (
    DispatchOutcome,
    GatewayClient,
    WriteDispatchResult,
)
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import (
    ArtifactRefModel,
    TagConfigImportResult,
    TagImportObservedState,
)
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.safety.executor import (
    MutationPreflight,
    MutationRequest,
    MutationResult,
    VerificationOutcome,
    execute_mutation,
    mutation_failure,
    preflight_mutation,
)
from ignition_rest_mcp.safety.policy import (
    CONFIG_MUTATION,
    TARGET_MATCH_PROVIDER_PREFIX,
    MutationOperation,
    PolicyDecision,
)
from ignition_rest_mcp.safety.reserved_tag_providers import refuse_reserved_tag_provider_decision
from ignition_rest_mcp.safety.verification import verdict
from ignition_rest_mcp.services.artifacts import artifact_visible
from ignition_rest_mcp.services.config_resources import bounded_text

#: D30 §4: the caller can never choose the collision policy.
COLLISION_POLICY = "Abort"

#: D30 §7/D30 §2: a Phase 4 Mutation answers `permission_denied` for a Target outside
#: its Target allowlist, and an explicit Gateway rejection is this Tool's result.
TAG_CONFIG_IMPORT = MutationOperation(
    op_id="tag_config_import",
    mutation_class=CONFIG_MUTATION,
    capability="tag_config_import",
    #: D11's Phase 4 amendment: the import creates Tags only, so it destroys nothing.
    destructive=False,
    target_denial_code="permission_denied",
    rejection_is_final=True,
    #: D30 §1/D08: the Target is a provider-qualified Tag path, so an entry authorizes
    #: the subtree below it at a segment boundary.
    target_match=TARGET_MATCH_PROVIDER_PREFIX,
)

IMPORT_PATH = "/data/api/v1/tags/import"
EXPORT_PATH = "/data/api/v1/tags/export"

#: The only artifact kind this Tool consumes (D17/D30 §6): the JSON Tag export
#: `tag_config_export` publishes, which is also the only ingress that can produce one
#: (the data-plane upload route accepts Project archives only).
IMPORT_ARTIFACT_KIND = "tag_config_export"

MAX_PROVIDER_LENGTH = 256
MAX_TAG_PATH_LENGTH = 1024
#: D10: the document is read and parsed under an explicit bound — the same JSON
#: ceiling `tag_config_export` validates its own output against — and an oversize
#: document fails instead of being truncated or partially imported.
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
#: D10: the declared Tag set is bounded before anything is dispatched, which is also
#: what bounds the Observed state the result carries. Both ceilings fail explicitly.
MAX_IMPORTED_TAGS = 500
MAX_IMPORTED_PATH_BYTES = 32_768
MAX_SEGMENT_LENGTH = 512
#: How many unresolved Tag paths an error message names (D06: the envelope carries a
#: bounded message, and the caller can re-read the rest).
MISSING_DISPLAY_LIMIT = 5

#: D30 §6: Tag CONFIG Mutations reach UDT definitions only through an explicit
#: ``_types_`` Target, so a document that declares UDT definitions may only be
#: imported into that subtree.
UDT_SEGMENT = "_types_"


async def tag_config_import(
    client: GatewayClient,
    registry: CapabilityRegistry,
    store: LocalArtifactStore,
    settings: Settings,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    artifact_id: str,
    provider: str,
    path: str,
) -> TagConfigImportResult:
    """Import one JSON Tag export into a provider-qualified path."""

    provider = _provider(provider)
    path = _import_path(path)
    validate_artifact_id(artifact_id)
    target_id = tag_target_id(provider, path)
    #: D30 §1: the reserved policy provider is refused by provider, before the Target
    #: allowlist, so neither `*` nor an entry naming it can reach the Runtime Target
    #: Policy. Built once and handed to both the early chain and the executor's.
    target_policy: Callable[[], PolicyDecision] = partial(
        refuse_reserved_tag_provider_decision, provider,
    )
    #: D30 §3: the whole chain runs before the artifact is read, so a Target the
    #: deployment does not name is refused without consuming the caller's artifact.
    #: The guarded executor runs the same chain again around the dispatch, which stays
    #: authoritative; this pass only refuses earlier.
    await preflight_mutation(
        registry=registry, settings=settings, context=context,
        preflight=MutationPreflight(
            operation=TAG_CONFIG_IMPORT, principal=principal, target_id=target_id,
            target_type="tag-provider", target_policy=target_policy,
            audit_fields={"provider": provider, "path": path},
        ),
    )
    artifact = await _import_artifact(store, principal, artifact_id)
    payload = await _read_bounded(store, artifact)
    declared = _DeclaredTags(_decode_document(payload), path)

    read_state: dict[str, Any] = {"declared": declared.paths}

    async def precondition() -> None:
        #: D11 collision policy: a Tag the document declares already exists at the
        #: destination, so the import has nothing to create. Nothing was dispatched and
        #: the caller keeps its read.
        present = declared.already_present(await _re_export(client, context, provider, path))
        read_state["observed"] = present
        if present:
            raise GatewayError(
                "conflict",
                "the import destination already holds importable Tags "
                f"({len(present)} of {len(declared.paths)}), and the always-sent "
                "'Abort' collision policy refuses the whole import; nothing was dispatched",
            )

    async def verify(dispatch: WriteDispatchResult) -> VerificationOutcome:
        present, missing = declared.compare(await _re_export(client, context, provider, path))
        read_state["observed"] = present
        read_state["missing"] = missing
        return verdict(
            claimed=_is_claimed_success(dispatch),
            #: Every declared Tag has to be visible for the intended state to be read:
            #: an import that created only some of them is not the state this call asked
            #: for, and is never reported as a success.
            intended=not missing,
            #: The pre-state of an import is the destination holding none of them.
            pre_state=not present,
        )

    mutation = await execute_mutation(
        client=client, registry=registry, settings=settings, context=context,
        request=MutationRequest(
            operation=TAG_CONFIG_IMPORT, principal=principal, target_id=target_id,
            request_path=IMPORT_PATH,
            method="POST",
            params=_import_params(provider, path),
            body_chunks=_chunked(payload),
            content_type="application/octet-stream",
            dispatch_deadline_seconds=settings.budget_deadline_seconds("ARTIFACT"),
            verification_deadline_seconds=settings.budget_deadline_seconds("ARTIFACT"),
            verify=verify,
            precondition=precondition,
            rejection=_gateway_rejection,
            target_policy=target_policy,
            audit_fields={"provider": provider, "path": path},
            target_type="tag-provider",
        ),
    )
    failure = _failure(mutation, read_state)
    if failure is not None:
        raise failure
    present = read_state.get("observed") or []
    return TagConfigImportResult(
        correlationId=context.correlation_id,
        provider=provider,
        path=path,
        artifact=ArtifactRefModel(**artifact.to_ref()),
        observedState=TagImportObservedState(
            present=list(present), missing=list(read_state.get("missing") or []),
        ),
    )


def tag_target_id(provider: str, path: str) -> str:
    """The D30 §3 Target of a Tag import: the provider-qualified destination path.

    This is the Ignition Tag-path form (``[default]CI/Imports``), so the Target a
    deployment allowlists is exactly the provider and path the import creates under.
    """

    return f"[{provider}]{path}"


# ------------------------------------------------------------------ input


def _provider(value: str) -> str:
    """The Tag provider an import addresses, validated before it becomes a Target.

    A provider name that carried ``[`` or ``]`` would make the Target identity
    ambiguous, so it is refused rather than encoded.
    """

    provider = bounded_text(value, "provider", MAX_PROVIDER_LENGTH, allow_empty=False)
    if any(character in provider for character in "[]"):
        raise GatewayError("invalid_argument", "provider must not contain '[' or ']'")
    return provider


def _import_path(value: str) -> str:
    """The destination path, normalized to the one form the Target identity names.

    Surrounding whitespace is trimmed, and a leading, trailing or doubled separator, or
    a ``.``/``..`` segment, is refused rather than silently normalized: either would let
    two different inputs address the same Gateway path while reading as different
    Targets.
    """

    path = bounded_text(value, "path", MAX_TAG_PATH_LENGTH, allow_empty=True)
    if not path:
        return ""
    segments = path.split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise GatewayError(
            "invalid_argument",
            "path must be a '/'-separated Tag path with no empty, '.' or '..' segment",
        )
    return path


async def _import_artifact(
    store: LocalArtifactStore, principal: VerifiedPrincipal, artifact_id: str,
) -> Artifact:
    """The READY Tag export the caller may consume, read under its explicit bound.

    Visibility is checked before the kind, so an artifact the caller cannot see answers
    exactly as a missing one does (no existence oracle), and a visible artifact of
    another kind is an input error.
    """

    artifact = await store.stat(artifact_id)
    if not artifact_visible(principal, artifact.owner):
        raise GatewayError("not_found", "Artifact not found")
    if artifact.kind != IMPORT_ARTIFACT_KIND:
        raise GatewayError(
            "invalid_argument",
            f"artifactId must name a {IMPORT_ARTIFACT_KIND} artifact; "
            f"{artifact.artifact_id} is a {artifact.kind}",
        )
    return artifact


def _decode_document(payload: bytes) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise GatewayError(
            "invalid_argument", "the artifact is not a JSON Tag export document",
        ) from error
    if not isinstance(document, dict):
        raise GatewayError("invalid_argument", "the artifact is not a JSON Tag export document")
    return document


async def _read_bounded(store: LocalArtifactStore, artifact: Artifact) -> bytes:
    """The artifact's bytes, read under the import ceiling.

    A READY artifact is immutable, so the bytes parsed for the declared Tag set are
    exactly the bytes dispatched: nothing re-reads them between the check and the send.
    """

    if artifact.size_bytes > MAX_DOCUMENT_BYTES:  # cheap refusal before opening anything
        raise GatewayError(
            "limit_exceeded",
            f"the artifact holds {artifact.size_bytes} bytes; the Tag import ceiling is "
            f"{MAX_DOCUMENT_BYTES} bytes",
        )
    reader = await store.open_read(artifact.artifact_id)
    payload = bytearray()
    try:
        while True:
            chunk = await reader.read_chunk()
            if chunk is None:
                break
            if len(payload) + len(chunk) > MAX_DOCUMENT_BYTES:
                raise GatewayError(
                    "limit_exceeded",
                    f"the artifact requires more than {MAX_DOCUMENT_BYTES} bytes; it was not "
                    "imported",
                )
            payload.extend(chunk)
    finally:
        await reader.close()
    return bytes(payload)


class _DeclaredTags:
    """The Tag paths one import document declares, as a bounded prefix trie.

    The trie is what keeps the verification bounded: the re-export walk only descends
    into observed subtrees that can still hold a declared Tag, so the cost is the
    document's own structure plus the children of the paths it names, never the whole
    provider.

    The document is read the way the Gateway imports it: its own nodes keep their
    declared paths, so a root that names a Tag (the export of a sub-path, e.g.
    ``{"name": "source", "tagType": "Folder", ...}``) is itself imported under ``base``
    and its ``tags`` are its children. A root with no name — a provider-root export —
    contributes nothing but its children, which is the shape the Runtime plane imports
    live.
    """

    def __init__(self, document: dict[str, Any], base: str) -> None:
        #: The provider-relative prefix the declared Tags land under.
        self.base = base
        self.paths: list[str] = []
        self._terminals: set[str] = set()
        self._trie: dict[str, Any] = {}
        self._collect(document)
        if not self.paths:
            raise GatewayError(
                "invalid_argument",
                "the artifact declares no Tags: a Tag import document must name at least "
                "one Tag or folder",
            )
        if len(self.paths) > MAX_IMPORTED_TAGS:
            raise GatewayError(
                "limit_exceeded",
                f"the artifact declares {len(self.paths)} Tags; the Tag import ceiling is "
                f"{MAX_IMPORTED_TAGS}",
            )
        if sum(len(path.encode("utf-8")) for path in self.paths) > MAX_IMPORTED_PATH_BYTES:
            raise GatewayError(
                "limit_exceeded",
                f"the artifact's declared Tag paths exceed the "
                f"{MAX_IMPORTED_PATH_BYTES}-byte ceiling",
            )
        if any(declared.split("/", 1)[0] == UDT_SEGMENT for declared in self.paths) and (
            base.split("/", 1)[0] != UDT_SEGMENT
        ):
            # D30 §6: UDT definitions are reachable only through an explicit `_types_`
            # Target, so a document that carries them may only be imported into it.
            raise GatewayError(
                "invalid_argument",
                "the artifact declares UDT definitions: import it into the provider's "
                f"'{UDT_SEGMENT}' path, the explicit UDT-definition target",
            )

    def _collect(self, document: dict[str, Any]) -> None:
        name = document.get("name")
        if isinstance(name, str) and name:
            _check_segment(name, "name")
            # The root names a Tag, so the import creates it under ``base`` and its
            # children belong to it. Without this the declared set would omit the very
            # node the caller's export was rooted at.
            current = f"{self.base}/{name}" if self.base else name
            self._add(current)
            children = document.get("tags")
            if isinstance(children, list):
                self._walk(children, current)
            return
        children = document.get("tags")
        if isinstance(children, list):
            self._walk(children, self.base)

    def _walk(self, nodes: list[Any], prefix: str) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = node.get("name")
            if not isinstance(name, str) or not name:
                continue
            _check_segment(name, "name")
            current = f"{prefix}/{name}" if prefix else name
            self._add(current)
            children = node.get("tags")
            if isinstance(children, list):
                self._walk(children, current)

    def _add(self, path: str) -> None:
        if len(path.encode("utf-8")) > MAX_TAG_PATH_LENGTH:
            raise GatewayError(
                "limit_exceeded",
                f"the artifact declares a Tag path longer than {MAX_TAG_PATH_LENGTH} bytes",
            )
        if path in self._terminals:
            return
        self._terminals.add(path)
        self.paths.append(path)
        node = self._trie
        for segment in path.split("/"):
            node = node.setdefault(segment, {})

    def compare(self, observed: dict[str, Any] | None) -> tuple[list[str], list[str]]:
        """(present, missing) for the declared Tags, in the document's own order."""

        found = self._walk_observed(observed)
        present = [path for path in self.paths if path in found]
        missing = [path for path in self.paths if path not in found]
        return present, missing

    def already_present(self, observed: dict[str, Any] | None) -> list[str]:
        """The declared Tags the Gateway already serves (the collision check's read)."""

        found = self._walk_observed(observed)
        return [path for path in self.paths if path in found]

    def _walk_observed(self, document: dict[str, Any] | None) -> set[str]:
        """The declared Tags one bounded re-export shows.

        The re-export root is either the provider root (which names nothing) or the
        node at the import path (which names its own segment). Its prefix is therefore
        the import path when the root names something and nothing when it does not, so
        the walk stays in the provider-relative namespace the declared Tags are in —
        whichever of the two the Gateway returns. An absent path (the Gateway served
        nothing) shows no Tags at all.
        """

        if not isinstance(document, dict):
            return set()
        root_name = document.get("name")
        base = self.base if isinstance(root_name, str) and root_name else ""
        found: set[str] = set()
        stack: list[tuple[dict[str, Any], str]] = [(document, base)]
        while stack:
            node, prefix = stack.pop()
            children = node.get("tags")
            if not isinstance(children, list):
                continue
            for child in children:
                if not isinstance(child, dict):
                    continue
                name = child.get("name")
                if not isinstance(name, str) or not name:
                    continue
                path = f"{prefix}/{name}" if prefix else name
                if path in self._terminals:
                    found.add(path)
                if self._subtree(path) is not None and isinstance(child.get("tags"), list):
                    stack.append((child, path))
        return found

    def _subtree(self, path: str) -> dict[str, Any] | None:
        node: dict[str, Any] = self._trie
        for segment in path.split("/"):
            child = node.get(segment)
            if not isinstance(child, dict):
                return None
            node = child
        return node


def _check_segment(name: str, label: str) -> None:
    if "/" in name or len(name) > MAX_SEGMENT_LENGTH:
        raise GatewayError(
            "invalid_argument",
            f"the artifact declares a Tag whose {label} is not a single path segment",
        )


# ------------------------------------------------------------------ gateway


def _import_params(provider: str, path: str) -> dict[str, Any]:
    """The documented import route's query, with D30 §4's fixed collision policy."""

    params: dict[str, Any] = {"provider": provider, "type": "json", "collisionPolicy": COLLISION_POLICY}
    if path:
        params["path"] = path
    return params


async def _re_export(
    client: GatewayClient, context: OperationContext, provider: str, path: str,
) -> dict[str, Any] | None:
    """The bounded re-export of the same provider and path the verification reads.

    ``recursive`` and ``includeUdts`` are both on so nothing a declared Tag could live
    under is hidden from the comparison; the body is capped explicitly, so a re-export
    too large to verify fails rather than being partly read. A Gateway that serves no
    such path answers 404, which for a Tag path means exactly one thing — nothing is
    there yet — so it is the empty export, and every other failure propagates.
    """

    params: dict[str, Any] = {"provider": provider, "type": "json", "recursive": True, "includeUdts": True}
    if path:
        params["path"] = path
    try:
        body = await client.get_bytes(
            EXPORT_PATH, params=params, context=context, limit_bytes=MAX_DOCUMENT_BYTES,
        )
    except GatewayError as error:
        if error.code == "not_found":
            return None
        raise
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise GatewayError(
            "schema_mismatch", "Gateway returned a Tag export body that is not valid JSON",
        ) from error
    if not isinstance(document, dict):
        raise GatewayError(
            "schema_mismatch", "Gateway returned a Tag export body this server cannot read",
        )
    return document


async def _chunked(body: bytes) -> AsyncIterator[bytes]:
    yield body


def _reported_body(dispatch: WriteDispatchResult) -> Any:
    """The Gateway's response body, with the transport's non-object carrier unwrapped.

    ``GatewayClient`` keeps a decoded body that is not a JSON object under ``value``
    (so a document the OpenAPI describes as an array is not indistinguishable from "no
    body"), which is exactly the shape the committed import route documents: a list of
    non-Good QualityCodes.
    """

    body = dispatch.body
    if isinstance(body, dict) and set(body) == {"value"}:
        return body["value"]
    return body


def _gateway_rejection(dispatch: WriteDispatchResult) -> GatewayError | None:
    """The Gateway's own refusal of one Tag import (D30 §2/§7).

    A refused import is reported inside the 2xx the route documents: the recorded
    8.3.8/8.3.9 answer is ``{"failureCount": n, "failures": [...], "successCount": 0}``
    (``tests/fixtures/recorded/gateway-8.3/phase4/tag-import-abort.json``), and the
    committed OpenAPI documents a list of non-Good QualityCodes instead. Either shape
    is read, and the Gateway's own text never reaches the caller.

    A *partial* report — some Tags imported, some not — is deliberately not read as a
    refusal: the Gateway did not refuse the call, the final state is what the bounded
    re-export has to establish, and an import that created only some of the document is
    ``recovery_required`` rather than a success.
    """

    body = _reported_body(dispatch)
    failures = _failure_count(body)
    if failures is None or failures == 0:
        return None
    if _reported_successes(body):
        return None
    if _is_collision(body):
        return GatewayError(
            "conflict",
            "Ignition refused the Tag import: the destination already holds a Tag the "
            "document declares, and the always-sent 'Abort' collision policy refuses the "
            "whole import; nothing was replayed",
        )
    return GatewayError(
        "upstream_error",
        f"Ignition reported that the Tag import failed ({failures} Tags); nothing was "
        "replayed",
    )


def _is_count(value: Any) -> TypeGuard[int]:
    """Whether one reported count is a number this server can read.

    A count that is negative, not a number at all, or a boolean is not a count: the
    response is uninterpretable, and an uninterpretable response is never a claim
    (D30 §2).
    """

    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _failure_count(body: Any) -> int | None:
    """How many failures a 2xx import response reports, or ``None`` if it reports none.

    ``None`` is also the answer for a response this server cannot interpret: a count
    that is negative or not a number, a ``failures`` value that is not a list, or a
    count that disagrees with the failure list it carries. An uninterpretable body is
    not a claim of success either (see :func:`_is_claimed_success`), so the call is
    never reported as one.
    """

    if isinstance(body, list):
        return len(body)
    if not isinstance(body, dict):
        return None
    count = body.get("failureCount")
    failures = body.get("failures")
    successes = body.get("successCount")
    if not _is_count(count) or (successes is not None and not _is_count(successes)):
        return None
    if failures is not None:
        if not isinstance(failures, list) or count != len(failures):
            return None
    return count


def _reported_successes(body: Any) -> int:
    """How many successes a 2xx response reports, 0 when it reports none readably."""

    count = body.get("successCount") if isinstance(body, dict) else None
    return count if _is_count(count) else 0


def _is_collision(body: Any) -> bool:
    """Whether the reported failures are the ``Abort`` collision refusal.

    The recorded collision refusal carries ``qualitySubCode`` 527 and names the
    collision policy; anything else is a Gateway-side failure.
    """

    entries = body if isinstance(body, list) else (body.get("failures") if isinstance(body, dict) else None)
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("qualitySubCode") == 527 or entry.get("userCode") == 527:
            return True
        message = entry.get("diagnosticMessage")
        if isinstance(message, str) and "already exists" in message.lower():
            return True
    return False


def _is_claimed_success(dispatch: WriteDispatchResult) -> bool:
    """Whether the Gateway claimed the import succeeded.

    Only an explicit zero-failure report is a claim: the summary object with
    ``failureCount: 0`` and no failures, or the empty QualityCode list the OpenAPI
    documents. An uninterpretable 2xx body is not a claim — the Tool cannot read a
    success out of a response it does not understand — and a partial report is not one
    either.
    """

    return (
        dispatch.outcome is DispatchOutcome.RESPONDED
        and dispatch.status is not None
        and 200 <= dispatch.status < 300
        and _failure_count(_reported_body(dispatch)) == 0
    )


def _failure(result: MutationResult, read_state: dict[str, Any]) -> GatewayError | None:
    """The error a caller must see when the import did not cleanly apply.

    The bounded re-export is this Tool's evidence, so what it observed is named when
    the call produced one: a caller that has to reconcile an unresolved import needs to
    know which declared Tags the Gateway was serving at that moment.
    """

    failure = mutation_failure(result)
    if failure is None:
        return None
    missing = read_state.get("missing")
    if not missing:
        return failure
    declared = read_state.get("declared") or []
    shown = ", ".join(str(path) for path in missing[:MISSING_DISPLAY_LIMIT])
    return GatewayError(
        failure.code,
        f"{failure.message} (the bounded re-export did not show {len(missing)} of "
        f"{len(declared)} declared Tags: {shown})",
        failure.status_code,
    )
