# D30 — Phase 4 Mutation Contract Rules

**Status:** DECIDED — approved by the project owner on 2026-09-22 during the Phase 4 scoping interview on `feature/phase-4`.

## Context

D08 sets the mutation safety chain, and D09, D11, D12 and D16 name the Mutation Tools and their owners. Before Phase 4 could expose any Mutation, a set of cross-cutting questions was still open:

- where the Runtime plane keeps its Target allowlists;
- what the precondition is for each Mutation;
- how a batch behaves when one item is invalid;
- which caller-chosen Gateway knobs are safe to leave open.

Each rule below answers one of those questions and applies to every Phase 4 Mutation Tool on both planes. The terms follow `CONTEXT.md`.

## Decision

### 1. Runtime Target Policy

Runtime Tool handlers enforce D09's target policy from a **Runtime Target Policy**. This is a deployment-owned document stored on the Gateway, outside the Runtime Bundle, so one deterministic bundle ZIP (D21) serves every deployment.

- `setup-native apply` writes the document. Until apply exists, live harnesses provision it test-only, under the same rule as the Phase 0–2 provisioning harness.
- Every Runtime Mutation handler reads the document before it acts. If the document is missing, unreadable or malformed, the handler fails closed with `operation_disabled`.
- The document holds:
  - Target allowlists per Mutation class;
  - the Service identity;
  - the D18 Runtime audit mode;
  - the `alarm_shelve` duration cap (D12 amendment).
- Mutation-class enablement stays in the Server Config profile (D09). The document never enables a Tool.
- Allowlist entries are provider-qualified path prefixes that match only at segment boundaries, so `[default]AHU` does not match `[default]AHU2`. `*` must be written explicitly, and an empty list means none (D08).
- The storage location on the Gateway is characterized by the first Phase 4 ticket. It must be readable by a handler at bounded cost, and the Runtime Server must not be able to write it.

### 2. Precondition tokens

D08 requires a precondition for destructive, overwrite and concurrency-sensitive Mutations. Phase 4 uses three kinds of **Precondition token**, and each is supplied by the caller from its own earlier read:

| Mutation | Token | Enforced by |
|---|---|---|
| `config_resource_update`, `config_resource_delete` | Resource signature (`expectedSignature`) | the Gateway (native `signature`) |
| `config_resource_rename` | Resource signature | a server-side read-compare; the Gateway's rename endpoint takes no signature |
| `tag_update`, `tag_delete`, `tag_move`, `tag_rename` | Tag config fingerprint (`expectedFingerprint`), per target | a handler read-compare just before `system.tag.configure` |
| `project_import` | Project fingerprint (`expectedFingerprint`, `pcf1`) | the D16 transaction |

- `config_resource_get` adds an explicit `signature` output field, and `tag_get_config` adds a `fingerprint` output field. Both changes are additive.
- A mismatch fails with `conflict` before anything is dispatched.
- For Mutations enforced by a server-side or handler read-compare, a race window remains between the check and the change. It is documented in the same way as D16's final import race, and no atomicity is claimed.
- `config_resource_create`, `tag_create` and `tag_copy` take no token. An existing target fails with `conflict` (D11 collision policy).

### 3. Preflight and batches

Every batch Mutation runs **Preflight**: each item's input, Target allowlist and Precondition token is checked before any item executes. If any item fails, none executes, and the result lists the failing items. After Preflight, items execute one at a time with no rollback, and outcomes are reported per item (D08 partial mutation).

### 4. Fixed Gateway knobs

The caller cannot choose these. The server always sends:

- `references=ABORT` on config rename;
- `allowInvalidReferences=false` on config create and update;
- `collisionPolicy=Abort` on `tag_config_import`. Existing Tags change only through `tag_update` with a Tag config fingerprint.

Each value keeps a Mutation's effects inside its named targets.

### 5. Refused resource types

`config_resource_create/update/delete/rename` refuse a repo-owned, contract-listed set of **Refused resource types** with `permission_denied`, whatever the Target allowlist says, even `*`. The set covers what D08 forbids generic mutation from administering: API tokens, security levels, security properties and zones, user sources, identity providers, OAuth2 clients, secret providers, system and local system properties, the EAM license and module types, and `com.inductiveautomation.mcp/server-config`.

