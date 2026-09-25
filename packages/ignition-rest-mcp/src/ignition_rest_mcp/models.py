"""Typed public results for the external Native REST capability plane."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GatewayInfoResult(StrictModel):
    correlationId: str
    name: str
    edition: str
    ignitionVersion: str
    redundancyRole: str
    deploymentMode: str
    timeZoneId: str
    jvmVersion: str


class StorageDiagnostics(StrictModel):
    """Additive low-cost self-diagnostics for the local data plane."""

    dataDirectoryConfigured: bool
    stateHealthy: bool
    auditHealthy: bool
    artifactsReady: int = Field(ge=0)
    projectWriterEnabled: bool
    singleWriterLimitation: str


class GatewayDiagnoseResult(StrictModel):
    correlationId: str
    gatewayReachable: bool
    authenticationOk: bool
    registryState: str
    registryGeneration: int = Field(ge=0)
    openapiSha256: str | None
    gatewayVersion: str | None
    moduleCount: int = Field(ge=0)
    message: str
    storage: StorageDiagnostics | None = None


class PageMetadata(StrictModel):
    total: int = Field(ge=0)
    matching: int = Field(ge=0)
    limit: int = Field(ge=0, le=500)
    offset: int = Field(ge=0)


class ProjectSummary(StrictModel):
    name: str
    description: str
    title: str
    enabled: bool
    parent: str
    inheritable: bool
    invalidParent: bool
    mutable: bool
    defaultDb: str
    tagProvider: str
    userSource: str
    identityProvider: str


class ProjectListResult(StrictModel):
    correlationId: str
    items: list[ProjectSummary] = Field(max_length=500)
    page: PageMetadata


class ConfigResourceTypeSummary(StrictModel):
    resourceType: str
    module: str
    typeId: str
    singleton: bool
    supportsNames: bool
    supportsList: bool
    supportsGet: bool


class ConfigResourceSearchResult(StrictModel):
    correlationId: str
    items: list[ConfigResourceTypeSummary] = Field(max_length=500)
    page: PageMetadata


class ConfigResourceDescribeResult(StrictModel):
    correlationId: str
    resourceType: str
    description: dict[str, Any]


class ConfigResourceName(StrictModel):
    name: str
    enabled: bool


class ConfigResourceNamesResult(StrictModel):
    correlationId: str
    resourceType: str
    items: list[ConfigResourceName] = Field(max_length=500)
    page: PageMetadata


class ConfigResourceListResult(StrictModel):
    correlationId: str
    resourceType: str
    items: list[dict[str, Any]] = Field(max_length=500)
    page: PageMetadata


class ConfigResourceGetResult(StrictModel):
    correlationId: str
    resourceType: str
    resource: dict[str, Any]
    #: D30 Resource signature: the explicit Precondition token a caller passes
    #: back as ``expectedSignature``. ``None`` when the Gateway reports none.
    signature: str | None


class ConfigResourceUpdateResult(StrictModel):
    """The change is verified by re-reading the resource; the read-back is
    reported as Observed state and the resulting Resource signature is the token
    for the caller's next change."""

    correlationId: str
    resourceType: str
    name: str
    collection: str
    signature: str | None
    observedState: dict[str, Any]


class ConfigResourceCreateResult(StrictModel):
    """A create takes no Precondition token, so the resource it published is
    what the bounded re-read reports, as Observed state, plus the Resource signature
    the caller needs for the next change to it."""

    correlationId: str
    resourceType: str
    name: str
    collection: str
    signature: str | None
    observedState: dict[str, Any]


class ConfigResourceDeleteResult(StrictModel):
    """A delete is verified by the Target's absence. That absence is the whole
    Observed state a delete leaves, so the read-back reports exactly it."""

    correlationId: str
    resourceType: str
    name: str
    collection: str
    #: False on success: the bounded read-back established that no resource exists at
    #: the Target. It is data, not a claim of durable absence.
    present: bool


