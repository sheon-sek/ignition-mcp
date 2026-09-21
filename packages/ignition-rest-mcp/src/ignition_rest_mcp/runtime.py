"""Mutable process state owned by the FastMCP lifespan."""

from __future__ import annotations

from dataclasses import dataclass

from ignition_rest_mcp.audit.sink import SqliteAuditSink
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.observability.metrics import Metrics
from ignition_rest_mcp.storage.database import Storage
from ignition_rest_mcp.storage.records import OperationRecordStore


@dataclass(slots=True)
class RuntimeState:
    client: GatewayClient | None = None
    registry: CapabilityRegistry | None = None
    metrics: Metrics | None = None
    storage: Storage | None = None
    records: OperationRecordStore | None = None
    audit_sink: SqliteAuditSink | None = None

    def require_client(self) -> GatewayClient:
        if self.client is None:
            raise RuntimeError("Gateway client is not initialized")
        return self.client

    def require_registry(self) -> CapabilityRegistry:
        if self.registry is None:
            raise RuntimeError("Capability registry is not initialized")
        return self.registry

    def require_metrics(self) -> Metrics:
        if self.metrics is None:
            raise RuntimeError("Metrics are not initialized")
        return self.metrics

    def require_storage(self) -> Storage:
        if self.storage is None:
            raise RuntimeError("Storage is not initialized")
        return self.storage

    def require_records(self) -> OperationRecordStore:
        if self.records is None:
            raise RuntimeError("Operation records are not initialized")
        return self.records

    def require_audit_sink(self) -> SqliteAuditSink:
        if self.audit_sink is None:
            raise RuntimeError("Audit sink is not initialized")
        return self.audit_sink
