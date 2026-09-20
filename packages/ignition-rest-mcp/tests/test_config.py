from __future__ import annotations

import pytest

from ignition_rest_mcp.config import ConfigurationError, Settings


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "gateway_url": "http://127.0.0.1:8088",
        "gateway_api_token": "name:key",
        "bind_host": "127.0.0.1",
        "bind_port": 8000,
        "mcp_path": "/mcp",
        "deployment_profile": "development",
        "auth_mode": "none",
        "static_token": None,
        "service_identity": "test",
        "watcher_interval_seconds": 60.0,
        "request_timeout_seconds": 10.0,
        "structured_output_limit_bytes": 262_144,
        "log_format": "auto",
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def test_development_rejects_non_loopback() -> None:
    settings = _settings(bind_host="0.0.0.0")
    with pytest.raises(ConfigurationError):
        settings.validate()


def test_trusted_internal_allows_explicit_non_loopback_none_auth() -> None:
    settings = _settings(bind_host="0.0.0.0", deployment_profile="trusted-internal")
    settings.validate()


def test_static_token_requires_secret() -> None:
    settings = _settings(auth_mode="static-token")
    with pytest.raises(ConfigurationError):
        settings.validate()


def test_auto_log_format_uses_json_outside_development() -> None:
    settings = _settings(deployment_profile="trusted-internal", bind_host="0.0.0.0")
    settings.validate()
    assert settings.resolved_log_format == "json"


def test_structured_output_limit_rejects_values_above_hard_ceiling() -> None:
    settings = _settings(structured_output_limit_bytes=1_048_577)
    with pytest.raises(ConfigurationError):
        settings.validate()
