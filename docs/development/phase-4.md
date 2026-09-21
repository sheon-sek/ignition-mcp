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
  workflow `Phase 4 Live Gateway G4a`, both rows green with `drift: {}` — 8.3.8
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

### Ticket #8 — Runtime `alarm_shelve` and `alarm_unshelve` (milestone 4a)

- Fixture-first coverage: 37 recorded-Jython fixtures
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
- Local rehearsal: `tests/harness/phase4-live/rehearse_local.py` — all nine stages
  against the recorded Gateway, `drift: {}`.
- Live (run
  [35661498161](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661498161),
  workflow `Phase 4 Live Gateway G4a`, both rows green with `drift: {}` — 8.3.8
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
- Frozen gates, green on the same head that records this evidence (`c68597f`): CI
  [35661498251](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661498251),
  Phase 0 G0
  [35661498193](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661498193),
  Phase 3 G3
  [35661498182](https://github.com/sheon-sek/ignition-mcp/actions/runs/35661498182) —
  the Runtime `readonly` inventory is unchanged by this ticket, so the G1–G3 rows
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

## Open questions

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
- **Ticket #8 — the Alarm Target allowlist language.** D30 §1 says allowlist entries are "provider-qualified path prefixes that match only at segment boundaries". The Tag language spells that boundary as `/`; an Alarm path carries a second qualifier separator (`:`), so `alarm_shelve`/`alarm_unshelve` match an entry when the target equals it or continues at the next character with `/` or `:`. That is what makes the ticket #6 finding — matching is literal, so `…/Exact` never reaches `…/ExactSibling` — true for the allowlist as well. The entry grammar is the Alarm path grammar: provider-qualified, no `*` anywhere except a standalone `*`, at most 2048 characters, no whitespace; a deployment that writes a Tag-shaped entry (`[default]AHU`) gets `operation_disabled` (`policyAllowlists`) rather than an allowlist that silently grants nothing. The document stays keyed per Tool (the ticket #7 note above), so `alarm_shelve` and `alarm_unshelve` are separate entries and a deployment can allow shelving without allowing unshelving. **For the owner:** confirm the per-Tool keys and the `:` boundary, or name a different entry language for Alarm targets.
- **Ticket #8 — the reserved-provider refusal covers Alarm paths too.** The shipped rule (ticket #6 open question 1, implemented for Tag Mutations by #7) refuses any target inside `IgnitionMCPPolicy`, whatever the allowlist says, including an explicit `*`. `alarm_shelve`/`alarm_unshelve` apply the same rule to their own target language: an Alarm path that names `IgnitionMCPPolicy` is refused before Preflight with `permission_denied` (`details.items[].reason = "reservedProvider"`). The alternative — a Tag-only rule with an undocumented gap for Alarm paths — would leave one reserved provider with two different reachability rules. **For the owner:** confirm, or scope the rule to Tag Mutations if an Alarm path can never reach the policy document's provider.
- **Ticket #8 — a duration above the deployment cap is `invalid_argument`.** D12's Phase 4 amendment says a deployment "may lower the maximum in the Runtime Target Policy but not raise it", and D30 §7 maps a stale Precondition token to `conflict`, an unallowlisted target to `permission_denied` and a missing policy to `operation_disabled` — but has no mapping for the lowered cap. The shipped behavior treats the cap as an argument range, exactly as `tag_write` treats its timeout range: `invalid_argument` with `details = {reason: "durationOverPolicyCap", requested, cap, hardMaximum}`. `limit_exceeded` (D10's over-budget code) stays with request cardinality rather than a value range. **For the owner:** confirm the code, or name a different one for a duration the deployment refuses.
- **Ticket #8 — Observed state does not decide success for the Alarm Mutations.** Shelving a literal pattern that matches nothing is not an error (ticket #6: a pattern without `*` matches only the spelling it names), so an item whose `alarm_shelved_list` entry is absent is still `executed`, with `observed[].shelved = false` reported as data. Making the shelf view the success decision would fail a legal shelve of a pattern whose Alarm is currently clear, and `alarm_unshelve` has the mirror-image case. Every item's outcome comes from its own dispatch, and `outcome_unknown` is reserved for an item whose own dispatch did not report back. **For the owner:** confirm that the shelf view stays Observed state rather than a success criterion for these two Tools.

