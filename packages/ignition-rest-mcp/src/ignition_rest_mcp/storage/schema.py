"""Forward-only SQLite schema migrations for the shared data directory.

Each database owns an ordered DDL list. Later slices append new versions here;
a stored version above the known count is a fail-closed error (never a silent
downgrade or partial open).
"""

STATE_DDL: list[str] = [
    # 1: D18 operation records
    """
    CREATE TABLE IF NOT EXISTS operation_records (
        correlation_id TEXT PRIMARY KEY,
        tool TEXT NOT NULL,
        permission_class TEXT NOT NULL,
        budget_class TEXT NOT NULL,
        destructive INTEGER NOT NULL,
        principal_key TEXT NOT NULL,
        transaction_id TEXT,
        client_request_id TEXT,
        status TEXT NOT NULL,
        outcome TEXT,
        error_code TEXT,
        phases_json TEXT NOT NULL DEFAULT '[]',
        phases_truncated INTEGER NOT NULL DEFAULT 0,
        audit_result_missing INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        downstream_correlation_id TEXT
    );
    CREATE INDEX IF NOT EXISTS operation_records_started ON operation_records(started_at);
    """,
    # 2: D17 artifact metadata (Slice 2)
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        state TEXT NOT NULL,
        filename TEXT NOT NULL,
        media_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        sha256 TEXT NOT NULL DEFAULT '',
        sensitivity TEXT NOT NULL,
        retention_class TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        owner_principal TEXT NOT NULL,
        gateway_id TEXT NOT NULL DEFAULT '',
        project_name TEXT NOT NULL DEFAULT '',
        correlation_id TEXT NOT NULL,
        transaction_id TEXT NOT NULL DEFAULT '',
        retention_lock INTEGER NOT NULL DEFAULT 0,
        reserved_bytes INTEGER NOT NULL DEFAULT 0,
        staging_deadline_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS artifacts_state_expiry ON artifacts(state, expires_at);
    CREATE INDEX IF NOT EXISTS artifacts_owner ON artifacts(owner_principal, state);
    """,
]

AUDIT_DDL: list[str] = [
    # 1: D18 audit rows (decision / attempt / result phases)
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        server TEXT NOT NULL,
        tool TEXT NOT NULL,
        actor_key TEXT NOT NULL,
        operation_class TEXT NOT NULL,
        destructive INTEGER NOT NULL,
        target_type TEXT,
        target_id TEXT,
        phase TEXT NOT NULL,
        outcome TEXT,
        duration_ms REAL,
        error_code TEXT,
        transaction_id TEXT,
        safe_fields_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS audit_log_timestamp ON audit_log(timestamp);
    CREATE INDEX IF NOT EXISTS audit_log_correlation ON audit_log(correlation_id);
    """,
]
