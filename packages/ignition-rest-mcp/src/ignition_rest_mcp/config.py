"""Environment-backed configuration with fail-closed deployment profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse

TEMP_FILESYSTEM_PREFIXES = ("/tmp", "/var/tmp", "/dev/shm")


def temp_filesystem_prefixes() -> tuple[str, ...]:
    """Temporary-filesystem prefixes refused for persistent data. On Windows the
    platform temp directory is added; on POSIX it is not, because
    ``tempfile.gettempdir()`` honours ``TMPDIR`` there."""

    if sys.platform == "win32":
        return (*TEMP_FILESYSTEM_PREFIXES, tempfile.gettempdir())
    return TEMP_FILESYSTEM_PREFIXES

# D07 canonical authorization scopes. No hierarchy: membership is the only rule.
READ_SCOPE = "ignition.read"
CONFIG_SCOPE = "ignition.config"
CONTROL_SCOPE = "ignition.control"
ADMIN_SCOPE = "ignition.admin"
CANONICAL_SCOPES = (READ_SCOPE, CONFIG_SCOPE, CONTROL_SCOPE, ADMIN_SCOPE)

#: Name of the single token configured by the legacy ``IGNITION_MCP_STATIC_TOKEN``.
#: It preserves the Phase 1–3 principal name so audit rows, operation records and
#: artifact owners recorded before the upgrade stay attributable to the same
#: principal.
LEGACY_STATIC_TOKEN_NAME = "trusted-internal-static-token"

MAX_STATIC_TOKENS = 32
MAX_STATIC_TOKEN_BYTES = 512


class ConfigurationError(ValueError):
    """Invalid or unsafe server configuration."""


@dataclass(frozen=True, slots=True)
class StaticToken:
    """One named static token and the scopes the deployment grants it (D07).

    The name — never the value — is the Mutation principal. ``token`` is excluded
    from ``repr`` so a logged or dumped Settings object cannot leak it.
    """

    name: str
    token: str = field(repr=False)
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Settings:
    gateway_url: str
    gateway_api_token: str
    bind_host: str
    bind_port: int
    mcp_path: str
    deployment_profile: str
    auth_mode: str
    static_tokens: tuple[StaticToken, ...]
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
    config_mutation_enabled: bool
    control_mutation_enabled: bool
    admin_mutation_enabled: bool
    mutation_operations: tuple[str, ...]
    mutation_targets: dict[str, tuple[str, ...]]
    project_designer_policy: str
    gateway_id: str
    project_writer_enabled: bool
    project_lock_timeout_seconds: float
    project_lock_max_entries: int
    project_reconcile_interval_seconds: float
    project_verification_timeout_seconds: float
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
            static_tokens=_static_tokens_env(),
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
            config_mutation_enabled=_bool_env("IGNITION_MCP_CONFIG_MUTATION_ENABLED"),
            control_mutation_enabled=_bool_env("IGNITION_MCP_CONTROL_MUTATION_ENABLED"),
            admin_mutation_enabled=_bool_env("IGNITION_MCP_ADMIN_MUTATION_ENABLED"),
            mutation_operations=_csv_env("IGNITION_MCP_MUTATION_OPERATIONS"),
            mutation_targets=_targets_env("IGNITION_MCP_MUTATION_TARGETS"),
            project_designer_policy=(os.getenv("IGNITION_MCP_PROJECT_DESIGNER_POLICY") or "deny").strip().lower(),
            gateway_id=(os.getenv("IGNITION_MCP_GATEWAY_ID") or "").strip(),
            project_writer_enabled=_bool_env("IGNITION_MCP_PROJECT_WRITER_ENABLED"),
            project_lock_timeout_seconds=float(os.getenv("IGNITION_MCP_PROJECT_LOCK_TIMEOUT_SECONDS", "10")),
            project_lock_max_entries=int(os.getenv("IGNITION_MCP_PROJECT_LOCK_MAX_ENTRIES", "32")),
            project_reconcile_interval_seconds=float(
                os.getenv("IGNITION_MCP_PROJECT_RECONCILE_INTERVAL_SECONDS", "60")
            ),
            project_verification_timeout_seconds=float(
                os.getenv("IGNITION_MCP_PROJECT_VERIFICATION_TIMEOUT_SECONDS", "60")
            ),
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
        if self.auth_mode == "static-token" and not self.static_tokens:
            raise ConfigurationError(
                "Static-token mode requires at least one token in IGNITION_MCP_STATIC_TOKENS "
                "(or the single-token IGNITION_MCP_STATIC_TOKEN)"
            )
        self._validate_static_tokens()
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

    def _validate_static_tokens(self) -> None:
        # D07 Phase 4 amendment: named static tokens with per-token scopes, no
        # hierarchy. Every rule fails closed at startup rather than at first call.
        if not self.static_tokens:
            return
        if self.auth_mode != "static-token":
            raise ConfigurationError(
                "IGNITION_MCP_STATIC_TOKENS is configured but IGNITION_MCP_AUTH_MODE is not static-token"
            )
        if len(self.static_tokens) > MAX_STATIC_TOKENS:
            raise ConfigurationError(f"IGNITION_MCP_STATIC_TOKENS accepts at most {MAX_STATIC_TOKENS} tokens")
        names: set[str] = set()
        values: set[str] = set()
        for entry in self.static_tokens:
            if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", entry.name) is None:
                raise ConfigurationError(
                    "static token name must be 1-64 characters of [A-Za-z0-9._:-]",
                )
            if entry.name in names:
                raise ConfigurationError(f"duplicate token name: {entry.name}")
            names.add(entry.name)
            if not entry.token.strip() or len(entry.token) > MAX_STATIC_TOKEN_BYTES:
                raise ConfigurationError(
                    f"static token {entry.name}: token value must be non-empty (never only "
                    f"whitespace) and at most {MAX_STATIC_TOKEN_BYTES} characters"
                )
            if entry.token in values:
                raise ConfigurationError(
                    "two static tokens share one token value; the principal would be ambiguous"
                )
            values.add(entry.token)
            if not entry.scopes:
                raise ConfigurationError(f"static token {entry.name} needs at least one scope")
            if len(set(entry.scopes)) != len(entry.scopes):
                raise ConfigurationError(f"static token {entry.name}: duplicate scope")
            unknown = sorted({scope for scope in entry.scopes if scope not in CANONICAL_SCOPES})
            if unknown:
                raise ConfigurationError(
                    f"static token {entry.name}: unknown scope(s) {unknown}; "
                    f"canonical scopes are {list(CANONICAL_SCOPES)}"
                )

    def _validate_storage(self) -> None:
        # D17/D18: the persistent data directory is mandatory in every profile.
        if not self.data_dir or not self.data_dir.strip():
            raise ConfigurationError("IGNITION_MCP_DATA_DIR is required in every deployment profile")
        if not Path(self.data_dir).is_absolute():
            raise ConfigurationError("IGNITION_MCP_DATA_DIR must be an absolute path")
        if self.deployment_profile in {"trusted-internal", "secured"}:
            normalized = os.path.normcase(os.path.normpath(self.data_dir))
            for prefix in temp_filesystem_prefixes():
                folded = os.path.normcase(os.path.normpath(prefix))
                if normalized == folded or normalized.startswith(folded.rstrip(os.sep) + os.sep):
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
        self._validate_mutation_policy()

    def _validate_mutation_policy(self) -> None:
        # D08: every mutation class defaults disabled; allowlists are explicit and bounded.
        if len(self.mutation_operations) > 100:
            raise ConfigurationError("IGNITION_MCP_MUTATION_OPERATIONS accepts at most 100 operations")
        for operation in self.mutation_operations:
            if len(operation) > 64 or not re.fullmatch(r"[A-Za-z0-9_*.-]+", operation):
                raise ConfigurationError(f"invalid mutation operation id: {operation[:70]!r}")
        if len(self.mutation_targets) > 100:
            raise ConfigurationError("IGNITION_MCP_MUTATION_TARGETS accepts at most 100 operations")
        for operation, targets in self.mutation_targets.items():
            if len(operation) > 64 or len(targets) > 500:
                raise ConfigurationError(f"invalid mutation target list for {operation[:70]!r}")
            for target in targets:
                if len(target) > 256:
                    raise ConfigurationError("mutation target entries must be <=256 characters")
        if self.project_designer_policy not in {"deny", "warn", "ignore"}:
            raise ConfigurationError("IGNITION_MCP_PROJECT_DESIGNER_POLICY must be deny, warn, or ignore")
        if self.gateway_id and (
            len(self.gateway_id) > 128 or re.fullmatch(r"[A-Za-z0-9._:-]+", self.gateway_id) is None
        ):
            raise ConfigurationError(
                "IGNITION_MCP_GATEWAY_ID must be <=128 characters of [A-Za-z0-9._:-]"
            )
        if self.project_writer_enabled and not self.gateway_id:
            raise ConfigurationError(
                "IGNITION_MCP_GATEWAY_ID is required when IGNITION_MCP_PROJECT_WRITER_ENABLED=true "
                "(one stable operator-chosen ID per Gateway, identical across replicas of that Gateway)"
            )
        if not 0 < self.project_lock_timeout_seconds <= 300:
            raise ConfigurationError("Project lock acquire timeout must be >0 and <=300 seconds")
        if self.project_lock_max_entries < 1:
            raise ConfigurationError("Project lock registry capacity must be >=1")
        if self.project_reconcile_interval_seconds <= 0:
            raise ConfigurationError("Project reconcile interval must be positive")
        if not 0 < self.project_verification_timeout_seconds <= 300:
            raise ConfigurationError("Project verification timeout must be >0 and <=300 seconds")

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


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name) or ""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _targets_env(name: str) -> dict[str, tuple[str, ...]]:
    raw = os.getenv(name) or ""
    if not raw:
        return {}
    if len(raw.encode("utf-8")) > 32 * 1024:
        raise ConfigurationError(f"{name} exceeds the 32 KiB bound")
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a JSON object mapping operation to target list") from error
    if type(value) is not dict:
        raise ConfigurationError(f"{name} must be a JSON object")
    result: dict[str, tuple[str, ...]] = {}
    for operation, targets in value.items():
        if not isinstance(operation, str) or not isinstance(targets, list) or any(
            not isinstance(target, str) for target in targets
        ):
            raise ConfigurationError(f"{name} values must be string lists keyed by operation")
        result[operation] = tuple(targets)
    return result


class _DuplicateKeyError(ValueError):
    """A JSON document repeated an object key, so its last value would silently win."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads`` object hook that refuses a repeated key.

    ``json.loads`` keeps only the last value of a repeated key, so a document that
    grants one name two different scope sets would start successfully under the
    narrowest or widest grant by accident. Configuration JSON is operator-authored
    and small: an ambiguous key is rejected instead of resolved.
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _static_tokens_env() -> tuple[StaticToken, ...]:
    """D07: named static tokens. The legacy single-token variable stays supported
    as one token named :data:`LEGACY_STATIC_TOKEN_NAME` with ``ignition.read``,
    verified exactly as given (the credential is never trimmed)."""

    legacy = os.getenv("IGNITION_MCP_STATIC_TOKEN")
    raw = os.getenv("IGNITION_MCP_STATIC_TOKENS") or ""
    if raw and legacy:
        raise ConfigurationError(
            "Set IGNITION_MCP_STATIC_TOKENS or the single-token IGNITION_MCP_STATIC_TOKEN, not both"
        )
    if legacy is not None:
        if not legacy.strip():
            raise ConfigurationError(
                "IGNITION_MCP_STATIC_TOKEN must not be empty or whitespace-only; "
                "unset it to run without a static token"
            )
        return (StaticToken(name=LEGACY_STATIC_TOKEN_NAME, token=legacy, scopes=(READ_SCOPE,)),)
    if not raw:
        return ()
    if len(raw.encode("utf-8")) > 32 * 1024:
        raise ConfigurationError("IGNITION_MCP_STATIC_TOKENS exceeds the 32 KiB bound")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except _DuplicateKeyError as error:
        raise ConfigurationError(
            f"IGNITION_MCP_STATIC_TOKENS repeats the key {error.key!r}; "
            "one name must appear exactly once"
        ) from error
    except ValueError as error:
        raise ConfigurationError(
            "IGNITION_MCP_STATIC_TOKENS must be a JSON object mapping token name to {token, scopes}"
        ) from error
    if type(value) is not dict:
        raise ConfigurationError("IGNITION_MCP_STATIC_TOKENS must be a JSON object")
    tokens: list[StaticToken] = []
    for name, entry in value.items():
        if (
            not isinstance(name, str)
            or type(entry) is not dict
            or set(entry) != {"token", "scopes"}
            or not isinstance(entry["token"], str)
        ):
            raise ConfigurationError(
                "IGNITION_MCP_STATIC_TOKENS values must be objects with exactly a string 'token' "
                "and a 'scopes' list, keyed by token name"
            )
        scopes = entry["scopes"]
        if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
            raise ConfigurationError("IGNITION_MCP_STATIC_TOKENS scopes must be a list of strings")
        tokens.append(StaticToken(name=name, token=entry["token"], scopes=tuple(scopes)))
    return tuple(tokens)


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