class ConfigResourceRenameResult(StrictModel):
    """The rename is verified by the old name being vacant and the new name
    holding the resource, whose Resource signature is the token for the next change."""

    correlationId: str
    resourceType: str
    #: The name the resource carries after the rename.
    name: str
    previousName: str
    collection: str
    signature: str | None
    observedState: dict[str, Any]


class ProjectImportResult(StrictModel):
    """The terminal transaction state of a Project import, as data.

    Only a satisfied outcome is a result: ``COMMITTED`` (the import landed and the
    post-import export C equals the candidate) or ``NO_CHANGE`` (the candidate was
    semantically equal to the baseline, so nothing was backed up or imported). Every
    other terminal state is a Tool error carrying the error code the state maps to,
    and its message names the state and the ``transactionId`` reported here.
    """

    correlationId: str
    projectName: str
    transactionId: str
    state: str = Field(pattern="^(COMMITTED|NO_CHANGE)$")
    #: Baseline A: the Project's content fingerprint at the start of the transaction.
    baselineFingerprint: str = Field(pattern="^pcf1:[0-9a-f]{64}$")
    #: Candidate B: the fingerprint of the archive this call staged and dispatched.
    candidateFingerprint: str = Field(pattern="^pcf1:[0-9a-f]{64}$")
    #: Re-export C after the import; equal to the candidate on a commit, and absent only
    #: on a no-op, where nothing was imported.
    resultFingerprint: str | None = Field(default=None, pattern="^pcf1:[0-9a-f]{64}$")
    #: True when this call dispatched an import request: the request left the server and
    #: the Gateway may have applied it — whether the response confirmed the import or an
    #: ambiguous dispatch was recovered as a success. False only when nothing was sent:
    #: ``NO_CHANGE``, or a refusal or non-attempt that ended the transaction before any
    #: byte left the process. It is never false when the Project may hold the candidate.
    importDispatched: bool
    #: True when the designer-session policy is ``warn`` and a Designer session was open.
    designerWarning: bool


class PerspectiveViewListResult(StrictModel):
    """The Local Views of one Project, one bounded page per call."""

    correlationId: str
    projectName: str = Field(min_length=1, max_length=256)
    #: Logical resource paths, each one acceptable as the `path` of a View Tool.
    items: list[str] = Field(max_length=500)
    page: PageMetadata


class PerspectiveViewGetResult(StrictModel):
    """One View document plus the Project fingerprint of the export it came from."""

    correlationId: str
    projectName: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=512)
    view: dict[str, Any]
    fingerprint: str = Field(pattern="^pcf1:[0-9a-f]{64}$")


class PerspectiveViewValidateResult(StrictModel):
    """Offline validation: no Gateway call, and no claim that an import accepts it."""

    correlationId: str
    valid: bool
    #: Serialized document size, measured against the byte ceiling.
    bytes: int = Field(ge=0)
    #: Container nesting levels, measured against the depth ceiling.
    depth: int = Field(ge=0)


class PerspectivePageConfigGetResult(StrictModel):
    """The Project's Page configuration document and the Project fingerprint."""

    correlationId: str
    projectName: str = Field(min_length=1, max_length=256)
    config: dict[str, Any]
    fingerprint: str = Field(pattern="^pcf1:[0-9a-f]{64}$")


class PerspectiveSessionPropsGetResult(StrictModel):
    """The Project's Session properties document and the Project fingerprint."""

    correlationId: str
    projectName: str = Field(min_length=1, max_length=256)
    props: dict[str, Any]
    fingerprint: str = Field(pattern="^pcf1:[0-9a-f]{64}$")


