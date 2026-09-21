from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from ignition_rest_mcp.config import ConfigurationError, Settings

PERSISTENT_DEFAULT = "/var/lib/ignition-mcp-tests"


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
        "data_dir": str(Path(tempfile.mkdtemp(prefix="ignition-mcp-test-"))),
        "tool_timeout_seconds": 30.0,
        "query_timeout_seconds": 30.0,
        "artifact_timeout_seconds": 120.0,
        "audit_max_rows": 50_000,
        "audit_max_age_days": 90,
        "operation_record_max_rows": 10_000,
        "operation_record_max_age_hours": 72,
        "retention_interval_seconds": 300.0,
        "retention_batch_rows": 500,
        "storage_probe_interval_seconds": 30.0,
        "artifact_max_bytes": 268_435_456,
        "artifact_total_bytes": 1_073_741_824,
        "artifact_max_count": 1000,
        "artifact_min_free_bytes": 104_857_600,
        "artifact_min_free_ratio": 0.05,
        "artifact_export_ttl_hours": 24,
        "artifact_recovery_ttl_days": 7,
        "artifact_staging_deadline_seconds": 900.0,
        "artifact_cleanup_interval_seconds": 300.0,
        "artifact_cleanup_batch": 50,
        "artifact_upload_enabled": False,
        "sensitive_exports_enabled": False,
        "config_mutation_enabled": False,
        "control_mutation_enabled": False,
        "admin_mutation_enabled": False,
        "mutation_operations": (),
        "mutation_targets": {},
        "project_designer_policy": "deny",
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def _persistent(**changes: object) -> Settings:
    return _settings(data_dir=PERSISTENT_DEFAULT, **changes)


def test_development_rejects_non_loopback() -> None:
    settings = _settings(bind_host="0.0.0.0")
    with pytest.raises(ConfigurationError):
        settings.validate()


def test_trusted_internal_allows_explicit_non_loopback_none_auth() -> None:
    settings = _persistent(bind_host="0.0.0.0", deployment_profile="trusted-internal")
    settings.validate()


def test_static_token_requires_secret() -> None:
    settings = _settings(auth_mode="static-token")
    with pytest.raises(ConfigurationError):
        settings.validate()


def test_auto_log_format_uses_json_outside_development() -> None:
    settings = _persistent(deployment_profile="trusted-internal", bind_host="0.0.0.0")
    settings.validate()
    assert settings.resolved_log_format == "json"


def test_structured_output_limit_rejects_values_above_hard_ceiling() -> None:
    settings = _settings(structured_output_limit_bytes=1_048_577)
    with pytest.raises(ConfigurationError):
        settings.validate()


def test_data_dir_is_mandatory_in_every_profile() -> None:
    for profile in ("development", "trusted-internal", "secured"):
        kwargs: dict[str, object] = {"data_dir": "", "deployment_profile": profile}
        if profile == "secured":
            kwargs.update(auth_mode="jwt", jwt_public_key="pem", jwt_issuer="iss", jwt_audience="aud")
        elif profile == "trusted-internal":
            kwargs["bind_host"] = "10.0.0.5"
        with pytest.raises(ConfigurationError, match="IGNITION_MCP_DATA_DIR is required"):
            _settings(**kwargs).validate()


@pytest.mark.parametrize("prefix", ["/tmp", "/var/tmp", "/dev/shm"])
@pytest.mark.parametrize("profile", ["trusted-internal", "secured"])
def test_production_profiles_reject_temporary_filesystem_data_dirs(prefix: str, profile: str) -> None:
    kwargs: dict[str, object] = {
        "data_dir": f"{prefix}/ignition-mcp-state",
        "deployment_profile": profile,
    }
    if profile == "secured":
        kwargs.update(auth_mode="jwt", jwt_public_key="pem", jwt_issuer="iss", jwt_audience="aud")
    else:
        kwargs["bind_host"] = "10.0.0.5"
    with pytest.raises(ConfigurationError, match="temporary filesystem"):
        _settings(**kwargs).validate()


def test_development_allows_temporary_filesystem_data_dir() -> None:
    _settings(data_dir="/tmp/ignition-mcp-dev-state").validate()


def test_relative_data_dir_rejected() -> None:
    with pytest.raises(ConfigurationError, match="absolute"):
        _settings(data_dir="relative/state").validate()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_max_bytes", 0),
        ("artifact_total_bytes", -5),
        ("artifact_max_count", 0),
        ("artifact_min_free_bytes", 0),
        ("artifact_min_free_ratio", 1.0),
        ("artifact_export_ttl_hours", 0),
        ("artifact_recovery_ttl_days", 0),
        ("artifact_staging_deadline_seconds", 0),
        ("artifact_cleanup_interval_seconds", 0),
        ("artifact_cleanup_batch", 0),
        ("tool_timeout_seconds", 31),
        ("tool_timeout_seconds", 0),
        ("query_timeout_seconds", 121),
        ("artifact_timeout_seconds", 301),
        ("audit_max_rows", 0),
        ("audit_max_age_days", 0),
        ("operation_record_max_rows", -1),
        ("operation_record_max_age_hours", 0),
        ("retention_batch_rows", 0),
        ("retention_batch_rows", 10_001),
        ("retention_interval_seconds", 0),
        ("storage_probe_interval_seconds", -2),
    ],
)
def test_storage_and_budget_settings_fail_closed(field: str, value: object) -> None:
    with pytest.raises(ConfigurationError):
        _settings(**{field: value}).validate()


def test_budget_deadlines_map_to_d10_classes() -> None:
    settings = _settings(tool_timeout_seconds=9.0, query_timeout_seconds=25.0, artifact_timeout_seconds=100.0)
    settings.validate()
    assert settings.budget_deadline_seconds("FAST") == 9.0
    assert settings.budget_deadline_seconds("QUERY") == 25.0
    assert settings.budget_deadline_seconds("ARTIFACT") == 100.0
