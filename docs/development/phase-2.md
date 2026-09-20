# Phase 2 — complete readonly capability surface

Status: **IN PROGRESS**.

Branch: `feature/phase-2-complete-readonly-surface`.

Base: Phase 1 merge commit `e1fae8fc9f658026dbf7fdc0272085f4574fab5d`.

This phase implements only D26 Phase 2 / G2. It must not absorb Phase 3+ work.

## Scope

### Existing Phase 1 Runtime surface retained

- `bundle_info`
- `tag_browse`
- `tag_read`

### Runtime READ additions

- `tag_query`
- `tag_get_config`
- `udt_type_list`
- `udt_type_get`
- `alarm_shelved_list`
- `historian_browse`
- `historian_query_series`
- `historian_query_aggregate`
- `database_query_list`
- `database_query`

### Runtime READ deferred during the pre-G2 audit

- `alarm_status` and `alarm_journal` are **disabled and excluded from every profile** per the [D12 Phase 2 bounded-execution amendment](../decisions/D12-alarm-tool-surface.md#phase-2-bounded-execution-amendment). The native 8.3 alarm queries expose no row limit, reliable continuation, or interruptible timeout, so the handler-side `maxResults` check after materialization cannot satisfy D10/D12. Their contract JSON, output schemas, and G2-ready handler code are preserved under `packages/ignition-runtime-bundle/deferred/`; re-enabling them requires a verified pre/during-execution bound plus fresh live evidence, not a code-only claim.

The frozen Phase 2 Runtime readonly inventory is therefore **13 Tools** (3 Phase 1 + 10 additions).

### External REST READ additions

- `project_list`
- `config_resource_search`
- `config_resource_describe`
- `config_resource_names`
- `config_resource_list`
- `config_resource_get`
- `audit_query` when the target OpenAPI exposes the required semantics
- `alarm_pipeline_list`
- `alarm_pipeline_status`

Every collection/query must satisfy D10 before breadth is added.

## Explicit exclusions

Phase 2 does **not** implement:

- ArtifactStore / artifact Tools
- project_export / project_import
- tag_config_export / tag_config_import
- operation_diagnose
- setup-native product workflow
- any Runtime CONTROL or CONFIG Tool
- any REST mutation
- Perspective authoring
- WebDev
- arbitrary REST/Jython/SQL escape hatches

Those remain gated by D26 Phase 3+.

## Authority order

For this repository:

1. D01–D28 and later explicit amendments;
2. repo-owned contracts/tooling and live compatibility evidence;
3. current implementation and tests;
4. the imported `ignition-mcp-tools` Skill as an implementation/reference aid.

The generic Skill still documents `NATIVE_BINDING_PENDING` and a narrower parameter metadata profile. Phase 0/1 already replaced those assumptions with repo-owned, live-verified behavior for the exact D27 tuple. Phase 2 must not regress the repository to the older scaffold state.

## Phase 0 / Phase 1 lessons that are hard constraints

1. **Real protocol evidence is mandatory.** Static validation, direct handler calls, mocks, ZIP shape and readable schema Resources do not prove Runtime behavior.
2. **D27 is exact-tuple only.** Gateway 8.3.8 build 2026071409 + MCP Module 1.3.5-SNAPSHOT build 2026021307 may use `VERIFIED_WITH_LIMITATION`; no other tuple inherits the missing-native-outputSchema exception.
3. **D28 applies to Runtime structured domain data.** Logical null must survive via `ignition-null-v1`; do not silently omit nullable members, make required fields optional, or reconstruct structured data from text.
4. **Empty Prompt inventory is capability-aware.** If initialize does not advertise prompts and the expected inventory is empty, record NOT_APPLICABLE. Do not add dummy Prompts or swallow an error from an advertised capability.
5. **Exact inventory means exact.** `tools/list`, `resources/list`, and `prompts/list` must match the expected product/profile inventory, not merely contain an expected subset.
6. **Deploy the exact artifact under test.** CI must build the Runtime ZIP and deploy that ZIP, then record its SHA-256. Do not test the source tree while claiming the built artifact was tested.
7. **Do not guess discovery metadata.** Resource URI/title/MIME/size and Tool schemas are verified from actual protocol results and source metadata.
8. **Bound the transport before parsing.** External Gateway responses are streamed under a finite byte limit and elapsed deadline; do not first buffer an unbounded response and then apply D10.
9. **Singleflight must coalesce.** A mutex that merely serializes duplicate refreshes is not singleflight; concurrent waiters share one shielded refresh task.
10. **Failure must degrade readiness honestly.** Connectivity/schema/auth failures cannot leave diagnostics claiming authentication success or readiness as healthy.
11. **Metadata reconciliation never replays an operation.** A schema/capability mismatch may refresh metadata; the failed operation is not automatically replayed.
12. **Cancellation and sibling requests must drain/close.** Startup, refresh and transport failure paths must not leak tasks, streams or clients.
13. **Secrets are masked before exposure.** Disposable credentials must be registered for log masking before they are exported or echoed by CI.
14. **Workflow cleanup must survive failure paths.** Shell strict mode and unset process IDs must not turn cleanup into the reported root cause.
15. **Runtime Jython rules remain strict.** `def onToolCalled(...):` is line 1 and the only top-level statement; helper/import code is nested; indentation is Tabs; Jython 2.7 only; parameter order matches `resource.json`.
16. **No generic shared Jython library yet.** D25 keeps Runtime handlers self-contained until a real repeated helper and verified Project Library format justify extraction.
17. **Domain-negative state is data.** Bad/Uncertain Quality, active alarms, disconnected status and empty searches are not automatically Tool Errors.
18. **Compatibility claims come from evidence.** G2 evidence may remain `UNTESTED`; never hand-edit a tuple to `SUPPORTED`.

## Phase 2 Runtime database registry

D14 requires an explicit approved Named Query registry but does not freeze its storage mechanism. Phase 2 uses a small deployment configuration rather than an arbitrary SQL/path surface or a guessed shared Project Library resource.

Gateway process environment variable:

`IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON`

Rules:

- schemaVersion is exactly 1;
- at most 100 aliases and 32 KiB serialized registry data;
- alias maps server-side to a fixed `project` + Named Query `path`;
- datasource policy is `named-query-fixed`; the caller cannot choose a datasource;
- only Value-style parameter types are accepted: string, integer, number, boolean, datetime;
- QueryString/Database parameters, arbitrary SQL, transactions and caller-supplied Named Query paths remain impossible;
- dataset aliases must declare either handler-owned offset pagination or an independently reviewed fixed row bound, both capped at D10's 2,000-row hard maximum;
- scalar aliases use `system.db.execScalar`;
- an absent registry is a valid empty approved registry; malformed registry configuration fails closed.

The two Runtime database Tools remain self-contained and independently validate the same deployment registry. This deliberately avoids introducing an unverified shared Jython Project Library during Phase 2. The G2 harness will configure a test-only approved alias and a separate PostgreSQL-backed fixture project without modifying the exact Runtime Bundle ZIP under test.

## Pre-G2 audit outcome and current implementation status

The pre-G2 audit closed the two open design questions with explicit owner-approved amendments and hardened the shipped code without regressing Phase 1 infrastructure:

1. **D12 amendment:** `alarm_status`/`alarm_journal` deferred as described above. `alarm_shelved_list` remains public because it reads finite current shelving state.
2. **D13 fill-mode amendment:** `historian_query_aggregate` intentionally does not expose `fillModes`/`includeBounds`/`excludeObservations`; 8.3.9 removed them from the public native signature and the stable semantic contract must not leak patch-specific legacy knobs.
3. **Runtime bounded input hardening:** every accepted string filter/path now has an explicit length ceiling before any native call; the aggregate array is capped before deduplication; `alarm_journal`'s no-op `includeData=True` now fails `unsupported_capability` before the native call instead of silently making the upstream query heavier.
4. **Named Query registry correctness:** project/path/pagination-parameter values are normalized before validation (closing a leading-whitespace `../` bypass); malformed deployment registry now maps to `schema_mismatch` in `database_query` (matching `database_query_list`) instead of masquerading as caller `invalid_argument`; offset continuation can no longer emit a `nextOffset` beyond the approved maximum; the decimal/non-finite Runtime marker encoding is collision-escaped through the `$ignition` reserved shape instead of a `oneOf`-ambiguous `{type,text}` object.
5. **External REST page integrity:** every upstream collection response is now reconciled against the requested page (metadata limit/offset equality, count consistency, exact item count versus `min(limit, matching - offset)`); public collection models carry the D10 500-item hard bound; Pydantic response validation failures are classified as `schema_mismatch`, degrade readiness, and trigger metadata-only reconciliation without replaying the operation.
6. **G2 harness (built, not yet executed):** `tests/harness/phase2-live/` provisions an exact-patch Gateway plus PostgreSQL, a test-only Named Query project, historical Tag/UDT fixtures, and the CI-only `MCP_CI_AUDIT` profile through Native REST; `probe.py` performs exact 13-Tool Runtime discovery, per-Tool real smoke calls, canonical-error checks, D27/D28-aware binding classification, and exact external capability verification; `.github/workflows/phase2-live-g2.yml` runs the 8.3.8 required + 8.3.9 compatibility matrix manually (per D23 trusted live CI) and always uploads machine-readable evidence. `alarm_pipeline_status` smoke adapts between a live pipeline and the canonical `not_found` negative without fabricating success.

**No G2 evidence exists yet.** The real-Gateway rows, native binding re-characterization on 8.3.9, and all L3/L4 results remain NOT RUN until the workflow is executed on trusted CI. Phase 2 stays open.

## Implementation order

Use dependency-first slices rather than adding the whole catalog at once.

1. Freeze contracts, output schemas, budgets and readonly profile inventory.
2. Runtime Tags: `tag_query`, `tag_get_config`, `udt_type_list`, `udt_type_get`.
3. Runtime Alarms: `alarm_status`, `alarm_journal`, `alarm_shelved_list` (the first two were later deferred by the D12 pre-G2 amendment; only `alarm_shelved_list` ships).
4. Runtime Historian: browse, bounded finite series, whole-range aggregate.
5. Runtime Database: approved Named Query registry only.
6. External REST readonly surface: projects, generic config-resource reads, capability-gated audit, Alarm Notification pipeline reads.
7. Expand real-Gateway G2 harness and evidence.
8. Run the 8.3.8 baseline row and an 8.3.9 candidate row without granting `SUPPORTED`.

For every Tool, D26 completion order remains:

semantic contract
→ input/budget validation
→ native adapter/client behavior
→ domain serialization
→ stable error mapping
→ observability requirements
→ unit/static tests
→ real Gateway smoke
→ failure/state verification.

## G2 acceptance

G2 closes only when all of the following are true:

- readonly profile inventory is frozen and exact;
- all Phase 2 READ contracts and L0/L1/L2 checks pass;
- every exposed READ Tool has a real smoke call on the applicable real-Gateway row;
- Runtime domain-negative states remain domain data;
- collection/query output obeys D10 and never silently truncates;
- 8.3.8 and 8.3.9 candidate rows emit machine-readable compatibility evidence;
- exact Runtime Tool / Resource / Prompt discovery is verified;
- D27/D28 limitations remain explicit;
- no tuple is promoted to D21 `SUPPORTED` without the required certification evidence.

Phase 2 ends at G2. Phase 3 starts only on a new user-directed feature branch.