class PerspectiveWriteResult(StrictModel):
    """The terminal transaction state of one Perspective write, as data.

    The fields are exactly the ``project_import`` result's, because the same
    transaction produces them: only a satisfied outcome is a result (``COMMITTED``,
    or ``NO_CHANGE`` when the candidate was semantically equal to the baseline), and
    every other terminal state is a Tool error carrying the error code the state maps to.
    """

    correlationId: str
    projectName: str
    transactionId: str
    state: str = Field(pattern="^(COMMITTED|NO_CHANGE)$")
    baselineFingerprint: str = Field(pattern="^pcf1:[0-9a-f]{64}$")
    candidateFingerprint: str = Field(pattern="^pcf1:[0-9a-f]{64}$")
    resultFingerprint: str | None = Field(default=None, pattern="^pcf1:[0-9a-f]{64}$")
    importDispatched: bool
    designerWarning: bool


class PerspectiveViewWriteResult(PerspectiveWriteResult):
    """A View write's result: the transaction state plus the path it changed."""

    path: str = Field(min_length=1, max_length=512)


class AuditRecord(StrictModel):
    action: str
    actionTarget: str
    actionValue: str
    actor: str
    actorHost: str
    originatingContext: int
    originatingSystem: str
    statusCode: int
    timestamp: int
    result: str


class AuditQueryResult(StrictModel):
    correlationId: str
    profile: str
    items: list[AuditRecord] = Field(max_length=500)
    page: PageMetadata


class AlarmPipelineSummary(StrictModel):
    path: str
    projectName: str
    pipelineName: str
    itemCount: int = Field(ge=0)
    active: bool


class AlarmPipelineListResult(StrictModel):
    correlationId: str
    items: list[AlarmPipelineSummary] = Field(max_length=500)
    page: PageMetadata


class AlarmPipelineInstance(StrictModel):
    pipelinePath: str
    source: str
    displayPath: str
    blockName: str
    status: str
    millis: int = Field(ge=0)
    alarmEventId: str


class AlarmPipelineStatusResult(StrictModel):
    correlationId: str
    path: str
    items: list[AlarmPipelineInstance] = Field(max_length=500)
    page: PageMetadata


class AlarmPipelineCancelObservedState(StrictModel):
    """The bounded ``alarm_pipeline_status`` re-read of the same pipeline path.

    ``items`` is what that read returned (one page, at most the Tool's own page size),
    and ``alarmEventReported`` is the comparison the verification made: whether a run
    for the requested Alarm Event is still among them. A successful cancel is confirmed
    by the run being gone, so the flag is False on every result this Tool returns.
    """

    items: list[AlarmPipelineInstance] = Field(max_length=100)
    alarmEventReported: bool


class AlarmPipelineCancelResult(StrictModel):
    """A pipeline cancel addresses one exact pipeline path and one Alarm Event.

    Only a satisfied outcome is returned as data: the Gateway claimed the cancel and
    the bounded re-read no longer reports that run. Every other outcome is a Tool error,
    and the observed state it carries is the re-read that was taken.
    """

    correlationId: str
    #: The exact pipeline path the cancel addressed (D30 §6: never a prefix).
    path: str = Field(min_length=1, max_length=512)
    alarmEventId: str = Field(min_length=1, max_length=128)
    observedState: AlarmPipelineCancelObservedState


class CapabilitiesResource(StrictModel):
    state: str
    generation: int = Field(ge=0)
    fetchedAt: str | None
    gatewayVersion: str | None
    openapiSha256: str | None
    supportedCapabilities: list[str]
    endpointCount: int = Field(ge=0)
    moduleVersions: dict[str, str]


class ArtifactDownload(StrictModel):
    path: str = Field(pattern=r"^/artifacts/[A-Za-z0-9._-]+$")


class ArtifactRefModel(StrictModel):
    """contracts/shared/artifact-ref.schema.json. Owner principal is internal."""

    artifactId: str = Field(min_length=1, max_length=128)
    mediaType: str = Field(min_length=1)
    sizeBytes: int = Field(ge=0)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    kind: str = Field(pattern="^(project_archive|project_export|tag_config_export)$")
    filename: str = Field(min_length=1, max_length=255)
    sensitivity: str = Field(pattern="^(INTERNAL|CONFIDENTIAL|RESTRICTED)$")
    retentionClass: str = Field(pattern="^(EPHEMERAL|EXPORT|RECOVERY)$")
    createdAt: str = Field(min_length=1)
    expiresAt: str | None
    download: ArtifactDownload


