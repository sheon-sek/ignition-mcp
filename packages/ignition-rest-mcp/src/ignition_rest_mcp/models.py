"""Typed Phase 1 public results."""

from __future__ import annotations

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


class CapabilitiesResource(StrictModel):
    state: str
    generation: int = Field(ge=0)
    fetchedAt: str | None
    gatewayVersion: str | None
    openapiSha256: str | None
    supportedCapabilities: list[str]
    endpointCount: int = Field(ge=0)
    moduleVersions: dict[str, str]


class OpenApiInfoResource(StrictModel):
    state: str
    generation: int = Field(ge=0)
    fetchedAt: str | None
    ageSeconds: float | None = Field(default=None, ge=0)
    gatewayVersion: str | None
    environmentFingerprint: str | None
    openapiSha256: str | None
