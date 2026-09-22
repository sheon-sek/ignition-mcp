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
from fault_proxy import FaultProxy  # noqa: E402
from provision import child_project_archive, parent_project_archive  # noqa: E402
from rest_driver import (  # noqa: E402
    ALARM_CANCEL_TOOL,
    CREATED_RESOURCE,
    DEFAULT_ALARM_EVENT_ID,
    DEFAULT_LOOKALIKE_PROVIDER,
    DEFAULT_OTHER_PROVIDER,
    DEFAULT_PERSPECTIVE_CHILD,
    DEFAULT_PERSPECTIVE_PARENT,
    DEFAULT_PROVIDER_RESOURCE_TYPE,
    DEFAULT_CONTROL_PIPELINE,
    DEFAULT_CONTROL_PROJECT,
    DEFAULT_PIPELINE,
    DEFAULT_PROJECT,
    DEFAULT_TAG_CONTROL_PATH,
    DEFAULT_RESERVED_TAG_PROVIDER,
    DEFAULT_TAG_PROVIDER,
    DEFAULT_TAG_SOURCE_PATH,
    DEFAULT_TAG_TARGET_PATH,
    PERSPECTIVE_PAGE_CONFIG_UPDATE_TOOL,
    PERSPECTIVE_SESSION_PROPS_UPDATE_TOOL,
    PERSPECTIVE_VIEW_DELETE_TOOL,
    PERSPECTIVE_VIEW_UPSERT_TOOL,
    RENAMED_RESOURCE,
    TAG_IMPORT_TOOL,
    RENAME_SOURCE,
    RENAME_SOURCE_2,
    REFUSED_NAME,
    REFUSED_TYPE,
    SINGLETON_TYPE,
    UNALLOWLISTED_RENAMED,
    run_fault_mode,
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
#: The Tag-provider *config resources* the ticket #36 cases address: the reserved provider
#: the Runtime Target Policy lives in, an ordinary provider, and a longer name that only
#: begins with the reserved one.
PROVIDER_TYPE = DEFAULT_PROVIDER_RESOURCE_TYPE
RESERVED_PROVIDER = DEFAULT_RESERVED_TAG_PROVIDER
OTHER_PROVIDER = DEFAULT_OTHER_PROVIDER
LOOKALIKE_PROVIDER = DEFAULT_LOOKALIKE_PROVIDER
#: The pipeline surface the #18 cases address: the exact path the Target allowlist
#: names (in the Project the import cases use, so it is run-unique live), a second
#: pipeline it does not name, and an alarm event no run holds.
PIPELINE = DEFAULT_PIPELINE
CONTROL_PIPELINE = DEFAULT_CONTROL_PIPELINE
ALARM_EVENT_ID = DEFAULT_ALARM_EVENT_ID
#: Phase 5 (P5-3): the Perspective fixture, the parent Project whose View the child
#: inherits, and the child the write allowlist names. Both are seeded as archives here
#: and imported by ``provision.py`` live.
PERSPECTIVE_PARENT = DEFAULT_PERSPECTIVE_PARENT
PERSPECTIVE_CHILD = DEFAULT_PERSPECTIVE_CHILD
ALLOWLISTED = "MCP_CI_AUDIT"
UNALLOWLISTED = "MCP_CI_AUDIT_OTHER"
READER_TOKEN = "phase4-rehearsal-reader"
AGENT_TOKEN = "phase4-rehearsal-agent"
OPERATOR_TOKEN = "phase4-rehearsal-operator"
#: The fault-mode deployment's budgets (ticket #20). Smaller than a production one so a
#: case does not wait out a full deadline, and matched by the live workflow's fault
#: instance; the proxy delays past *this* budget, which is the rule under test.
FAULT_TOOL_TIMEOUT = 8.0
FAULT_ARTIFACT_TIMEOUT = 20.0
#: The D16 reconcile interval the fault mode runs with, so a cancelled import settles
#: inside the case instead of at the next restart.
FAULT_RECONCILE_INTERVAL = 2.0
#: The same per-Tool Target allowlists the live workflow configures: one entry list
#: per Mutation Tool, and every name a case addresses that must be allowed.
MUTATION_TARGETS = {
    # Ticket #36: the Tag-provider resources are deliberately allowlisted — including the
    # reserved one the four config Tools must refuse — so a `permission_denied` in those
    # cases is the name rule and not the Target allowlist.
    "config_resource_update": (
        f"{RESOURCE_TYPE}/{ALLOWLISTED}", SINGLETON_TYPE,
        f"{PROVIDER_TYPE}/{RESERVED_PROVIDER}", f"{PROVIDER_TYPE}/{OTHER_PROVIDER}",
        f"{PROVIDER_TYPE}/{LOOKALIKE_PROVIDER}",
    ),
    "config_resource_create": (
        f"{RESOURCE_TYPE}/{CREATED_RESOURCE}",
        f"{RESOURCE_TYPE}/{RENAME_SOURCE}",
        f"{RESOURCE_TYPE}/{RENAME_SOURCE_2}",
        f"{PROVIDER_TYPE}/{RESERVED_PROVIDER}", f"{PROVIDER_TYPE}/{OTHER_PROVIDER}",
        f"{PROVIDER_TYPE}/{LOOKALIKE_PROVIDER}",
    ),
    "config_resource_delete": (
        f"{RESOURCE_TYPE}/{ALLOWLISTED}",
        f"{RESOURCE_TYPE}/{CREATED_RESOURCE}",
        f"{PROVIDER_TYPE}/{RESERVED_PROVIDER}", f"{PROVIDER_TYPE}/{OTHER_PROVIDER}",
    ),
    "config_resource_rename": (
        f"{RESOURCE_TYPE}/{RENAME_SOURCE}",
        f"{RESOURCE_TYPE}/{RENAME_SOURCE_2}",
        f"{RESOURCE_TYPE}/{RENAMED_RESOURCE}",
        f"{PROVIDER_TYPE}/{RESERVED_PROVIDER}", f"{PROVIDER_TYPE}/{OTHER_PROVIDER}",
    ),
    # D30 §6: the Target of the Project import is the Project itself.
    "project_import": (PROJECT,),
    # D30 §3/#17: the Target of a Tag import is the provider-qualified destination path.
    TAG_IMPORT_TOOL: (f"[{TAG_PROVIDER}]{TAG_TARGET_PATH}",),
    # D30 §3/#19: the Target of an artifact removal is the artifact's own storage
    # identifier, which is generated at removal time — so the deployment writes the
    # explicit wildcard, and ownership (D30 §6) is what bounds it.
    "artifact_delete": ("*",),
    # D30 §6/#18: the Target of a pipeline cancel is the exact pipeline path.
    ALARM_CANCEL_TOOL: (PIPELINE,),
    # Phase 5 (P5-3): every Perspective write addresses the child Project, which is also
    # the Project the inherited-resource case writes *into*, so the refusal there is the
    # inherited rule and never the allowlist: this Project is authorized.
    PERSPECTIVE_VIEW_UPSERT_TOOL: (PERSPECTIVE_CHILD,),
    PERSPECTIVE_VIEW_DELETE_TOOL: (PERSPECTIVE_CHILD,),
    PERSPECTIVE_PAGE_CONFIG_UPDATE_TOOL: (PERSPECTIVE_CHILD,),
    PERSPECTIVE_SESSION_PROPS_UPDATE_TOOL: (PERSPECTIVE_CHILD,),
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _settings(
    gateway_url: str, data_dir: str, *, mutation_enabled: bool, fault: bool = False,
) -> Settings:
    """The deployment the rehearsal runs, optionally the fault mode's own.

    ``fault`` is the D23 injected-failure deployment (ticket #20): it points its Gateway
    URL at the fault proxy, and its budgets are small so a case does not have to wait out
    a production deadline — the proxy delays past *this* deployment's deadline, which is
    the same rule a real one follows.
    """

    return Settings(
        gateway_url=gateway_url, gateway_api_token=API_TOKEN, bind_host="127.0.0.1",
        bind_port=_free_port(), mcp_path="/mcp", deployment_profile="development",
        auth_mode="static-token",
        static_tokens=(
            StaticToken(name="rehearsal-reader", token=READER_TOKEN, scopes=("ignition.read",)),
            StaticToken(name="rehearsal-agent", token=AGENT_TOKEN,
                        scopes=("ignition.read", "ignition.config")),
            StaticToken(name="rehearsal-operator", token=OPERATOR_TOKEN,
                        scopes=("ignition.read", "ignition.control")),
        ),
        service_identity="phase4-rehearsal", watcher_interval_seconds=2.0, request_timeout_seconds=10.0,
        structured_output_limit_bytes=262_144, log_format="text", data_dir=data_dir,
        tool_timeout_seconds=FAULT_TOOL_TIMEOUT if fault else 30.0, query_timeout_seconds=30.0,
        artifact_timeout_seconds=FAULT_ARTIFACT_TIMEOUT if fault else 120.0,
        audit_max_rows=50_000, audit_max_age_days=90, operation_record_max_rows=10_000,
        operation_record_max_age_hours=72, retention_interval_seconds=3600.0, retention_batch_rows=500,
        storage_probe_interval_seconds=3600.0, artifact_max_bytes=268_435_456,
        artifact_total_bytes=1_073_741_824, artifact_max_count=1000, artifact_min_free_bytes=104_857_600,
        artifact_min_free_ratio=0.05, artifact_export_ttl_hours=24, artifact_recovery_ttl_days=7,
        artifact_staging_deadline_seconds=900.0, artifact_cleanup_interval_seconds=3600.0,
        artifact_cleanup_batch=50, artifact_upload_enabled=True, sensitive_exports_enabled=True,
        config_mutation_enabled=mutation_enabled, control_mutation_enabled=mutation_enabled,
        admin_mutation_enabled=False, mutation_operations=tuple(MUTATION_TARGETS),
        mutation_targets=MUTATION_TARGETS,
        project_designer_policy="deny", gateway_id="phase4-rehearsal", project_writer_enabled=True,
        project_lock_timeout_seconds=10.0, project_lock_max_entries=32,
        project_reconcile_interval_seconds=FAULT_RECONCILE_INTERVAL if fault else 3600.0,
        project_verification_timeout_seconds=60.0,
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


@contextmanager
def _fault_proxy(upstream_url: str) -> Iterator[tuple[str, str]]:
    """The fault proxy (ticket #20) in front of the recorded Gateway.

    The proxy is real TCP code, so a rehearsal exercises the same transport failures a
    live run does; only the Gateway behind it is recorded.
    """

    host, port = upstream_url.split("//", 1)[1].split("/", 1)[0].split(":")
    proxy = FaultProxy(
        listen_host="127.0.0.1", listen_port=_free_port(), control_host="127.0.0.1",
        control_port=0, upstream_host=host, upstream_port=int(port),
    )
    ready = threading.Event()
    loop_holder: dict[str, Any] = {}

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_holder["loop"] = loop
        loop.create_task(proxy.serve())

        async def announce() -> None:
            while proxy.control_port == 0 or not proxy.state.listening:
                await asyncio.sleep(0.02)
            ready.set()

        loop.create_task(announce())
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        if not ready.wait(10):
            raise RuntimeError("the fault proxy did not start")
        yield f"http://127.0.0.1:{proxy.listen_port}", proxy.control_address
    finally:
        loop = loop_holder.get("loop")
        if loop is not None:
            loop.call_soon_threadsafe(proxy.stop)
        thread.join(timeout=10)


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


def _perspective_archives() -> dict[str, bytes]:
    """The two Perspective Projects, in the layout a real export uses.

    The parent defines the View the child does not define locally (so the child inherits
    it), and the child defines its own View, a Page configuration, Session properties and
    one unrelated resource. These are ``provision.py``'s own archive builders, so the
    rehearsal seeds byte-for-byte the fixture a live run imports.
    """

    return {
        PERSPECTIVE_PARENT: parent_project_archive("Phase 5 REST rehearsal parent"),
        PERSPECTIVE_CHILD: child_project_archive(
            "Phase 5 REST rehearsal child", PERSPECTIVE_PARENT,
        ),
    }


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
    # Ticket #36: the three Tag-provider resources the reserved-name cases address. The
    # reserved one is the provider the Runtime Target Policy lives in; the other two prove
    # the refusal is by name inside an allowed type.
    for provider, description in (
        (RESERVED_PROVIDER, "CI policy provider"),
        (OTHER_PROVIDER, "CI Tag provider"),
        (LOOKALIKE_PROVIDER, "CI Tag provider (not the reserved name)"),
    ):
        gateway.seed_resource(
            PROVIDER_TYPE, provider,
            config={"profile": {"type": "STANDARD"}, "settings": {}}, description=description,
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
        **_perspective_archives(),
    }
    with tempfile.TemporaryDirectory(prefix="phase4-rehearsal-") as data_dir, \
            RecordedGateway(projects=projects) as gateway:
        _seed(gateway)
        with _server(_settings(gateway.base_url, data_dir, mutation_enabled=True)) as url:
            gate_on = asyncio.run(run_gate_on(
                rest_url=url, reader_token=READER_TOKEN, agent_token=AGENT_TOKEN,
                operator_token=OPERATOR_TOKEN,
                resource_type=RESOURCE_TYPE, allowlisted=ALLOWLISTED,
                unallowlisted=UNALLOWLISTED, singleton_type=SINGLETON_TYPE,
                created_name=CREATED_RESOURCE, rename_source=RENAME_SOURCE,
                rename_source_2=RENAME_SOURCE_2, renamed_name=RENAMED_RESOURCE,
                unallowlisted_renamed=UNALLOWLISTED_RENAMED,
                project=PROJECT, control_project=CONTROL_PROJECT,
                tag_provider=TAG_PROVIDER, tag_source_path=TAG_SOURCE_PATH,
                tag_target_path=TAG_TARGET_PATH, tag_control_path=TAG_CONTROL_PATH,
                pipeline=PIPELINE, control_pipeline=CONTROL_PIPELINE,
                alarm_event_id=ALARM_EVENT_ID,
                parent_project=PERSPECTIVE_PARENT, child_project=PERSPECTIVE_CHILD,
                raw_dir=args.raw_dir,
            ))
        with _server(_settings(gateway.base_url, data_dir, mutation_enabled=False)) as url:
            gate_off = asyncio.run(run_gate_off(
                rest_url=url, agent_token=AGENT_TOKEN, raw_dir=args.raw_dir,
            ))
        # The fault cases run last: they leave the allowlisted resource and the import
        # Project in the states their transport failures produced.
        with _fault_proxy(gateway.base_url) as (proxied_url, control_url):
            fault_settings = _settings(proxied_url, data_dir, mutation_enabled=True, fault=True)
            with _server(fault_settings) as url:
                fault = asyncio.run(run_fault_mode(
                    rest_url=url, agent_token=AGENT_TOKEN, proxy_control_url=control_url,
                    data_dir=Path(data_dir), resource_type=RESOURCE_TYPE, allowlisted=ALLOWLISTED,
                    project=PROJECT, tool_timeout_seconds=fault_settings.tool_timeout_seconds,
                    raw_dir=args.raw_dir, provider_type=PROVIDER_TYPE,
                    reserved_provider=RESERVED_PROVIDER, other_provider=OTHER_PROVIDER,
                    lookalike_provider=LOOKALIKE_PROVIDER,
                ))

    cases: list[dict[str, Any]] = [*gate_on["cases"], *gate_off["cases"], *fault["cases"]]
    report = {
        "schemaVersion": 1, "gate": "G4", "milestone": "4c", "rehearsal": True,
        "cases": cases,
        "observations": {
            **gate_on["observations"], **gate_off["observations"], **fault["observations"],
        },
        "passed": all(case["ok"] for case in cases),
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