class _ForwardRefMarker:  # placeholder to keep file order valid; real class defined below
    pass


class ArtifactPage(StrictModel):
    total: int = Field(ge=0)
    matching: int = Field(ge=0)
    limit: int = Field(ge=0, le=500)
    offset: int = Field(ge=0)
    nextOffset: int | None = Field(default=None, ge=0)


class ArtifactListResult(StrictModel):
    correlationId: str
    items: list[ArtifactRefModel] = Field(max_length=500)
    page: ArtifactPage


class ArtifactInfoResult(StrictModel):
    correlationId: str
    artifact: ArtifactRefModel


class ArtifactDeleteResult(StrictModel):
    """Removing an artifact is verified by its absence from the store.

    A delete leaves nothing to describe, so the bounded read-back reports exactly what
    it found, no READY artifact at the identifier, and the kind of the artifact that
    was removed names what went away. Only a satisfied outcome is returned as data:
    a refusal (a retention-locked artifact, a Target the deployment does not name) or
    an unestablished state is a Tool error.
    """

    correlationId: str
    artifactId: str = Field(min_length=1, max_length=128)
    #: The kind of the artifact the pre-state read resolved (D17 kinds).
    kind: str = Field(pattern="^(project_archive|project_export|tag_config_export)$")
    #: False on success: the bounded read-back found no READY artifact at the Target.
    present: bool


class OperationPhase(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    at: str = Field(min_length=1)


class OperationDiagnoseResult(StrictModel):
    correlationId: str
    tool: str
    outcome: str = Field(
        pattern="^(in_progress|succeeded|failed|outcome_unknown|cancelled|interrupted)$"
    )
    errorCode: str | None
    startedAt: str
    finishedAt: str | None
    phases: list[OperationPhase] = Field(max_length=32)
    phasesTruncated: bool
    transactionId: str | None
    downstreamCorrelationId: str | None
    auditResultMissing: bool


class ProjectExportResult(StrictModel):
    correlationId: str
    projectName: str = Field(min_length=1, max_length=256)
    fingerprint: str = Field(pattern="^pcf1:[0-9a-f]{64}$")
    artifact: ArtifactRefModel


class TagConfigExportResult(StrictModel):
    correlationId: str
    provider: str = Field(min_length=1, max_length=256)
    path: str = Field(max_length=1024)
    artifact: ArtifactRefModel


class TagImportObservedState(StrictModel):
    """The bounded re-export's comparison (Observed state, bounded).

    Both lists hold the provider-relative Tag paths the import document declares: the
    ones the re-export of the same provider and path showed, and the ones it did not.
    """

    present: list[str] = Field(max_length=500)
    missing: list[str] = Field(max_length=500)


class TagConfigImportResult(StrictModel):
    """A Tag config import creates Tags only, and its verification is the
    bounded re-export of the same provider and path.

    Only a satisfied outcome is a result: the Gateway claimed the import succeeded and
    the re-export showed every Tag the document declares. A claimed success that does
    not is ``recovery_required`` — an error whose message names the Tags the re-export
    was not showing — so ``missing`` is empty on every result this Tool returns.
    """

    correlationId: str
    provider: str = Field(min_length=1, max_length=256)
    path: str = Field(max_length=1024)
    #: The READY Tag export the import consumed (D17/D30 §6).
    artifact: ArtifactRefModel
    observedState: TagImportObservedState


class OpenApiInfoResource(StrictModel):
    state: str
    generation: int = Field(ge=0)
    fetchedAt: str | None
    ageSeconds: float | None = Field(default=None, ge=0)
    gatewayVersion: str | None
    environmentFingerprint: str | None
    openapiSha256: str | None
