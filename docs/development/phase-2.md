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
- `alarm_status`
- `alarm_journal`
- `alarm_shelved_list`
- `historian_browse`
- `historian_query_series`
- `historian_query_aggregate`
- `database_query_list`
- `database_query`

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

## Implementation order

Use dependency-first slices rather than adding the whole catalog at once.

1. Freeze contracts, output schemas, budgets and readonly profile inventory.
2. Runtime Tags: `tag_query`, `tag_get_config`, `udt_type_list`, `udt_type_get`.
3. Runtime Alarms: `alarm_status`, `alarm_journal`, `alarm_shelved_list`.
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
