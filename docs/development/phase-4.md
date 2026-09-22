# Phase 4 — Controlled non-Perspective mutations

Status: **IN PROGRESS** (scope frozen 2026-09-22).

Branch: `feature/phase-4` (user-created; D26 new-branch rule).

Base: `main` at `4de7e37` (PR #5 merge).

This phase implements only D26 Phase 4 / G4. Phases 0–3 are closed and frozen. Nothing here may weaken a G0–G3 guarantee or edit their evidence.

## Authority

1. D01–D30 plus recorded amendments. The primary ones are D07 (Phase 4 amendment), D08, D09, D10, D11 (Phase 4 amendment), D12 (Phase 4 amendment), D16, D17, D18, D20, D21, D23, D26 and **D30**.
2. `CONTEXT.md`, the glossary. Tickets, tests and contracts use its terms.
3. The repo-owned `contracts/` and `tooling/`, and live compatibility evidence.
4. This runbook.

If a decided rule cannot be met as written, stop that ticket, record it under "Open questions", and ask the owner. Never reinterpret a decision silently.

## Goal

Expose D26's Phase 4 Mutation Tools on both Planes. Each goes through the full D08 chain, with D30's Precondition tokens, Preflight and Target allowlists. Close G4 with the D23 L5 failure suite. Every Mutation stays disabled by default: implemented code does not mean a deployment allows it.

## Scope

### Runtime Plane (milestones 4a and 4b)

| Tool | Class | Destructive | Milestone | Precondition token | Verification (Observed state) |
|---|---|---|---|---|---|
| `tag_write` | CONTROL | no | 4a | — | bounded `readBlocking` of the written paths |
| `alarm_shelve` | CONTROL | no | 4a | — | `alarm_shelved_list` state for the exact paths |
| `alarm_unshelve` | CONTROL | no | 4a | — | same |
| `alarm_acknowledge` | CONTROL | yes | 4a | — | **PARKED** — #6 evidence: one exact Alarm path accumulates an event per unacknowledged activate/clear cycle, so exact-path `queryStatus` is not bounded (D12 Phase 4 amendment condition failed) |
| `tag_update` | CONFIG | no | 4b | Tag config fingerprint | `tag_get_config` re-read |
| `tag_create` | CONFIG | no | 4b | — (existing target → `conflict`) | same |
| `tag_copy` | CONFIG | no | 4b | — (existing destination → `conflict`) | same |
| `tag_delete` | CONFIG | yes | 4b | Tag config fingerprint | target absent |
| `tag_move` | CONFIG | yes | 4b | Tag config fingerprint (source) | source absent, destination present |
| `tag_rename` | CONFIG | no | 4b | Tag config fingerprint | old path absent, new path present |

Additive READ changes: `tag_get_config` emits `fingerprint`.

Profiles (`contracts/profiles/`) are explicit lists, never `*`:
- `operator` adds the CONTROL Tools;
- `configurator` adds the CONFIG Tools;
- `full` adds both.

`readonly` is unchanged. The bundle gets a D21 MINOR bump per released milestone.

### REST Plane (milestone 4c)

| Tool | Class | Destructive | Precondition token | Verification |
|---|---|---|---|---|
| `config_resource_update` | CONFIG (ADMIN for high-risk types, D07) | no | Resource signature (Gateway-enforced) | re-read |
| `config_resource_delete` | CONFIG | yes | Resource signature (Gateway-enforced) | absent |
| `config_resource_rename` | CONFIG | no | Resource signature (server read-compare) | old absent, new present |
| `config_resource_create` | CONFIG | no | — | present |
| `project_import` | CONFIG | yes | Project fingerprint | D16 export C reconcile |
| `tag_config_import` | CONFIG | no | — (`collisionPolicy=Abort`) | bounded re-export; present/missing Tags as Observed state |
| `alarm_pipeline_cancel` | CONTROL | yes | — | bounded `alarm_pipeline_status` |
| `artifact_delete` | CONFIG | yes | — (retention lock → `conflict`) | artifact absent |

Additive READ changes: `config_resource_get` emits `signature`.

Authentication: the D07 Phase 4 amendment adds named static tokens with per-token scopes. `auth=none` stays read-only.

### Operator tooling (milestone 4d)

`ignition-mcp setup-native apply` writes the managed bundle project, the Server Config for the selected profile and the Runtime Target Policy. Security Levels and the Runtime API token are created only when explicitly requested with flags; the token secret goes only to an operator-named `0600` file. Apply stops on any `BLOCKED` plan line, never rolls back, and ends with `verify` (D20).

## Explicit exclusions

- Re-enabling `alarm_status`/`alarm_journal`.
- `project_create`/`copy`/`rename`/`delete`.
- All Perspective work (Phase 5).
- `install-module` (Phase 6).
- The `DELETE /artifacts/{id}` route (dropped by D30).
- Any Runtime operation-record store or diagnose Tool.
- Dataset and Document `tag_write` values.
- A caller-chosen collision, reference or invalid-reference policy.
- A production candidate builder or generic project-resource editor (D15).
- Promoting any tuple to `SUPPORTED`.
- New error codes.

## Cross-cutting rules

These rules come from D30. The decision holds the full text.

- **Runtime Target Policy.** A Gateway document outside the bundle. It is read by every Runtime Mutation handler and fails closed with `operation_disabled`. It holds Target allowlists per Mutation class (provider-qualified prefixes matched at segment boundaries), the Service identity, the audit mode and the shelve cap. UDT definitions need an explicit `_types_` prefix. Its document contract is `contracts/shared/runtime-target-policy.schema.json`; ticket #7 keyed `allowlists` by Tool name (see Open questions).
- **Preflight.** Every item in a batch passes input, allowlist and Precondition-token checks before any item executes. Items then execute one at a time, with per-item outcomes and no rollback.
- **Fixed knobs.** `references=ABORT`, `allowInvalidReferences=false`, `collisionPolicy=Abort`.
- **Refused resource types.** Contract-listed and refused even under `*`. An unclassified type is refused, and a test fails on any unclassified type in a supported OpenAPI document.
- **Audit.** REST uses the Phase 3 AuditSink ordering. Runtime uses `system.util.audit` with the D18 mode from the policy. `required` checks the audit profile before executing. A failed post-Mutation audit write gives `auditRecorded=false`, never a failure.
- **Fixture first.** Every ticket that touches Gateway behavior adds a recorded-Gateway fixture (`tests/harness/recorded_gateway.py`) or a Jython fixture (D29) that fails locally before any live CI run.
- **Budgets (D10).** `tag_write` defaults to 20 and has a hard max of 100. Config Mutation targets default to 20, hard max 100. The timeout class is FAST unless an artifact is involved, in which case it is ARTIFACT.

## Tickets

Tickets are tracer bullets: each one cuts through contract, schema, implementation, fixture tests, profile and inventory lint, and the live harness. The Issue column links each ticket to its GitHub issue, and GitHub's native issue dependencies hold the blocking edges. Tickets marked **codex** are hard or critical; the rest can go to omp.

| # | Issue | Milestone | Ticket | Blocked by | Agent |
|---|---|---|---|---|---|
| 1 | #6 | 4a | Characterize Runtime Target Policy storage + bounded exact-path alarm `queryStatus` | — | codex |
| 2 | #7 | 4a | `tag_write` tracer bullet (Target Policy read, Preflight, Observed state, Runtime audit, operator profile, `phase4-live` harness) | 1 | codex |
| 3 | #8 | 4a | `alarm_shelve` + `alarm_unshelve` | 2 | omp |
| 4 | #9 | 4a | ~~`alarm_acknowledge` (`{alarmPath, eventId}` pairs)~~ **PARKED** by #6 evidence (see Open questions) | 1, 2 | codex |
| 5 | #10 | 4b | `tag_get_config` fingerprint + `tag_update` tracer bullet (configurator profile) | 2 | codex |
| 6 | #11 | 4b | `tag_create` + `tag_copy` | 5 | omp |
| 7 | #12 | 4b | `tag_delete` + `tag_move` + `tag_rename` | 5 | omp |
| 8 | #13 | 4c | Named static tokens with per-token scopes (D07 amendment) | — | omp |
| 9 | #14 | 4c | `config_resource_update` tracer bullet (signature, Refused resource types, fixed knobs) | — | codex |
| 10 | #15 | 4c | `config_resource_create` + `delete` + `rename` | 9 | omp |
| 11 | #16 | 4c | `project_import` | 9 | codex |
| 12 | #17 | 4c | `tag_config_import` | 9 | omp |
| 13 | #18 | 4c | `alarm_pipeline_cancel` | 9 | omp |
| 14 | #19 | 4c | `artifact_delete` | 9 | omp |
| 15 | #20 | 4c | REST fault-injecting proxy + live timeout / ambiguous / cancellation cases | 9 | codex |
| 16 | #21 | 4d | `setup-native apply`: bundle project, Server Config, Runtime Target Policy | 1, 2 | codex |
| 17 | #22 | 4d | `setup-native apply` opt-in Security Level + API token provisioning | 16 | omp |
| 18 | #23 | G4 | G4 close: L5 failure suite matrix, evidence rows, runbook results | 3–17 | codex |

## G4 acceptance

G4 closes only when all of the following are true:

- every Tool in the Scope tables is implemented through the D08 chain, with its D30 Precondition token and Target allowlist, and has contract, schema, fixture and live coverage;
- the D23 L5 cases are covered:
  - partial failure, permission denied, oversize, concurrent modification, and audit failure (`required` mode) are proven live on both Planes;
  - timeout, ambiguous outcome and cancellation are proven live on REST through the fault-injecting proxy;
  - on the Runtime Plane the same three are fixture-only, recorded as a limitation in evidence;
- no unsafe automatic retry anywhere; structural tests still confine Gateway writes to the guarded executor;
- inventories are exact:
  - REST Mutation Tools appear only when their class is enabled and the matching capability is present;
  - each Runtime profile's `tools/list` equals its contract list exactly;
  - `readonly` is unchanged;
- Mutations are disabled in the default deployment;
- `setup-native apply` is live-verified with `plan → apply → verify` on a disposable Gateway;
- 8.3.8 required and 8.3.9 candidate rows produce schema-valid evidence, and no tuple is `SUPPORTED`;
- all CI commands are green, and the G0–G3 workflows still pass on the branch head.

## Validation commands

Run the full command block in `AGENTS.md` (Commands) after every ticket. Before spending a live CI run, rehearse locally against `tests/harness/recorded_gateway.py`.

## Results

### Ticket #14 — REST `config_resource_update` (milestone 4c)

- Fixture-first coverage: the 8.3.8/8.3.9 resource-type classification, the
  `config_resource_get` signature, the refusal, the precondition and the two
  deployment gates are all covered by `packages/ignition-rest-mcp/tests/test_phase4_*`
  against the recorded Gateway; the full `AGENTS.md` command block is green.
- Local rehearsal: `tests/harness/phase4-live-rest/rehearse_local.py` — 14/14 cases
  against the recorded Gateway.
- Live ([run 35639720856](https://github.com/sheon-sek/ignition-mcp/actions/runs/35639720856),
  and again with the OpenAPI capture in
  [run 35640169826](https://github.com/sheon-sek/ignition-mcp/actions/runs/35640169826)):
  workflow `Phase 4 Live Gateway REST mutation`, both rows green, 14/14 live cases on
  8.3.8 (`2026071409`, required) and on 8.3.9 (`2026082511`, candidate) — exact REST
  inventory with the class enabled and disabled, the read-only credential excluded
  from the Mutation Tool, an allowlisted update applied and confirmed by an
  independent re-read, a stale signature refused with `conflict` and changing
  nothing, `ignition/api-token` refused with `permission_denied` and left usable, and
  a resource outside the Target allowlist denied with nothing changed. The observed
  Target-denial code is `operation_disabled` (see Open questions).
- Review round 3 fixes (see the ticket report): an explicit Gateway rejection is final
  for the Tool (D30 §2) — a 4xx or a 2xx carrying `success=false` with a `problem` is
  the result, mapped to `conflict` for a signature mismatch, and no read-back may turn
  it into a success (a competing writer can make the resource show the requested
  values). A success comes only from a Gateway claim, or from a genuinely ambiguous
  dispatch whose change is attributable; where attribution cannot be established the
  result is `outcome_unknown`. Declared per operation (`rejection_is_final`), so the
  Phase 3 machinery keeps the behaviour its frozen tests and G3 evidence pin.
- Review round 2 fixes (see the ticket report): the bundler keeps sibling keywords on
  every `$ref` occurrence (a repeated reference to one definition used to drop the
  later occurrence's constraint); the snapshot's request schemas are deeply immutable,
  so no holder can change the rules a later write is validated against; and a refused
  or ambiguous dispatch is a success only when the resource moved in the direction the
  call asked for, so a competing writer winning the race between the signature read and
  the write is never reported as this caller's success (with a deterministic race test
  that changes an unrelated field at PUT time).
- Review round 1 fixes (see the ticket report): a non-allowlisted Target is `permission_denied`
  (D30 §7) through a Tool-scoped mapping that leaves the frozen G3 behavior and evidence untouched;
  the change item is validated against the target Gateway's own documented PUT request schema (D03)
  before dispatch, and an update route without a usable schema exposes no update; a
  collection-qualified change is refused; and a Gateway refusal is never reported as a success when
  the requested values happened to equal the pre-state.
- The same runs captured each Gateway's `/openapi.json`; the 8.3.9 candidate exposes
  56 resource types, a strict subset of the 8.3.8 document's 57 (the difference is
  the MCP Module's own `server-config`), and every one of them is classified. The
  derived inventory is committed with its source SHA-256 and run ID.
- Frozen gates: CI and Phase 3 G3 have been green on every pushed head of this
  branch, for example CI runs
  [35639720685](https://github.com/sheon-sek/ignition-mcp/actions/runs/35639720685),
  [35640170047](https://github.com/sheon-sek/ignition-mcp/actions/runs/35640170047) and
  [35640770475](https://github.com/sheon-sek/ignition-mcp/actions/runs/35640770475), and
  Phase 3 G3 runs
  [35639720676](https://github.com/sheon-sek/ignition-mcp/actions/runs/35639720676),
  [35640169783](https://github.com/sheon-sek/ignition-mcp/actions/runs/35640169783) and
  [35640770477](https://github.com/sheon-sek/ignition-mcp/actions/runs/35640770477).

### Ticket #7 — Runtime `tag_write` (milestone 4a)

- Fixture-first coverage: 20 recorded-Jython fixtures
  (`tooling/native/jython_runner/fixtures/tag_write-*.json`) driven by
  `tooling/native/jython_runner/tests/test_tag_write.py` cover the policy gate
  (missing, over-cap, invalid, length mismatch, malformed, explicit null), the
  Target allowlist at a segment boundary, the reserved provider under an explicit
  `*`, a whole-batch Preflight refusal, the `required`/`off` audit paths, a failed
  Observed read, the Native-outcome edges and the two input limits. The D29 runner
  now replays an ordered native call sequence, so a fixture also fails when the
  handler makes an unrecorded call or skips a recorded one, and
  `run_recorded_tool_error` returns the canonical D06 error so a test can assert
  *which* refusal a batch produced.
- Contracts: `contracts/tools/runtime/tag_write.contract.json` (CONTROL, not
  destructive, FAST, 1..100 items), `contracts/schemas/tag-write.output.schema.json`,
  and the policy document contract
  `contracts/shared/runtime-target-policy.schema.json`. `tag_write` joins the
  `operator` and `full` profiles (`readonly` and `configurator` unchanged), and
  `BUNDLE_VERSION` goes 0.2.0 → 0.3.0 (D21 MINOR).
- Handler: the gated two-step policy read from ticket #6, an all-items Preflight
  (input, reserved provider, Target allowlist), one `system.tag.writeBlocking`
  call whose per-item QualityCode is the outcome, a bounded `readBlocking`
  Observed state, and `system.util.audit` attempt/result rows under the policy's
  audit mode with the policy's Service identity as actor.
- Local rehearsal: `tests/harness/phase4-live/rehearse_local.py` — all seven
  stages against the recorded Gateway, `drift: {}`.
- The bundle bump renamed the release artifact, which the Phase 3 G3 row pinned by
  version; that row now resolves the ZIP and manifest the release step just built,
  so the frozen G0–G3 workflows keep passing on a bumped bundle.
- Live ([run 35653953120](https://github.com/sheon-sek/ignition-mcp/actions/runs/35653953120),
  workflow `Phase 4 Live Gateway G4a`, both rows green with `drift: {}`, on 8.3.8
  `2026071409` required and 8.3.9 candidate):
  - a Gateway with no Runtime Target Policy refuses the Mutation with
    `operation_disabled` (`declaredLengthUnavailable`), before anything executes;
  - the `MCP_CI_AUDIT` profile is provisioned and the policy document is
    installed through Native REST and confirmed by a Tool-handler read of the
    served Tag (SHA-256 `75c25bc7…` on both rows);
  - the deployed `operator` inventory is exactly the 14 Tools of
    `contracts/profiles/operator.yaml`, `tag_write` included;
  - the allowlisted batch (4 items, one of them a path inside the allowlisted
    prefix that does not exist) returned 3 Good and 1 `Bad_NotFound` Native
    outcome, 0 `outcome_unknown`, and Observed state equal to what was written;
  - `[default]IgnitionMCP_CI2` — a sibling that only shares a string prefix — was
    refused with `permission_denied` / `targetNotAllowlisted` and left at its
    pre-value;
  - a batch mixing an allowlisted and a refused item was rejected whole, with the
    allowlisted target still at its pre-value (the Preflight executed nothing);
  - under an explicit `*` allowlist the reserved provider was refused with
    `permission_denied` / `reservedProvider`, its Tag value was unchanged and the
    policy document itself was unclobbered;
  - Runtime audit ran in `best_effort`, `auditRecorded=true`, and the
    `MCP_CI_AUDIT` log held both rows (attempt + result) for the call's
    correlation ID with `actor` equal to the policy's Service identity.
- **The harness Server Configs now select their profile's explicit Tool list.** The pinned Module documents the Server Config's `tools` mapping as `"providerId": "[tool1, tool2]"`, with a wildcard as the alternative. The G1–G3 harnesses used the wildcard while the bundle happened to hold exactly the read-only Tools, so the served inventory matched the `readonly` profile by coincidence; once the bundle carries `tag_write` a wildcard would serve a CONTROL Tool from a read-only deployment, and the G3 `setup-native` doctor check (`expected 13, endpoint advertised 14: extra=[tag_write]`) caught it. `phase1-runtime`, `phase2-runtime`, `phase3-runtime` now select the `readonly` list and `phase4-operator` selects the `operator` list (probe projects keep the wildcard). `tooling/native/tests/test_phase1_server_config.py` fails if any product harness config goes back to a Tool wildcard or selects a different list than its profile, and it asserts the read-only selection excludes every Runtime Mutation Tool. This is also the deployment model `setup-native apply` (#21) has to write.

- **Review round 1 fixes** (the ticket report holds the detail; the same three
  Runtime-plane fixes apply to `alarm_shelve`/`alarm_unshelve` from #8):
  - **D10 input bounds.** `tag_write` now enforces the 20-write project default
    inside D10's 100-write hard ceiling, raisable by the Runtime Target Policy's
    optional `tagWriteMaxWrites`; the Alarm Mutations use the same shape with
    `alarmMaxPaths`. Both add a per-path byte/character ceiling, a per-string and
    per-array value ceiling for `tag_write`, and one finite aggregate input-byte
    budget (64 KiB), all refused before dispatch with `limit_exceeded` and the
    requested amount plus the limit. Every Runtime Mutation contract declares the
    numbers as `inputBounds`, and `tooling/contracts/lint.py` keeps the Policy
    field a document-schema property.
  - **Denied mutations are audited.** After a usable Policy is read, an allowlist
    or reserved-provider refusal writes one `decision` row before returning
    `permission_denied`; `off` still records nothing, and `required` refuses the
    call (`operation_disabled`/`auditAttemptFailed`, `phase: "decision"`) when the
    row cannot be written. The ordered D29 fixtures prove the denied case makes no
    dispatch and one audit call.
  - **The outcomes outlive the Observed state.** The Observed section carries its
    own budget (per-value and total) and reports what it cannot return as an
    explicit `limit_exceeded` observed error; a serializer failure or an
    over-ceiling payload re-renders the result without the Observed state instead
    of replacing a known per-item outcome with `outcome_unknown`, and the last
    resort states the requested bytes, the limit and the outcome counts.
  - **The G4a 8.3.9 row's provider failure is diagnosed and fixed.** Run
    35654626095 lost the candidate row because the provision stage imported the
    policy Tag while the freshly created provider was still performing its initial
    load: the Gateway logged `Error creating actor for tag ... cleanPath is null`
    (`ImportTagLoaderAdapter.onInitialLoad`), the Tag kept its config but its actor
    never started, and the running provider answered every handler read with
    `Error_Configuration` while `/resources/find` and `/tags/export` stayed 200 —
    so the 240 s of config-plane re-imports could not heal it and the row died one
    step before the restart that does. The provision stage now gates the first
    import on a *handler-scope* read (an absent path in the provider must answer
    `Bad_NotFound`), the policy-read loop distinguishes "not serving" from "not
    yet applied" (waiting instead of re-importing in the first case), the gate
    verification reads the value quality rather than the probe's label, and the
    workflow gives the stage one bounded Gateway restart — the recorded heal —
    before failing the job.
- **Review round 2 fixes** (the ticket report holds the detail):
  - **A Dataset Observed value is measured by walking its cells.** The estimate
    charged a fixed cost per cell, so a one-cell Dataset holding an arbitrarily
    large string passed the check and was materialized by the read-back. The walk
    now sums the cells' structural sizes under the same byte budget with an early
    exit and never reads a cell to measure it; an over-budget Dataset becomes an
    explicit `limit_exceeded` observed error naming its size and the ceiling, and a
    Dataset inside the budget is still reported as Observed state.
  - **The provider's QualityCode text is bounded.** `diagnosticMessage` was copied
    unbounded, so a verbose provider could make `items` alone exceed the 256 KiB
    ceiling and hide every established outcome behind a counts-only Tool Error. The
    identifiers (`code`, `name`, `level`, `good`) stay exact, the diagnostic keeps a
    bounded 512-byte prefix, and `diagnosticMessageOverLimitBytes` states the size
    it had — present exactly when the text was bounded, and declared in the output
    schema. The `#8` Alarm Tools need no equivalent change: their per-item outcomes
    carry no provider text, and their Observed entries' `user`/`expiration` already
    bound to an explicit `limit_exceeded` observed error.
  - **The pinned actionlint download is resilient.** Run 35669308034's 8.3.8 row
    failed before Gateway startup because the release CDN answered `HTTP 500` to
    the pinned fetch. The fetch now retries a transport failure, and a body whose
    digest is not the pinned one, with exponential backoff (five attempts, 1 s to
    8 s); the sha256 still decides what is accepted, so a retry cannot substitute a
    different artifact, and a body over the pinned size is refused without a retry.
    **Live evidence (head `ccd0820`, draft PR #32):** `Phase 4 Live Gateway G4a`
    run
    [35672150054](https://github.com/sheon-sek/ignition-mcp/actions/runs/35672150054)
    is **green on both rows** (8.3.8 `2026071409` required and 8.3.9 candidate) with
    `drift: {}`, the 8.3.8 row's validation stage (the one that failed on the
    download) green, the gate serving on its first attempt on both rows, one policy
    import, one policy read with no repair and a verified gate, and every ticket
    #7/#8 live case holding. CI
    [35672150036](https://github.com/sheon-sek/ignition-mcp/actions/runs/35672150036),
    Phase 0 G0
    [35672150084](https://github.com/sheon-sek/ignition-mcp/actions/runs/35672150084),
    Phase 3 G3
    [35672150073](https://github.com/sheon-sek/ignition-mcp/actions/runs/35672150073),
    the REST mutation workflow
    [35672150065](https://github.com/sheon-sek/ignition-mcp/actions/runs/35672150065)
    and `Phase 4 Live Gateway G4b`
    [35672150067](https://github.com/sheon-sek/ignition-mcp/actions/runs/35672150067)
    are green on the same head. GitHub Actions created no run between
    2026-09-21T23:49:41Z and 2026-09-22T00:29:12Z, so the round-2 head was briefly
    marked `LIVE EVIDENCE PENDING (Actions outage)`; this run replaced that mark.
- **Live evidence for the fix** (head `764744f`, draft PR #32): `Phase 4 Live
    Gateway G4a` run
    [35668515373](https://github.com/sheon-sek/ignition-mcp/actions/runs/35668515373)
    **green on both rows** (8.3.8 `2026071409` required and 8.3.9 candidate) with
    `drift: {}` — the gate passed on its first attempt on both rows
    (`providerHandlerReadQuality` = `Bad_NotFound`, no import retry, one policy
    read with `policyReadRepairImports: 0` and a verified gate, and no use of the
    restart heal), and every ticket #7/#8 live case still holds with the changed
    handlers (allowlisted batch 3 succeeded + 1 Bad, sibling denial at the segment
    boundary, Preflight that executed nothing, two audit rows with the Service
    identity, the Alarm shelve + Observed state, the policy-cap refusal).
    CI, Phase 0 G0, Phase 3 G3 and the REST mutation workflow are green on the
    same head. The first push of the fix (head `5d039e0`, runs
    [35667242361](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667242361)
    / [35667242363](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667242363))
    failed both rows of G4a *and* G4b in `policy-provision`: the new gate called
    the probe Tool without opening its MCP session, and the Module answers
    `tools/call` with `Session is required for method: tools/call` (HTTP 400) —
    61 attempts, none served. That is fixed, and a test now drives the gate
    against an endpoint that enforces the same rule; the recorded Gateway fake
    does not model the session requirement, which is why the local rehearsal could
    not catch it (recorded as a residual below). The `Phase 4 Live Gateway G4b`
    rows are red on that head and on the `p4/runtime` head (`28622ca`,
    `0b50a52`) for a #10 reason — its `tag-update` stage expects `not_found` for
    `[default]IgnitionMCP_CI/Missing` but the live Gateway answers
    `conflict`/`fingerprintMismatch` with an observed fingerprint — so that row is
    not attributable to this fix's Runtime-plane changes.

### Ticket #8: Runtime `alarm_shelve` and `alarm_unshelve` (milestone 4a)

- Fixture-first coverage: 39 recorded-Jython fixtures
  (`tooling/native/jython_runner/fixtures/alarm_{shelve,unshelve}-*.json`) driven by
  `tooling/native/jython_runner/tests/test_alarm_{shelve,unshelve}.py` cover the
  policy gate (missing, oversize, length mismatch, unparseable, malformed,
  explicit-null `auditProfile`, invalid `alarmShelveMaxSeconds`, and a
  Tag-shaped allowlist entry), the Alarm path input grammar (wildcard, empty
  array, over the hard maximum), the Target allowlist at a segment boundary, the
  reserved provider under an explicit `*`, the deployment shelve cap and the D12
  24 h hard maximum, a whole-batch Preflight refusal, the `required`/`off` audit
  paths, a raised dispatch, a failed Observed read and an over-limit shelved view.
  The D29 launcher gained the `system.alarm` namespace and a `shelved-paths`
  result kind.
- Contracts: `contracts/tools/runtime/alarm_shelve.contract.json` and
  `alarm_unshelve.contract.json` (CONTROL, not destructive, FAST, 1..100 exact
  Alarm paths; `timeoutSeconds` required, 1..86400, lowerable by the policy),
  `contracts/schemas/alarm-shelve.output.schema.json` and
  `alarm-unshelve.output.schema.json`. Both Tools join the `operator` and `full`
  profiles (`readonly` and `configurator` unchanged), the CONTROL inventory in
  `tooling/contracts/lint.py` carries them, and `BUNDLE_VERSION` goes
  0.3.0 → 0.4.0 (D21 MINOR). The harness `phase4-operator` Server Config selects
  its profile's explicit Tool list, now 16 Tools.
- Handlers: the ticket #6 gated two-step policy read, an all-items Preflight
  (Alarm path grammar, reserved provider, Target allowlist), one native call per
  item (`system.alarm.shelve([path], seconds)` / `system.alarm.unshelve([path])`)
  with per-item outcomes and no rollback, and the `alarm_shelved_list` view of
  the exact paths as Observed state.
- Local rehearsal: `tests/harness/phase4-live/rehearse_local.py`: all nine stages
  against the recorded Gateway, `drift: {}`.
- Live (run
  [35661498161](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661498161),
  workflow `Phase 4 Live Gateway G4a`, both rows green with `drift: {}`, on 8.3.8
  `2026071409` required and 8.3.9 `2026082511` candidate; the first
  characterization of the same cases is run
  [35659773936](https://github.com/sheon-sek/ignition-mcp/actions/runs/35659773936),
  whose payloads the recorded fixtures hold), at bundle SHA-256
  `9a0760d8…`:
  - a Gateway with no Runtime Target Policy refuses both Alarm Mutations with
    `operation_disabled` (`declaredLengthUnavailable`) before anything executes;
  - the deployed `operator` inventory is exactly the 16 Tools of
    `contracts/profiles/operator.yaml`;
  - the allowlisted shelve of the run's own exact Alarm path
    (`prov:default:/tag:mcp_p4_<run>/Exact:/alm:ProbeHi`, the pattern the ticket #6
    `alarm_probe` measured) executed, and both the Tool's Observed state and an
    independent `alarm_shelved_list` read reported it shelved, with
    `user = usr:gateway-script` and an expiration one hour out (`expired: false`);
  - Runtime audit ran in `best_effort`, `auditRecorded=true`, and the
    `MCP_CI_AUDIT` log held both rows (attempt + result) for the call's
    correlation ID with `actor` equal to the policy's Service identity;
  - a duration of 7200 s against a 3600 s policy cap was refused with
    `invalid_argument` / `durationOverPolicyCap` (cap 3600, hard maximum 86400),
    86401 s was refused by the D12 hard maximum before the policy was read, and
    the target stayed unshelved after both;
  - a wildcard target was refused with `invalid_argument` / `wildcardPath`, and a
    sibling Alarm root that only shares a string prefix was refused with
    `permission_denied` / `targetNotAllowlisted`, with the shelved view unchanged;
  - a batch mixing an allowlisted and a refused path was rejected whole, with the
    allowlisted path still unshelved (the Preflight executed nothing);
  - the allowlisted unshelve executed and both the Tool's Observed state and
    `alarm_shelved_list` reported the path unshelved; the sibling and wildcard
    refusals held for the unshelve as well.
- The live run also confirms the native signature assumptions: an exact,
  provider-qualified Alarm source pattern is what `system.alarm.shelve` accepts and
  what `getShelvedPaths()` answers with, and the shelving identity is the Gateway
  script user, not a caller-supplied one (D12 forbids a forged acknowledgement or
  shelving identity).
- Recorded fixtures: the Alarm bodies under
  `tests/fixtures/recorded/gateway-8.3/phase4/` are run 35659773936's payloads,
  with provenance recorded and `__ALARM_ROOT__` / `__CORRELATION__` templated
  (both are run-scoped; the fake substitutes the run's values). The two Ticket #6
  probe recordings stay byte-frozen.
- **Review round 1 fix: the reserved-provider refusal matches the provider
  component.** Issue #8's first review found both handlers searching for
  `IgnitionMCPPolicy` anywhere in the rendered Alarm path, so under an explicit `*`
  allowlist an allowed target such as `prov:default:/tag:IgnitionMCPPolicyPump:/alm:High`
  was wrongly refused as `reservedProvider`; the fixtures that "covered" the rule
  used exactly such a `prov:default:` target, so they proved nothing about the
  provider. Both handlers now parse the `prov:<provider>:` component — the text
  between the scheme and the next separator — and compare that value, per D30's
  owner ruling (`reserved_provider_match: provider_component_only`). A true
  `prov:IgnitionMCPPolicy:/…` target is still refused before the Target allowlist
  is consulted and under an explicit `*` (`permission_denied` /
  `reservedProvider`), and a later Tag or Alarm segment that spells the name is an
  ordinary allowlist-checked target, refused as `targetNotAllowlisted` when it is
  outside the allowlist. Fixture-first: two new D29 fixtures per Tool
  (`-reserved-name-in-later-segment`, `-reserved-name-not-allowlisted`) fail on the
  old handlers and pass on the new ones, and the two `-reserved-provider` fixtures
  now target the reserved provider itself; the two Alarm suites stand at 57
  fixtures and 63 cases after the change (53 and 59 before). Live (run
  [35676245268](https://github.com/sheon-sek/ignition-mcp/actions/runs/35676245268),
  workflow `Phase 4 Live Gateway G4a`, both rows green with `drift: {}`, on 8.3.8
  `2026071409` required and 8.3.9 candidate, head `b2951aa` — the fix commit
  `58efda5` merged with the `p4/runtime` tip that PR #32 needed): every Alarm case
  the stage already runs still holds with the changed handlers — the exact-path
  shelve and unshelve, the policy-cap and D12 hard-maximum refusals, the wildcard
  and segment-boundary sibling refusals, the whole-batch Preflight refusal, and
  both audit rows per call with the Service identity as actor. The stage has no
  reserved-provider or reserved-name Alarm case, so the provider-component rule is
  proven by the D29 fixtures above and not live; adding that case is left to G4
  close (#23), where it costs no extra run.
- Frozen gates, green on the same head that records this evidence (`c68597f`): CI
  [35661498251](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661498251),
  Phase 0 G0
  [35661498193](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661498193),
  and Phase 3 G3
  [35661498182](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661498182).
  The Runtime `readonly` inventory is unchanged by this ticket, so the G1–G3 rows
  keep their exact 13-Tool read inventory.

### Ticket #15 — REST `config_resource_create`, `config_resource_delete` and `config_resource_rename` (milestone 4c)

- Fixture-first coverage: `packages/ignition-rest-mcp/tests/test_phase4_config_resource_create_delete_rename.py`
  (38 cases) drives the real server through MCP against
  `tests/harness/recorded_gateway.py`, which now models the collection `POST`, the
  signature-carrying `DELETE` and the rename routes, their collisions, their races and
  the ambiguous-dispatch boundaries. The full `AGENTS.md` command block is green
  (785 pytest cases).
- Per-Tool rules implemented and pinned: create takes no Precondition token and relies
  on the D11 collision policy (checked against the Gateway before dispatch); delete
  carries the signature in the native `DELETE` path *and* read-compares it before
  dispatch, verifies the Target's absence, and never sends the route's `confirm` flag;
  rename is a server-side read-compare only (the D30 §2 race window is documented, and
  a live case shows another writer's version being renamed inside it), always sends
  `references=ABORT`, and verifies both names.
- D30 §3 Preflight extended to more than one Target: `MutationRequest` gained
  `additional_target_ids`, the guarded executor checks every Target before anything
  executes and names the refused Target in the audit row, and a rename therefore needs
  both its source and its destination allowlisted. Unit and live cases cover the
  destination denial and the source denial separately.
- D03 extended to the new writes: the capability snapshot bundles a type's documented
  `POST` item schema and an operation's request body (rename) next to the existing
  `PUT` item schema; a write route without a usable schema, or without a lookup route to
  precondition from, exposes no write. Unit tests hold all three routes of every
  committed 8.3.8 type to that rule.
- Local rehearsal: `tests/harness/phase4-live-rest/rehearse_local.py` — **47/47 cases**
  against the recorded Gateway, both deployment gates.
- Live ([run 35652623709](https://github.com/sheon-sek/ignition-mcp/actions/runs/35652623709),
  re-run on the documentation head
  [35654240134](https://github.com/sheon-sek/ignition-mcp/actions/runs/35654240134)):
  workflow `Phase 4 Live Gateway REST mutation`, both rows green on every head,
  **47/47 live cases on 8.3.8 (`2026071409`, required) and on 8.3.9 (`2026082511`,
  candidate)** — the exact four-Tool inventory with the class enabled and the read-only
  inventory without it, an allowlisted create/delete/rename each confirmed by an
  independent read, a create and a delete collision, a rename onto an occupied
  destination, stale tokens refused with `conflict` and changing nothing,
  `ignition/api-token` refused with `permission_denied` for all three Tools while the
  token keeps working, an unallowlisted Target denied for each Tool (including the
  rename destination), and a refused cross-check that a denied create/delete/rename
  published or removed nothing. `provision.json` records the four provisioned resources
  and the nine required OpenAPI routes.
- Frozen gates, green on every pushed head of this ticket (`b4fcb59`, `020befb`,
  `a7c6dc5`): CI
  [35652623688](https://github.com/sheon-sek/ignition-mcp/actions/runs/35652623688) and
  [35654240141](https://github.com/sheon-sek/ignition-mcp/actions/runs/35654240141),
  Phase 3 Live Gateway G3
  [35652623873](https://github.com/sheon-sek/ignition-mcp/actions/runs/35652623873) and
  [35654240140](https://github.com/sheon-sek/ignition-mcp/actions/runs/35654240140), and
  Phase 4 Live Gateway G4a
  [35652623753](https://github.com/sheon-sek/ignition-mcp/actions/runs/35652623753) and
  [35654240189](https://github.com/sheon-sek/ignition-mcp/actions/runs/35654240189) — all
  success. One G3 row needed a rerun for a Gateway-side reason recorded in Open
  questions.

### Ticket #10 — Runtime `tag_get_config` fingerprint and `tag_update` (milestone 4b)

- **The Tag config fingerprint is a committed shared contract.** D30 §2 makes it
  repo-defined, so `contracts/shared/tag-config-fingerprint.json` holds the token
  form (`tcf1:<64 hex>`), the canonical-JSON rule, the D28 encoding step, the
  coverage and race-window statements, and four golden vectors. The rule is
  implemented twice — Jython 2.7 in the handlers (D29 self-containment) and Python
  3 in `tooling/contracts/lint.py` — and the vectors are what keep the two
  honest: the lint recomputes every vector from its recorded native read
  (native read → D28 encoding → canonical JSON → SHA-256), and the D29 suite runs
  the shipped `tag_get_config` handler under Jython 2.7.4 over the same reads and
  requires the same fingerprints, including the non-ASCII, control-character,
  D28-null and escaped-reserved-key cases.
- **Fixture-first coverage.** 8 recorded-Jython tests
  (`tooling/native/jython_runner/tests/test_tag_fingerprint.py`) over four golden
  vectors, and 38 tests for `tag_update`
  (`tooling/native/jython_runner/tests/test_tag_update.py`) over 32 fixtures that
  cover: the policy gate (missing, oversize, length mismatch, malformed,
  explicit-null `auditProfile`, a broken own-key entry, a policy that names the
  Tool no allowlist at all), the item array bounds and every item-shape refusal
  (keys, path grammar, fingerprint form, empty config, and the three refused keys),
  the Target allowlist at a segment boundary, the reserved provider under an
  explicit `*`, the D30 §6 `_types_` rule in all three shapes (bare `*`, plain
  prefix, explicit `_types_` entry), whole-batch Preflight refusal for a stale
  fingerprint and for a refused target, the fingerprint read-compare itself
  (`conflict` with nothing dispatched), a missing target (`not_found`, never
  created), a Preflight read that raises, the `required`/`best_effort`/`off` audit
  paths, an item-scoped indeterminate native outcome, a dispatch that raises (the
  item is `outcome_unknown` and later items are `not_executed`), and a failed or
  empty observed read. The D29 runner gained the `system.tag.getConfiguration` and
  `system.tag.configure` recordings, so a fixture proves the *absence* of a call
  as well as its result.
- Contracts: `contracts/tools/runtime/tag_update.contract.json` (CONFIG, not
  destructive, FAST, 1..100 targets, the D30 §2 Precondition token, the fixed
  `MergeOverwrite` collision policy, the `not_found` never-create rule, the refused
  configuration keys, the `_types_` rule and the race window),
  `contracts/schemas/tag-update.output.schema.json`, the additive
  `tag_get_config.fingerprint` field (schema, contract and resource.json), and
  `tag_update` in the `configurator` and `full` profiles (`readonly` and `operator`
  unchanged). `BUNDLE_VERSION` goes 0.4.0 → 0.5.0 (D21 MINOR);
  `tooling/contracts/lint.py` carries the CONFIG inventory, the fingerprint
  contract and the additive-field check.
- The Runtime Tag CONFIG Mutation belongs to the CONFIG class, so the harness now
  deploys a second Phase 4 Server Config (`phase4-configurator`) whose explicit
  Tool list equals `contracts/profiles/configurator.yaml`, and the live stage
  asserts both that the configurator endpoint serves exactly that list and that the
  operator endpoint does not serve `tag_update` (CONTROL ≠ CONFIG).
- Local rehearsal: `tests/harness/phase4-live/rehearse_local.py --stages 4b` — the
  milestone selector picks the stage set, the expectations file and the verdict
  shape, so the 4a evidence is untouched; all four 4b stages against the recorded
  Gateway, `drift: {}`.
- The live stage also recomputes the published fingerprint from the published
  configuration with the repository's own Python copy of the rule, so the Jython
  handler and the contract definition have to agree on a real Gateway's data.
- Live (workflow `Phase 4 Live Gateway G4b`). Two runs shaped this ticket before the one that
  records the evidence: [35666588649](https://github.com/sheon-sek/ignition-mcp/actions/runs/35666588649)
  failed its missing-target case on both rows and is what found the existence behavior below, and
  [35668653064](https://github.com/sheon-sek/ignition-mcp/actions/runs/35668653064) ran every
  stage on both rows and drifted on exactly one expectation — the batch case, which the driver
  had built after the wildcard policy was installed, so the sibling it meant to have refused was
  allowlisted and the batch was refused by the stale token instead. Its recorded bodies are the
  fixtures and the two live golden vectors committed here. The cases below hold on 8.3.8
  `2026071409` (required) and 8.3.9 `2026082511` (candidate):
  - a Gateway with no Runtime Target Policy refuses `tag_update` with `operation_disabled`
    (`declaredLengthUnavailable`), before anything executes;
  - the deployed `configurator` inventory is exactly the 14 Tools of
    `contracts/profiles/configurator.yaml`, and the `operator` deployment's 16 Tools do not
    include `tag_update` (CONTROL ≠ CONFIG);
  - the published fingerprint is exactly the D30 rule over the published configuration: the
    harness recomputes it with the repository's own Python copy of the canonical-JSON rule and
    gets the handler's token byte for byte
    (`tcf1:e8e52cd42f2a89b94ca1621cd5107070db9cd346e3b31fda9ba85c8a28b757c3` for
    `[default]IgnitionMCP_CI/WriteTarget`, identical on both rows), and two reads of the same
    target agree;
  - the allowlisted merge-update executed with a `Good` native outcome and changed the target,
    confirmed by an independent re-read (`documentation` + `engUnits`), with the observed
    fingerprint equal to that re-read's;
  - a Folder (`[default]IgnitionMCP_CI/Nested`) is a target too: `system.tag.exists` answers
    for one, the merge landed, and the independent read shows it;
  - the token read before the change is stale after it: the same call was refused with
    `conflict` / `fingerprintMismatch`, carrying both fingerprints, and the target kept the
    first call's values;
  - a target that is not there was refused with `not_found` / `targetMissing` and really is
    absent: the provider's own export carries no such Tag;
  - a sibling that only shares a string prefix was refused with `permission_denied` /
    `targetNotAllowlisted` and left at its pre-value, and a batch mixing it with an allowlisted
    item was refused whole with the allowlisted target unchanged;
  - a UDT definition target was refused with `permission_denied` /
    `udtDefinitionNotAllowlisted` both under a bare `*` and under the plain Tag prefix, while an
    explicit `_types_` entry let the same target through to the existence check (`not_found`),
    which is what proves the entry is honoured;
  - under an explicit `*` the reserved `IgnitionMCPPolicy` provider was refused with
    `permission_denied` / `reservedProvider`, its Tag value was unchanged and the policy
    document was not clobbered;
  - Runtime audit ran in `best_effort`, `auditRecorded=true`, and the `MCP_CI_AUDIT` log held
    both rows (attempt + result) for the call's correlation ID with `actor` equal to the
    policy's Service identity;
  - an over-budget batch (21 targets against the 20-target default) was refused with
    `limit_exceeded` / `itemsOverPolicyLimit` before any native call;
  - a refused Target and a refused Precondition both reported `auditRecorded=true`, so the D18
    decision row is live-proven as well as fixture-proven;
  - the exact UDT definition read was allowed and published a `tcf1` token, and that token is
    what the `_types_` update case handed back;
  - the run's own bodies are recorded: the `tag_get_config` read of the fixture Tag and the node
    a Gateway synthesizes for a missing path are the two live golden vectors in
    `contracts/shared/tag-config-fingerprint.json`, and the refusal bodies the fake replays are
    in `tests/fixtures/recorded/gateway-8.3/phase4/` with their provenance.
- **Review round 1 fixes.** Five blockers, all addressed, with the same patterns the #7 fix
  (`ac8ed4c` onward, `origin/p4/runtime-fix`) introduced so the two lanes merge:
  - *D10 input bounds.* `tag_update` now enforces the 20-target project default inside the
    100-target hard ceiling, raisable only through the Runtime Target Policy
    (`tagUpdateMaxItems`, 1..100; a value outside that range fails closed with
    `policyTagUpdateMaxItems`), plus a 2048-byte path ceiling, a 16384-byte configuration-string
    ceiling, a 1000-element array ceiling, a nesting ceiling of 8, a 32768-byte per-configuration
    budget and one finite 65536-byte aggregate input budget. Every one of them is pure validation
    over the request, refused with `limit_exceeded` (`requested` and `limit`) before the policy
    read; the contract declares them as `inputBounds` and the linter checks them, including that
    the named Policy field is part of the document schema.
  - *Denied Mutations are audited (D08/D18).* A refused Target and a refused Precondition each
    write one bounded `decision` row and dispatch nothing; `off` records nothing, and `required`
    refuses the call (`auditAttemptFailed`, `phase=decision`) when the row cannot be written. The
    ordered D29 fixtures prove the denial produces exactly one audit call and no `configure`.
  - *Outcome preservation.* The Observed state carries its own budget (16384 bytes per
    configuration, 65536 bytes in total) measured on the raw native read before conversion, so an
    over-budget configuration is an explicit `limit_exceeded` observed error; the serializer is
    caught locally and the result is re-rendered without the Observed state instead of replacing
    the per-item Native outcomes, with a last-resort `limit_exceeded` that states the requested
    bytes, the limit and the outcome counts. The Native `diagnosticMessage` is bounded too.
  - *An allowed UDT definition can obtain its token.* `tag_get_config` accepts one exact
    definition path (`recursive=false`), which is the read that publishes the `tcf1` token a
    `tag_update` on that definition compares; a recursive read of the definition namespace stays
    `invalid_argument` because `udt_type_get` owns the subtree view. A test runs the read and the
    update it authorizes and asserts the two tokens are the same value.
  - *The live verifier no longer double-encodes D28.* The driver hashes the published
    `configuration` as it stands (it is already the encoded value), and the recorded fake
    publishes the encoded form and fingerprints exactly what it publishes. The verifier is tested
    against all five committed golden vectors, including the null and reserved-key ones. The
    recorded fake also reads the reserved provider from the provider component instead of a
    prefix test.
  - The D29 launcher gained the #7 fix's `utilFailures` injection (so a handler's serialization
    failure is reachable from a recording) and sends handler diagnostics to stderr.
  - Live, after merging `origin/feature/phase-4` into the lane branch (workflow
    `Phase 4 Live Gateway G4b`,
    [run 35673872197](https://github.com/sheon-sek/ignition-mcp/actions/runs/35673872197) at
    `16eb362`, both rows green with `drift: {}` on 8.3.8 `2026071409` required and 8.3.9
    `2026082511` candidate): the D10 item ceiling refused a 21-target batch with
    `limit_exceeded` / `itemsOverPolicyLimit` before any native call; a refused Target and a
    refused Precondition both reported `auditRecorded=true`, so the D18 decision row is
    live-proven as well as fixture-proven; the exact UDT definition read was allowed, published a
    `tcf1` token, and that token is what the `_types_` update case handed back; and every earlier
    case still held, including the fingerprint recomputed from the published configuration with
    the corrected (single-encoding) verifier. CI, Phase 0 G0, Phase 3 G3, Phase 4 G4a and the REST
    row are green on the same head.
- Frozen gates, green on the same head that records this evidence: CI, Phase 0 G0 and Phase 3
  G3, plus the Phase 4 G4a and REST rows.

### Ticket #16 — REST `project_import` (milestone 4c)

- Fixture-first coverage: the recorded Gateway now models the Project import the way it
  models the config-resource writes — one competing writer at dispatch time
  (`race_import_with`), one ambiguous status that applies nothing (`fail_imports_with`),
  one refusal carried inside a 200 (`refuse_imports_with`), plus a scheduled external
  change that lands after a given number of exports (`change_project_after_exports`) and
  an out-of-band Project change (`change_project_out_of_band`). The new module
  `test_phase4_project_import.py` (22 cases) drives the real server through MCP against
  that Gateway; it fails before the change (the Tool and its operation do not exist) and
  the full `AGENTS.md` command block is green (807 pytest cases).
- **D30 §2 and the D16 reconcile rule, in one place.** `expectedFingerprint` is the
  caller's `pcf1` token from `project_export`, and the transaction compares it with
  baseline A before it stages a candidate, backs anything up or dispatches; a mismatch
  ends the transaction `CONFLICTED` (`importAttempted=false`) with the D30 §2/§7
  `conflict` code and the export is cleaned up. The transaction itself declares
  `PROJECT_IMPORT_TOOL_OPERATION` — the same `project_import` operation with
  `target_denial_code="permission_denied"` and `rejection_is_final=True` — because the
  frozen G3 harness and its evidence record `operation_disabled` and the pre-Phase-4
  rejection behaviour for the same op id; the frozen operation is untouched (a unit test
  pins the split, and `PROJECT_IMPORT_OPERATION` keeps `operation_disabled`/`False`).
  With `rejection_is_final`, a 4xx rejection is the result: no read-back can turn it into
  a success, so a Project that shows the candidate after a rejected dispatch is never
  credited to this caller (pinned by a test where a competing writer lands exactly the
  candidate B at dispatch time). `recovered_success` stays reachable only the way D16
  says it is — an ambiguous dispatch (possibly sent with no response, or 5xx) whose
  post-import export C equals the staged candidate B — and it is reachable because the
  candidate is staged and fingerprinted under the store before dispatch and D16's no-op
  short-circuit guarantees B differs from A; all three branches (C == B, C == A, foreign
  C) are pinned through the Tool, including the recovery lock the last one keeps.
- **The D08 chain now runs before the transaction's work.** `safety/executor.py`
  extracts `preflight_mutation` (principal, scope, class, operation allowlist, the D30 §5
  Target-class rule, every Target allowlist, the Precondition hook) out of
  `execute_mutation` and the Tool calls it before the writer lock, the baseline export
  and any staging. A Target the allowlist does not name is therefore `permission_denied`
  without exporting a Project the deployment said not to touch and without a transaction
  row, the audited reason is the one the executor would have written (one
  implementation, two callers), and the executor's own preflight remains authoritative —
  no gap opens if the deployment changes mid-call.
- Artifact input (D30 §6/D17): a READY `project_archive` or `project_export` visible to
  the same Mutation principal; anything else answers `not_found`, a non-archive kind is
  `invalid_argument`, and the candidate pass re-validates the archive through the D15 ZIP
  gate, so an unsafe archive fails `invalid_argument` before any dispatch (pinned with an
  artifact published straight through the store, since no public ingress can make one
  READY).
- Terminal-state surface (D06): `COMMITTED` and `NO_CHANGE` are returned as data
  (`state`, `transactionId`, `baselineFingerprint`, `candidateFingerprint`,
  `resultFingerprint`, `importDispatched`, `designerWarning`); every other D16 terminal
  state raises the D30 §7 error with the state and the transaction id named in the
  message, and the operation record is linked to the transaction (`set_transaction`), so
  `operation_diagnose` follows a refused or unresolved import back to it. The contract
  spells the whole mapping out (`transaction.terminalStateSurface`) and the linter
  requires it to cover every terminal state.
- Wiring: `project_import` is registered as a CONFIG-scope, destructive, audited Tool,
  gated by `IGNITION_MCP_CONFIG_MUTATION_ENABLED` and the `project_import` capability
  (already derived from the documented import route); contract, output schema, audit
  allowlist, inventories and the structural/destructive pins were updated together, and
  `tooling/contracts/lint.py` learned the per-Tool applicability D30 implies (the Refused
  resource types rule and D03 body validation govern config resources, not a Project
  import, while a reachable recovered success must cite D16).
- Local rehearsal: `tests/harness/phase4-live-rest/rehearse_local.py` — **61/61 cases**
  against the recorded Gateway, both deployment gates. The live harness now provisions
  and verifies two disposable Projects (the allowlisted Target and a Project the
  allowlist does not name) and enables the sensitive exports, artifact upload and the D16
  writer in its server environment, which is why the driver's expected read inventory
  includes the two sensitive-export Tools.
- Live ([run 35658893521](https://github.com/sheon-sek/ignition-mcp/actions/runs/35658893521)):
  workflow `Phase 4 Live Gateway REST mutation`, both rows green — **61/61 live cases on
  8.3.8 (`2026071409`, required) and on 8.3.9 (`2026082511`, candidate)** — the exact
  inventory with the class enabled and disabled, a Project archive imported and confirmed
  by an independent re-export this harness fingerprints itself, the marker the candidate
  carried present in the Project the Gateway now serves, re-importing that content a
  `NO_CHANGE`, the pre-commit fingerprint a `conflict` that changed nothing, a Project
  outside the Target allowlist `permission_denied` with that Project untouched, and an
  archive another principal owns `not_found`. `provision.json` records the two provisioned
  Projects and the required OpenAPI routes.
- **Review round 1 (`16-review-1.md`) raised two blockers, both fixed on `p4/rest-fix`
  (base `p4/rest`).**
  - *A restart could turn a known refusal into a recovered success.* The row persisted
    `IMPORT_SENT` before the dispatch and only wrote the answer afterwards, so a process
    that died once the Gateway had answered and before the terminal write left a row the
    reconciler read as an ambiguous dispatch: it re-exported, found the candidate B a
    competing writer had landed, and reported `COMMITTED`. The fix makes the dispatch
    classification durable. The guarded executor now classifies every answer once, the
    moment it exists and before any verification or read-back, and hands it to
    `MutationRequest.on_dispatch_boundary`; the transaction persists it in the new
    `project_transactions.dispatch_boundary` column (forward-only DDL version 4) together
    with the raw dispatch outcome, status and error code. The value written before the
    dispatch depends on the operation: `unattributable` when a refusal is final for it
    (D30 §2) and `attributable` otherwise, which is exactly what a pre-Phase-4 row meant.
    Restart reconciliation now acts on the recorded class and never replays an import:
    `not_sent` and `refused` end `NOT_APPLIED` **without any read-back** (exporting the
    Project could only misread another writer's identical content as this call's success),
    `claimed` needs a re-export equal to candidate B and is `RECOVERY_REQUIRED` otherwise,
    `unattributable` is `NOT_APPLIED` only when the Project still equals baseline A and
    `OUTCOME_UNKNOWN` otherwise — never `COMMITTED` — and `attributable`, like a row with
    no recorded class, follows D16's comparison unchanged. Frozen G3 rows carry no class,
    so every Phase 3 reconciliation branch behaves exactly as before (the five new
    mapping cases are pinned in `test_phase3_transactions.py`; the process-death window
    end-to-end, with candidate B present, in `test_phase4_project_import.py`).
  - *`importDispatched` contradicted its own published description.* The schema and the
    model documented `false` for a commit recovered from an ambiguous dispatch while the
    implementation returned `true`. The field's meaning is now declared once and used
    everywhere: it answers whether an import request left the server — true for a commit
    the response confirmed and for one recovered from an ambiguous dispatch, false only
    when nothing was sent (`NO_CHANGE`, or a refusal or non-attempt before any byte left
    the process). That is also what the frozen G3 evidence already records (`true` on
    `COMMITTED`, `false` on `CONFLICTED`/`NO_CHANGE`), so no committed evidence changes.
    The contract declares it (`transaction.importDispatched`) and `tooling.contracts.lint`
    requires the declaration, the same way it requires the terminal-state surface.
  - The refusal reader the contract's `rejectionPolicy` already promised is now wired:
    a refused import reported inside a 200 (`{"success": false, "problem": {...}}`) is a
    known rejection (`conflict`, the Gateway's own text never reaches the caller) instead
    of a claim to confirm. Before the fix that response was read as a claim, and with a
    competing writer landing the identical content it was reported as this call's
    `COMMITTED`.
  - Contract/schema/lint alignment: `transaction.dispatchBoundary` declares the durable
    vocabulary and the restart rule, `transaction.importDispatched` declares the field's
    semantics, and three contract-lint drift cases require both to stay declared.
  - Verified on the fix head `653f7b9` (merge of `origin/p4/rest` at `603e0f6`, so the
    lane's #17/#18 work is under the fix): the full `AGENTS.md` command block is green
    (878 pytest cases, ruff, mypy strict, `tooling.contracts.lint`, the workflows check,
    both deterministic Runtime builds and the release, and `sync_schemas` leaving the tree
    clean) and the local live rehearsal against the recorded Gateway is **82/82 cases**.
    The pushed branch ran CI
    [35666670814](https://github.com/sheon-sek/ignition-mcp/actions/runs/35666670814),
    Phase 3 Live Gateway G3
    [35666670824](https://github.com/sheon-sek/ignition-mcp/actions/runs/35666670824) —
    **both Gateway rows success, so the frozen G3 behavior and its evidence still replay**
    — and Phase 4 Live Gateway REST mutation
    [35666670831](https://github.com/sheon-sek/ignition-mcp/actions/runs/35666670831) —
    **82/82 live cases, 0 failures on 8.3.8 (`2026071409`, required) and on 8.3.9
    (`2026082511`, candidate)**, including all 13 Project import cases
    (`project-import-commits` `COMMITTED`, `project-import-reports-the-dispatch` true,
    `project-import-no-change-dispatches-nothing` false, and
    `project-import-stale-fingerprint-is-conflict` `conflict`). Draft PR
    [#31](https://github.com/sheon-sek/ignition-mcp/pull/31) (base `p4/rest`) carries the
    fix, and **every head of it ran the same three workflows green**: the documentation
    head `98ac7b0` (CI
    [35667330968](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667330968), G3
    [35667331990](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667331990),
    REST [35667331015](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667331015))
    and the logging/typing cleanup head `6a963c9` (CI
    [35667808523](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667808523), G3
    [35667808580](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667808580),
    REST [35667806718](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667806718),
    again 82/82 cases with 0 failures on both rows).
- **The first live attempt failed both rows, and the fix is in the harness.** Run
  [35658093734](https://github.com/sheon-sek/ignition-mcp/actions/runs/35658093734) on
  the code head returned `RECOVERY_REQUIRED` for the commit case: the candidate archive
  appended a root entry, and the live Gateway rewrites `project.json` on import and does
  not carry an entry it does not recognise as a resource into its re-export, so C could
  never equal B. The driver now appends the marker to the first named-query payload — the
  edit the G3 transaction case proves the Gateway stores verbatim (see
  `tests/harness/phase3-live/driver.py`) — and writes the per-entry round-trip diff of the
  candidate against the Gateway's re-export into `observations.json` before it fails, so
  one run diagnoses a mismatch instead of costing another. No server code changed.
- Frozen gates, green on every head of this ticket (`c83e45a` and `a2a7ef0`): CI
  [35658093748](https://github.com/sheon-sek/ignition-mcp/actions/runs/35658093748) and
  [35658893377](https://github.com/sheon-sek/ignition-mcp/actions/runs/35658893377),
  Phase 3 Live Gateway G3
  [35658093689](https://github.com/sheon-sek/ignition-mcp/actions/runs/35658093689) and
  [35658893366](https://github.com/sheon-sek/ignition-mcp/actions/runs/35658893366), and
  Phase 4 Live Gateway G4a
  [35658093769](https://github.com/sheon-sek/ignition-mcp/actions/runs/35658093769) and
  [35658893421](https://github.com/sheon-sek/ignition-mcp/actions/runs/35658893421) — all
  success. The 8.3.9 candidate row of the Phase 4 REST workflow is `continue-on-error`,
  but both of its Gateway rows passed on the head above.


### Ticket #17 — REST `tag_config_import` (milestone 4c)

- Fixture-first coverage: the recorded Gateway now models a provider's Tag state —
  `seed_tags`, a path-scoped JSON export, and an import that applies the document with
  D30 §4's `Abort` collision refusal — plus the knobs the cases need: a competing writer
  at dispatch time, a failure reported inside a 200 in either observed wire shape (the
  summary object live 8.3.8/8.3.9 answer, and the QualityCode list the committed OpenAPI
  documents), an ambiguous status that applies nothing, a partial application, and a
  clean claim that creates nothing. The new module
  `test_phase4_tag_config_import.py` (28 cases) drives the real server through MCP
  against that Gateway; it fails before the change (the Tool and its operation do not
  exist) and the full `AGENTS.md` command block is green (835 pytest cases).
- **D30 §4** fixes the only caller-chosen knob: `collisionPolicy=Abort` is always sent,
  so the Tool creates Tags and can never overwrite one. The dispatched body is the
  artifact's own bytes (a test pins the request body's SHA-256 to the artifact ref), and
  `tooling/contracts/lint.py` requires the contract to declare exactly that knob.
- **D30 §2 has no Precondition token here** — the collision policy is the concurrency
  rule instead. It is checked against the Gateway before dispatch (a destination that
  already holds a declared Tag is a `conflict` that sends nothing), and the Gateway's own
  `Abort` refusal inside a 200 is mapped to `conflict` (`qualitySubCode` 527) or, for any
  other reported failure, to `upstream_error`. `rejection_is_final=True`, so a refusal is
  never reconciled into a success — pinned by a case where a competing writer lands the
  whole document at dispatch time.
- **Attribution is the #14/#15 rule, shared.** The D30 §2 verdict moved to
  `safety/verification.py` (`verdict`), so the config-resource Tool and this one cannot
  drift: a claimed success is confirmed only by the bounded re-export, an ambiguous
  dispatch is `outcome_unknown` (a re-export showing the intended Tags proves nothing
  about who wrote them) or `not_applied` when nothing landed, and `recovered_success`
  stays unreachable.
- **Verification (D30 §6 Observed state).** A bounded re-export of the same provider and
  path is compared with the Tag paths the document declares; `observedState.present`
  names them and `observedState.missing` is empty on every returned result, because a
  declared Tag the re-export does not show makes the call `recovery_required` whose
  message names up to five of them. A Gateway that claims success and creates nothing is
  pinned by a fixture case, and a partial application — some Tags created, some reported
  failed — is `recovery_required` rather than a per-item success (D30 §3's per-item
  surface is Preflight's; a partly-applied import is not a claim).
- Input bounds (D10): the artifact must be a READY `tag_config_export` visible to the
  Mutation principal (anything else `not_found`, another kind `invalid_argument`), read
  under an 8 MiB ceiling and parsed before dispatch; at most 500 declared Tag paths and
  32 KiB of declared path bytes, each path at most 1024 bytes; an oversize document fails
  `limit_exceeded` with nothing sent. The Target is the exact provider-qualified path
  `[provider]path` (D30 §3/§7), a denial is `permission_denied` before the artifact is
  read, and the path is refused when it could name two targets (`/a`, `a/`, `a//b`,
  `a/../b`). `_types_` (D30 §6): a document declaring the provider's UDT folder may only
  be imported into that explicit path.
- Wiring: registered as a CONFIG-scope, non-destructive, audited Tool with the ARTIFACT
  budget class, gated by `IGNITION_MCP_CONFIG_MUTATION_ENABLED` and a new
  `tag_config_import` capability derived from the documented `POST /tags/import` route;
  contract, output schema, audit allowlist, inventories and the lifecycle-routing pin were
  updated together.
- Local rehearsal: `tests/harness/phase4-live-rest/rehearse_local.py` — **72/72 cases**
  against the recorded Gateway, both deployment gates.
- Live ([run 35662815877](https://github.com/sheon-sek/ignition-mcp/actions/runs/35662815877)):
  workflow `Phase 4 Live Gateway REST mutation`, both rows green — **72/72 live cases on
  8.3.8 (`2026071409`, required) and on 8.3.9 (`2026082511`, candidate)**, 11 of them the
  Tag import cases: the tool inventory with the class enabled and the read-only inventory
  without it, a real `tag_config_export` artifact imported into an allowlisted destination
  and confirmed by a second, independently downloaded export of that destination, the
  source path untouched, a re-import of the same document a `conflict` that changed
  nothing, a destination outside the Target allowlist `permission_denied` with none of the
  source Tags at it, and an export another principal owns `not_found`. `provision.json`
  records the disposable provider, the source Tags and the import conventions below.
- **The import document rule is live-proven, both shapes, in every row.** Run
  [35663426202](https://github.com/sheon-sek/ignition-mcp/actions/runs/35663426202)
  records `tagProvider.convention`: importing a document whose root names its own node
  into a throwaway path created `convention_probe_named/Probe` and
  `convention_probe_named/Probe/Leaf`, and importing a provider-root document created
  `convention_probe_flat/Flat` — the two readings the Tool's declaration follows (a named
  root is imported under the request path, a nameless root contributes its children). The
  source Tags and both probes took one import attempt each on both Gateway rows.
- **The first live attempt failed its Tag section on both rows, and it exposed two real
  defects.** Run
  [35661939628](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661939628)
  reported `outcome_unknown` for the import: the artifact was the export of a *sub-path*,
  whose root names its own node (`{"name": "source", "tagType": "Folder", ...}`), and the
  Tool had declared only the document's `tags` — a wrapper rule that only the nameless
  provider-root document had ever proved live, so every declared path was reported
  missing. The Tool now declares what the document declares (a named root is imported
  under the request path; a nameless root contributes its children), which the live
  destination confirms — the Tag the import created there is named `source`. Second, the
  driver failed *open*: with no structured result it recorded the abort and returned
  without a failing case, so the workflow was green with a broken Tool. It now records
  the destination and the source as the Gateway serves them and adds the failing case
  before returning. `provision.py` also probes both document shapes into throwaway paths
  and re-exports the provider root, so every row records where the Gateway really puts
  each shape (`tagProvider.convention`) instead of leaving the rule an assumption.
- **Review round 1 (codex) found three blockers, all fixed on `p4/rest-fix17`** (the
  review report and `17-fix-1.md` record the findings and the fix):
  - **The reserved policy provider is refused (D30 §1).** The #6 research note
    identified `IgnitionMCPPolicy` as the provider the Runtime Target Policy lives in and
    required every Tag Mutation to refuse it whatever the Target allowlist says, so the
    policy cannot be overwritten or extended. `safety/reserved_tag_providers.py` holds the
    reserved set, and the Tool hands its decision to the D08 chain as the operation's
    Target-class rule (`target_policy`), which runs *before* the Target allowlist — the
    same ordering D30 §5 gives a Refused resource type — so neither `*` nor an entry that
    names the provider reaches it. The comparison is case-insensitive and the audited
    reason names the reserved provider rather than the caller's spelling. The contract
    declares the provider and the linter refuses a Tag Target that does not; four MCP
    cases cover the provider root, a nested path, the policy Tag itself, a folded
    spelling, an explicitly allowlisted provider, and the ordering (the refusal precedes
    the artifact read, so even an unknown artifact is answered by the policy).
  - **The Target allowlist matches provider-qualified path prefixes at segment
    boundaries (the issue, D30 §1).** `MutationOperation.target_match` declares the rule
    per operation: `exact` stays the membership rule for every Target that is not a
    provider-qualified Tag path (the frozen Phase 3 machinery and its G3 evidence are
    untouched), and `provider_prefix` is this Tool's, so `[MCP_CI_TAGS]target` authorizes
    `[MCP_CI_TAGS]target/sub`. Boundaries and the provider qualifier are enforced exactly:
    `[MCP_CI_TAGS]target` does not reach `[MCP_CI_TAGS]target2`, `[MCP_CI_TAGS]` does not
    reach `[MCP_CI_TAGS_OTHER]target`, and an entry with no path is the provider root and
    covers that provider. Two live cases prove both directions on the Gateway.
  - **A malformed import report is uninterpretable, never a success (D30 §2).** The
    report reader accepted `{"failureCount": -1, "failures": []}` as an explicit
    zero-failure claim, so a real import with an unreadable report could be returned as
    `succeeded`. A count that is negative, not a number, or a boolean, a `failures` value
    that is not a list, and a count that disagrees with its own failure list are now all
    uninterpretable: no claim and no refusal is read from them, so the call ends
    `recovery_required`/`outcome_unknown`. The recorded Gateway grew one modelled knob
    (`answer_tag_import_with`, a 2xx body substituted while the transition still applies)
    and eight shapes are covered — the boundary case (the documented empty QualityCode
    list) still reports a clean import as a success, so the rule is pinned on both sides.
- **The fix heads were validated and proven live.** `p4/rest-fix17` carries the fixes plus
  a merge of `origin/p4/rest` (the branch had no merge commit after #18 landed, so no
  workflow could run on it). Full `AGENTS.md` block green: 883 pytest cases, mypy strict
  clean, contract lint, workflow lint (7 files, 107 run blocks), native validate, compat
  evidence (6 rows, no SUPPORTED claim). Local rehearsal **86/86** (both gates; 12 of the
  cases are the Tag import section). Live on head `aa731a5`, both Gateway rows green at
  **86/86 cases each** — `Phase 4 Live Gateway REST mutation`
  [35666536297](https://github.com/sheon-sek/ignition-mcp/actions/runs/35666536297),
  with `tag-import-under-the-allowlisted-prefix-applies`,
  `tag-import-prefix-destination-serves-every-source-tag`,
  `tag-import-reserved-policy-provider-is-permission-denied` and
  `tag-import-reserved-policy-provider-says-which-rule` ok on 8.3.8 and 8.3.9. The
  refusal's own message is in both rows' `observations.json`
  (`tagReservedProviderResult`: `permission_denied`, "the target is inside the reserved
  Runtime Target Policy provider…"), and `tagNestedNames` shows the nested destination
  serving the source Tags. Frozen gates on the same head: CI
  [35666536324](https://github.com/sheon-sek/ignition-mcp/actions/runs/35666536324), Phase 3
  Live Gateway G3
  [35666536299](https://github.com/sheon-sek/ignition-mcp/actions/runs/35666536299), Phase 4
  Live Gateway G4a
  [35666536363](https://github.com/sheon-sek/ignition-mcp/actions/runs/35666536363) — all
  success. The docs-only heads `75610c6` and `ccf43e8` repeat all four green: CI
  [35667023607](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667023607) and
  [35667665418](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667665418), REST
  mutation [35667023701](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667023701)
  and [35667665425](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667665425)
  (both rows **86/86**, 0 failures), G3
  [35667023714](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667023714) and
  [35667665510](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667665510), G4a
  [35667023689](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667023689) and
  [35667665448](https://github.com/sheon-sek/ignition-mcp/actions/runs/35667665448). Every
  later docs-only head re-triggers the same four and was verified green the same way.

- Frozen gates, green on every head of this ticket (`89c8b52`, `8e2745a`, `9d25d98`):
  CI
  [35661939654](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661939654),
  [35662815732](https://github.com/sheon-sek/ignition-mcp/actions/runs/35662815732) and
  [35663426154](https://github.com/sheon-sek/ignition-mcp/actions/runs/35663426154);
  Phase 3 Live Gateway G3
  [35661939740](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661939740),
  [35662816113](https://github.com/sheon-sek/ignition-mcp/actions/runs/35662816113) and
  [35663426231](https://github.com/sheon-sek/ignition-mcp/actions/runs/35663426231); and
  Phase 4 Live Gateway G4a
  [35661939632](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661939632),
  [35662815762](https://github.com/sheon-sek/ignition-mcp/actions/runs/35662815762) and
  [35663426276](https://github.com/sheon-sek/ignition-mcp/actions/runs/35663426276) — all
  success. The first REST run on `89c8b52` is also recorded as success by the workflow;
  that green is what the driver's fail-open path produced, and the head after it is where
  the Tag cases are proven. The REST workflow ran on all three heads; the run cited above
  is the one on `9d25d98`, and the earlier two are
  [35661939628](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661939628) (the
  failing attempt) and
  [35662815877](https://github.com/sheon-sek/ignition-mcp/actions/runs/35662815877).

### Ticket #18 — REST `alarm_pipeline_cancel` (milestone 4c)

- Fixture-first coverage: the recorded Gateway now models Alarm Notification Pipeline
  runtime state — `seed_pipeline` publishes the runs one exact path serves, the status
  route answers from it, and the documented cancel route is a `DELETE` that takes its two
  facts in a JSON body — plus the knobs the cases need: a competing cancel at dispatch
  time (`race_cancel_with`), a claim that leaves the run in place
  (`claim_cancels_without_applying`), a 2xx that claims nothing
  (`answer_cancels_without_claiming`), a refusal inside a 200 (`refuse_cancels_with`) and
  an ambiguous status that applies nothing (`fail_cancels_with`). The new module
  `test_phase4_alarm_pipeline_cancel.py` (32 cases) drives the real server through MCP
  against that Gateway; it fails before the change (the Tool and its operation do not
  exist) and the full `AGENTS.md` command block is green (867 pytest cases).
- **D30 §6, exactly.** The Target is the caller's own pipeline path, matched exactly:
  `alarm_pipeline_list` reports that string, the deployment's Target allowlist names it,
  and a path under it — or its own parent — is a different Target
  (`permission_denied`). A caller-supplied `*` is `invalid_argument`, because the
  wildcard is an allowlist entry and not a pipeline.
- **D12, exactly.** The cancel touches the pipeline runtime route only: the bounded status
  read and the documented `DELETE`. It never acknowledges or clears the Alarm Event, and a
  case enumerates every Gateway route the call touched to keep it that way.
- **D30 §2 gives this Tool no Precondition token, so the Tool establishes the pre-state
  itself.** A destructive cancel's post-state is absence, and absence proves nothing on
  its own — another operator can cancel the same run — so the Tool reads the same bounded
  `alarm_pipeline_status` before it dispatches and refuses `not_found` when the pipeline
  holds no run for that alarm event, dispatching nothing. That read is also the
  verification, so the Observed state the caller gets is what `alarm_pipeline_status`
  would have answered, plus the one comparison this Tool makes.
- **The D10 bound is about coverage, not page size.** The Tool reads one page of 100 runs;
  when that page cannot cover every run the Gateway matched, the call fails
  `limit_exceeded` naming the matched count and the limit, and nothing is dispatched. A
  pipeline whose last run is the page's last item hides nothing, so its absence is
  definitive — both cases are pinned.
- **Attribution (D30 §2) in one place.** `rejection_is_final=True`: a 4xx, or the 2xx the
  route documents carrying `success=false`, is the result and is never reconciled into a
  success — pinned by a case where another writer makes the run disappear at dispatch
  time. A claimed success is confirmed only by a re-read that no longer reports the run; a
  claim whose run is still reported is `recovery_required`; an ambiguous dispatch is
  `not_applied` when the run is still there and `outcome_unknown` when it is gone, so
  `recovered_success` stays unreachable. A 2xx this Tool cannot read as a claim is not a
  success either (`recovery_required`, never a success).
- Wiring (D07/D30 §7): registered as a CONTROL-scope, destructive, audited Tool gated by
  `IGNITION_MCP_CONTROL_MUTATION_ENABLED` and a new `alarm_pipeline_cancel` capability
  that requires *both* documented pipeline routes — a Gateway whose status route is
  missing cannot be read back, so it exposes no cancel. Contract, output schema, audit
  allowlist and inventories moved together, and `tooling/contracts/lint.py` now derives
  each mutation contract's permission class and scope from its mutation class instead of
  assuming `CONFIG`, so a CONTROL Tool can no longer be declared with a CONFIG surface.
- Local rehearsal: `tests/harness/phase4-live-rest/rehearse_local.py` — **82/82 cases**
  against the recorded Gateway, both deployment gates and all three credentials.
- Live: pending — recorded below once the runs are in.

## Open questions

- **Ticket #7 — the D10 deployment override is two new optional Policy fields.**
  D10's budget layers are "project safe default → deployment override → absolute
  hard ceiling", and the Runtime plane's deployment-owned document is the Runtime
  Target Policy, so the override is `tagWriteMaxWrites` (1..100) for `tag_write`
  and `alarmMaxPaths` (1..100) for both Alarm Mutations; each handler validates
  its own field and fails closed (`operation_disabled`) outside that range, and
  the contract linter requires the field to be a property of the document schema.
  D30 §1's list of what the document holds does not name a budget field, so the
  two names are this ticket's choice, sitting alongside the already-decided
  `alarmShelveMaxSeconds`. **For the owner:** confirm the field names (a single
  shared budget key is the alternative).
- **Ticket #7 — a denied target under `required` audit mode fails closed on the
  denial row too.** D18 requires denied mutations to be audited and says
  `required` mode fails during preflight when required audit cannot be provided.
  A denial is refused either way, so the shipped behavior is: the audit-profile
  check runs first (as D30 §6 says), a refusal then writes one `decision` row, and
  when `required` mode cannot write that row the Tool reports
  `operation_disabled` (`reason: auditAttemptFailed`, `phase: decision`) instead of
  `permission_denied`. **For the owner:** confirm, or report the denial with
  `auditRecorded=false` in its details under `required` mode as well.
- **Ticket #7 — the G4a live assertions still stop before the new bound and
  audit cases.** The D29 fixtures prove the D10 ceilings, the denial decision row
  and the Observed-state budget, and the next G4a run exercises the changed
  handlers through every existing live case on both rows, but no live case submits
  a 21-write batch or reads a denial's audit rows yet: `tests/fixtures/recorded/gateway-8.3/phase4/`
  holds payloads recorded before this change, so a driver case asserting the new
  fields would drift the rehearsal until the payloads are re-recorded from a live
  run. **For the owner:** acceptable, or spend the next live run recording those
  payloads so the driver can pin them.
- **Ticket #10 — milestone 4b gets its own live workflow, reusing the `phase4-live` environment.**
  `.github/workflows/phase4-live-g4b.yml` verifies `tag_get_config`/`tag_update` on both Gateway rows
  and reuses the `phase4-live` GitHub environment unchanged, so it inherits the already-recorded
  owner-accepted deviation (no protection rules) with the same compensating controls (trusted-repo
  guard, no repository or environment secrets, compose-localhost endpoints only, run-unique Tag
  paths, and the driver's CI-marker plus Gateway-identity guard). The marker label and the
  `gatewayId` are milestone-scoped (`g4b`), so a rehearsal or a G4a run can never satisfy the G4b
  guard. **For the owner:** confirm the per-milestone workflow split, or fold 4b into the G4a
  workflow if one evidence bundle per milestone is not wanted.
- **Ticket #10 — the recorded `phase4` bodies for this ticket are modelled until the live run
  re-records them.** `tests/fixtures/recorded/gateway-8.3/phase4/tag-config.json` (the fixture Tags'
  configurations the fake serves) and the six `tag-update-*.json` refusal bodies are modelled from
  the shipped handler's shape plus the ticket #6 recorded read (the AtomicTag key set: `dataType`,
  `defaultValue`, `enabled`, `name`, `path`, `tagType`, `value`, `valueSource`), because no live
  Gateway is reachable from the workstation. The live run's own results are the evidence; a
  follow-up commit replaces the modelled bodies with the recorded ones once the run exists, the way
  tickets #7 and #8 recorded theirs. Two differences are modelled rather than recorded and are
  called out here: the fake answers a Tool Error where a live Gateway answers a synthesized node
  for a configuration read of a missing path, and its `default` provider export is derived from the
  configuration it serves.
- **Ticket #10 — a Gateway answers a configuration read for a path that is not there.** The
  first live `tag-update` run (workflow `Phase 4 Live Gateway G4b`, run 35666588649, both rows)
  failed at the missing-target case: `system.tag.getConfiguration("[default]IgnitionMCP_CI/Missing",
  false, false)` returned a node, and its fingerprint was the *same* on 8.3.8 and 8.3.9
  (`tcf1:1d225d79b0b34008df860a452a087d89e743974be538f42a7c4e4dc075216e30`), so a synthesized
  default configuration, not an empty read. The configuration read therefore cannot be the
  existence check D30/D11 need (`not_found`, and a create that must never happen), and
  `tag_update` now calls `system.tag.exists(path)` per item before it reads that item's
  configuration. That is the documented 8.3 scripting primitive for exactly this question. A raise
  or a non-boolean answer from it is `upstream_error` (`existenceCheckFailed` /
  `existenceCheckIndeterminate`), never read as "present". The harness proves absence
  independently through the provider's own `GET /tags/export`. **For the owner:** nothing to
  decide; recorded because it is a Gateway behavior the plan did not predict and because the
  recorded fake still answers a Tool Error for a missing path, so a rehearsal models this
  difference rather than the Gateway.
- **Ticket #10 — a Gateway-side provider flake can fail the G4a `policy-read` stage.** On the
  `0b50a52` head the 8.3.8 row of `Phase 4 Live Gateway G4a`
  ([run 35668653059](https://github.com/sheon-sek/ignition-mcp/actions/runs/35668653059)) failed
  after the workflow's Gateway restart: the provider's initial Tag load hit
  `java.lang.NullPointerException: … "cleanPath" is null` in `TagConfigResource.createResourcePath`
  once per Tag and then reported `Error_Configuration` for the policy Tag with a `Bad_Unsupported`
  write probe, so the stage never saw the document it had just verified through Native REST. The
  8.3.9 row of the same run passed, the same head's provisioning stage found the provider ready and
  its export correct, and an immediate `gh run rerun --failed` of that job passed — the same class
  of Gateway-side provider-startup flake the ticket #6 evidence recorded and that
  `phase4/tag-import-provider-not-ready.json` already documents. **For the owner:** no decision;
  recorded because a G4a or G4b row can fail this way on an unmodified head and needs a rerun.
- **Ticket #10 — the missing lane-head runs after the outage were a CONFLICTING pull request, not
  the outage (corrected).** Actions recovered at 00:29Z and other lanes got runs immediately, but
  `p4/runtime` and a throwaway branch pointing at the same commit still created none. The cause was
  `feature/phase-4` moving 23 commits ahead of the lane branch, which left PR #27 `CONFLICTING` /
  `DIRTY`; GitHub does not build a merge commit — and therefore creates no `pull_request` workflow
  run — for a conflicted head. Merging `origin/feature/phase-4` into `p4/runtime` made the PR
  `MERGEABLE` and every workflow run again on `16eb362`. The lesson for the coordinator: a lane
  that stops producing runs should be checked for `mergeable_state` before it is written off as an
  outage; the outage itself (23:49:41Z to 00:29:12Z) was real and affected every branch.
- **Ticket #10 — GitHub Actions stopped creating `pull_request` runs for the lane head.** After
  the `p4/runtime-fix` batch created its six runs at 23:49:41Z, no workflow run was created for the
  repository at all: three pushes to `p4/runtime` (`25c8516`, `b5dde77`, `8593cb8`), a close/reopen
  of draft PR #27 and a rerun of an older G4a row produced no new `github-actions` check suites for
  those heads (only the `claude` app's suite appears), while the platform had accepted the same
  repository's runs minutes earlier. A probe pull request (#33, opened and immediately closed, from
  a throwaway branch at the same head) fired nothing either, so the cause is repository- or
  account-wide rather than specific to this pull request. The lesson for the coordinator: the head's live row is
  outstanding for that reason, not for a red result. Everything the head changes *after* the last
  live row is either documentation or the harness's own batch-case expectation; the shipped
  `tag_update` handler the live row exercised (`system.tag.exists` included) is byte-identical on
  the head, and the local rehearsal plus the D29 and lint suites cover the rest. **For the owner:**
  re-trigger the `phase4-live-g4b` workflow on the head when Actions accepts runs again, and confirm
  whether the account's Actions limit was the cause.
- **Ticket #10 — `tag_update` refuses three configuration keys beyond D30's text.** D30 §6
  says nothing about which properties a Tag CONFIG Mutation may merge, so the shipped handler
  refuses, with `invalid_argument`, the three keys that would leave its class or its target:
  `value` (a Tag value write is `tag_write`'s, and `tag_update` is CONFIG — a CONFIG allowlist
  must not be able to do what a CONTROL Tool does), `tags` (a child is its own target with its
  own fingerprint, and the Preflight read is non-recursive, so a nested child change would sit
  outside the token that authorized it), and a `name` that differs from the target's own leaf
  (renaming is `tag_rename`'s, which checks the new path against the Target allowlist; without
  this rule, `tag_update` would move a Tag to a path nothing checked). **For the owner:**
  confirm, or name the keys a CONFIG merge should allow.
- **Ticket #10 — the D30 §6 `_types_` rule reads as "an entry that itself names `_types_`".**
  The decision says a UDT definition (`[provider]_types_/…`) is reachable "only when the Runtime
  Target Policy lists an explicit `_types_` prefix, and a bare `*` does not cover it". The shipped
  rule is therefore: for a target with a `_types_` segment, at least one matching allowlist entry
  must also carry a `_types_` segment. That makes `[default]_types_/IgnitionMCP_CI` reach
  `[default]_types_/IgnitionMCP_CI/ProbeType`, and refuses both `*` and a plain
  `[default]IgnitionMCP_CI` entry. **For the owner:** confirm, or say whether the entry must
  repeat the target's own `_types_` prefix literally.
- **Ticket #10 — review round 1 adopted the #7 fix's D10 shape, including one shared linter helper.**
  `_check_input_bounds` is byte-identical to the helper the #7 fix introduces, but this lane calls it
  from the CONFIG path while that fix calls it from the shared `_check_runtime_mutation`, and this
  lane's Policy-field loop skips a contract that does not declare `inputBounds` yet (`tag_write` and
  the Alarm Tools on this lane). The merged tree checks every Mutation once the two lanes are
  together. **For the owner:** no decision; recorded so a merge resolver keeps both call sites.
- **Ticket #10 — the UDT definition *positive* case is still fixture-proven.** The live `_types_`
  cases now read the definition's token with `tag_get_config` and hand that exact token back, which
  proves the read is allowed and the token flows, but the disposable Gateway has no UDT definition,
  so the flow still ends at the existence check (`not_found`). A live definition update needs a probe
  that creates one; #11/#12 exercise the same handler path.
- **Ticket #10 — the `tag_update` output ceiling is enforced but not fixture-provable.** D10
  says an oversize structured output must fail explicitly rather than truncate, and the handler
  keeps that ceiling (256 KiB). It cannot be covered by a D29 fixture: the runner refuses a
  recorded fixture over the same 256 KiB, so a fixture large enough to overflow the output is
  itself rejected before Jython starts. The live cases stay far under it. The oversize case the
  L5 matrix asks for is the *input* one (`items` over the hard maximum, fixture-covered), and
  #23 should either accept that scope or find a bound that is provable.
- **Ticket #10 — a UDT definition update and a Folder target are fixture-proven only.** The live
  cases prove the two D30 §6 refusals (under `*` and under a plain prefix) and that an explicit
  `_types_` entry is honoured (the target then reaches the existence check and answers
  `not_found`), but they do not create a UDT definition and merge into it, and no live case
  targets a Folder. Both paths are covered by fixtures, and creating a UDT definition on the
  disposable Gateway would need a new probe. **For the owner:** decide whether G4 needs a live
  UDT-definition update; #11/#12 will exercise the same handler path for `tag_create`/`tag_copy`.
- **Ticket #10 — the `_types_` and reserved-provider rules are stated per Tool.** `alarm_shelve`,
  `alarm_unshelve`, `tag_write` and `tag_update` each carry their own copy of the reserved-provider
  refusal (a bundle has no shared modules), and the shared contract text is
  `contracts/shared/tag-config-fingerprint.json` for the token only. **For the owner:** note that
  a future Tool's omission of the rule is caught only by its own tests, not by a shared reader.
- **Ticket #7 — the recorded Gateway fake does not model the Module's MCP session
  requirement.** `tests/harness/recorded_gateway.py` answers `tools/list` and
  `tools/call` without an `Mcp-Session-Id`, while the Module refuses them with
  HTTP 400 until the caller has initialized — which is how the fix's first live
  round failed both G4a rows in `policy-provision` after the local rehearsal and
  the stage's own test were green. The gate now opens its session and a test
  enforces the rule against a small endpoint, but the fake still cannot catch this
  class of bug for any other caller. **For the owner:** accept, or model the
  session in the fake (it needs an `Mcp-Session-Id` response header on
  `initialize` and a 400 for a session-less request).
- **Ticket #18 — a live cancel of a *running* pipeline is not provisioned by this
  harness.** A fresh CI Gateway serves no Alarm Notification Pipeline runs (the Phase 2
  live probe recorded that), and producing one needs an Alarm Event notifying through a
  provisioned `alarm-notification-profile` whose pipeline holds a block that keeps the run
  in flight. This harness provisions none of that, so the live rows prove the whole
  decision surface around the dispatch — the CONTROL class, the scope-by-effect refusal,
  the exact-path Target rule, both D10 input bounds and the bounded pre-dispatch read
  refusing a run that does not exist — while the dispatched-and-verified path (the claim,
  the re-read, the refusal inside a 200, the ambiguous dispatch) is fixture-proven, exactly
  as #16's A-vs-A' drift variant is. **For the owner:** confirm, or ask for a provisioned
  notification profile plus an alarm that notifies through it; that would also record
  whether the Gateway keeps a cancelled run in its status page, the one behaviour this
  Tool's verification reads and this repository has never observed live.
- **Ticket #18 — the pre-dispatch read is the Tool's own attribution requirement.** D30 §6
  names a bounded `alarm_pipeline_status` re-read as this Tool's verification and D30 §2
  gives it no Precondition token, so the Tool reads before it dispatches as well as after.
  That costs one extra bounded read per call, and it is what makes `not_found` — rather
  than a vacuous success — the answer for an alarm event the pipeline is not running.
  **For the owner:** confirm, or amend D30 §2 to give this Tool a token that would make the
  extra read unnecessary.
- **Ticket #18 — a read that cannot cover every match is `limit_exceeded`.** The Tool takes
  one page (100 runs) and treats the read as evidence only when it covers everything the
  Gateway matched; otherwise the state cannot be established and the call fails with the
  D10 code, naming the matched count and the limit. The alternative readings — `not_found`
  (claiming an absence the page did not establish) or `outcome_unknown` (a transport-shaped
  answer for an input bound) — were rejected as less honest. **For the owner:** confirm, or
  name the code you want for a collection whose verification bound cannot cover it.

- **Ticket #17 — the Target of a Tag import is the exact provider-qualified path, not a
  prefix.** The issue says "Target allowlist on the provider and path prefix". What is
  implemented is the D08 Target identity every other Phase 4 Tool uses: the exact
  `[provider]path` (`evaluate_deployment_policy` matches a Target by exact membership),
  where the path is the prefix *under which* the import creates Tags — so an allowlist
  entry `[default]CI/Imports` authorizes exactly that destination, and a call into
  `[default]CI/Imports/Nested` is refused. The alternative reading (an entry authorizes
  any sub-path under it) would need a second matching rule inside the shared deployment
  policy the frozen Phase 3 machinery and its G3 evidence also use. **For the owner:**
  confirm the exact-Target reading, or amend D08/D30 to add a provider-qualified prefix
  rule (the Runtime Target Policy already has one, D30 §1).
  **RESOLVED in review round 1: the prefix rule is implemented, as the issue and D30 §1
  require.** Review found the exact reading a blocker ("changes the requested
  authorization semantics"), and D30 §1 already states the rule the Runtime Target Policy
  uses — "allowlist entries are provider-qualified path prefixes that match only at
  segment boundaries" (`allowlist_match: provider_qualified_prefix_segment_boundary`).
  `MutationOperation.target_match` now carries it per operation (default `exact`, so
  every non-Tag Target and the frozen Phase 3 machinery keep membership), and the
  contract's `targetId.match` plus the linter pin it. The token is uniform across the
  Phase 4 mutation contracts: `alarm_pipeline_cancel`'s existing prose sentence moved
  verbatim into `targetId.matchNote` beside `"match": "exact"`, so the linter can check
  every mutation's rule instead of one arbitrary string.
- **Ticket #17 — a document's root decides which Tag paths the import declares.** The
  first live run showed the assumption mattered: the Tool declared only the document's
  `tags`, while the Gateway also creates the document's *named* root. The implemented rule
  is "the document declares what it declares" — a named root is imported under the request
  path, a nameless root contributes its children — and both shapes are now recorded live
  in every row (`tagProvider.convention`, run 35663426202). A caller whose artifact is a
  per-Tag (rather than per-folder) export is the remaining unprobed case: its root is the
  Tag itself and the rule reads it as one Tag under the path. **For the owner:** confirm,
  or ask for a live case for a leaf-root document.
  **RESOLVED in review round 1: the rule stands; the leaf-root live probe is a coverage
  limitation, not a reason to broaden imports.** Review found the reading reasonable,
  backed by both the named-root and the provider-root live probes, and consistent with the
  parser's root handling; a per-Tag artifact still verifies, because a leaf root declares
  exactly one Tag under the request path and the bounded re-export compares that path.
  Neither the harness nor the Tool changed here.
- **Ticket #17 — an uninterpretable 2xx body is not a success.** The import route's
  documented response is a list of non-Good QualityCodes; live 8.3.8/8.3.9 answer a
  summary object. The Tool reads both, treats a zero-failure report as the Gateway's
  claim (`failureCount: 0` with no failures, or an empty list), and treats anything it
  cannot interpret — including an empty body — as no claim, which ends the call
  `recovery_required`/`outcome_unknown` rather than a success. No live run has produced
  that shape (both rows recorded the summary), so the reading is undecided for a Gateway
  that answers 200 with no body at all. **For the owner:** confirm the fail-closed
  reading, or name the accepted body.
  **RESOLVED in review round 1: the fail-closed reading is confirmed, and the one path
  that violated it is fixed.** Review found the policy correct but the report reader
  accepting `{"failureCount": -1, "failures": []}` as an explicit zero-failure claim, so
  an uninterpretable 2xx could still be returned as a success when the re-export showed
  the Tags. Negative, non-numeric and boolean counts, a `failures` value that is not a
  list, and a count that disagrees with its own failure list are now all uninterpretable
  (no claim, no refusal), the call ends `recovery_required`/`outcome_unknown`, and eight
  fixture cases pin it — including the documented empty QualityCode list, which is still a
  clean claim. Empty and otherwise unparseable bodies keep the same fail-closed reading.
- **Ticket #17 — a partly applied import is `recovery_required`, not per-item data.**
  D30 §3's per-item reporting is about Preflight; a Gateway that reports some successes
  and some failures has neither refused the call nor completed it, so the Tool fails
  closed with `outcome_unknown` and the message names the Tags the re-export was not
  showing. The Runtime plane's per-item QualityCode surface is the `tag_write`
  precedent. **For the owner:** confirm, or ask for a per-item result surface here too.
  **RESOLVED in review round 1: confirmed.** Review found treating a partial report as
  `recovery_required` correct for a single Tool result, because per-item outcomes would
  falsely suggest the call was safely completed. No change.
- **Ticket #17 — `missing` is empty on every returned result by construction.** The
  Observed state carries both lists because that is the comparison the verification made,
  but a declared Tag the bounded re-export does not show is `recovery_required` (an error
  naming up to five of them), so a caller only ever sees `present`. **For the owner:**
  confirm, or ask for the comparison itself to be returned as data on a failed
  verification.
  **RESOLVED in review round 1: confirmed.** Review found keeping `missing` empty on every
  returned result correct, because any missing declared Tag makes the call an error, and an
  unresolved error names the missing paths (up to five) in its message. No change.
- **Ticket #17 — the `_types_` rule is read from D30 §6 onto the REST Target allowlist.**
  D30 §6 states the explicit-`_types_` requirement for the Runtime Target Policy
  (`[provider]_types_/…`); this Tool applies the same reading to its REST Target: a
  document that declares the provider's UDT folder may only be imported into that path
  (`[provider]_types_`), anything else is `invalid_argument`. A `_types_` folder deeper in
  the path is an ordinary folder name and is not restricted. **For the owner:** confirm,
  or state the REST-plane rule separately.
  **RESOLVED in review round 1: confirmed.** Review found requiring an explicit `_types_`
  destination for documents that declare UDT definitions consistent with D30 §6 and
  appropriately fail-closed. One consequence of the new prefix rule is recorded here: the
  requirement is satisfied by the *call* naming the `_types_` path, since the Tool refuses
  such a document anywhere else, so an allowlist entry that covers a provider does not by
  itself make UDT definitions importable — the caller still has to spell the `_types_`
  Target. No change.
- **Ticket #17 — provisioning a fresh Tag provider needs a retry, the Tool never
  retries.** The recorded 8.3.8 behaviour (Bad 776 `cleanPath is null` on the first import
  after a provider is created) is handled in `provision.py`, which imports the source Tags
  with `MergeOverwrite` and retries until the Gateway serves them; the Tool itself still
  sends exactly one dispatch and never replays (D30 §2). Both live rows show one attempt
  was enough this time (`tagProvider.import.attempts: 1`). **For the owner:** no action
  needed unless `setup-native` should adopt the same retry-and-verify discipline for the
  Runtime Target Policy provider, which #6 already recommends.
  **RESOLVED in review round 1: confirmed.** Review found keeping readiness retry logic
  out of the Tool correct — the harness may retry, the mutation dispatch stays exactly
  once. No change here; the `setup-native` adoption is #21's.
- **Ticket #17 — the Runtime Target Policy provider is reserved: RESOLVED by a
  Target-class rule (review round 1).** D30 §1 requires that the Runtime MCP server cannot
  write the policy, and the #6 research note showed Jython handler scope is not a security
  boundary, so the rule has to be the product's. Review found the REST Tag import able to
  write there — with a `*` allowlist, or with an entry naming the provider — which is the
  same class of hole D30 §5 closes for Refused resource types. `safety/reserved_tag_providers.py`
  now holds the reserved set (`IgnitionMCPPolicy`, compared case-insensitively), the Tool
  passes its decision to the D08 chain as the operation's Target-class rule, and the rule
  therefore runs before the Target allowlist: no `*`, no explicit entry. The contract
  (`reservedTagProviders`) and the linter require it for a Tool whose Target is a Tag path,
  and the live REST driver asserts the refusal and its message on every row. **Residual,
  recorded for the owner:** the rule covers Tag Mutations, which is what D30 §1 protects;
  `config_resource_update`/`delete` can still change the `ignition/tag-provider` *resource*
  because D30 §5 classifies that type as allowed. That is a different plane — the provider's
  configuration, not the policy Tag — and it fails closed rather than open: removing or
  breaking the provider leaves the policy unreadable, which every Runtime Mutation answers
  with `operation_disabled` (D30 §1/§7). If the owner wants that resource type refused as
  well, it is a one-line addition to `contracts/shared/refused-resource-types.json` and the
  Runtime Text Resource that mirrors it.
- **Resolved (#16 review round 1) — which artifacts `project_import` consumes: keep the
  union.** D30 §6 names a READY `project_archive`; the ticket names "the artifact ID of a
  READY `project_archive` ... (uploaded through `POST /artifacts` or produced by
  `project_export`)", and D17 names server-produced exports as a legitimate binary ingress
  source. The review kept the union of `project_archive` and `project_export`: both are
  `application/zip` Project archives that passed the D15 ZIP gate before they became
  READY, both kinds are declared in the Tool's contract (`artifactInput.kinds`), the
  linter requires that declaration, every other kind is `invalid_argument`, and an
  artifact the caller cannot see answers `not_found` first. No narrowing.
- **Resolved (#16 review round 1) — a stale `expectedFingerprint` ends the transaction
  `CONFLICTED`.** D16 defines `CONFLICTED` for the mandatory pre-import re-export finding
  an external change (`concurrent_modification`, `import_attempted = false`). The D30 §2
  token gate detects the same class of event, one step earlier and without a dispatch, so
  the Tool reuses that state instead of inventing a new one: the transaction ends
  `CONFLICTED` with the `conflict` code D30 §2/§7 decides, the release set is D16's, and
  the caller receives `conflict` with the state and the `transactionId` named in the
  message (the operation record carries the transaction id, D19), which is what separates
  the two events. The review confirmed this is the right conservative choice; no distinct
  state is introduced.
- **Resolved (#16 review round 1) — the REST Tool path proves `CONFLICTED` live through
  the token gate; its A-vs-A' drift variant is fixture-only there.** Landing an external
  writer between the baseline export and the pre-import re-export deterministically is not
  possible through this harness: the window is inside one MCP call and a race would make
  the row flaky. The review accepted the fixture-only coverage for the HTTP harness: the
  branch is driven live by the G3 in-process harness (with a hooked client, and it still
  is), the unit fixture (`change_project_after_exports`) drives it on the Tool path, and
  the live REST cases prove the same terminal state through the deterministic stale-token
  gate. A proxy-based live race belongs to #20's fault-injection work.
- **Recorded for the owner (#16 fix) — D30 §2 narrows D16's recovered success when a
  dispatch answer was never recorded.** D16's restart rule recovers `C == B` as a
  committed transaction; D30 §2 makes an explicit refusal final. A process that dies after
  the Gateway answered but before the answer was written down leaves a row in which a
  refusal is indistinguishable from an ambiguous boundary, and the two rules then point in
  opposite directions. The fix takes the safety rule, because the alternative is crediting
  a refusal to another writer's identical content: such a row is `NOT_APPLIED` only when
  the Project still equals baseline A and `OUTCOME_UNKNOWN` otherwise, never `COMMITTED`.
  A *recorded* ambiguous boundary still recovers a success (`C == B`), in-process and on
  restart, so D16's recovery rule is intact everywhere the answer is known — including for
  every phase-3 row, which carries no class and keeps D16's comparison unchanged.
  **For the owner:** confirm that D30 §2 takes precedence in that unrecorded window, or
  amend D16 to say an unrecorded answer is attributed by the re-export.

- **Ticket #15 — the frozen G3 `head-get-parity` check can fail for a Gateway reason.**
  On the #15 head the 8.3.8 G3 row failed once at `head-get-parity` and passed on an
  immediate rerun ([run 35653162977](https://github.com/sheon-sek/ignition-mcp/actions/runs/35653162977),
  job rerun `--failed`). The check (`tests/harness/phase3-live/driver.py:533`) GETs the
  first `project_export` artifact and HEADs the *second* export of the same project, so
  it asserts that two exports of an unchanged project are byte-identical. In the failed
  row the two artifacts had the same size (903 bytes) and the same project fingerprint
  (`pcf1:f2a6639c…`, stable across the pair) but different bytes
  (`52962dde…` vs `a37a321f…`), which is Gateway-side export non-determinism, not a
  server or harness defect: no code under test is involved, and the other 23 checks in
  that stage passed. The evidence is in the failed job's `observations.json`
  (`checks[name=head-get-parity].detail`). **For the owner:** decide whether the check
  should compare the two artifacts of one export (a GET and a HEAD of the same path, the
  representation parity it is named for) or be relaxed to a fingerprint/size comparison —
  either is a Phase 3 amendment, so this ticket did not touch it. Until then a G3 row can
  fail for an unmodified head and needs a rerun.
- **Ticket #15 — a config rename has two Targets, and D30 does not say so.** D30 §6 gives the Target
  rule for the Runtime Tag operations ("`tag_move` checks the source and the destination, and
  `tag_rename` checks the new path"); it says nothing about a *config* rename, which both changes the
  resource at its old `<resourceType>/<name>` and produces one at `<resourceType>/<newName>`. The
  conservative reading is implemented — **both** names are Targets (D30 §3 Preflight), both are
  checked and both must be allowlisted (`MutationRequest.additional_target_ids`, with a unit test
  proving the destination denial and a live case proving it on 8.3.8 and 8.3.9) — and the behaviour
  is recorded in the Tool's contract (`targetIds`). **For the owner:** confirm the both-names rule, or
  narrow it to the destination only (the Runtime `tag_rename` reading) if that is what the decision
  intended.
- **Ticket #15 — two Gateway behaviours in the recorded fixture are modelled, not recorded.** The
  fixture answers an existing create target and an occupied rename destination with 409 (the status
  the committed document responds with elsewhere, and the one D30 §7 maps to `conflict`), and a stale
  signature in a `DELETE` path with 409 as well. No live Gateway is reachable from the local fixture,
  so the live cases were written not to need them: every live create collision, rename collision and
  stale-token case is refused by the server's own pre-dispatch checks. The shape the real Gateway uses
  for those three refusals is therefore still unverified and should be captured on the next live run
  (`observations.json` records what the live cases observed).
- **Ticket #14 — D30 §7 vs the frozen Phase 3 deployment policy: RESOLVED by a Tool-scoped
  mapping.** D30 §7 maps "target not allowlisted" to `permission_denied`; the Phase 3 machinery
  answers `operation_disabled`, which `test_phase3_safety_executor.py` and the live G3 driver
  (`tests/harness/phase3-live/driver.py:696`) pin as recorded, frozen evidence. `MutationOperation`
  now declares `target_denial_code` (D30 §7 decides `permission_denied` for
  `config_resource_update`; `PROJECT_IMPORT_OPERATION` keeps `operation_disabled`), so the Phase 4
  Tool returns the decided code, no G3 artifact or evidence changes, and a unit test pins the
  per-operation split. `tooling/contracts/lint.py` refuses a Phase 4 mutation contract that does
  not declare `permission_denied`. The live REST driver asserts exactly that code.
- **Ticket #14 — the 8.3.9 candidate's full OpenAPI document is not committed.**
  `docs/ignition-8.3.9-openapi/resource-types.json` records the resource-type inventory a live
  8.3.9 Gateway exposed (56 types, a strict subset of the 8.3.8 document's 57 — the difference is
  the MCP Module's own `server-config`), with the source document's SHA-256, the Gateway build and
  the capturing run. Every listed type is classified. Committing the 12.7 MB document itself is a
  repository-size decision for the owner; until then the classification test enforces the inventory
  and the discovery-by-path rule, and anything unclassified stays refused.
- **Ticket #14 — a collection-qualified Target policy does not exist yet.** The Gateway reads and
  changes a config resource by collection as well as by name, so a Target allowlist entry for
  `<resourceType>/<name>` would otherwise authorize the same name in every collection.
  `config_resource_update` therefore refuses a caller-supplied `collection` with `invalid_argument`
  and addresses only the default collection, which closes the gap without inventing an encoding the
  Target policy has no room for. Supporting collections means amending the Target policy (D08/D30)
  to name the triple unambiguously; until then a two-collection test proves the refusal, and the
  fixture keys resource state by `(name, collection)` so the distinction is observable.
- **Ticket #14 — the `phase4-live` GitHub environment has no protection rules.** The Phase 4 REST
  live workflow reuses the owner-accepted `phase3-live` deviation (no required reviewers, no wait
  timer, no deployment-branch restriction), already recorded in `docs/development/phase-3.md` Open
  questions. The compensating controls are mandatory in the workflow: the trusted-repo guard, no
  repository or environment secrets in the job, loopback/compose-only Gateway and MCP endpoints,
  run-unique CI-only credentials, and the driver's expectation checks over live data. Each run
  repeats the note in its uploaded `identity.json`.
- **Ticket #6 outcome (2026-09-22, `phase4-live-g4a` run 35635887711, both Gateway rows).** Runtime Target Policy storage is characterized and one location is recommended: a dedicated `IgnitionMCPPolicy` Tag provider holding a String Tag `RuntimeTargetPolicy` with the canonical JSON policy text, read by handlers through the gated two-step read described in the policy-read-bound question below (companion `RuntimeTargetPolicyLength` Int4 Tag first, then the document). `setup-native apply` writes it through Native REST (create the provider, import the Tag with a bounded retry; `Abort` on create, `MergeOverwrite` on update), and reads it back with `/tags/export` plus the provider resource signature. Evidence and the rejected candidates are in [the research note](../research/runtime-target-policy-storage-and-alarm-query-bound.md). **For the owner to approve:** the reserved provider name, and the product rule that every Runtime Tag Mutation refuses any target inside the policy provider *before* Preflight executes, whatever the Target allowlist says, including an explicit `*`. The rule must cover `tag_write`, `tag_update`, `tag_delete` and `tag_create` (all targets), and **both the source and the destination** of `tag_move`, `tag_rename` and `tag_copy` — a copy into the provider writes the policy, a copy out of it publishes the document elsewhere, and `tag_create` could otherwise add Tags to it under a permissive CONFIG allowlist. D30 §1 states the property but not the enforcement point; the harness measured that a handler can write Tags inside that provider, so the boundary has to be the product rule.
- **`alarm_acknowledge` (ticket #9) is parked.** The D12 Phase 4 amendment holds only if recorded evidence shows an exact-path `queryStatus` is bounded before or during execution. The recorded run shows the opposite: one exact Alarm path returned 1 → 2 → 3 items over three unacknowledged activate/clear cycles, because cleared-unacknowledged events accumulate until they are acknowledged, and the query exposes no limit or continuation (D12 Phase 2 amendment). The handler-side Observed state for an acknowledge has no bounded source, so the ticket cannot be implemented as specified. **For the owner:** approve the park, or supply a credible pre/during-execution bound (a verified native limit/continuation, or an independently bounded alarm backend). The scope and ticket tables mark it parked.
- **Policy read bound (ticket #6 follow-up).** The Runtime Target Policy is a `String` Tag, and Ignition documents no maximum length for a Tag value; `system.tag.readBlocking` takes only paths and a timeout, so a post-read length check is not a bound (the reasoning D12's Phase 2 amendment applied to `alarm_status`). The recommendation is therefore conditional on a product-enforced cap: the policy Tag carries a companion `RuntimeTargetPolicyLength` Int4 Tag that `setup-native apply` writes in the same import, and the reader refuses a document whose declared length is missing, non-integer or over `IgnitionMcpPolicyMaxBytes` (32 KiB) **without reading the value at all**, then re-checks the value's byte length after reading. The harness measures both the served, length-verified read and a deliberately oversize pair that must be skipped unmaterialized. **For the owner to approve or reject:** the cap value and the rule that `apply` is the only writer of that provider (which is what makes the declared length an enforced maximum). If the cap is rejected, the fail-closed default is to keep Runtime Mutations disabled and move the policy to a mechanism with a native bound.
- **`phase4-live` environment reuse.** The new `phase4-live` GitHub environment was created with no protection rules, reusing the owner-accepted deviation recorded for `phase3-live`. The compensating controls are the trusted-repo guard, no repository or environment secrets in the job, compose-localhost endpoints only, run-unique Alarm paths, and the driver-enforced CI marker plus Gateway-identity check that fails closed before any probe call. Recorded in every evidence row (`ownerAcceptedDeviations`).
- **Ticket #7 resolved the two policy questions above in the fail-closed direction.** `tag_write` implements the reserved-provider refusal and the companion-length gate, so the recommendations in the two questions above are now the shipped behavior rather than a proposal. The document contract is `contracts/shared/runtime-target-policy.schema.json` (lint-checked against the reader's required fields), and the harness documents are validated against it in `tooling/native/tests/test_phase4_harness.py`.
- **Policy `allowlists` is keyed by Tool name, not by Mutation class.** D30 §1 says "Target allowlists per class"; the document is keyed by Tool (`tag_write`, `alarm_shelve`, …) because a Tag path prefix and an Alarm source pattern have different grammars and one class-wide list would have to accept both. This is strictly finer-grained than per-class — a Tool with no key has no targets — and it matches the document the ticket #6 evidence recorded. The contract linter requires each Runtime Mutation contract's `allowlistKey` to be its own Tool name.
- **The policy carries an optional `auditProfile`, and `required` mode verifies it through `system.config.getResource`.** D30 §6 says `required` mode "checks that the Project's audit profile is configured". Ignition 8.3 exposes no scripting getter for the project's audit profile (`system.project` has `getProjectName`, `getProjectNames`, `requestScan`), so a handler cannot read that setting directly. The shipped behavior is therefore: `required` mode requires the policy to name an `auditProfile` that resolves through `system.config.getResource("ignition", "audit-profile", name)` to an enabled resource, and fails closed with `operation_disabled` (`auditProfileUnavailable`) otherwise; all audit writes pass that profile explicitly, so the row does not depend on the project's own setting. This is a stronger, verifiable form of the D30 rule, and the optional field is part of the ticket #7 contract work. **For the owner:** confirm this reading, or name the native read that exposes the project's audit profile.
- **The reserved-provider refusal is `permission_denied`, and it precedes the allowlist check per item.** D30 §7 maps "target not allowlisted, or a Refused resource type" to `permission_denied`; the reserved provider is the same class of refusal, so `tag_write` answers `permission_denied` with `details.items[].reason = "reservedProvider"`. Because the check runs before the allowlist for each item, a reserved target is refused identically under a prefix allowlist and under an explicit `*`, which is exactly what the live case proves.
- **An unattributable Native outcome list is a whole-batch `outcome_unknown`.** `system.tag.writeBlocking` answers one QualityCode per path. If the count does not match, no item can be attributed positionally, so the Tool returns `outcome_unknown` for the batch (with `requested`/`returned` in the details) instead of guessing a prefix. Per-item `outcome_unknown` is reserved for an item whose own Native outcome is present but indeterminate (a null QualityCode).
- **Live proof of the Runtime `required` audit mode is left to G4 close (#23).** Ticket #7 fixture-covers the `required` path (audit profile missing, attempt write failed, and the success shape), and its live stage runs `best_effort` with the audit rows read back from the profile. The G4 acceptance item "audit failure (`required` mode) proven live on both Planes" needs a policy state whose `auditMode` is `required`; the harness can install one with `install_tag_write_policy(audit_mode="required")`, but doing it in this ticket would spend a live run on a case the ticket does not require.
- **`tag_write` live evidence (ticket #7).** `phase4-live-g4a` run 35653953120, both Gateway rows green (`drift: {}`); the per-case results are in the ticket #7 entry under Results, and the served policy equals what `apply` wrote. The Runtime audit path is verified end to end: `system.util.audit(action=…, actionTarget=…, actionValue=…, auditProfile=…, actor=…)` recorded both rows, so the pinned Module accepts that keyword form.
- **Ticket #8: the Alarm Target allowlist language.** D30 §1 says allowlist entries are "provider-qualified path prefixes that match only at segment boundaries". The Tag language spells that boundary as `/`; an Alarm path carries a second qualifier separator (`:`), so `alarm_shelve`/`alarm_unshelve` match an entry when the target equals it or continues at the next character with `/` or `:`. That is what makes the ticket #6 finding hold for the allowlist as well: matching is literal, so `…/Exact` never reaches `…/ExactSibling`. The entry grammar is the Alarm path grammar: provider-qualified, no `*` anywhere except a standalone `*`, at most 2048 characters and no whitespace. A deployment that writes a Tag-shaped entry (`[default]AHU`) gets `operation_disabled` (`policyAllowlists`) rather than an allowlist that silently grants nothing. The document stays keyed per Tool (the ticket #7 note above), so `alarm_shelve` and `alarm_unshelve` are separate entries and a deployment can allow shelving without allowing unshelving. **For the owner:** confirm the per-Tool keys and the `:` boundary, or name a different entry language for Alarm targets.
- **Ticket #8: the reserved-provider refusal covers Alarm paths too, by provider component.** The shipped rule (ticket #6 open question 1, implemented for Tag Mutations by #7) refuses any target inside `IgnitionMCPPolicy`, whatever the allowlist says, including an explicit `*`. D30's owner ruling (`reserved_provider_match: provider_component_only`) settles what "inside" means: the `prov:<provider>:` component of the rendered Alarm path, compared as a whole, never a substring of a later segment. `alarm_shelve`/`alarm_unshelve` therefore refuse `prov:IgnitionMCPPolicy:/…` before Preflight with `permission_denied` (`details.items[].reason = "reservedProvider"`), while an allowed `prov:default:` target such as `prov:default:/tag:IgnitionMCPPolicyPump:/alm:High` is an ordinary allowlist-checked target and answers `targetNotAllowlisted` when it is outside the allowlist. The rule stays per Tool (see the #10 bullet above): each handler carries its own copy, and the D29 fixtures `-reserved-name-in-later-segment` and `-reserved-name-not-allowlisted` pin both directions for each Tool.
- **Ticket #8: a duration above the deployment cap is `invalid_argument`.** D12's Phase 4 amendment says a deployment "may lower the maximum in the Runtime Target Policy but not raise it", and D30 §7 maps a stale Precondition token to `conflict`, an unallowlisted target to `permission_denied` and a missing policy to `operation_disabled`, but has no mapping for the lowered cap. The shipped behavior treats the cap as an argument range, exactly as `tag_write` treats its timeout range: `invalid_argument` with `details = {reason: "durationOverPolicyCap", requested, cap, hardMaximum}`. `limit_exceeded` (D10's over-budget code) stays with request cardinality rather than a value range. **For the owner:** confirm the code, or name a different one for a duration the deployment refuses.
- **Ticket #8: Observed state does not decide success for the Alarm Mutations.** Shelving a literal pattern that matches nothing is not an error (ticket #6: a pattern without `*` matches only the spelling it names), so an item whose `alarm_shelved_list` entry is absent is still `executed`, with `observed[].shelved = false` reported as data. Making the shelf view the success decision would fail a legal shelve of a pattern whose Alarm is currently clear, and `alarm_unshelve` has the same case in reverse. Every item's outcome comes from its own dispatch, and `outcome_unknown` is reserved for an item whose own dispatch did not report back. **For the owner:** confirm that the shelf view stays Observed state rather than a success criterion for these two Tools.

