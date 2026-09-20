# D13 — Historian Tool Surface

**Status:** DECIDED

## Public v1 surface
```text
ignition-runtime
├── historian_browse
├── historian_query_series
└── historian_query_aggregate
```

All three are `READ` operations.

The v1 surface intentionally does not expose every `system.historian.*` function.

## `historian_browse`
Backend: `system.historian.browse`.

Use it as the canonical discovery mechanism for Historian paths.

Expose bounded semantic parameters such as:
- root path;
- optional snapshot time;
- name filters;
- recursive flag;
- metadata flag;
- page size;
- continuation cursor.

Use native continuation when available.

Historical paths are not treated as interchangeable with live Tag paths. Query tools accept historical paths; the server must not silently guess or rewrite live Tag paths into Historian paths.

## `historian_query_series`
Backend: `system.historian.queryRawPoints`.

Public semantics are a bounded historical time series with a finite positive `sample_count`.

Do not expose native `returnSize` directly.

Forbidden public values/semantics:
- `returnSize = -1` true/on-change raw retrieval;
- `returnSize = 0` natural/unbounded-style retrieval.

A bounded positive sample count is required so result size can be controlled before execution.

Canonical output preserves:
- historical path;
- timestamp;
- value;
- Quality information.

Do not expose Ignition Dataset layout details such as WIDE/TALL/CALCULATION as the main public contract.

D10 budgets apply to:
- path count;
- time range;
- total `paths × sample_count`;
- execution/output limits.

## `historian_query_aggregate`
Backend: `system.historian.queryAggregatedPoints`.

Semantics: perform one or more aggregate calculations over the entire requested time range.

It is not defined as a time-bucket-series API.

Expose a validated set of supported aggregate names and fill modes in descriptions and Jython validation. Do not accept arbitrary aggregate strings.

Typical aggregate calculations include average/minimum/maximum/sum/count/duration/quality calculations as supported by the target Ignition version.

## D10 correction
The previous Historian aggregate budget language based on:

```text
paths × buckets
```

is replaced by:

```text
paths × requested aggregate calculations
+ bounded time range
```

Time-bucket-style series retrieval belongs to `historian_query_series`.

## Deferred from v1
Do not expose:
- true/unbounded `historian_query_raw`;
- `historian_query_metadata`;
- `historian_query_annotations`;
- annotation create/update/delete;
- `system.historian.storeDataPoints`;
- metadata store/update operations;
- registered path mutation operations.

The main reasons are bounded-resource guarantees, lack of reliable native continuation/limits for some APIs, and the much higher risk of Historian write operations.

Historian write capability, if ever added, requires a separate high-risk decision.

## Patch-version compatibility
Public Tool contracts must not leak unstable/native patch-specific parameters such as implementation-specific query formatting knobs.

The Runtime adapter may vary its `system.historian.*` invocation by target Ignition patch/version capability while keeping the semantic MCP contract stable.

## Phase 2 fill-mode amendment

**Approved by the project owner during the Phase 2 pre-G2 audit.**

The earlier requirement to expose a validated set of aggregate fill modes is withdrawn for the v1 public contract. Ignition 8.3.9 removed `fillModes` from the public `system.historian.queryAggregatedPoints` parameter table (while retaining unspecified runtime backward compatibility). Publishing it would leak a patch-specific legacy knob and conflict with this decision's stable semantic contract requirement.

`historian_query_aggregate` therefore exposes validated whole-range aggregate names only and uses the target Gateway's native default fill behavior. It does not accept `fillModes`, `includeBounds`, or `excludeObservations`. Adding an explicit semantic fill policy later requires a new versioned contract and real compatibility evidence rather than passing an undocumented native parameter through.

## Permission summary
| Tool | Server | Class |
|---|---|---|
| historian_browse | runtime | READ |
| historian_query_series | runtime | READ |
| historian_query_aggregate | runtime | READ |

```yaml
decision: D13
status: DECIDED
public_true_raw_query_v1: false
public_metadata_query_v1: false
public_annotation_surface_v1: false
historian_write_v1: false
series_requires_finite_positive_sample_count: true
aggregate_is_whole_range_calculation: true
aggregate_fill_modes_public_v1: false
patch_specific_legacy_fill_parameters_public: false
```
