# Phase 0 — Repository foundation + Native binding proof

Status: **IN PROGRESS — G0 requires real Gateway evidence**.

## Implemented in this phase branch

- D25 monorepo skeleton with the two canonical product packages.
- Root uv workspace and pinned development quality tooling.
- GPL-3.0-only release metadata.
- Repo-owned Designer project validator and deterministic ZIP builder.
- Stable D06 error taxonomy and D08/D10/D17/D22 shared contracts.
- Runtime permission profile manifests.
- Disabled Runtime `bundle_info` scaffold carrying `NATIVE_BINDING_PENDING`.
- Machine-readable native-binding evidence schema and G0 characterization plan.
- CI for L0/L1/L2 checks that do not require protected Ignition artifacts.

## G0 remains open

A real Ignition Gateway plus the exact MCP Module build must prove Tool output binding (`outputSchema`, success `structuredContent`, failure `isError`). Static validation, direct handler calls, or a readable Text Resource are insufficient.

Phase 1 must not be merged as production-compatible Runtime work until that evidence exists or a new Decision explicitly changes D06/D26.
