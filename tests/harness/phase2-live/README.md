# Phase 2 live Gateway harness (G2)

This harness provisions an isolated exact-patch Ignition Gateway, the checksum-pinned official MCP Module, PostgreSQL, the exact built Runtime ZIP, a test-only Named Query project, and bounded Tag/Historian/audit fixtures. It then performs real MCP discovery and calls across both capability planes.

## Evidence scope

Each matrix row records:

- Gateway exact version/build/image digest;
- MCP Module version/build/artifact SHA-256;
- Runtime Bundle SHA-256;
- OpenAPI SHA-256;
- exact Tool/Resource/Prompt discovery;
- one positive smoke call per Phase 2 readonly Tool;
- selected canonical negative/error behavior;
- D27/D28 status without promoting the tuple to `SUPPORTED`.

The 8.3.8 row is required. The 8.3.9 row is a compatibility candidate and is allowed to expose a fail-closed native-binding limitation; it does not inherit D27.

## Current gate

`alarm_status` and `alarm_journal` are excluded from the bundle and the probe per the [D12 Phase 2 bounded-execution amendment](../../../docs/decisions/D12-alarm-tool-surface.md): the native queries expose no row limit, reliable continuation, or interruptible timeout, so a post-materialization `len()` check cannot prove bounded execution. The probe therefore verifies the frozen 13-Tool Runtime inventory. The harness, workflow, and evidence schema remain valid when those Tools are re-enabled after bounded-mechanism evidence.

The fixture namespace is disposable and CI-only. The API token and database trust authentication are not production settings. No G2 evidence row exists yet; until a trusted run completes, Phase 2 acceptance remains open.