- Refusing `server-config` stops an agent from widening its own Tool inventory. `setup-native apply` writes the Server Config through its own curated path.
- A resource can also be refused **by name within an allowed type**: the Tag-provider resource named `IgnitionMCPPolicy` is refused by `config_resource_*` before the Target allowlist check, even under `*` (owner ruling 4 below). Other Tag-provider resources stay manageable.
- Every resource type in a supported OpenAPI document must be classified as allowed or refused. An unclassified type is refused, and a test fails when a new OpenAPI version adds one.

### 6. Other Tool-level rules

- `tag_write` accepts scalars (bool, number, string, and datetime as ISO-8601) and arrays of scalars. Dataset and Document values fail with `invalid_argument`. The per-item Native outcome (QualityCode) is the result. A bounded read-back is returned as Observed state and does not decide success.
- Tag CONFIG Mutations may target UDT definitions (`[provider]_types_/…`) only when the Runtime Target Policy lists an explicit `_types_` prefix. A bare `*` does not cover UDT definitions.
- Target checks for moves and copies: `tag_copy` checks the destination, `tag_move` checks the source and the destination, and `tag_rename` checks the new path.
- `project_import` targets an existing Project only (anything else fails with `not_found`), and consumes a READY `project_archive` artifact visible to the same Mutation principal.
- `artifact_delete` has class `CONFIG` and is destructive. Only the owning principal can delete an artifact, or `ignition.admin`; artifacts the caller cannot see answer `not_found`. A retention-locked artifact fails with `conflict`. The Phase 3 runbook's planned `DELETE /artifacts/{id}` data-plane route is **dropped**; the Tool is the only delete path.
- `alarm_pipeline_cancel` targets exact pipeline paths or `*`, never prefixes. Its verification is a bounded `alarm_pipeline_status` re-read, returned as Observed state.
- For the Runtime `required` audit mode, the handler first checks that the Project's audit profile is configured, and fails with `operation_disabled` if it is not. It never writes a probe row. If the audit write fails after a Mutation succeeds, the result reports `auditRecorded=false` and stays a success (D18).
- The Runtime plane gets no operation records and no diagnose Tool. After an ambiguous transport outcome, the agent re-reads with the Runtime read Tools, and `system.util.audit` rows carry the correlation ID. A handler reports `outcome_unknown` only for items whose Native outcome is itself indeterminate.

### 7. Error taxonomy

Phase 4 adds no error code. Mappings:

- stale Precondition token or collision → `conflict`;
- target not allowlisted, or a Refused resource type → `permission_denied`;
- class disabled, missing Runtime Target Policy, or `required` audit unavailable → `operation_disabled`;
- possibly-dispatched outcome → `outcome_unknown`.

Details such as which precondition failed go into structured error details.

## Considered options

- **Baking allowlists into the bundle** would give one ZIP per deployment, which breaks D21's single deterministic release. **Relying only on Security Levels** is rejected by D09 ("server permission alone is insufficient").
- **A server-fetched precondition** would only check the server's own read, not the state the agent reasoned about.
- **Independent per-item batch execution** was rejected. An invalid item usually means the agent's plan is stale, so running the other items would act on that stale plan.
- **Caller-chosen `references=UPDATE`, `collisionPolicy=MergeOverwrite` or `allowInvalidReferences=true`** would let one Mutation change resources outside its Target allowlist, or bypass the Tag config fingerprint.

## Consequences

- `config_resource_get` and `tag_get_config` gain additive output fields, so their schemas change additively. The Runtime bundle gets a D21 MINOR version bump when its Tools are added.
- Every new supported OpenAPI version must pass resource-type classification before config Mutations run against it.
- Runtime timeout, ambiguous-outcome and cancellation cases for G4 are proven with recorded fixtures only, and the evidence records that limitation. D10 notes that blocking Jython calls cannot be interrupted, so live fault injection there would only test the harness. On the REST plane these cases are also proven live, through a fault-injecting proxy in the compose network.

