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
    """Additive (D19) low-cost self-diagnostics for the local data plane."""

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
    """contracts/shared/artifact-ref.schema.json (D17). Owner principal is internal."""

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


class OpenApiInfoResource(StrictModel):
    state: str
    generation: int = Field(ge=0)
    fetchedAt: str | None
    ageSeconds: float | None = Field(default=None, ge=0)
    gatewayVersion: str | None
    environmentFingerprint: str | None
    openapiSha256: str | None
