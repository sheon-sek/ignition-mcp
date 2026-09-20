"""Environment-backed configuration with fail-closed deployment profiles."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from urllib.parse import urlparse


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
        if self.auth_mode not in {"none", "static-token"}:
            raise ConfigurationError("Phase 1 supports auth modes none and static-token only")
        if self.auth_mode == "static-token" and not self.static_token:
            raise ConfigurationError("Static-token mode requires IGNITION_MCP_STATIC_TOKEN")
        if self.deployment_profile == "secured":
            raise ConfigurationError(
                "secured profile requires JWT/OAuth configuration not yet enabled in the Phase 1 slice"
            )
        if self.deployment_profile == "development" and not _is_loopback(self.bind_host):
            raise ConfigurationError("development profile may bind only to loopback")
        if (
            self.deployment_profile != "trusted-internal"
            and self.auth_mode == "none"
            and not _is_loopback(self.bind_host)
        ):
            raise ConfigurationError("Unauthenticated non-loopback binding requires trusted-internal profile")


    @property
    def resolved_log_format(self) -> str:
        if self.log_format != "auto":
            return self.log_format
        return "text" if self.deployment_profile == "development" else "json"


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
