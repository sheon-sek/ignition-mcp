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

- **Runtime Target Policy.** A Gateway document outside the bundle. It is read by every Runtime Mutation handler and fails closed with `operation_disabled`. It holds Target allowlists per class (provider-qualified prefixes matched at segment boundaries), the Service identity, the audit mode and the shelve cap. UDT definitions need an explicit `_types_` prefix.
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
    fix; the doc head re-ran the same three workflows green.
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
- **Ticket #17 — a document's root decides which Tag paths the import declares.** The
  first live run showed the assumption mattered: the Tool declared only the document's
  `tags`, while the Gateway also creates the document's *named* root. The implemented rule
  is "the document declares what it declares" — a named root is imported under the request
  path, a nameless root contributes its children — and both shapes are now recorded live
  in every row (`tagProvider.convention`, run 35663426202). A caller whose artifact is a
  per-Tag (rather than per-folder) export is the remaining unprobed case: its root is the
  Tag itself and the rule reads it as one Tag under the path. **For the owner:** confirm,
  or ask for a live case for a leaf-root document.
- **Ticket #17 — an uninterpretable 2xx body is not a success.** The import route's
  documented response is a list of non-Good QualityCodes; live 8.3.8/8.3.9 answer a
  summary object. The Tool reads both, treats a zero-failure report as the Gateway's
  claim (`failureCount: 0` with no failures, or an empty list), and treats anything it
  cannot interpret — including an empty body — as no claim, which ends the call
  `recovery_required`/`outcome_unknown` rather than a success. No live run has produced
  that shape (both rows recorded the summary), so the reading is undecided for a Gateway
  that answers 200 with no body at all. **For the owner:** confirm the fail-closed
  reading, or name the accepted body.
- **Ticket #17 — a partly applied import is `recovery_required`, not per-item data.**
  D30 §3's per-item reporting is about Preflight; a Gateway that reports some successes
  and some failures has neither refused the call nor completed it, so the Tool fails
  closed with `outcome_unknown` and the message names the Tags the re-export was not
  showing. The Runtime plane's per-item QualityCode surface is the `tag_write`
  precedent. **For the owner:** confirm, or ask for a per-item result surface here too.
- **Ticket #17 — `missing` is empty on every returned result by construction.** The
  Observed state carries both lists because that is the comparison the verification made,
  but a declared Tag the bounded re-export does not show is `recovery_required` (an error
  naming up to five of them), so a caller only ever sees `present`. **For the owner:**
  confirm, or ask for the comparison itself to be returned as data on a failed
  verification.
- **Ticket #17 — the `_types_` rule is read from D30 §6 onto the REST Target allowlist.**
  D30 §6 states the explicit-`_types_` requirement for the Runtime Target Policy
  (`[provider]_types_/…`); this Tool applies the same reading to its REST Target: a
  document that declares the provider's UDT folder may only be imported into that path
  (`[provider]_types_`), anything else is `invalid_argument`. A `_types_` folder deeper in
  the path is an ordinary folder name and is not restricted. **For the owner:** confirm,
  or state the REST-plane rule separately.
- **Ticket #17 — provisioning a fresh Tag provider needs a retry, the Tool never
  retries.** The recorded 8.3.8 behaviour (Bad 776 `cleanPath is null` on the first import
  after a provider is created) is handled in `provision.py`, which imports the source Tags
  with `MergeOverwrite` and retries until the Gateway serves them; the Tool itself still
  sends exactly one dispatch and never replays (D30 §2). Both live rows show one attempt
  was enough this time (`tagProvider.import.attempts: 1`). **For the owner:** no action
  needed unless `setup-native` should adopt the same retry-and-verify discipline for the
  Runtime Target Policy provider, which #6 already recommends.
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
