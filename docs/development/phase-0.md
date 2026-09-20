# Phase 0 — Repository foundation + Native binding proof

Status: **G0 LIVE EVIDENCE OBTAINED — D27 exact-tuple limitation resolution in progress**.

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

## G0 live result

GitHub Actions run `35505312397` provisioned a fresh `inductiveautomation/ignition:8.3.8` Gateway and the checksum-pinned official MCP Module, then exercised the real MCP transport. `initialize`, exact Tool inventory/input schema, success `structuredContent`, failure `isError=true`, Resources and Prompts passed. Native `tools/list.outputSchema` was absent.

D27 explicitly resolves that exact official Module limitation without a text-only fallback: repo-owned output schemas remain mandatory semantic contracts, while the exact baseline tuple may be `VERIFIED_WITH_LIMITATION`. Future tuples must re-characterize and do not inherit the exception.
