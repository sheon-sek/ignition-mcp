# D11 — Tag Tool Surface

**Status:** DECIDED

## Public surface
```text
Tag Capability
├── Discovery
│   ├── tag_browse
│   └── tag_query
├── Runtime Values
│   ├── tag_read
│   └── tag_write
├── Granular Configuration
│   ├── tag_get_config
│   ├── tag_create
│   ├── tag_update
│   ├── tag_delete
│   ├── tag_copy
│   ├── tag_move
│   └── tag_rename
├── UDT Read Surface
│   ├── udt_type_list
│   └── udt_type_get
└── Bulk Configuration Artifact
    ├── tag_config_export → ignition-rest
    └── tag_config_import → ignition-rest
```

## Discovery
### `tag_browse`
Hierarchical browsing using `system.tag.browse`.
Use D10 pagination/recursion limits.

### `tag_query`
Conditional/global discovery using `system.tag.query`.
Use validated query schema and continuation.

`browse` = known hierarchy navigation.
`query` = criterion-based discovery.

## `tag_read`
Backend: `system.tag.readBlocking`.

Do not expose `readAsync`.

Requirements:
- provider-qualified absolute Tag paths;
- current Tag value semantics;
- preserve QualifiedValue: value, quality, timestamp.

Bad/Uncertain Quality is domain data, not Tool failure.

### No property-path privilege bypass
`tag_read` / `tag_write` are current-value tools, not arbitrary Property Path escape hatches.
Configuration modification must use CONFIG-class tools.

## `tag_write`
Backend: `system.tag.writeBlocking`.
Do not expose `writeAsync`.

Caller input uses item objects:
```json
{"writes":[{"path":"[default]AHU/AHU01/Setpoint","value":22}]}
```
not parallel paths/values arrays.

Return per-item QualityCode/outcome and batch summary.
Permission: `CONTROL`.

## Configuration
### No public raw `tag_configure`
`system.tag.configure` is a backend primitive.

Expose strict semantic tools:

### `tag_create`
- backend: `system.tag.configure`;
- existing target → `conflict`;
- never overwrite;
- abort on collision.

### `tag_update`
- backend: `system.tag.configure`;
- missing target → `not_found`;
- merge-update semantics;
- must not create missing target;
- v1 has no `tag_upsert`.

### Collision policy
Caller cannot freely choose collision policy.
Canonical behavior:
- create → abort;
- update → controlled merge-update;
- copy → abort;
- move → abort;
- rename → abort.

### Other tools
- `tag_get_config` → READ
- `tag_delete` → CONFIG, destructive
- `tag_copy` → CONFIG
- `tag_move` → CONFIG, destructive
- `tag_rename` → CONFIG

All mutations enforce D08 and Runtime target allowlists.

## `tag_exists`
`system.tag.exists` is internal helper only for validation/preconditions/verification.

## Bulk import/export
Official Native REST is canonical owner.
Runtime does not expose public `system.tag.exportTags/importTags`.

External:
- `tag_config_export` → READ
- `tag_config_import` → CONFIG

Export uses artifact reference, not inline Base64.

## UDT
Provide first-class read tools:
- `udt_type_list`
- `udt_type_get`

They hide Ignition-specific UDT namespace details.
No dedicated UDT mutation tools in v1; use normal Tag config mutation surface where appropriate.

## Deferred
`system.tag.requestGroupExecution` is deferred from v1 public surface.

## Permission summary
| Tool | Server | Class |
|---|---|---|
| tag_browse | runtime | READ |
| tag_query | runtime | READ |
| tag_read | runtime | READ |
| tag_get_config | runtime | READ |
| udt_type_list | runtime | READ |
| udt_type_get | runtime | READ |
| tag_write | runtime | CONTROL |
| tag_create | runtime | CONFIG |
| tag_update | runtime | CONFIG |
| tag_delete | runtime | CONFIG |
| tag_copy | runtime | CONFIG |
| tag_move | runtime | CONFIG |
| tag_rename | runtime | CONFIG |
| tag_config_export | REST | READ |
| tag_config_import | REST | CONFIG |

```yaml
decision: D11
status: DECIDED
raw_tag_configure_public: false
tag_upsert_v1: false
tag_exists_public: false
async_variants_public: false
bulk_config_owner: ignition-rest
```

## Phase 4 amendment — Tag Mutation contracts (2026-09-22)

**Approved by the project owner during the Phase 4 scoping interview.** Details are in D30.

- `tag_write` accepts scalars and arrays of scalars. Dataset and Document values fail with `invalid_argument`. The per-item QualityCode is the outcome. A bounded read-back is reported as Observed state and does not decide success.
- `tag_update`, `tag_delete`, `tag_move` and `tag_rename` require a per-target Tag config fingerprint, which `tag_get_config` emits.
- UDT definitions (`_types_`) are valid CONFIG targets only when the Runtime Target Policy lists an explicit `_types_` prefix.
- `tag_config_import` always uses `collisionPolicy=Abort`. It creates Tags only.

```yaml
tag_write_value_types: [scalar, scalar_array]
tag_config_fingerprint_required: [tag_update, tag_delete, tag_move, tag_rename]
udt_definition_targets: explicit_types_prefix_only
tag_config_import_collision_policy: Abort
```

## Amendment — wildcard UDT Definition targets (2026-09-25)

**Approved by the project owner.** This amendment supersedes the explicit-prefix-only rule above for Runtime Tag CONFIG Mutations. D30's amendment defines `allowlistsWildcardIncludeUdtTypes`: when true, an explicit `*` covers UDT Definitions under `_types_`; when absent or false, an explicit `_types_` entry remains required. The reserved Runtime Target Policy provider remains refused.
