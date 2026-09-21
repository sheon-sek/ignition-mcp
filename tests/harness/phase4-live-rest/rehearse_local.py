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
    CREATED_RESOURCE,
    DEFAULT_CONTROL_PROJECT,
    DEFAULT_PROJECT,
    DEFAULT_TAG_CONTROL_PATH,
    DEFAULT_TAG_PROVIDER,
    DEFAULT_TAG_SOURCE_PATH,
    DEFAULT_TAG_TARGET_PATH,
    RENAMED_RESOURCE,
    TAG_IMPORT_TOOL,
    RENAME_SOURCE,
    RENAME_SOURCE_2,
    REFUSED_NAME,
    REFUSED_TYPE,
    SINGLETON_TYPE,
    UNALLOWLISTED_RENAMED,
    run_gate_off,
    run_gate_on,
)

RESOURCE_TYPE = "ignition/audit-profile"
#: The two Projects the import cases use: the allowlisted Target and a Project the
#: Target allowlist does not name. Both are provisioned live by the workflow.
PROJECT = DEFAULT_PROJECT
CONTROL_PROJECT = DEFAULT_CONTROL_PROJECT
#: The Tag surface the #17 cases address: the provider the live Gateway creates (and
#: whose source Tags ``provision.py`` publishes), the path they are published at, and
#: the destination the Target allowlist names.
TAG_PROVIDER = DEFAULT_TAG_PROVIDER
TAG_SOURCE_PATH = DEFAULT_TAG_SOURCE_PATH
TAG_TARGET_PATH = DEFAULT_TAG_TARGET_PATH
TAG_CONTROL_PATH = DEFAULT_TAG_CONTROL_PATH
ALLOWLISTED = "MCP_CI_AUDIT"
UNALLOWLISTED = "MCP_CI_AUDIT_OTHER"
READER_TOKEN = "phase4-rehearsal-reader"
AGENT_TOKEN = "phase4-rehearsal-agent"
#: The same per-Tool Target allowlists the live workflow configures: one entry list
#: per Mutation Tool, and every name a case addresses that must be allowed.
MUTATION_TARGETS = {
    "config_resource_update": (f"{RESOURCE_TYPE}/{ALLOWLISTED}", SINGLETON_TYPE),
    "config_resource_create": (
        f"{RESOURCE_TYPE}/{CREATED_RESOURCE}",
        f"{RESOURCE_TYPE}/{RENAME_SOURCE}",
        f"{RESOURCE_TYPE}/{RENAME_SOURCE_2}",
    ),
    "config_resource_delete": (
        f"{RESOURCE_TYPE}/{ALLOWLISTED}",
        f"{RESOURCE_TYPE}/{CREATED_RESOURCE}",
    ),
    "config_resource_rename": (
        f"{RESOURCE_TYPE}/{RENAME_SOURCE}",
        f"{RESOURCE_TYPE}/{RENAME_SOURCE_2}",
        f"{RESOURCE_TYPE}/{RENAMED_RESOURCE}",
    ),
    # D30 §6: the Target of the Project import is the Project itself.
    "project_import": (PROJECT,),
    # D30 §3/#17: the Target of a Tag import is the provider-qualified destination path.
    TAG_IMPORT_TOOL: (f"[{TAG_PROVIDER}]{TAG_TARGET_PATH}",),
}


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
        artifact_cleanup_batch=50, artifact_upload_enabled=True, sensitive_exports_enabled=True,
        config_mutation_enabled=mutation_enabled, control_mutation_enabled=False,
        admin_mutation_enabled=False, mutation_operations=tuple(MUTATION_TARGETS),
        mutation_targets=MUTATION_TARGETS,
        project_designer_policy="deny", gateway_id="phase4-rehearsal", project_writer_enabled=True,
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


