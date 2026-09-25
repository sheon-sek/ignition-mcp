"""D12/D30 Alarm Notification Pipeline cancel on the Native REST plane (Phase 4, 4c).

``alarm_pipeline_cancel`` stops one *run* of one Alarm Notification Pipeline: the run
the ``(path, alarmEventId)`` pair names. D12 is explicit about the two boundaries this
Tool has to hold:

- "Cancelling a notification pipeline does not clear or acknowledge the Alarm Event
  itself." The Tool therefore talks to the pipeline runtime surface and nothing else —
  it never touches an Alarm Event, a Tag or a config resource.
- "Target a specific pipeline path plus Alarm Event ID", and D30 §6 tightens the Target:
  "exact pipeline paths or ``*``, never prefixes". The Target is the caller's own path
  string, matched exactly, so a deployment entry authorizes exactly the pipeline the
  Gateway reported for it.

Where each rule lands:

- **D30 §3 Preflight.** The D08 chain — verified principal, CONTROL scope, deployment
  class, operation allowlist and the Target allowlist — runs inside the guarded executor
  before anything is dispatched, so a Target the deployment does not name answers
  ``permission_denied`` (D30 §7) without a single Gateway call.
- **D30 §2 no Precondition token.** The decision gives this Tool no token, so its
  concurrency rule is the pre-dispatch read below plus the bounded verification. The
  operation is declared ``rejection_is_final``: a 4xx, or a 2xx carrying ``success=false``
  with a problem, is the result, and no read-back may turn it into a success.
- **Attribution.** A cancel is destructive and its post-state is *absence*, which on its
  own proves nothing: another operator can cancel the same run. The Tool therefore
  establishes the pre-state with the same bounded read it verifies with — the run must
  exist at dispatch time or the call is a ``not_found`` that dispatches nothing — and
  only a Gateway claim plus a re-read that no longer reports the run confirms it. An
  ambiguous dispatch is a success only for the D16 Project transaction, which can
  fingerprint the state it staged; here a vanished run is ``outcome_unknown``.
- **Verification (Observed state).** D30 §6: a bounded ``alarm_pipeline_status``
  re-read. The read is the read Tool's own service function, so the Observed state the
  caller gets is exactly what ``alarm_pipeline_status`` would have answered, plus the
  one comparison this Tool makes.
- **D10 bound.** The Tool reads one page. When the Gateway matches more runs than that
  page covers, the state cannot be established, so the call fails with ``limit_exceeded``
  naming what was matched and what the bound is — never a guess, and never a silent
  partial read.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

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
    AlarmPipelineCancelObservedState,
    AlarmPipelineCancelResult,
    AlarmPipelineInstance,
    PageMetadata,
)
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.safety.executor import (
    MutationRequest,
    MutationResult,
    VerificationOutcome,
    execute_mutation,
    mutation_failure,
)
from ignition_rest_mcp.safety.policy import CONTROL_MUTATION, MutationOperation
from ignition_rest_mcp.safety.verification import verdict
from ignition_rest_mcp.services.readonly import alarm_pipeline_status

#: D30 §7/D30 §2: a Phase 4 Mutation answers `permission_denied` for a Target outside
#: its Target allowlist, and an explicit Gateway rejection is this Tool's result.
ALARM_PIPELINE_CANCEL = MutationOperation(
    op_id="alarm_pipeline_cancel",
    mutation_class=CONTROL_MUTATION,
    capability="alarm_pipeline_cancel",
    #: D12: a cancel stops the notification work an Alarm Event is running; it does not
    #: clear or acknowledge the event, but it destroys the run.
    destructive=True,
    target_denial_code="permission_denied",
    rejection_is_final=True,
)

CANCEL_PATH = "/data/alarm-notification/api/v1/pipeline"

#: The documented path form is ``project:<name>:/pipeline:<name>`` and the read Tool's
#: own ceiling for the same string is 512 characters, so one bound covers both.
MAX_PIPELINE_PATH_LENGTH = 512
#: The Alarm Event is named by its UUID; the bound is deliberately looser than 36 so a
#: Gateway that reports a longer identifier is still addressable.
MAX_ALARM_EVENT_ID_LENGTH = 128
#: D10: the Tool reads one page of pipeline runs per call, and the page is explicit in
#: every refusal that depends on it.
STATUS_PAGE_LIMIT = 100


async def alarm_pipeline_cancel(
    client: GatewayClient,
    registry: CapabilityRegistry,
    settings: Settings,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    path: str,
    alarm_event_id: str,
) -> AlarmPipelineCancelResult:
    """Cancel the run one ``(pipeline path, alarm event)`` pair names."""

    path = _bounded(path, "path", MAX_PIPELINE_PATH_LENGTH)
    alarm_event_id = _bounded(alarm_event_id, "alarmEventId", MAX_ALARM_EVENT_ID_LENGTH)
    if "*" in path:
        #: D30 §6: the allowlist holds exact pipeline paths or `*`. A caller-supplied
        #: `*` is neither, so it is refused rather than looked up as a Target.
        raise GatewayError(
            "invalid_argument",
            "path must name one exact pipeline path; '*' is a Target-allowlist "
            "entry, not a pipeline",
        )
    target_id = pipeline_target_id(path)
    read_state: dict[str, Any] = {}

    async def precondition() -> None:
        """The pre-state this call has to be attributed against.

        A destructive cancel that leaves nothing behind cannot be verified from its
        post-state alone, and the run has to exist for the caller's read to be current.
        Both questions are answered by the same bounded read the verification uses.
        """

        instances, page = await _read_runs(client, registry, context, path)
        state = _run_state(instances, page, alarm_event_id)
        read_state["pre_dispatch"] = instances
        if state is None:
            raise GatewayError("limit_exceeded", _unbounded_message(path, page, alarm_event_id))
        if state is False:
            raise GatewayError(
                "not_found",
                f"the pipeline {path} holds no run for alarm event {alarm_event_id}; "
                "nothing was dispatched",
            )

    async def verify(dispatch: WriteDispatchResult) -> VerificationOutcome:
        instances, page = await _read_runs(client, registry, context, path)
        state = _run_state(instances, page, alarm_event_id)
        read_state["observed"] = AlarmPipelineCancelObservedState(
            items=instances, alarmEventReported=state is True,
        )
        return verdict(
            claimed=_is_claimed_cancel(dispatch),
            #: The intended post-state of a cancel is that run gone, and only a read
            #: that covered every match can say so.
            intended=state is False,
            #: The pre-state — established before dispatch — is that run still running.
            pre_state=state is True,
        )

    mutation = await execute_mutation(
        client=client, registry=registry, settings=settings, context=context,
        request=MutationRequest(
            operation=ALARM_PIPELINE_CANCEL, principal=principal, target_id=target_id,
            request_path=CANCEL_PATH, method="DELETE",
            body_chunks=_chunked(_cancel_body(path, alarm_event_id)),
            content_type="application/json",
            dispatch_deadline_seconds=settings.budget_deadline_seconds("FAST"),
            verification_deadline_seconds=settings.budget_deadline_seconds("FAST"),
            verify=verify,
            precondition=precondition,
            rejection=_gateway_rejection,
            audit_fields={"path": path, "alarmEventId": alarm_event_id},
            target_type="alarm-pipeline",
        ),
    )
    failure = _failure(mutation, alarm_event_id, read_state)
    if failure is not None:
        raise failure
    observed: AlarmPipelineCancelObservedState = read_state["observed"]
    return AlarmPipelineCancelResult(
        correlationId=context.correlation_id,
        path=path,
        alarmEventId=alarm_event_id,
        observedState=observed,
    )


def pipeline_target_id(path: str) -> str:
    """The D30 §3 Target of a pipeline cancel: the exact pipeline path.

    D30 §6 is explicit that the allowlist holds exact paths and never prefixes, so the
    Target is the path itself — the identity ``alarm_pipeline_list`` reports and the
    caller hands back.
    """

    return path


# ------------------------------------------------------------------ input


def _bounded(value: str, name: str, limit: int) -> str:
    """One bounded text input (D10), refused with the requested value and the limit.

    Surrounding whitespace is trimmed before anything else, so the path that is read,
    dispatched and audited is one string. A control character is refused rather than
    carried into a log line or an audit row.
    """

    if not isinstance(value, str):
        raise GatewayError("invalid_argument", f"{name} must be a string")
    text = value.strip()
    if not text:
        raise GatewayError("invalid_argument", f"{name} must be non-empty")
    if len(text) > limit:
        raise GatewayError(
            "limit_exceeded",
            f"{name} is {len(text)} characters; the limit is {limit}",
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        raise GatewayError("invalid_argument", f"{name} must not contain control characters")
    return text


def _cancel_body(path: str, alarm_event_id: str) -> bytes:
    """The documented request body: the pipeline path and the alarm event id."""

    return json.dumps(
        {"path": path, "alarmEventId": alarm_event_id}, separators=(",", ":"),
    ).encode("utf-8")


async def _chunked(body: bytes) -> AsyncIterator[bytes]:
    yield body


# ------------------------------------------------------------------ the read


async def _read_runs(
    client: GatewayClient, registry: CapabilityRegistry, context: OperationContext, path: str,
) -> tuple[list[AlarmPipelineInstance], PageMetadata]:
    """One bounded page of a pipeline's runs, through the read Tool's own service.

    D30 §6 names ``alarm_pipeline_status`` as the verification source, so the read is
    that Tool's implementation with this Tool's page: one page at offset 0. A path the
    Gateway does not serve answers ``not_found``, which for a pipeline path means
    exactly one thing — it holds no run — so it is the empty read rather than an error.
    """

    try:
        result = await alarm_pipeline_status(
            client, registry, context, path=path, limit=STATUS_PAGE_LIMIT, offset=0,
        )
    except GatewayError as error:
        if error.code != "not_found":
            raise
        return [], PageMetadata(total=0, matching=0, limit=STATUS_PAGE_LIMIT, offset=0)
    return list(result.items), result.page


def _run_state(
    instances: list[AlarmPipelineInstance], page: PageMetadata, alarm_event_id: str,
) -> bool | None:
    """Whether this bounded read reports a run for the alarm event.

    ``True``: the read named one. ``False``: the read covered every run the Gateway
    matched and none of them is this one, so the absence is established. ``None``: the
    matching set continues past the page, so the bound can establish neither — the
    caller gets an explicit failure rather than a guess.
    """

    if any(instance.alarmEventId == alarm_event_id for instance in instances):
        return True
    if page.matching - page.offset <= len(instances):
        return False
    return None


def _unbounded_message(path: str, page: PageMetadata, alarm_event_id: str) -> str:
    """The D10 refusal, naming what was matched and what the bound is."""

    return (
        f"the pipeline {path} matches {page.matching} runs and this Tool reads one page of "
        f"{STATUS_PAGE_LIMIT}; alarm event {alarm_event_id} is not among the runs it read, "
        "so its state cannot be established and nothing was dispatched"
    )


# ------------------------------------------------------------------ the dispatch


def _reported_body(dispatch: WriteDispatchResult) -> Any:
    """The Gateway's response body, with the transport's non-object carrier unwrapped.

    ``GatewayClient`` keeps a decoded body that is not a JSON object under ``value``, so
    a document this Tool cannot read is never mistaken for a field of one.
    """

    body = dispatch.body
    if isinstance(body, dict) and set(body) == {"value"}:
        return body["value"]
    return body


def _is_claimed_cancel(dispatch: WriteDispatchResult) -> bool:
    """Whether the Gateway claimed the cancel succeeded.

    The documented 200 answers ``{"success": bool, "alarmEventId": ...}``. Only an
    explicit ``success: true`` is a claim: a body this Tool cannot read — no field, the
    wrong type, an empty body — is not one, and a body that refuses is handled as a
    rejection before the claim is ever asked for.
    """

    body = _reported_body(dispatch)
    return (
        dispatch.outcome is DispatchOutcome.RESPONDED
        and dispatch.status is not None
        and 200 <= dispatch.status < 300
        and isinstance(body, dict)
        and body.get("success") is True
    )


def _gateway_rejection(dispatch: WriteDispatchResult) -> GatewayError | None:
    """The Gateway's own refusal of one pipeline cancel (D30 §2/§7).

    Ignition reports a refused change inside the 2xx the route documents as
    ``success=false`` with a problem (the shape the resource PUT answers, and the one
    this fixture models for this route). That is a known rejection, not a claimed
    success, and D30 §2 makes it final: the run the caller addressed was not in the
    state their read described, so the D30 §7 code for a moved state is ``conflict``.
    """

    body = _reported_body(dispatch)
    if not isinstance(body, dict) or body.get("success") is not False:
        return None
    return GatewayError(
        "conflict",
        "Ignition refused the pipeline cancel: the run is no longer in the state the "
        "caller read, or the pipeline does not hold it; nothing was replayed",
    )


def _failure(
    result: MutationResult, alarm_event_id: str, read_state: dict[str, Any],
) -> GatewayError | None:
    """The error a caller must see when the cancel did not cleanly apply.

    The bounded re-read is this Tool's evidence, so what it observed is named when the
    call produced one: a caller reconciling an unresolved cancel needs to know whether
    the run was still being reported at that moment.
    """

    failure = mutation_failure(result)
    if failure is None:
        return None
    observed: AlarmPipelineCancelObservedState | None = read_state.get("observed")
    if observed is None or not observed.alarmEventReported:
        return failure
    return GatewayError(
        failure.code,
        f"{failure.message} (the bounded re-read of the same path still reports a run for "
        f"alarm event {alarm_event_id})",
        failure.status_code,
    )