```yaml
decision: D30
status: DECIDED
runtime_target_policy: gateway_document_outside_bundle
runtime_target_policy_missing: fail_closed
allowlist_match: provider_qualified_prefix_segment_boundary
precondition_tokens: [resource_signature, tag_config_fingerprint, project_fingerprint]
precondition_source: caller_prior_read
batch_preflight: all_or_nothing_before_execution
batch_rollback: false
config_rename_references: ABORT
config_allow_invalid_references: false
tag_config_import_collision_policy: Abort
refused_resource_types: contract_listed_unclassified_refused
tag_write_value_types: [scalar, scalar_array]
udt_definition_targets: explicit_types_prefix_only
artifact_delete_http_route: false
runtime_diagnose_tool: false
new_error_codes: false
```

## Owner rulings — 2026-09-22 (during Phase 4 implementation)

**Approved by the project owner on 2026-09-22.** These resolve the questions raised by tickets #6, #9 and #14.

1. **Runtime Target Policy storage (§1).**
   - The policy is stored as a Tag in the reserved Tag provider `IgnitionMCPPolicy`, read through a declared-length gate with a 32 KiB cap that `setup-native apply` enforces. The evidence is `docs/research/runtime-target-policy-storage-and-alarm-query-bound.md`.
   - Every Tag Mutation on both Planes refuses a target in the reserved provider before the Target allowlist check, even under an explicit `*`. For moves, copies and renames this covers both source and destination.
   - The provider is matched by its provider component (`[IgnitionMCPPolicy]…` or `prov:IgnitionMCPPolicy:`), never as a substring of a later path segment.
2. **`alarm_acknowledge` is parked.** Under the D12 Phase 4 amendment it needs proof of a bound, and an exact-path `queryStatus` accumulates unacknowledged events (1, 2, then 3 on both Gateway rows). It stays off the Phase 4 surface until there is a verified native bound or a new decision.
3. **`phase4-live` environment.** The owner accepts that it has no protection rules. This is the same deviation, with the same compensating controls, as `phase3-live`.
4. **The reserved provider's own config resource.** Generic config Mutations (`config_resource_create`, `config_resource_update`, `config_resource_delete`, `config_resource_rename`) refuse the Tag-provider config resource named `IgnitionMCPPolicy` — type `ignition/tag-provider`, collection `core`, name `IgnitionMCPPolicy` — before the Target allowlist check, with `permission_denied`, even under an explicit `*`. Other Tag-provider resources remain manageable through `config_resource_*`. This is a refusal by name within an allowed type, not a new entry in the Refused resource types set (§5). Tracked as issue #36.
5. **Config resource collection.** Generic config Mutations always target the `core` collection. The server sends `collection=core` explicitly. A caller-supplied collection is accepted only when it is `core`; any other value fails with `invalid_argument`. A Target's identity is therefore `<resourceType>/<name>` in `core`.
6. **Delivery priority: speed.** For the rest of Phase 4 the goal is complete, working Tools: every Tool runs and has correct input and output behaviour. Blockers are limited to:
   - functional failures, meaning a wrong result or error code, or an overwrite or unintended Mutation;
   - broken safety rules (allowlist, reserved provider, `_types_`, Precondition token, secrets);
   - red live runs.

   Precision in D10 bound accounting is **deferred, not waived**. That covers exact serialized-byte accounting of Observed state, the aggregate early stop, and the wording of requested amounts and reduction advice. Coarse bounds must still exist: item-count ceilings and the output ceiling. The deferred findings are tracked in issue #41. D10 itself is unchanged, and #41 must be resolved or explicitly re-ruled before any claim beyond G4.

```yaml
owner_rulings_2026_09_22:
  runtime_target_policy_storage: reserved_tag_provider_IgnitionMCPPolicy
  reserved_provider_refusal: all_tag_mutations_both_planes_before_allowlist_including_star
  reserved_provider_match: provider_component_only
  alarm_acknowledge: parked
  phase4_live_environment_protection: none_owner_accepted
  config_collection: core_only
  reserved_provider_config_resource: refuse_by_name_in_config_resource_tools
  reserved_provider_config_refusal_code: permission_denied
  delivery_priority: speed_functional_io_first
  d10_accounting_precision: deferred_issue_41
```