def _project_archive(marker: str) -> bytes:
    """A Project archive in the shape the recorded Gateway serves on export."""

    import io
    import json
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            zipfile.ZipInfo("project.json", date_time=(1980, 1, 1, 0, 0, 0)),
            json.dumps({
                "title": f"Phase 4 REST rehearsal {marker}",
                "description": "fixture", "parent": "", "enabled": True, "inheritable": False,
            }, sort_keys=True).encode("utf-8"),
        )
        archive.writestr(
            zipfile.ZipInfo("ignition/named-query/mcp_probe/query.sql", date_time=(1980, 1, 1, 0, 0, 0)),
            b"SELECT 1",
        )
    return buffer.getvalue()


def _source_tags() -> list[dict[str, Any]]:
    """The same source Tag document ``provision.py`` publishes on the live Gateway."""

    return [
        {"name": "Folder", "tagType": "Folder", "tags": [
            {"name": "Int", "tagType": "AtomicTag", "valueSource": "memory",
             "dataType": "Int4", "value": 7, "enabled": True},
            {"name": "Inner", "tagType": "Folder", "tags": [
                {"name": "Text", "tagType": "AtomicTag", "valueSource": "memory",
                 "dataType": "String", "value": "p4-rest", "enabled": True},
            ]},
        ]},
        {"name": "Sibling", "tagType": "AtomicTag", "valueSource": "memory",
         "dataType": "String", "value": "keep", "enabled": True},
    ]


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
    # The rename sources: provisioned live, so the rename cases do not depend on the
    # create Tool working first. The name the create case publishes is deliberately
    # not seeded.
    for name in (RENAME_SOURCE, RENAME_SOURCE_2):
        gateway.seed_resource(
            RESOURCE_TYPE, name,
            config={"profile": {"type": "local", "retentionDays": 9}, "settings": {}},
            description="Disposable Phase 4 CI audit profile (rename source)",
        )
    # An allowed singleton: its documented change item carries no name.
    gateway.seed_resource(
        SINGLETON_TYPE, "cobranding", config={"enabled": True}, description="CI branding",
    )
    # The refused resource the live Gateway really has: its own CI API token.
    gateway.seed_resource(
        REFUSED_TYPE, REFUSED_NAME,
        config={"profile": {"type": "basic-token"}, "settings": {"tokenHash": "<redacted>"}},
        description="Disposable CI-only API token",
    )
    # The Tag provider the #17 cases address: its state is the source document
    # ``provision.py`` imports into ``source`` on the live Gateway, so an export of that
    # path serves the same Tags here as it does there.
    gateway.seed_tags(TAG_PROVIDER, [
        {"name": TAG_SOURCE_PATH, "tagType": "Folder", "tags": _source_tags()},
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("artifacts/phase4-rest-rehearsal"))
    args = parser.parse_args()

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    projects = {
        PROJECT: _project_archive("target"),
        CONTROL_PROJECT: _project_archive("control"),
    }
    with tempfile.TemporaryDirectory(prefix="phase4-rehearsal-") as data_dir, \
            RecordedGateway(projects=projects) as gateway:
        _seed(gateway)
        with _server(_settings(gateway, data_dir, mutation_enabled=True)) as url:
            gate_on = asyncio.run(run_gate_on(
                rest_url=url, reader_token=READER_TOKEN, agent_token=AGENT_TOKEN,
                resource_type=RESOURCE_TYPE, allowlisted=ALLOWLISTED,
                unallowlisted=UNALLOWLISTED, singleton_type=SINGLETON_TYPE,
                created_name=CREATED_RESOURCE, rename_source=RENAME_SOURCE,
                rename_source_2=RENAME_SOURCE_2, renamed_name=RENAMED_RESOURCE,
                unallowlisted_renamed=UNALLOWLISTED_RENAMED,
                project=PROJECT, control_project=CONTROL_PROJECT,
                tag_provider=TAG_PROVIDER, tag_source_path=TAG_SOURCE_PATH,
                tag_target_path=TAG_TARGET_PATH, tag_control_path=TAG_CONTROL_PATH,
                raw_dir=args.raw_dir,
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
