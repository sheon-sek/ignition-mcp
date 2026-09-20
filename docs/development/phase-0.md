# Phase 0 — Repository foundation + Native binding proof

Status: **IN PROGRESS — G0 evidence is produced by CI-owned ephemeral Gateway tests**.

## Implemented in this phase branch

- D25 monorepo skeleton with the two canonical product packages.
- Root uv workspace and pinned development quality tooling.
- GPL-3.0-only release metadata.
- Repo-owned Designer project validator and deterministic ZIP builder.
- Stable D06 error taxonomy and D08/D10/D17/D22 shared contracts.
- Runtime permission profile manifests.
- Disabled Runtime `bundle_info` scaffold carrying `NATIVE_BINDING_PENDING`.
- Machine-readable native-binding evidence schema and G0 characterization plan.
- CI for L0/L1/L2 checks.
- Phase 0 policy: the real Gateway is provisioned by GitHub Actions from the pinned official Ignition Docker image; a user-supplied or long-lived Gateway is not a prerequisite.
- The project owner has authorized the official MCP Module artifact used for baseline characterization to be committed as a checksum-pinned CI fixture.

## G0 remains open

GitHub Actions must provision a fresh `inductiveautomation/ignition:8.3.8` Gateway, install the checksum-pinned exact MCP Module build, and prove Tool output binding (`outputSchema`, success `structuredContent`, failure `isError`) through the real MCP transport. Static validation, direct handler calls, or a readable Text Resource are insufficient. Do not ask the user to provide a separate real Gateway for this gate.

Phase 1 must not be merged as production-compatible Runtime work until that evidence exists or a new Decision explicitly changes D06/D26.
