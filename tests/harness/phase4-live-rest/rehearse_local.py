#!/usr/bin/env python3
"""Docker-free rehearsal of the Phase 4 REST live harness.

Starts the real ``ignition-rest`` server (in-process, on an ephemeral port) against
``tests/harness/recorded_gateway.py`` and drives the exact cases the live driver
drives, twice: once with ``CONFIG_MUTATION`` enabled and once with it disabled.

It rehearses everything the live run depends on except the Gateway itself
(``provision.py`` is exercised live only, because it creates resources through the
Gateway's own API), so a change to the driver, the gating or the server is caught
before a live CI run is spent.

    uv run --locked --package ignition-rest-mcp python tests/harness/phase4-live-rest/rehearse_local.py
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from typing import Any, Iterator

import uvicorn

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.config import Settings, StaticToken

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(HARNESS.parent))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402
from rest_driver import (  # noqa: E402
    REFUSED_NAME,
    REFUSED_TYPE,
    run_gate_off,
    run_gate_on,
)

RESOURCE_TYPE = "ignition/audit-profile"
ALLOWLISTED = "MCP_CI_AUDIT"
UNALLOWLISTED = "MCP_CI_AUDIT_OTHER"
READER_TOKEN = "phase4-rehearsal-reader"
AGENT_TOKEN = "phase4-rehearsal-agent"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _settings(gateway: RecordedGateway, data_dir: str, *, mutation_enabled: bool) -> Settings:
    return Settings(
        gateway_url=gateway.base_url, gateway_api_token=API_TOKEN, bind_host="127.0.0.1",
        bind_port=_free_port(), mcp_path="/mcp", deployment_profile="development",
        auth_mode="static-token",
        static_tokens=(
            StaticToken(name="rehearsal-reader", token=READER_TOKEN, scopes=("ignition.read",)),
            StaticToken(name="rehearsal-agent", token=AGENT_TOKEN,
                        scopes=("ignition.read", "ignition.config")),
        ),
        service_identity="phase4-rehearsal", watcher_interval_seconds=2.0, request_timeout_seconds=10.0,
        structured_output_limit_bytes=262_144, log_format="text", data_dir=data_dir,
        tool_timeout_seconds=30.0, query_timeout_seconds=30.0, artifact_timeout_seconds=120.0,
        audit_max_rows=50_000, audit_max_age_days=90, operation_record_max_rows=10_000,
        operation_record_max_age_hours=72, retention_interval_seconds=3600.0, retention_batch_rows=500,
        storage_probe_interval_seconds=3600.0, artifact_max_bytes=268_435_456,
        artifact_total_bytes=1_073_741_824, artifact_max_count=1000, artifact_min_free_bytes=104_857_600,
        artifact_min_free_ratio=0.05, artifact_export_ttl_hours=24, artifact_recovery_ttl_days=7,
        artifact_staging_deadline_seconds=900.0, artifact_cleanup_interval_seconds=3600.0,
        artifact_cleanup_batch=50, artifact_upload_enabled=False, sensitive_exports_enabled=False,
        config_mutation_enabled=mutation_enabled, control_mutation_enabled=False,
        admin_mutation_enabled=False, mutation_operations=("config_resource_update",),
        mutation_targets={"config_resource_update": (f"{RESOURCE_TYPE}/{ALLOWLISTED}",)},
        project_designer_policy="deny", gateway_id="phase4-rehearsal", project_writer_enabled=False,
        project_lock_timeout_seconds=10.0, project_lock_max_entries=32,
        project_reconcile_interval_seconds=3600.0, project_verification_timeout_seconds=60.0,
    )


@contextmanager
def _server(settings: Settings) -> Iterator[str]:
    """The real server, over real Streamable HTTP, on an ephemeral port."""

    mcp = server_module.create_server(settings)
    app = mcp.http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=settings.bind_port, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    try:
        deadline = threading.Event()
        for _ in range(300):
            if instance.started:
                break
            deadline.wait(0.1)
        if not instance.started:
            raise RuntimeError("the rehearsal server did not start")
        yield f"http://127.0.0.1:{settings.bind_port}"
    finally:
        instance.should_exit = True
        thread.join(timeout=15)


def _seed(gateway: RecordedGateway) -> None:
    gateway.seed_resource(
        RESOURCE_TYPE, ALLOWLISTED,
        config={"profile": {"type": "local", "retentionDays": 14}, "settings": {}},
        description="Disposable Phase 4 CI audit profile (allowlisted Target)",
    )
    gateway.seed_resource(
        RESOURCE_TYPE, UNALLOWLISTED,
        config={"profile": {"type": "local"}, "settings": {}},
        description="Disposable Phase 4 CI audit profile (allowlist control)",
    )
    # The refused resource the live Gateway really has: its own CI API token.
    gateway.seed_resource(
        REFUSED_TYPE, REFUSED_NAME,
        config={"profile": {"type": "basic-token"}, "settings": {"tokenHash": "<redacted>"}},
        description="Disposable CI-only API token",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("artifacts/phase4-rest-rehearsal"))
    args = parser.parse_args()

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phase4-rehearsal-") as data_dir, RecordedGateway() as gateway:
        _seed(gateway)
        with _server(_settings(gateway, data_dir, mutation_enabled=True)) as url:
            gate_on = asyncio.run(run_gate_on(
                rest_url=url, reader_token=READER_TOKEN, agent_token=AGENT_TOKEN,
                resource_type=RESOURCE_TYPE, allowlisted=ALLOWLISTED,
                unallowlisted=UNALLOWLISTED, raw_dir=args.raw_dir,
            ))
        with _server(_settings(gateway, data_dir, mutation_enabled=False)) as url:
            gate_off = asyncio.run(run_gate_off(
                rest_url=url, agent_token=AGENT_TOKEN, raw_dir=args.raw_dir,
            ))

    cases: list[dict[str, Any]] = [*gate_on["cases"], *gate_off["cases"]]
    report = {
        "schemaVersion": 1, "gate": "G4", "milestone": "4c", "rehearsal": True,
        "cases": cases, "passed": all(case["ok"] for case in cases),
    }
    (args.raw_dir / "rehearsal.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    for case in cases:
        print(f"{'ok  ' if case['ok'] else 'FAIL'} {case['case']}: {json.dumps(case['observed'])[:160]}")
    print(f"rehearsal: {sum(1 for case in cases if case['ok'])}/{len(cases)} cases passed")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
