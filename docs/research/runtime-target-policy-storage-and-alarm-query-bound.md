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

`phase4-live-g4a` run
[35635225572](https://github.com/sheon-sek/ignition-mcp/actions/runs/35635225572),
artifact `phase4-g4a-8.3.9-35635225572` (`policy-provision.json`,
`policy-read-before-restart.json`, `policy-read-after-restart.json`), plus the
8.3.8 job of the same run for the provider-startup race. Both Gateway rows
produced the same policy facts.

| Step | Live result |
|---|---|
| required Native REST routes present | `POST /resources/ignition/tag-provider`, `POST /tags/import`, `GET /tags/export` all present (0 missing) |
| `POST /resources/ignition/tag-provider` | HTTP 200 `{"changes":[{"collection":"core","name":"IgnitionMCPPolicy","type":"ignition/tag-provider","newSignature":"ba2596…"}],"problem":null,"success":true}` |
| provider readable through REST | `GET /resources/find/ignition/tag-provider/IgnitionMCPPolicy` → 200, `collection: core`, `config.profile.type: STANDARD`, signature `ba2596…` = the create report's `newSignature` |
| first `POST /tags/import?collisionPolicy=Abort` on a just-created provider | HTTP 200 `{"successCount":0,"failureCount":2,"failures":[Bad 776 "Cannot invoke …TagPath.getPathLength() because cleanPath is null"]}` — a retry is required (recorded from the 8.3.8 job) |
| `POST /tags/import?collisionPolicy=Abort` after the retry | HTTP 200 `{"successCount":2,"failureCount":0,"failures":[]}` |
| `POST /tags/import?collisionPolicy=Abort` on the existing Tags | HTTP 200 `{"successCount":0,"failureCount":2,"failures":[Bad 527 "Tag '[IgnitionMCPPolicy]RuntimeTargetPolicy' already exists, and 'abort' collision policy has been specified"]}` |
| `POST /tags/import?collisionPolicy=MergeOverwrite` | HTTP 200 `{"successCount":2,"failureCount":0}` |
| `GET /tags/export?type=json` read-back | 283-byte policy text, SHA-256 `b98bedf5…` = the applied document's SHA-256 |
| handler `system.tag.readBlocking(["[IgnitionMCPPolicy]RuntimeTargetPolicy"], 5000)` | `Quality: Good`, 1 result, `unicode`, 283 bytes, SHA-256 `b98bedf5…` = the REST read-back, `system.util.jsonDecode` → dict with keys `alarmShelveMaxSeconds, allowlists, auditMode, schemaVersion, serviceIdentity`, 24–26 ms |
| handler read of an absent policy path | `Bad_NotFound("Path '[IgnitionMCPPolicy]MissingPolicy' not found.")` |
| handler `system.tag.getConfiguration` (policy Tag and provider root) | both read cleanly; the Tag entry carries `dataType, defaultValue, enabled, name, path, tagType, value, valueSource` |
| handler `system.tag.writeBlocking` on a sibling Tag *inside* the policy provider | `Good`; the value read back afterwards equals the written value |
| Gateway restart, then the same handler read | same 283 bytes and same SHA-256 |
| `system.config.getResource(moduleId="ignition", typeId="tag-provider", name="IgnitionMCPPolicy")` | readable; `signature` = the REST signature; `config` keys `profile, settings` |
| `system.config.getResourceTypes()` | 35 registered types, every one module-owned (recorded list in the evidence) |
| handler scope inventory | `system.config`, `system.alarm`, `system.file`, `system.tag`, `system.util` all present; `System.getenv` callable; `system.util.getProjectName()` = `mcp_p4_probe` |

### What the live run shows

- **A Tag holding the policy document is the only candidate that satisfies all
  three D30 properties.** A dedicated Tag provider and its Tags are creatable and
  readable through Native REST, a handler reads one exact Tag path in ~25 ms
  with a bounded timeout, and the read is the same bytes `apply` wrote.
- **The write path needs a readiness retry, not a stronger bound.** One 8.3.8
  run had a freshly created provider answer the first import with
  `Bad 776 … cleanPath is null`; the identical request succeeded on retry and on
  the other row. `setup-native apply` must therefore poll (or retry) the policy
  import under a deadline instead of assuming one import is enough. Recorded as
  `phase4/tag-import-provider-not-ready.json`.
- **A second 8.3.8 run showed the other half of the same hazard: an *accepted*
  import is not proof that the provider serves the Tags.** In
  `phase4-live-g4a` run 35636395175 the import succeeded, `/tags/export` and
  `system.tag.getConfiguration` both returned the document, and yet a handler
  read of the same path answered `Error_Configuration` before the Gateway
  restart and `Bad_NotFound` afterwards — the running provider served no Tags at
  all. `apply` (and its `verify`) must therefore confirm a *handler-scope read of
  the Tag*, not just a config-level read, and repair by re-importing under a
  bounded deadline; the harness does exactly that and records
  `policyReadAttempts` / `policyReadRepairImports`.
- **`Abort` and `MergeOverwrite` are both needed by `apply`.** `Abort` refuses
  the write when the policy Tags already exist (Bad 527 per Tag), so an update
  needs an explicitly chosen collision policy; `MergeOverwrite` of an identical
  document succeeds, which is what an idempotent re-apply looks like. The
  resulting signatures (`newSignature`, `find … signature`, the Tag provider
  resource signature) are available on every row, so `plan`/`verify` can diff
  without guessing.
- **The document survives a Gateway restart.** The post-restart handler read
  returned the same 283 bytes and SHA-256 as the pre-restart read, so `apply`
  does not have to re-write the policy on every restart — but `verify` should
  still compare the signature, because nothing else enforces the document's
  presence.
- **`system.config.getResource` works, and still does not give candidate B a
  document type.** The read succeeded against the very provider resource the
  harness had created, and the live type inventory has 35 module-owned types and
  no free-form one.
- **The Runtime plane's inability to write the policy is a product rule.** The
  handler wrote a Tag inside the policy provider successfully, so the boundary
  cannot be the Jython scope; see the recommended rule below.

### Chosen location and read primitive

```text
provider resource   ignition/tag-provider  name IgnitionMCPPolicy
policy Tag          [IgnitionMCPPolicy]RuntimeTargetPolicy         (AtomicTag, String, JSON text)
length Tag          [IgnitionMCPPolicy]RuntimeTargetPolicyLength   (AtomicTag, Int4, byte length)
read primitive      system.tag.readBlocking([lengthPath], timeoutMs)   -> declared byte length
                    refuse unless 0 < declared <= IgnitionMcpPolicyMaxBytes (32768)
                    system.tag.readBlocking([policyPath], timeoutMs)   -> the document
                    refuse unless the value's byte length equals the declared length
apply write path    POST /data/api/v1/resources/ignition/tag-provider   (create the provider)
                    POST /data/api/v1/tags/import?type=json&collisionPolicy=Abort
apply read-back     GET  /data/api/v1/tags/export?provider=IgnitionMCPPolicy&type=json
```

The document is canonical JSON text in a String Tag, not a Tag data type of its
own, so a handler does one normalization: `system.util.jsonDecode`, then schema
validation. Because the Tag value and the REST export carry the same bytes, the
policy's SHA-256 can be compared between `apply`, a REST read-back and a live
handler read.

### How the read is bounded — and what is not

**There is no native pre-read size limit to rely on.** Ignition documents no
maximum character count for a `String` Tag value, and `system.tag.readBlocking`
takes only paths and a timeout: by the time a handler holds the value, the whole
string has been materialized. A handler-side length check *after* that read is
therefore not an execution or memory bound — the same reasoning D12's Phase 2
amendment used to park `alarm_status` and `alarm_journal`.

**The bound is the companion length Tag, checked before the document is read.**
The reader:

1. reads `RuntimeTargetPolicyLength` — a small `Int4`, bounded by its data type,
   not by the document;
2. fails closed (`operation_disabled`) if that Read is missing, not Good, not a
   positive integer, or **greater than `IgnitionMcpPolicyMaxBytes` (32 KiB)** —
   without ever touching the policy Tag (the gate reports `invalid` for a
   non-positive declaration and `oversize` above the cap);
3. only then reads the policy Tag and refuses the document if its byte length
   does not match the declared length.

The write side carries the same cap: `setup-native apply` refuses to write a
document larger than the cap, writes the length Tag in the same import, and
`verify` re-reads the export and fails when the stored bytes or the declared
length disagree. Together with the refusal rule above, `apply` is the only writer
of the reserved provider, so the declared length is an enforced maximum rather
than a hint.

**Residual limitation, stated plainly.** The cap is enforced by the product, not
by the Gateway: an operator who edits the Tag in the Designer or drops a file
into `config/resources` can still store an oversize document, and the next
handler read would materialize it. The reader's post-read comparison detects that
and fails closed, but detection is not a memory bound. The gate therefore makes
the storage choice **conditional**: it holds for a deployment whose policy is
written by `apply` (the supported path, and the only path the product exposes);
if the owner will not accept this product-level cap as the enforcement, Runtime
Mutations must stay disabled and the location must move to a mechanism with a
native bound.

The harness measures the whole mechanism live: the served, length-verified read
of the real policy, and a deliberately oversize companion pair that must be
skipped with the value never materialized. That oversize pair is a harness
fixture placed in the same provider so it goes through the same admission path; a
deployment's provider holds only the policy Tag and its length Tag. Recorded
values are in the evidence table below.

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
must be enforced by the product.

**Recommended rule (needs an owner decision, see Open question 1).** Every
Runtime Tag Mutation refuses any target that resolves inside the reserved policy
provider **before** Preflight executes, whatever the Target allowlist says,
including an explicit `*` — exactly as D30 §5 makes Refused resource types refuse
even under `*`. The refusal is by provider, so it covers the policy Tag, its
companion length Tag and anything else a deployment puts there.

| Mutation | Refused when |
|---|---|
| `tag_write`, `tag_update`, `tag_delete` | the target is inside `IgnitionMCPPolicy` |
| `tag_create` | the new Tag would be created inside `IgnitionMCPPolicy` |
| `tag_move`, `tag_rename` | the **source** or the **destination** is inside `IgnitionMCPPolicy` |
| `tag_copy` | the **destination** (a copy *into* the provider) or the **source** (a copy *out of* the provider, which would publish the policy document elsewhere) is inside `IgnitionMCPPolicy` |

`tag_create` matters as much as the rest: with a permissive CONFIG target
allowlist it could otherwise create a Tag inside the provider, and `tag_copy`
can both write into the provider and lift the policy document out of it. Refusing
source *and* destination closes both directions for every read-or-write pair of
operations. The same refusal belongs in the D08 Preflight stage, so a batch that
contains a policy-provider target is rejected whole rather than partially
applied (D30 §3).

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

`phase4-live-g4a` run
[35635887711](https://github.com/sheon-sek/ignition-mcp/actions/runs/35635887711),
artifacts `phase4-g4a-8.3.8-35635887711` and `phase4-g4a-8.3.9-35635887711`
(`alarm.json`, `evidence.json`). Both Gateway rows produced identical
measurements; the numbers below are from the 8.3.8 row.

Fixture: 62 Tags in one run-unique folder under `[default]`, four of them the
measured paths (`Exact`, `ExactSibling`, `Fold/ChildA`, `Fold/ChildB`) and 60
noise Alarms, each with one manual-acknowledge `AboveValue` Alarm. All 64
Alarms were activated before the measurements. Query repetitions: 3 per form.

| Query pattern | Items returned |
|---|---|
| exact source `prov:default:/tag:mcp_p4_<run>/Exact:/alm:ProbeHi` | **1** |
| sibling source `…/ExactSibling:/alm:ProbeHi` | **1** |
| tag-path only `prov:default:/tag:mcp_p4_<run>/Exact` | 0 |
| tag-path only `[default]mcp_p4_<run>/Exact` | 0 |
| tag-path only `mcp_p4_<run>/Exact` | 0 |
| folder tag-path `prov:…/tag:mcp_p4_<run>/Fold` | 0 |
| partial leaf `prov:…/tag:mcp_p4_<run>/Fold/Chi` | 0 |
| folder trailing wildcard `…/Fold/*` | 2 |
| alarm-name wildcard `…/Exact:/alm:*` | 1 |
| root trailing wildcard `prov:…/tag:mcp_p4_<run>/*` | 64 |
| root bare wildcard `*mcp_p4_<run>*` | 64 |
| no filters at all | 64 |
| exact source through `source=` instead of `path=` | 1 |
| exact source with `state=["ActiveUnacked"]` | 1 |

| Experiment | Live result |
|---|---|
| one exact Alarm path, activated → cleared without acknowledgement, three times | exact-path item count **1 → 2 → 3** |
| event detail after those cycles | the same exact pattern listed **3** events, `getState()` = `Cleared, Unacknowledged` |
| `system.alarm.acknowledge(ids, note, user)` on the returned event ids | 3 ids acknowledged, 0 left unacknowledged, state afterwards `Cleared, Acknowledged` |
| median query time | exact 0 ms, root wildcard 1 ms, unfiltered 0 ms |

The policy-read gate, measured in the same run (`phase4-live-g4a` run
[35640303173](https://github.com/sheon-sek/ignition-mcp/actions/runs/35640303173),
both Gateway rows, no drift):

| Gate step | Live result (8.3.8 / 8.3.9) |
|---|---|
| companion length Tag `RuntimeTargetPolicyLength` | `Good`, declared byte length **283** = the applied document's byte length |
| policy gate outcome | `served`, the document's SHA-256 equals the applied document's SHA-256, value byte length equals the declared length |
| gated read cost | 6 ms / 3 ms |
| configured maximum | `IgnitionMcpPolicyMaxBytes` = 32768 |
| deliberately oversize pair (`OversizePolicyProbe`) | declared byte length **39967** > cap → gate `oversize`, `materialized: false` — the value Tag was never read |

`phase4-live-g4a` run
[35636981286](https://github.com/sheon-sek/ignition-mcp/actions/runs/35636981286)
(commit `ca8fd33`) reproduced every fact above on both Gateway rows with no drift
from `characterization.json`, so these numbers are stable across runs and not a
one-off.

### What the live run shows

**Matching is literal, and that is good news for target checks.** A pattern
without `*` matches only the source string it spells out. A tag-path-only
pattern matches nothing at all — not the alarm on that Tag, and not anything
below a folder — and `Fold/Chi` does not match `ChildA`. `*` is the only
expansion mechanism, so a handler that builds an exact source pattern cannot
accidentally reach a subtree.

**One exact Alarm path is nevertheless not bounded, and `alarm_acknowledge` is
therefore parked.** The same exact pattern that returned one item returned two
after the next unacknowledged activate/clear cycle and three after the third.
Ignition keeps each *cleared and unacknowledged* event; nothing about the
caller's request bounds how many exist, because the count is driven by how long
operators have left alarms unacknowledged on that source. A single exact Alarm
path on a busy Gateway can therefore hold an arbitrarily large number of current
events, and `queryStatus` returns all of them: the recorded run already shows
`len(results)` growing 1 → 2 → 3 over three cycles, with the query itself
carrying no limit, no continuation and no interruptible timeout (D12 Phase 2
amendment).

**No per-call execution or cost bound can be claimed either.** There is one
fixed query implementation: the exact pattern, the wildcard pattern and the
unfiltered query all take the same sub-millisecond median at 64 live events,
and the unfiltered query returns 64× the exact query's items. Nothing in the
measurement shows the filter being applied during execution in a way the
handler could rely on to keep memory or time bounded.

That fails the D12 Phase 4 amendment's condition — "recorded evidence shows an
exact-path `queryStatus` is bounded before or during execution, the standard set
by the Phase 2 amendment" — so `alarm_acknowledge` is parked like `alarm_status`
and `alarm_journal`, and the Phase 4 scope loses ticket #9 until an owner
decision supplies a credible bound.

**Positive side effect for `alarm_shelve`/`alarm_unshelve` (tickets #3).**
Literal matching is exactly what "exact targets only, wildcard mutation
forbidden" (D08/D12) needs: a caller cannot pass a folder or a prefix to widen a
shelve, because such a pattern matches nothing rather than a subtree. Shelving
also returns no materialized result for the handler to collect.

## 3. Consequences for Phase 4

- **Ticket #7 (`tag_write`) and the Tag CONFIG Mutations (#10–#12)** read the
  policy through the gated two-step read above (length Tag first, cap, then the
  document), validate the schema and fail closed with `operation_disabled` when
  the document is missing, over the cap, or malformed. They must also apply the
  reserved-provider refusal rule — including `tag_create` and the source side of
  `tag_copy`/`tag_move`/`tag_rename` — before Preflight runs.
- **Ticket #21 (`setup-native apply`)** creates the provider with
  `POST /data/api/v1/resources/ignition/tag-provider`, imports the policy *and*
  its length Tag with a bounded retry loop (the first import on a fresh provider
  can fail while the provider starts), refuses a document over
  `IgnitionMcpPolicyMaxBytes` before importing, uses `Abort` for a create and
  `MergeOverwrite` for a deliberate update, and verifies with a *handler-scope*
  read of the Tag plus `GET /data/api/v1/tags/export` and the provider resource
  signature. An accepted import is not proof that the running provider serves
  the Tags, so `verify` must read the Tag and `apply` must repair by
  re-importing; the document survives a Gateway restart, so it does not have to
  be rewritten on every boot.
- **Ticket #9 (`alarm_acknowledge`) is parked** and its row in the Phase 4 scope
  table must stop being planned work until the owner decides. `alarm_status` and
  `alarm_journal` stay parked for the same class of reason.
- **Ticket #3 (`alarm_shelve`/`alarm_unshelve`) is unaffected** and gains a
  safety argument: literal pattern matching means an exact-path shelve cannot
  widen to a subtree, and shelving materializes no result for the handler.
- **No new error code and no contract change** results from this ticket; it adds
  no Tool. The policy document's field names live in ticket #7's contract work.

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
   rule that every Runtime Tag Mutation refuses any target inside it before
   Preflight, including under an explicit `*` allowlist. The rule must cover
   `tag_write`, `tag_update`, `tag_delete`, `tag_create`, and both the source and
   the destination of `tag_move`, `tag_rename` and `tag_copy`. D30 §1 states the
   property; the enforcement point, and `tag_create`/source-side coverage, have
   to be named.
2. Approve the deployment-enforced policy size cap
   (`IgnitionMcpPolicyMaxBytes`, 32 KiB) carried by the companion length Tag, or
   reject it. There is no native size limit on a Tag value and no size option on
   `system.tag.readBlocking`, so without this cap the read cost equals whatever
   is stored and the storage choice is not bounded. If the cap is rejected, the
   fail-closed default is to keep Runtime Mutations disabled and move the policy
   to a mechanism with a native bound.
3. Approve parking `alarm_acknowledge` (ticket #9). The D12 Phase 4 amendment
   holds only with recorded evidence of a bounded exact-path `queryStatus`, and
   the recorded run shows the opposite: one exact Alarm path grows an event per
   unacknowledged activate/clear cycle. Re-opening it needs a credible
   pre/during-execution bound, not a handler-side check after the fact.
