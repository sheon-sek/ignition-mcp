# Runtime Target Policy storage and the exact-path alarm query bound

**Ticket:** GitHub issue #6 — `[P4-01] Characterize Runtime Target Policy storage and bounded exact-path alarm queryStatus` (Phase 4 milestone 4a).

**Status:** live evidence collected on disposable CI Gateways; recommendation
recorded in the [Phase 4 runbook Open questions](../development/phase-4.md#open-questions).

**Decisions consulted:** D09, D12 (Phase 2 and Phase 4 amendments), D20, D21, D23, D29, D30.

Two facts later tickets depend on were still open. Ticket #7 (`tag_write`) needs
a place to read the Runtime Target Policy from. Ticket #9
(`alarm_acknowledge`) needs a bounded exact-path `system.alarm.queryStatus`.
This note characterizes both with recorded live evidence instead of assumption.

## 1. Runtime Target Policy storage (D30 §1)

### What D30 requires

D30 §1 fixes three properties for the storage location:

1. a Runtime Tool handler (`onToolCalled.py`) reads it **at bounded cost**;
2. the **Runtime MCP server cannot write it**;
3. `setup-native apply` can write it later **through Native REST**.

It must also live outside the Runtime Bundle so one deterministic bundle ZIP
(D21) serves every deployment, and a missing/malformed document fails closed
with `operation_disabled`.

### Candidates considered

| Candidate | Handler read primitive | Native REST write | Runtime plane can write? | Verdict |
|---|---|---|---|---|
| **A. String Tag in a dedicated Tag provider** (chosen) | `system.tag.readBlocking([path], timeoutMs)` | `POST /data/api/v1/resources/ignition/tag-provider` + `POST /data/api/v1/tags/import` | Only through the Phase 4 Tag Mutations, so the product must reserve the provider | Meets all three; chosen |
| B. Gateway config resource | `system.config.getResource(moduleId, typeId, name)` (8.3.8+) | typed resource route | no config-resource Mutation exists on the Runtime plane | No registered resource type carries a deployment-owned free-form JSON document; every type in the supported OpenAPI inventory has a fixed schema, and the API is 8.3.8+ |
| C. Gateway process environment variable (Phase 2 database-registry precedent) | Java interop `System.getenv` | none | not writable at runtime | Not REST-writable; changing it needs a Gateway restart |
| D. File under `data/` | `system.file.*` | none | n/a | D20 forbids apply writing the Gateway filesystem; read is not size-bounded by default |

### Evidence (frozen live run)

`@@ EVIDENCE-POLICY @@`

### What the live run shows

`@@ FINDINGS-POLICY @@`

### Chosen location and read primitive

```text
provider resource   ignition/tag-provider  name IgnitionMCPPolicy
policy Tag          [IgnitionMCPPolicy]RuntimeTargetPolicy   (AtomicTag, String, JSON text)
read primitive      system.tag.readBlocking([ "[IgnitionMCPPolicy]RuntimeTargetPolicy" ], timeoutMs)
apply write path    POST /data/api/v1/resources/ignition/tag-provider   (create the provider)
                    POST /data/api/v1/tags/import?type=json&collisionPolicy=Abort
apply read-back     GET  /data/api/v1/tags/export?provider=IgnitionMCPPolicy&type=json
```

The document is canonical JSON text in a String Tag, not a Tag data type of its
own, so a handler does one bounded normalization: read one path, check the byte
length against a fixed ceiling, `system.util.jsonDecode`, then validate the
schema. Because the Tag value and the REST export carry the same bytes, the
policy's SHA-256 can be compared between `apply`, a REST read-back and a live
handler read.

### Why the other candidates lose

- **B (Gateway config resource).** `system.config.getResource` is real and
  handler-scoped, and the harness measured it against the Tag provider resource
  it had just created. It still fails: the resource *type* inventory of a
  supported Gateway has no "deployment-owned document" type — every type is a
  module-owned schema (database connections, alarm journals, tag providers, the
  MCP server config, …). Storing the policy by abusing a typed schema would put
  a deployment document inside a module's contract, and the refused-type rule in
  D30 §5 rests on types being exactly what their module says they are.
  It also narrows the Gateway baseline to 8.3.8+.
- **C (process environment).** Phase 2 already uses this for the approved Named
  Query registry, but the registry is deployment-start configuration, not policy
  that `apply` owns and `verify` re-reads after a change. It fails property 3.
- **D (filesystem).** No Native REST route exists for it, and D20 explicitly
  forbids `apply` writing the Gateway filesystem.

### Property 2 is a product rule, not a Gateway rule

The harness measured a handler writing *inside* the policy provider
(`system.tag.writeBlocking` succeeded from `onToolCalled.py`). Jython handler
scope is not a security boundary, so "the Runtime MCP server cannot write it"
must be enforced by the product rule that a Runtime Tag Mutation refuses any
target inside the reserved policy provider **before** Preflight executes,
whatever the Target allowlist says — including an explicit `*`, exactly as D30 §5
makes Refused resource types refuse even under `*`.

The rule has to hold for every Runtime Tag Mutation that can reach a Tag:
`tag_write`, `tag_update`, `tag_delete`, `tag_move`, `tag_rename`, and
`tag_copy`'s destination (D30 §6 already makes `tag_move` check source and
destination). It needs an owner decision because D30 §1 states the requirement
but does not name the enforcement point; it is recorded under Open questions.

## 2. Exact-path `system.alarm.queryStatus` (D12 Phase 4 amendment)

### What the amendment requires

`alarm_acknowledge` takes `{alarmPath, eventId}` pairs, runs `queryStatus` on
that exact Alarm path, and holds **only if recorded evidence shows the
exact-path query is bounded before or during execution**, to the standard the
Phase 2 amendment set: a post-materialization `maxResults` check is not a bound.

### What was measured

The `alarm_probe` handler built a disposable fixture under one run-unique folder
and activated it:

- `Exact` and `ExactSibling`: two Tags whose paths share a string prefix;
- `Fold/ChildA` and `Fold/ChildB`: descendants of a folder, plus a
  `Fold/Chi` partial-leaf pattern;
- `Noise/*`: 60 additional active Alarms, so the system holds 64 live events
  that an unbounded query would return.

Each pattern form was queried three times, and one exact path was cycled
active → clear without acknowledgement to test accumulation.

### Evidence (frozen live run)

`@@ EVIDENCE-ALARM @@`

### What the live run shows

`@@ FINDINGS-ALARM @@`

## 3. Consequences for Phase 4

`@@ CONSEQUENCES @@`

## 4. Evidence index

- Workflow: `.github/workflows/phase4-live-g4a.yml`, environment `phase4-live`.
- Harness: `tests/harness/phase4-live/` (`policy_probe`, `alarm_probe`, driver,
  rehearsal, `characterization.json`).
- Recorded bodies and handler reports: `tests/fixtures/recorded/gateway-8.3/phase4/`
  with run ids in `tests/fixtures/recorded/gateway-8.3/provenance.json`.
- Revisible raw evidence: the `phase4-g4a-<gateway version>-<run id>` workflow
  artifacts (`evidence.json`, stage records, Gateway logs).

## 5. Open questions for the owner

Recorded in the runbook; repeated here so this note is self-contained.

1. Approve the reserved policy provider (`IgnitionMCPPolicy`) and the product
   rule that every Runtime Tag Mutation refuses targets inside it before
   Preflight, including under an explicit `*` allowlist.
2. `@@ OPEN-ALARM @@`
