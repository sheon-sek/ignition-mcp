# Ignition MCP

A dual-server MCP ecosystem for Inductive Automation Ignition.

- `ignition-rest`: external FastMCP server for curated Ignition Native REST capabilities.
- `ignition-runtime`: the Ignition Official MCP Module hosting the Runtime MCP Bundle (`system.*`).

The architecture and implementation order are frozen in the [decision index](docs/decisions/INDEX.md). Implementation is dependency-first and phase-gated.

## Current implementation status

**Phase 0 is complete and G0 is closed.**

The repository foundation, deterministic Runtime Bundle tooling, real Gateway CI, exact MCP Module characterization, D27 native-`outputSchema` limitation handling, and CI-owned full-access API-token bootstrap are all live-verified on the Phase 0 baseline:

- Ignition `8.3.8 (b2026071409)`;
- `inductiveautomation/ignition:8.3.8`;
- MCP Module `1.3.5-SNAPSHOT (b2026021307)`;
- pinned module artifact SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`.

The Runtime native binding status for this exact tuple is `VERIFIED_WITH_LIMITATION`: native `structuredContent` and `isError` are verified, while the Module does not publish Tool `outputSchema`; D27 keeps repo-owned JSON Schemas as the mandatory semantic output contract.

**Phase 1 is complete and G1 is closed** on implementation commit `7f2b3a1`. [CI](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513611887), [G0](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513612004), and [G1](https://github.com/sheon-sek/ignition-mcp/actions/runs/35513611855) passed on that same commit; 72 local tests pass.

The readonly slice exposes external `gateway_info` / `gateway_diagnose`, Runtime `bundle_info` / `tag_browse` / `tag_read`, five Text Resources across both servers, and no Prompts. [D28](docs/decisions/D28-runtime-null-wire-encoding.md) documents the owner-approved lossless Runtime null encoding required by the pinned Module. G1 is not production `SUPPORTED` certification or complete v1.

See the [Phase 1 analysis and runbook](docs/development/phase-1.md), [persisted G1 evidence](tests/compatibility/evidence/g1-8.3.8-mcp-2026021307/evidence.json), and [Phase 0 record](docs/development/phase-0.md).

**Phase 2 is complete and G2 is closed** on implementation commit `852fc37`. Same-commit [CI](https://github.com/sheon-sek/ignition-mcp/actions/runs/35534976025), [G0](https://github.com/sheon-sek/ignition-mcp/actions/runs/35534976030), and the [G2 dual-row matrix](https://github.com/sheon-sek/ignition-mcp/actions/runs/35534976018) passed. Every Tool of the frozen 13-Tool Runtime readonly inventory (per the [D12 pre-G2 amendment](docs/decisions/D12-alarm-tool-surface.md), `alarm_status`/`alarm_journal` stay deferred until a native pre-execution bound exists) and all 11 External Tools ran real Gateway calls on both rows — schema-validated structured results throughout, except `alarm_pipeline_status`, which correctly returns the canonical `not_found` Tool Error on a pipeline-less Gateway. The [8.3.8 required row](tests/compatibility/evidence/g2-8.3.8-mcp-2026021307/README.md) is `VERIFIED` / `VERIFIED_WITH_LIMITATION` under the exact D27 tuple; the [8.3.9 candidate row](tests/compatibility/evidence/g2-8.3.9-mcp-2026021307/README.md) ran the identical full smoke and records the fail-closed `UNVERIFIED_LIMITATION` — D27 does not propagate and deployment compatibility remains `UNTESTED`. See the [Phase 2 runbook](docs/development/phase-2.md); the [G2 workflow](.github/workflows/phase2-live-g2.yml) is now a historical `workflow_dispatch`-only gate. Phase 3 starts only on a new user-directed branch.
