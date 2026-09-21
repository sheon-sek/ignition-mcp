"""Environment-backed configuration with fail-closed deployment profiles."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from urllib.parse import urlparse

TEMP_FILESYSTEM_PREFIXES = ("/tmp", "/var/tmp", "/dev/shm")


class ConfigurationError(ValueError):
    """Invalid or unsafe server configuration."""


@dataclass(frozen=True, slots=True)
class Settings:
    gateway_url: str
    gateway_api_token: str
    bind_host: str
    bind_port: int
    mcp_path: str
    deployment_profile: str
    auth_mode: str
    static_token: str | None
    service_identity: str
    watcher_interval_seconds: float
    request_timeout_seconds: float
    structured_output_limit_bytes: int
    log_format: str
    data_dir: str
    tool_timeout_seconds: float
    query_timeout_seconds: float
    artifact_timeout_seconds: float
    audit_max_rows: int
    audit_max_age_days: int
    operation_record_max_rows: int
    operation_record_max_age_hours: int
    retention_interval_seconds: float
    retention_batch_rows: int
    storage_probe_interval_seconds: float
    artifact_max_bytes: int
    artifact_total_bytes: int
    artifact_max_count: int
    artifact_min_free_bytes: int
    artifact_min_free_ratio: float
    artifact_export_ttl_hours: int
    artifact_recovery_ttl_days: int
    artifact_staging_deadline_seconds: float
    artifact_cleanup_interval_seconds: float
    artifact_cleanup_batch: int
    artifact_upload_enabled: bool
    sensitive_exports_enabled: bool
    jwt_jwks_uri: str | None = None
    jwt_public_key: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            gateway_url=os.getenv("IGNITION_MCP_GATEWAY_URL", "http://127.0.0.1:8088").rstrip("/"),
            gateway_api_token=os.getenv("IGNITION_MCP_GATEWAY_API_TOKEN", ""),
            bind_host=os.getenv("IGNITION_MCP_HOST", "127.0.0.1"),
            bind_port=int(os.getenv("IGNITION_MCP_PORT", "8000")),
            mcp_path=os.getenv("IGNITION_MCP_PATH", "/mcp"),
            deployment_profile=os.getenv("IGNITION_MCP_DEPLOYMENT_PROFILE", "development"),
            auth_mode=os.getenv("IGNITION_MCP_AUTH_MODE", "none"),
            static_token=os.getenv("IGNITION_MCP_STATIC_TOKEN") or None,
            service_identity=os.getenv("IGNITION_MCP_SERVICE_IDENTITY", "ignition-rest"),
            watcher_interval_seconds=float(os.getenv("IGNITION_MCP_WATCHER_INTERVAL_SECONDS", "60")),
            request_timeout_seconds=float(os.getenv("IGNITION_MCP_GATEWAY_TIMEOUT_SECONDS", "10")),
            structured_output_limit_bytes=int(
                os.getenv("IGNITION_MCP_STRUCTURED_OUTPUT_LIMIT_BYTES", "262144")
            ),
            log_format=os.getenv("IGNITION_MCP_LOG_FORMAT", "auto").lower(),
            data_dir=os.getenv("IGNITION_MCP_DATA_DIR", ""),
            tool_timeout_seconds=float(os.getenv("IGNITION_MCP_TOOL_TIMEOUT_SECONDS", "30")),
            query_timeout_seconds=float(os.getenv("IGNITION_MCP_QUERY_TIMEOUT_SECONDS", "30")),
            artifact_timeout_seconds=float(os.getenv("IGNITION_MCP_ARTIFACT_TIMEOUT_SECONDS", "120")),
            audit_max_rows=int(os.getenv("IGNITION_MCP_AUDIT_MAX_ROWS", "50000")),
            audit_max_age_days=int(os.getenv("IGNITION_MCP_AUDIT_MAX_AGE_DAYS", "90")),
            operation_record_max_rows=int(os.getenv("IGNITION_MCP_OPERATION_RECORD_MAX_ROWS", "10000")),
            operation_record_max_age_hours=int(
                os.getenv("IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS", "72")
            ),
            retention_interval_seconds=float(os.getenv("IGNITION_MCP_RETENTION_INTERVAL_SECONDS", "300")),
            retention_batch_rows=int(os.getenv("IGNITION_MCP_RETENTION_BATCH_ROWS", "500")),
            storage_probe_interval_seconds=float(os.getenv("IGNITION_MCP_STORAGE_PROBE_INTERVAL_SECONDS", "30")),
            artifact_max_bytes=int(os.getenv("IGNITION_MCP_ARTIFACT_MAX_BYTES", "268435456")),
            artifact_total_bytes=int(os.getenv("IGNITION_MCP_ARTIFACT_TOTAL_BYTES", "1073741824")),
            artifact_max_count=int(os.getenv("IGNITION_MCP_ARTIFACT_MAX_COUNT", "1000")),
            artifact_min_free_bytes=int(os.getenv("IGNITION_MCP_ARTIFACT_MIN_FREE_BYTES", "104857600")),
            artifact_min_free_ratio=float(os.getenv("IGNITION_MCP_ARTIFACT_MIN_FREE_RATIO", "0.05")),
            artifact_export_ttl_hours=int(os.getenv("IGNITION_MCP_ARTIFACT_EXPORT_TTL_HOURS", "24")),
            artifact_recovery_ttl_days=int(os.getenv("IGNITION_MCP_ARTIFACT_RECOVERY_TTL_DAYS", "7")),
            artifact_staging_deadline_seconds=float(
                os.getenv("IGNITION_MCP_ARTIFACT_STAGING_DEADLINE_SECONDS", "900")
            ),
            artifact_cleanup_interval_seconds=float(
                os.getenv("IGNITION_MCP_ARTIFACT_CLEANUP_INTERVAL_SECONDS", "300")
            ),
            artifact_cleanup_batch=int(os.getenv("IGNITION_MCP_ARTIFACT_CLEANUP_BATCH", "50")),
            artifact_upload_enabled=_bool_env("IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED"),
            sensitive_exports_enabled=_bool_env("IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED"),
            jwt_jwks_uri=os.getenv("IGNITION_MCP_JWT_JWKS_URI") or None,
            jwt_public_key=os.getenv("IGNITION_MCP_JWT_PUBLIC_KEY") or None,
            jwt_issuer=os.getenv("IGNITION_MCP_JWT_ISSUER") or None,
            jwt_audience=os.getenv("IGNITION_MCP_JWT_AUDIENCE") or None,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        parsed = urlparse(self.gateway_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError("IGNITION_MCP_GATEWAY_URL must be an absolute http(s) URL")
        if not self.gateway_api_token:
            raise ConfigurationError("IGNITION_MCP_GATEWAY_API_TOKEN is required")
        if not self.mcp_path.startswith("/"):
            raise ConfigurationError("IGNITION_MCP_PATH must start with '/'")
        if self.bind_port < 1 or self.bind_port > 65535:
            raise ConfigurationError("IGNITION_MCP_PORT must be between 1 and 65535")
        if not 0 < self.request_timeout_seconds <= 30:
            raise ConfigurationError("Gateway timeout must be >0 and <=30 seconds")
        if self.watcher_interval_seconds <= 0:
            raise ConfigurationError("Watcher interval must be positive")
        if not 0 < self.structured_output_limit_bytes <= 1_048_576:
            raise ConfigurationError(
                "Structured output limit must be >0 and <=1048576 bytes"
            )
        if self.log_format not in {"auto", "text", "json"}:
            raise ConfigurationError("IGNITION_MCP_LOG_FORMAT must be auto, text, or json")
        if self.deployment_profile not in {"development", "trusted-internal", "secured"}:
            raise ConfigurationError("Unknown deployment profile")
        if self.auth_mode not in {"none", "static-token", "jwt"}:
            raise ConfigurationError("Supported auth modes are none, static-token, and jwt")
        if self.auth_mode == "jwt":
            if bool(self.jwt_jwks_uri) == bool(self.jwt_public_key):
                raise ConfigurationError("JWT requires exactly one JWKS URI or static public key")
            if not self.jwt_issuer or not self.jwt_audience:
                raise ConfigurationError("JWT requires issuer and audience validation")
            if self.jwt_jwks_uri and urlparse(self.jwt_jwks_uri).scheme != "https":
                raise ConfigurationError("JWT JWKS URI must use HTTPS")
        if self.auth_mode == "static-token" and not self.static_token:
            raise ConfigurationError("Static-token mode requires IGNITION_MCP_STATIC_TOKEN")
        if self.deployment_profile == "secured" and self.auth_mode != "jwt":
            raise ConfigurationError("secured profile requires JWT authentication")
        if self.deployment_profile == "development" and not _is_loopback(self.bind_host):
            raise ConfigurationError("development profile may bind only to loopback")
        if (
            self.deployment_profile != "trusted-internal"
            and self.auth_mode == "none"
            and not _is_loopback(self.bind_host)
        ):
            raise ConfigurationError("Unauthenticated non-loopback binding requires trusted-internal profile")
        self._validate_storage()

    def _validate_storage(self) -> None:
        # D17/D18: the persistent data directory is mandatory in every profile.
        if not self.data_dir or not self.data_dir.strip():
            raise ConfigurationError("IGNITION_MCP_DATA_DIR is required in every deployment profile")
        if not self.data_dir.startswith("/"):
            raise ConfigurationError("IGNITION_MCP_DATA_DIR must be an absolute path")
        if self.deployment_profile in {"trusted-internal", "secured"}:
            normalized = os.path.normpath(self.data_dir)
            for prefix in TEMP_FILESYSTEM_PREFIXES:
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    raise ConfigurationError(
                        f"IGNITION_MCP_DATA_DIR must not live on a temporary filesystem path ({prefix}) "
                        "in trusted-internal or secured deployments"
                    )
        if not 0 < self.tool_timeout_seconds <= 30:
            raise ConfigurationError("Tool (FAST) timeout must be >0 and <=30 seconds")
        if not 0 < self.query_timeout_seconds <= 120:
            raise ConfigurationError("QUERY timeout must be >0 and <=120 seconds")
        if not 0 < self.artifact_timeout_seconds <= 300:
            raise ConfigurationError("ARTIFACT timeout must be >0 and <=300 seconds")
        if self.audit_max_rows < 1 or self.operation_record_max_rows < 1:
            raise ConfigurationError("Audit and operation-record maximum row counts must be >=1")
        if self.audit_max_age_days < 1 or self.operation_record_max_age_hours < 1:
            raise ConfigurationError("Audit and operation-record retention ages must be >=1")
        if self.retention_interval_seconds <= 0 or self.storage_probe_interval_seconds <= 0:
            raise ConfigurationError("Retention and storage-probe intervals must be positive")
        if not 0 < self.retention_batch_rows <= 10_000:
            raise ConfigurationError("Retention batch size must be >0 and <=10000 rows")
        self._validate_artifact_quotas()

    def _validate_artifact_quotas(self) -> None:
        # D17: storage must be finite; 0/negative/unlimited is rejected everywhere.
        for name, value in (
            ("IGNITION_MCP_ARTIFACT_MAX_BYTES", self.artifact_max_bytes),
            ("IGNITION_MCP_ARTIFACT_TOTAL_BYTES", self.artifact_total_bytes),
            ("IGNITION_MCP_ARTIFACT_MAX_COUNT", self.artifact_max_count),
            ("IGNITION_MCP_ARTIFACT_MIN_FREE_BYTES", self.artifact_min_free_bytes),
            ("IGNITION_MCP_ARTIFACT_EXPORT_TTL_HOURS", self.artifact_export_ttl_hours),
            ("IGNITION_MCP_ARTIFACT_RECOVERY_TTL_DAYS", self.artifact_recovery_ttl_days),
            ("IGNITION_MCP_ARTIFACT_CLEANUP_BATCH", self.artifact_cleanup_batch),
        ):
            if int(value) <= 0:
                raise ConfigurationError(f"{name} must be a finite positive value")
        if self.artifact_max_bytes > self.artifact_total_bytes:
            raise ConfigurationError("ARTIFACT_MAX_BYTES must not exceed ARTIFACT_TOTAL_BYTES")
        if not 0.0 <= self.artifact_min_free_ratio < 1.0:
            raise ConfigurationError("IGNITION_MCP_ARTIFACT_MIN_FREE_RATIO must be in [0, 1)")
        if self.artifact_staging_deadline_seconds <= 0:
            raise ConfigurationError("IGNITION_MCP_ARTIFACT_STAGING_DEADLINE_SECONDS must be positive")
        if self.artifact_cleanup_interval_seconds <= 0:
            raise ConfigurationError("IGNITION_MCP_ARTIFACT_CLEANUP_INTERVAL_SECONDS must be positive")

    def budget_deadline_seconds(self, budget_class: str) -> float:
        return {
            "FAST": self.tool_timeout_seconds,
            "QUERY": self.query_timeout_seconds,
            "ARTIFACT": self.artifact_timeout_seconds,
        }[budget_class]

    @property
    def resolved_log_format(self) -> str:
        if self.log_format != "auto":
            return self.log_format
        return "text" if self.deployment_profile == "development" else "json"


def _bool_env(name: str) -> bool:
    raw = (os.getenv(name) or "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigurationError(f"{name} must be a boolean ('true' or 'false')")


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
