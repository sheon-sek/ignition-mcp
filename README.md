# Ignition MCP

A dual-server MCP ecosystem for Inductive Automation Ignition.

- `ignition-rest`: external FastMCP server for curated Ignition Native REST capabilities.
- `ignition-runtime`: the Ignition Official MCP Module hosting the Runtime MCP Bundle (`system.*`).

The architecture and implementation order are frozen by `docs/decisions/D01-D27`. Implementation is dependency-first and phase-gated.

## Current implementation status

**Phase 0 is complete and G0 is closed.**

The repository foundation, deterministic Runtime Bundle tooling, real Gateway CI, exact MCP Module characterization, D27 native-`outputSchema` limitation handling, and CI-owned full-access API-token bootstrap are all live-verified on the Phase 0 baseline:

- Ignition `8.3.8 (b2026071409)`;
- `inductiveautomation/ignition:8.3.8`;
- MCP Module `1.3.5-SNAPSHOT (b2026021307)`;
- pinned module artifact SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`.

The Runtime native binding status for this exact tuple is `VERIFIED_WITH_LIMITATION`: native `structuredContent` and `isError` are verified, while the Module does not publish Tool `outputSchema`; D27 keeps repo-owned JSON Schemas as the mandatory semantic output contract.

Next implementation entry point: **D26 Phase 1 / G1 minimal read-only dual-plane vertical slice**.

See `docs/development/phase-0.md`.
