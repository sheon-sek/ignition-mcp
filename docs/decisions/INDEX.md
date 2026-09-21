# Ignition MCP Architecture Decisions — D01–D29

**Status:** DECIDED

**Canonical location:** `docs/decisions/` (see D25). This single index replaces the three per-batch index files, which are kept unchanged in `docs/_archive/`.

D01–D29 are binding unless explicitly reopened through a new decision or amendment. Design content was frozen by the pre-D26 consistency cleanup only; see [Pre-D26 consistency cleanup](#pre-d26-consistency-cleanup-applied).

## Decision index

| Decision | Status | Topic | Notes |
|---|---|---|---|
| D01 | DECIDED | Repository License and Upstream Reuse Policy | |
| D02 | DECIDED | MCP Capability Ownership | ownership rules are scoped to operation semantics/Tools |
| D03 | DECIDED | External FastMCP Public Surface Model | |
| D04 | DECIDED | OpenAPI Capability Registry Lifecycle | |
| D05 | DECIDED | MCP Server and Tool Naming Convention | canonical Tool names stay `snake_case`; Resource/Prompt identifiers are discovery-verified |
| D06 | DECIDED | Tool Result and Error Contract | Tool-scoped; Text Resources/Prompts use native MCP semantics |
| D07 | DECIDED | External FastMCP Authentication and Authorization | amended by D07-A (consolidated in D07) |
| D08 | DECIDED | Mutation Safety Model | |
| D09 | DECIDED | Ignition Runtime MCP Permission and Security Model | one Runtime MCP Bundle: Tools + Text Resources + Prompts |
| D10 | DECIDED | Request Budgets, Pagination and Output Limits | bounded interface extended to Resource/Prompt payloads |
| D11 | DECIDED | Tag Tool Surface | |
| D12 | DECIDED | Alarm Tool Surface | |
| D13 | DECIDED | Historian Tool Surface | |
| D14 | DECIDED | Database Tool Surface | approved Named Query registry; no arbitrary SQL |
| D15 | DECIDED | Perspective / Project Resource Editing | |
| D16 | DECIDED | Project ZIP Transaction, Concurrency and Backup | |
| D17 | DECIDED | Binary Artifact / Export Handling | |
| D18 | DECIDED | Unified Audit, Correlation and Observability | carries the authoritative D07-A text |
| D19 | DECIDED | Diagnostics / Capability Discovery Public Surface | discovery covers `tools/list` + `resources/list` + `prompts/list` |
| D20 | DECIDED | Ignition Official MCP Server Setup Automation | verifies Tool, Resource and Prompt inventories |
| D21 | DECIDED | Runtime MCP Bundle Compatibility / Versioning | renamed from “Built-in Tool Bundle” |
| D22 | DECIDED | `contracts/` Strategy: Schemas / Fixtures / Tests vs Codegen | extended to per-primitive contracts |
| D23 | DECIDED | Testing Matrix and Real Ignition Gateway CI | Resource/Prompt protocol and artifact tests |
| D24 | DECIDED | Canonical Dual-Server Architecture and Optional Unified Facade | future federation must cover Prompts |
| D25 | DECIDED | Final Monorepo / Package Layout | second product is `ignition-runtime-bundle` |
| D26 | DECIDED | v1 Scope, Milestones, and Implementation Order | dependency-first vertical slices; Native response binding is hard gate G0 |
| D27 | DECIDED | Runtime Native outputSchema Limitation | exact baseline tuple exception; structuredContent/isError remain mandatory |
| D28 | DECIDED | [Runtime lossless null encoding](D28-runtime-null-wire-encoding.md) | owner-approved ignition-null-v1; capability-aware empty Prompt discovery |
| D29 | DECIDED | [Recorded Jython runner for Runtime Tool handlers](D29-recorded-jython-runtime-runner.md) | owner-approved test-only `jython-standalone` 2.7.4 + Java 11, required locally and in CI; adds to D23 L4, does not replace it |

## Cross-decision amendment

### D07-A — HTTP / Internal Trusted Deployment
Approved while finalizing D18. It amends the earlier D07 assumption that production External FastMCP requires stronger authentication/TLS-style deployment.

Rules:
- plain HTTP is a first-class production transport;
- trusted internal deployments may explicitly use `auth=none` (including non-loopback);
- static-token authentication is a valid simple production mode;
- TLS/OAuth/OIDC/JWT remain supported for deployments that need them;
- transport security and authentication are independently configurable.

D18 contains the authoritative text of D07-A. D07 itself is now consolidated onto these rules, so the two files no longer state opposite production requirements.

## Frozen architecture

```text
AI Agent / MCP Client
├── ignition-rest
│   └── FastMCP 4
│       ├── Ignition Native REST
│       ├── Project ZIP adapters
│       └── Artifact HTTP data plane
│
└── ignition-runtime
    └── Ignition Official MCP Module
        └── Runtime MCP Bundle
            ├── tools/
            ├── resources/
            └── prompts/
```

Key frozen principles:
- GPL-3.0.
- Both upstream repos may be selectively reused; neither is a fork-and-patch base.
- Native REST is canonical owner when a semantically complete official REST operation exists.
- Runtime MCP owns non-REST Gateway runtime capabilities.
- No duplicate equivalent operations across the two servers.
- No WebDev bridge.
- Curated tools + controlled generic config layer + MCP resources.
- No arbitrary REST request Tool.
- OpenAPI drives a version/module-aware capability registry.
- Stable semantic Tool names in `snake_case`; Resource/Prompt identifiers come from real MCP discovery.
- MCP structured output + shared error taxonomy; no universal `{ok,result,error}` envelope.
- Production External FastMCP supports both `secured` and `trusted-internal` deployment profiles.
- Shared permission concepts: read/config/control/admin.
- Mutation safety is layered and deny-by-default.
- Runtime MCP uses explicit Server Profiles/Security Levels/Tool inventories.
- All Tools have bounded input/execution/output; Resource/Prompt payloads are bounded too.
- Repository is a monorepo with two primary products and one canonical decision directory.
- v1 implementation is dependency-first: prove Native response binding and a minimal dual-plane read-only vertical slice before broad Tool expansion; mutations and Perspective authoring are gated behind their safety/transaction foundations.

## Runtime MCP primitive model

The Runtime product is an MCP primitive bundle, not a Tools-only collection:

```text
com.inductiveautomation.mcp/
  tools/<folders>/<name>/resource.json + onToolCalled.py
  resources/<folders>/<name>/resource.json + data.bin
  prompts/<folders>/<name>/resource.json + onPrompt.py
```

Two capabilities are independent and must not be conflated:

- publishing a JSON-schema **Text Resource** with `resources/list` + `resources/read`;
- registering Tool `outputSchema`, returning `structuredContent`, and signalling `isError`.

The exact baseline Tool native response binding is `VERIFIED_WITH_LIMITATION` under D27; D28 records the explicitly approved lossless null encoding. Other tuples require re-characterization. A readable schema Resource alone does not satisfy D06 and cannot be used to claim production `SUPPORTED` (D21); binding must be proven by D23 live verification.

The canonical MCP server name remains `ignition-runtime`, provided by the official Module. `ignition-runtime-bundle` is a product/artifact name, not a third MCP Server.

## Frozen additions from D12–D18
- Runtime Alarm surface is separated from Native REST Alarm Notification Pipeline runtime operations.
- Acknowledgement identity cannot be caller-forged.
- Shelve/unshelve mutations use exact Alarm Paths; wildcard mutation is forbidden.
- Historian v1 exposes browse, bounded series, and aggregate calculations only.
- True unbounded/raw Historian retrieval and Historian writes are deferred.
- Database v1 exposes only MCP-approved bounded Named Query capabilities; no arbitrary SQL.
- Perspective project-local authoring uses Project Export → typed ZIP adapter → Project Import.
- No WebDev/filesystem+scan Project authoring.
- Project mutation uses optimistic concurrency, per-Project MCP writer locking, mandatory pre-import backup/re-export, and mandatory post-import verification.
- Project Import is never blindly retried after an ambiguous mutation outcome.
- Binary archives travel through an ArtifactStore + HTTP data plane, not Base64 Tool JSON.
- Project/Gateway archive Tools exchange Artifact IDs/metadata.
- Plain HTTP is fully supported for internal deployments.
- Unified correlation/audit/log/metric semantics are defined without forcing a common observability backend.
- Runtime mutation audit defaults to best-effort and never fabricates caller identity.

## Important corrections to earlier research

### D10 Historian budget correction
Historian aggregate budgeting is not modeled as `paths × time buckets`.

Use:
```text
paths × requested aggregate calculations
+ bounded time range
```

Bucketed/finite time-series retrieval belongs to `historian_query_series`.

### Large FastMCP Resources
Large binary artifacts should not be carried through MCP byte Resources as the normal transport path.

Use ArtifactRef + HTTP streaming. Runtime Text Resource support does not turn MCP Resources into the binary artifact channel.

### Skill scaffold defaults are not project contracts
The generic Skill scaffold's `tag-read` example defaults `timeout` to 5000 ms and uses a kebab-case sample title. D10 sets `tag_read` default timeout to 10 s with a 30 s hard max, and D05 keeps canonical public Tool names in `snake_case`. Project decisions remain authoritative; the scaffold is parameterized to them during implementation, and final naming is verified against real `tools/list`.

## Canonical upstream repositories
Use these repository URLs for all future upstream source comparisons:

```text
https://github.com/jsgorana/ignition-mcp.git
https://github.com/WhiskeyHouse/ignition-mcp.git
```

## Decision directory conventions
- Canonical decisions live in `docs/decisions/` (D25).
- `INDEX.md` is the single index for D01–D26.
- Later changes to an existing decision must leave an explicit trace: a new decision, an amendment section, or a recorded D07-A style amendment.
- `docs/_archive/` holds pre-cleanup batch snapshots and is not authoritative.

## D26 implementation entry point

D26 freezes the first complete product scope and converts D01–D25 into an executable delivery sequence:

1. **Phase 0 / G0:** repository foundation plus real Native MCP response-binding proof.
2. **Phase 1 / G1:** minimal read-only end-to-end slice for both canonical servers.
3. **Phase 2 / G2:** complete readonly surface.
4. **Phase 3 / G3:** Artifact, Project, diagnostics, deployment and mutation-safety foundations.
5. **Phase 4 / G4:** controlled non-Perspective mutations.
6. **Phase 5 / G5:** typed Perspective authoring on D16/D17 transaction infrastructure.
7. **Phase 6 / G6:** deployment completion and compatibility-certified release.

The critical first implementation rule is that `NATIVE_BINDING_PENDING` must be resolved through a real Gateway + exact MCP Module build before Runtime breadth-first implementation or any production `SUPPORTED` claim.

## Pre-D26 consistency cleanup (applied)

Applied before D26, without reopening D01–D25 design:

1. **C01 — D07 ↔ D18 / D07-A.** Consolidated D07 onto D07-A so no two `DECIDED` files state opposite production-authentication rules.
2. **C02 — D18 ↔ D25.** Reworded D18's “shared Jython helpers” to shared audit semantics/pattern; packaging follows D25.
3. **C03 — D23 ↔ D06.** Mutation verification now says “do not trust MCP call success / HTTP success alone”.
4. **C04 — D23 ↔ D14.** Database integration tests rephrased around the approved Named Query registry.
5. **C05 — D02/D03 ↔ D19.** D19 now scopes `generic_mcp_audit_query_v1=false` to MCP-local/generic audit browsing and preserves D02's Gateway REST audit-query ownership.
6. **Runtime MCP primitive expansion.** D09, D19, D20, D21, D22, D23, D25 upgraded from a Tool-only Runtime model to Tools + Text Resources + Prompts; D21 renamed to “Runtime MCP Bundle Compatibility / Versioning”; D25's second product renamed to `ignition-runtime-bundle` with a `tools/ + resources/ + prompts/` layout.
7. **Scope clarifications.** D02 (ownership governs operation semantics/Tools), D05 (Resource/Prompt identifiers are discovery-verified), D06 (Tool-scoped contract), D10 (bounded Resource/Prompt payloads), D18 (Resource/Prompt errors are logging, not forced Tool audit), D24 (future facade federation covers Prompts).
8. **D10 DB budget cleanup.** Removed the stale `hard max SQL text 32 KiB` budget, which assumed caller-supplied SQL that D14 no longer exposes.

Unchanged: D01, D03, D04, D08, D11–D17, and every architectural principle they establish.

## Resume point

The original D01–D26 architecture / implementation decision backlog is now complete.

**G0, G1, G2 and G3 are closed**; see the [Phase 0](../development/phase-0.md), [Phase 1](../development/phase-1.md), [Phase 2](../development/phase-2.md) and [Phase 3](../development/phase-3.md) runbooks for evidence. Phase 4 starts only on a new user-directed feature branch.

Treat D01–D29 plus D07-A as binding unless explicitly reopened through a later Decision or Amendment.
