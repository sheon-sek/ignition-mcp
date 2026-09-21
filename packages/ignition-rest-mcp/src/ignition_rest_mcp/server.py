"""FastMCP 4 Streamable HTTP server with the central D18 invocation lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.artifacts.routes import register_artifact_routes
from ignition_rest_mcp.projects.identity import gateway_identity
from ignition_rest_mcp.projects.locks import SINGLE_WRITER_LIMITATION
from ignition_rest_mcp.projects.locks import ProcessWriterGuard, ProjectLockRegistry
from ignition_rest_mcp.projects.transactions import ProjectTransactionService
from ignition_rest_mcp.auth import build_auth, current_principal
from ignition_rest_mcp.authorization import ScopeAuthorizationMiddleware
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry, CapabilitySnapshot
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.invocation.lifecycle import enforce_output_budget, invoke_tool
from ignition_rest_mcp.models import (
    AlarmPipelineListResult,
    AlarmPipelineStatusResult,
    ArtifactInfoResult,
    ArtifactListResult,
    AuditQueryResult,
    OperationDiagnoseResult,
    CapabilitiesResource,
    ConfigResourceDescribeResult,
    ConfigResourceGetResult,
    ConfigResourceListResult,
    ConfigResourceNamesResult,
    ConfigResourceSearchResult,
    GatewayDiagnoseResult,
    GatewayInfoResult,
    OpenApiInfoResource,
    ProjectListResult,
    ProjectExportResult,
    StorageDiagnostics,
    TagConfigExportResult,
)
from ignition_rest_mcp.observability.logging import configure_logging
from ignition_rest_mcp.observability.metrics import Metrics
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.runtime import RuntimeState
from ignition_rest_mcp.services.exports import (
    project_export as project_export_service,
    tag_config_export as tag_config_export_service,
)
from ignition_rest_mcp.services.artifacts import (
    artifact_info as artifact_info_service,
    artifact_list as artifact_list_service,
    operation_diagnose as operation_diagnose_service,
)
from ignition_rest_mcp.services.gateway import (
    capabilities_resource,
    gateway_diagnose as diagnose_service,
    gateway_info as info_service,
    openapi_info_resource,
)
from ignition_rest_mcp.services.readonly import (
    alarm_pipeline_list as alarm_pipeline_list_service,
    alarm_pipeline_status as alarm_pipeline_status_service,
    audit_query as audit_query_service,
    config_resource_describe as config_resource_describe_service,
    config_resource_get as config_resource_get_service,
    config_resource_list as config_resource_list_service,
    config_resource_names as config_resource_names_service,
    config_resource_search as config_resource_search_service,
    project_list as project_list_service,
)
from ignition_rest_mcp.storage.database import Storage, StorageUnavailable
from ignition_rest_mcp.storage.paths import validate_data_directory
from ignition_rest_mcp.storage.records import OperationRecordStore

LOGGER = logging.getLogger("ignition_rest_mcp")
TModel = TypeVar("TModel", bound=BaseModel)

# D10 ARTIFACT hard ceiling: in_progress records older than this at startup are
# interrupted (never re-executed).
HARD_ARTIFACT_DEADLINE_SECONDS = 300.0
RETENTION_PASS_DEADLINE_SECONDS = 30.0


def create_server(settings: Settings) -> FastMCP:
    settings.validate()
    data_dir = validate_data_directory(settings.data_dir, settings.deployment_profile)
    state = RuntimeState()

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        client = GatewayClient(
            base_url=settings.gateway_url,
            api_token=settings.gateway_api_token,
            timeout_seconds=settings.request_timeout_seconds,
        )
        registry = CapabilityRegistry(client)
        metrics = _startup_metrics()
        storage = Storage(data_dir)
        try:
            await storage.open()
        except Exception as error:
            await client.aclose()
            raise error
        records = OperationRecordStore(storage.state)
        audit_sink = _startup_audit_sink(storage)
        artifacts = LocalArtifactStore(storage.state, data_dir, quotas_from_settings(settings))
        artifacts.prepare()
        await artifacts.reconcile(batch=settings.artifact_cleanup_batch, deadline_seconds=HARD_ARTIFACT_DEADLINE_SECONDS)
        writer_guard = ProcessWriterGuard(data_dir)
        if settings.project_writer_enabled:
            await writer_guard.acquire()
        transaction_service = ProjectTransactionService(
            client=client, registry=registry, store=artifacts, settings=settings,
            locks=ProjectLockRegistry(
                timeout_seconds=settings.project_lock_timeout_seconds,
                max_entries=settings.project_lock_max_entries,
            ),
            identity=gateway_identity(settings.gateway_id, settings.gateway_url),
            db=storage.state, metrics=metrics,
        )
        state.client = client
        state.registry = registry
        state.metrics = metrics
        state.storage = storage
        state.records = records
        state.audit_sink = audit_sink
        state.artifacts = artifacts
        state.transactions = transaction_service
        interrupted = await records.mark_interrupted_stale(HARD_ARTIFACT_DEADLINE_SECONDS)
        if interrupted:
            LOGGER.warning(
                "Marked interrupted operation records from a previous run",
                extra={"event": "operation_record_recovery", "outcome": "interrupted"},
            )
        watcher: asyncio.Task[None] | None = None
        retention: asyncio.Task[None] | None = None
        prober: asyncio.Task[None] | None = None
        janitor: asyncio.Task[None] | None = None
        reconciler: asyncio.Task[None] | None = None
        try:
            await registry.refresh()
            _apply_visibility(mcp, registry.snapshot, settings)
            LOGGER.info(
                "Capability registry initialized",
                extra={
                    "event": "capability_registry",
                    "registryState": registry.snapshot.state,
                    "registryGeneration": registry.snapshot.generation,
                },
            )
            watcher = asyncio.create_task(
                _watch_capabilities(mcp, registry, settings.watcher_interval_seconds, settings),
            )
            retention = asyncio.create_task(_retention_loop(storage, records, audit_sink, settings))
            prober = asyncio.create_task(_probe_loop(storage, settings))
            janitor = asyncio.create_task(_artifact_janitor_loop(artifacts, settings, metrics))
            if settings.project_writer_enabled:
                reconciler = asyncio.create_task(_transaction_reconcile_loop(transaction_service, settings))
            yield
        finally:
            try:
                for task in (watcher, retention, prober, janitor, reconciler):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(*(
                    task for task in (watcher, retention, prober, janitor, reconciler) if task is not None
                ), return_exceptions=True)
                writer_guard.release_sync()
                await registry.aclose()
            finally:
                try:
                    await client.aclose()
                finally:
                    try:
                        await storage.close()
                    finally:
                        state.client = None
                        state.registry = None
                        state.metrics = None
                        state.storage = None
                        state.records = None
                        state.audit_sink = None
                        state.artifacts = None
                        state.transactions = None

    mcp = FastMCP(
        name="ignition-rest",
        version="0.1.0a0",
        auth=build_auth(settings),
        middleware=[ScopeAuthorizationMiddleware(settings)],
        lifespan=lifespan,
        mask_error_details=True,
    )

    async def _invoke(
        tool: str,
        budget_class: str,
        handler: Callable[[OperationContext], Awaitable[TModel]],
        *,
        permission_class: str = "READ",
        destructive: bool = False,
        audited: bool = False,
    ) -> TModel:
        return await invoke_tool(
            state=state,
            settings=settings,
            tool=tool,
            budget_class=budget_class,
            principal=current_principal(settings),
            handler=handler,
            permission_class=permission_class,
            destructive=destructive,
            audited=audited,
        )

    @mcp.tool(
        name="gateway_info",
        description="Return a bounded identity summary for the connected Ignition Gateway.",
        output_schema=GatewayInfoResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:gateway_info"},
    )
    async def gateway_info() -> GatewayInfoResult:
        async def flow(context: OperationContext) -> GatewayInfoResult:
            return await info_service(state.require_client(), state.require_registry(), context)

        return await _invoke("gateway_info", "FAST", flow)

    @mcp.tool(
        name="gateway_diagnose",
        description="Run low-cost connectivity, authentication, and capability-registry diagnostics.",
        output_schema=GatewayDiagnoseResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "diagnostic"},
    )
    async def gateway_diagnose() -> GatewayDiagnoseResult:
        async def flow(context: OperationContext) -> GatewayDiagnoseResult:
            result = await diagnose_service(
                state.require_client(), state.require_registry(), context,
                await _storage_diagnostics(state, settings),
            )
            if not (result.gatewayReachable and result.authenticationOk):
                state.require_metrics().record_tool("gateway_diagnose", "degraded")
            return result

        return await _invoke("gateway_diagnose", "FAST", flow)

    @mcp.tool(
        name="project_list",
        description="List Ignition Projects through the bounded Native REST collection endpoint.",
        output_schema=ProjectListResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:project_list"},
    )
    async def project_list(search: str = "", limit: int = 100, offset: int = 0) -> ProjectListResult:
        return await _invoke(
            "project_list", "FAST",
            lambda context: project_list_service(
                state.require_client(), state.require_registry(), context,
                search=search, limit=limit, offset=offset,
            ),
        )

    @mcp.tool(
        name="config_resource_search",
        description="Search OpenAPI-discovered Gateway configuration resource types. No REST path is caller-controlled.",
        output_schema=ConfigResourceSearchResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:config_resource_search"},
    )
    async def config_resource_search(
        query: str = "", limit: int = 100, offset: int = 0,
    ) -> ConfigResourceSearchResult:
        async def flow(context: OperationContext) -> ConfigResourceSearchResult:
            return config_resource_search_service(
                state.require_registry(), context, query=query, limit=limit, offset=offset,
            )

        return await _invoke("config_resource_search", "FAST", flow)

    @mcp.tool(
        name="config_resource_describe",
        description="Describe one exact OpenAPI-discovered Gateway configuration resource type.",
        output_schema=ConfigResourceDescribeResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:config_resource_describe"},
    )
    async def config_resource_describe(resourceType: str) -> ConfigResourceDescribeResult:
        return await _invoke(
            "config_resource_describe", "FAST",
            lambda context: config_resource_describe_service(
                state.require_client(), state.require_registry(), context, resource_type=resourceType,
            ),
        )

    @mcp.tool(
        name="config_resource_names",
        description="List bounded names for a non-singleton OpenAPI-discovered configuration resource type.",
        output_schema=ConfigResourceNamesResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:config_resource_names"},
    )
    async def config_resource_names(
        resourceType: str, search: str = "", limit: int = 100, offset: int = 0,
    ) -> ConfigResourceNamesResult:
        return await _invoke(
            "config_resource_names", "FAST",
            lambda context: config_resource_names_service(
                state.require_client(), state.require_registry(), context,
                resource_type=resourceType, search=search, limit=limit, offset=offset,
            ),
        )

    @mcp.tool(
        name="config_resource_list",
        description="List bounded, redacted configuration resources for one OpenAPI-discovered non-singleton type.",
        output_schema=ConfigResourceListResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:config_resource_list"},
    )
    async def config_resource_list(
        resourceType: str, search: str = "", limit: int = 100, offset: int = 0,
    ) -> ConfigResourceListResult:
        return await _invoke(
            "config_resource_list", "FAST",
            lambda context: config_resource_list_service(
                state.require_client(), state.require_registry(), context,
                resource_type=resourceType, search=search, limit=limit, offset=offset,
            ),
        )

    @mcp.tool(
        name="config_resource_get",
        description="Read one exact or singleton configuration resource selected only through the OpenAPI capability catalog.",
        output_schema=ConfigResourceGetResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:config_resource_get"},
    )
    async def config_resource_get(
        resourceType: str, name: str = "", collection: str = "", defaultIfUndefined: bool = False,
    ) -> ConfigResourceGetResult:
        return await _invoke(
            "config_resource_get", "FAST",
            lambda context: config_resource_get_service(
                state.require_client(), state.require_registry(), context,
                resource_type=resourceType, name=name, collection=collection,
                default_if_undefined=defaultIfUndefined,
            ),
        )

    @mcp.tool(
        name="audit_query",
        description="Query one Ignition Gateway audit profile with bounded pagination and optional native filters.",
        output_schema=AuditQueryResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:audit_query"},
    )
    async def audit_query(
        profile: str,
        actor: str = "",
        action: str = "",
        target: str = "",
        value: str = "",
        system: str = "",
        originatingContext: str = "",
        startTime: str = "",
        endTime: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> AuditQueryResult:
        return await _invoke(
            "audit_query", "FAST",
            lambda context: audit_query_service(
                state.require_client(), state.require_registry(), context,
                profile=profile, actor=actor, action=action, target=target, value=value,
                system=system, originating_context=originatingContext,
                start_time=startTime, end_time=endTime, limit=limit, offset=offset,
            ),
        )

    @mcp.tool(
        name="alarm_pipeline_list",
        description="List bounded Alarm Notification Pipeline runtime overview records through Native REST.",
        output_schema=AlarmPipelineListResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:alarm_pipeline_list"},
    )
    async def alarm_pipeline_list(
        search: str = "", limit: int = 100, offset: int = 0,
    ) -> AlarmPipelineListResult:
        return await _invoke(
            "alarm_pipeline_list", "FAST",
            lambda context: alarm_pipeline_list_service(
                state.require_client(), state.require_registry(), context,
                search=search, limit=limit, offset=offset,
            ),
        )

    @mcp.tool(
        name="alarm_pipeline_status",
        description="Read bounded runtime instances for one exact Alarm Notification Pipeline path.",
        output_schema=AlarmPipelineStatusResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:alarm_pipeline_status"},
    )
    async def alarm_pipeline_status(
        path: str, limit: int = 100, offset: int = 0,
    ) -> AlarmPipelineStatusResult:
        return await _invoke(
            "alarm_pipeline_status", "FAST",
            lambda context: alarm_pipeline_status_service(
                state.require_client(), state.require_registry(), context,
                path=path, limit=limit, offset=offset,
            ),
        )

    @mcp.tool(
        name="project_export",
        description="Export one Ignition Project into a server-held artifact (sensitive export; deployment-gated).",
        output_schema=ProjectExportResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:project_export", "sensitive-export"},
    )
    async def project_export(projectName: str) -> ProjectExportResult:
        return await _invoke(
            "project_export", "ARTIFACT",
            lambda context: project_export_service(
                state.require_client(), state.require_registry(), state.require_artifacts(), context,
                settings_gate_on=settings.sensitive_exports_enabled,
                project_name=projectName, gateway_id="",
                deadline_seconds=settings.budget_deadline_seconds("ARTIFACT"),
            ),
            audited=True,
        )

    @mcp.tool(
        name="tag_config_export",
        description=(
            "Export bulk Tag configuration (JSON only) into a server-held artifact "
            "(sensitive export; deployment-gated)."
        ),
        output_schema=TagConfigExportResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "capability:tag_config_export", "sensitive-export"},
    )
    async def tag_config_export(
        provider: str, path: str = "", recursive: bool = True, includeUdts: bool = False,
    ) -> TagConfigExportResult:
        return await _invoke(
            "tag_config_export", "ARTIFACT",
            lambda context: tag_config_export_service(
                state.require_client(), state.require_registry(), state.require_artifacts(), context,
                settings_gate_on=settings.sensitive_exports_enabled,
                provider=provider, path=path, recursive=recursive, include_udts=includeUdts,
                gateway_id="", deadline_seconds=settings.budget_deadline_seconds("ARTIFACT"),
            ),
            audited=True,
        )

    @mcp.tool(
        name="artifact_list",
        description="List READY artifact metadata visible to the calling principal (bounded, paginated).",
        output_schema=ArtifactListResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "storage", "principal-scoped"},
    )
    async def artifact_list(kind: str = "", limit: int = 100, offset: int = 0) -> ArtifactListResult:
        principal = current_principal(settings)
        return await _invoke(
            "artifact_list", "FAST",
            lambda context: artifact_list_service(
                state.require_artifacts(), context, principal=principal, kind=kind, limit=limit, offset=offset,
            ),
        )

    @mcp.tool(
        name="artifact_info",
        description="Read the metadata of one READY artifact visible to the calling principal.",
        output_schema=ArtifactInfoResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "storage", "principal-scoped"},
    )
    async def artifact_info(artifactId: str) -> ArtifactInfoResult:
        principal = current_principal(settings)
        return await _invoke(
            "artifact_info", "FAST",
            lambda context: artifact_info_service(
                state.require_artifacts(), state.audit_sink, context,
                principal=principal, artifact_id=artifactId, metrics=state.metrics,
            ),
        )

    @mcp.tool(
        name="operation_diagnose",
        description="Diagnose one prior operation by its exact UUIDv7 correlationId (D19, principal-scoped).",
        output_schema=OperationDiagnoseResult.model_json_schema(),
        tags={"read", "scope:ignition.read", "diagnostic", "storage", "principal-scoped"},
    )
    async def operation_diagnose(correlationId: str) -> OperationDiagnoseResult:
        principal = current_principal(settings)
        return await _invoke(
            "operation_diagnose", "FAST",
            lambda context: operation_diagnose_service(
                state.require_records(), context, principal=principal, correlation_id=correlationId,
            ),
        )

    @mcp.resource(
        "ignition://gateway/capabilities",
        name="gateway-capabilities",
        description="Bounded metadata for the current immutable Gateway capability snapshot.",
        mime_type="application/json",
        tags={"read", "scope:ignition.read"},
    )
    async def gateway_capabilities() -> str:
        value: CapabilitiesResource = capabilities_resource(state.require_registry())
        try:
            enforce_output_budget(value, settings.structured_output_limit_bytes)
        except GatewayError as error:
            raise ResourceError(str(error)) from error
        return value.model_dump_json()

    @mcp.resource(
        "ignition://gateway/openapi-info",
        name="gateway-openapi-info",
        description="OpenAPI fingerprint and registry metadata; does not expose the full OpenAPI document.",
        mime_type="application/json",
        tags={"read", "scope:ignition.read"},
    )
    async def gateway_openapi_info() -> str:
        value: OpenApiInfoResource = openapi_info_resource(state.require_registry())
        try:
            enforce_output_budget(value, settings.structured_output_limit_bytes)
        except GatewayError as error:
            raise ResourceError(str(error)) from error
        return value.model_dump_json()

    @mcp.custom_route("/health/live", methods=["GET"], include_in_schema=False)
    async def health_live(_: Request) -> Response:
        return JSONResponse({"live": True})

    @mcp.custom_route("/health/ready", methods=["GET"], include_in_schema=False)
    async def health_ready(_: Request) -> Response:
        registry = state.registry
        registry_ready = registry is not None and registry.snapshot.state == "READY"
        subsystems = state.storage.health() if state.storage is not None else []
        storage_ready = bool(subsystems) and all(item["healthy"] for item in subsystems)
        ready = registry_ready and storage_ready
        status = 200 if ready else 503
        return JSONResponse(
            {
                "ready": ready,
                "registryState": registry.snapshot.state if registry is not None else "UNAVAILABLE",
                "storageReady": storage_ready,
                "subsystems": subsystems,
            },
            status_code=status,
        )

    @mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
    async def metrics(_: Request) -> Response:
        current = state.metrics
        body = current.render() if current is not None else ""
        if current is not None and state.artifacts is not None:
            try:
                ready_count, ready_bytes = await state.artifacts.totals()
            except StorageUnavailable:
                ready_count = ready_bytes = -1
            body += (
                "# HELP ignition_mcp_artifacts_ready Gauge of READY artifacts (unlabeled aggregate).\n"
                "# TYPE ignition_mcp_artifacts_ready gauge\n"
                f"ignition_mcp_artifacts_ready {ready_count}\n"
                "# HELP ignition_mcp_artifact_bytes_total Gauge of READY artifact bytes (unlabeled aggregate).\n"
                "# TYPE ignition_mcp_artifact_bytes_total gauge\n"
                f"ignition_mcp_artifact_bytes_total {max(ready_bytes, 0)}\n"
            )
        if state.storage is not None and current is not None:
            lines = [
                "# HELP ignition_mcp_storage_subsystem_healthy Storage subsystem health (1 healthy, 0 failing).",
                "# TYPE ignition_mcp_storage_subsystem_healthy gauge",
            ]
            lines.extend(
                f'ignition_mcp_storage_subsystem_healthy{{subsystem="{item["name"]}"}} '
                f'{"1" if item["healthy"] else "0"}'
                for item in state.storage.health()
            )
            body = body + "\n".join(lines) + "\n"
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    register_artifact_routes(mcp, state, settings)

    return mcp


def _startup_metrics() -> Any:
    from ignition_rest_mcp.observability.metrics import Metrics

    return Metrics()


def _startup_audit_sink(storage: Storage) -> Any:
    from ignition_rest_mcp.audit.sink import SqliteAuditSink

    return SqliteAuditSink(storage.audit)


async def _watch_capabilities(
    mcp: FastMCP, registry: CapabilityRegistry, interval: float, settings: Settings,
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            if await registry.fingerprint_changed():
                await registry.refresh()
            _apply_visibility(mcp, registry.snapshot, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Capability watcher iteration failed",
                extra={"event": "capability_watch"},
            )


async def _retention_loop(
    storage: Storage, records: OperationRecordStore, audit_sink: Any, settings: Settings,
) -> None:
    while True:
        await asyncio.sleep(settings.retention_interval_seconds)
        try:
            if records.healthy:
                await records.enforce_retention(
                    settings.operation_record_max_rows, settings.operation_record_max_age_hours,
                    settings.retention_batch_rows, RETENTION_PASS_DEADLINE_SECONDS,
                )
            if audit_sink.healthy:
                await audit_sink.enforce_retention(
                    settings.audit_max_rows, settings.audit_max_age_days,
                    settings.retention_batch_rows, RETENTION_PASS_DEADLINE_SECONDS,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Retention pass failed",
                extra={"event": "retention", "outcome": "error"},
            )


async def _probe_loop(storage: Storage, settings: Settings) -> None:
    previous: dict[str, bool] = {}
    while True:
        await asyncio.sleep(settings.storage_probe_interval_seconds)
        try:
            results = await storage.probe_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Storage probe iteration failed", extra={"event": "storage_probe"})
            continue
        for name, healthy in results.items():
            if previous.get(name) is not healthy:
                LOGGER.warning(
                    "Storage subsystem health changed",
                    extra={"event": "storage_health", "outcome": "healthy" if healthy else "unhealthy"},
                    )
        previous = results


async def _artifact_janitor_loop(
    artifacts: LocalArtifactStore, settings: Settings, metrics: Metrics,
) -> None:
    passes = 0
    while True:
        await asyncio.sleep(settings.artifact_cleanup_interval_seconds)
        passes += 1
        try:
            deleted = await artifacts.cleanup_expired()
            stats = {"orphan_files": 0}
            if passes % 4 == 0:
                # Reconciliation converges orphan sweeps in bounded periodic passes.
                stats = await artifacts.reconcile(
                    batch=settings.artifact_cleanup_batch, deadline_seconds=HARD_ARTIFACT_DEADLINE_SECONDS,
                )
            metrics.record_artifact_cleanup(deleted, int(stats.get("orphan_files", 0)))
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Artifact janitor pass failed",
                extra={"event": "artifact_janitor", "outcome": "error"},
            )


async def _storage_diagnostics(state: RuntimeState, settings: Settings) -> StorageDiagnostics | None:
    storage = state.storage
    if storage is None:
        return None
    health = {item["name"]: bool(item["healthy"]) for item in storage.health()}
    artifacts_ready = 0
    if state.artifacts is not None and health.get("state", False):
        try:
            artifacts_ready, _bytes = await state.artifacts.totals()
        except StorageUnavailable:
            artifacts_ready = 0
    return StorageDiagnostics(
        dataDirectoryConfigured=bool(settings.data_dir),
        stateHealthy=health.get("state", False),
        auditHealthy=health.get("audit", False),
        artifactsReady=artifacts_ready,
        projectWriterEnabled=settings.project_writer_enabled,
        singleWriterLimitation=SINGLE_WRITER_LIMITATION,
    )


SENSITIVE_EXPORT_TOOLS = frozenset({"project_export", "tag_config_export"})


async def _transaction_reconcile_loop(service: ProjectTransactionService, settings: Settings) -> None:
    # Startup catch-up first, then an interval loop; reconciliation never replays
    # an import (bounded read-only comparison inside the service).
    try:
        await service.reconcile_interrupted(
            batch=settings.retention_batch_rows,
            per_txn_seconds=settings.artifact_timeout_seconds,
        )
    except Exception:
        LOGGER.exception("Startup transaction reconciliation failed", extra={"event": "txn_reconcile"})
    while True:
        await asyncio.sleep(settings.project_reconcile_interval_seconds)
        try:
            await service.reconcile_interrupted(
                batch=settings.retention_batch_rows,
                per_txn_seconds=settings.artifact_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Transaction reconciliation failed", extra={"event": "txn_reconcile"})


def _apply_visibility(mcp: FastMCP, snapshot: CapabilitySnapshot, settings: Settings) -> None:
    gated = {
        "gateway_info": "gateway_info",
        "project_list": "project_list",
        "config_resource_search": "config_resource_search",
        "config_resource_describe": "config_resource_describe",
        "config_resource_names": "config_resource_names",
        "config_resource_list": "config_resource_list",
        "config_resource_get": "config_resource_get",
        "audit_query": "audit_query",
        "alarm_pipeline_list": "alarm_pipeline_list",
        "alarm_pipeline_status": "alarm_pipeline_status",
        "project_export": "project_export",
        "tag_config_export": "tag_config_export",
    }
    usable = snapshot.state in {"READY", "STALE"}
    for tool_name, capability in gated.items():
        # Sensitive exports (D08 deny-by-default; D17): discovery requires BOTH the
        # Gateway capability and the deployment gate; the service repeats the gate
        # at call time so a stale tools/list can never bypass it.
        gate_blocks = tool_name in SENSITIVE_EXPORT_TOOLS and not settings.sensitive_exports_enabled
        if usable and not gate_blocks and capability in snapshot.semantic_capabilities:
            mcp.enable(names={tool_name}, components={"tool"})
        else:
            mcp.disable(names={tool_name}, components={"tool"})


def main() -> None:
    settings = Settings.from_env()
    configure_logging(log_format=settings.resolved_log_format, level=os_log_level())
    mcp = create_server(settings)
    mcp.run(
        transport="streamable-http",
        host=settings.bind_host,
        port=settings.bind_port,
        path=settings.mcp_path,
        show_banner=False,
    )


def os_log_level() -> str:
    import os

    return os.getenv("IGNITION_MCP_LOG_LEVEL", "INFO").upper()


if __name__ == "__main__":
    main()
