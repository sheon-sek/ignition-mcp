# Phase 4 REST live harness (milestone 4c)

Live proof for the REST Mutation Tools — `config_resource_update`,
`config_resource_create`, `config_resource_delete`, `config_resource_rename`,
`project_import` and `tag_config_import` (CONFIG), `alarm_pipeline_cancel`
(CONTROL) and `artifact_delete` (CONFIG) — on disposable CI-owned Gateways. Driven
by `.github/workflows/phase4-live-rest.yml` on pull requests in the trusted
repository, under the `phase4-live` GitHub environment.

## What it proves

The driver (`rest_driver.py`) talks MCP over HTTP to a real `ignition-rest`
server, uses the artifact data plane the way an agent would, and reads the Gateway
only through the server's own read Tools, so every case is something an agent could
observe:

| Case | Expectation |
|---|---|
| `inventory-agent-exact` | with both classes enabled, the config-scoped credential sees exactly the read inventory plus the six CONFIG Tools |
| `inventory-operator-exact` | the CONTROL credential sees exactly the read inventory plus `alarm_pipeline_cancel` (D07: discovery follows the credential's scopes) |
| `inventory-reader-exact` | the read-only credential sees exactly the read inventory: D07 discovery still filters every Mutation Tool |
| `allowlisted-update-applies` | an allowlisted change returns structured success |
| `update-moves-the-signature` | the Resource signature moved, so the caller has a fresh Precondition token |
| `observed-state-carries-the-change` | the Observed state re-read shows the intended change |
| `update-reports-the-core-collection` | an omitted `collection` means `core`, and the result reports it (D30 owner ruling 5) |
| `independent-reread-confirms` | a separate `config_resource_get` agrees with the reported signature |
| `explicit-core-collection-is-accepted` | naming `core` explicitly is accepted |
| `explicit-core-collection-reports-core` | …and the result reports the same collection |
| `non-core-collection-is-invalid-argument` | any other collection value is `invalid_argument` |
| `non-core-collection-changes-nothing` | …and the resource is left exactly as it was |
| `stale-signature-is-conflict` | re-sending the pre-change signature is a `conflict` |
| `stale-signature-changes-nothing` | the refused change left the resource alone |
| `refused-resource-type-is-permission-denied` | the Gateway's own API token is `permission_denied` under any Target allowlist |
| `refused-resource-still-usable` | the refused resource is untouched and still readable |
| `non-allowlisted-target-is-permission-denied` | a resource the Target allowlist does not name is refused |
| `non-allowlisted-target-changes-nothing` | …and left alone |
| `singleton-update-applies`, `singleton-update-moves-the-signature` | a singleton's item carries no name, so it is built from the Gateway's own schema |
| `allowlisted-create-applies` | a create of an allowlisted name returns structured success |
| `create-reports-the-published-resource` | the Observed state re-read shows the resource the create asked for |
| `create-independent-reread-confirms` | a separate read agrees with the reported signature |
| `create-of-an-existing-target-is-conflict` | D11 collision policy: the existing target is a `conflict` |
| `create-of-an-existing-target-changes-nothing` | …and the resource is left as it was |
| `create-of-a-refused-resource-type-is-permission-denied` | D30 §5 refuses the API-token type |
| `create-of-a-refused-resource-type-publishes-nothing` | …and nothing was published |
| `create-outside-the-target-allowlist-is-permission-denied` | a create the allowlist does not name is refused |
| `create-outside-the-target-allowlist-publishes-nothing` | …and nothing was published |
| `allowlisted-delete-applies` | a delete with the signature in the native path succeeds |
| `delete-reports-absence` | the Observed state is the Target's absence |
| `delete-independent-reread-shows-absence` | a separate read answers `not_found` |
| `delete-of-an-absent-target-is-not-found` | deleting it again is `not_found`, not a success |
| `delete-with-a-stale-signature-is-conflict` | a token that belongs to another resource is a `conflict` |
| `delete-with-a-stale-signature-changes-nothing` | …and the resource is still there |
| `delete-of-a-refused-resource-type-is-permission-denied` | D30 §5 covers every config Mutation |
| `refused-resource-survives-the-delete-denial` | …with the token still working |
| `delete-outside-the-target-allowlist-is-permission-denied` | a delete the allowlist does not name is refused |
| `delete-outside-the-target-allowlist-changes-nothing` | …and the resource is still there |
| `allowlisted-rename-applies` | a rename of an allowlisted source succeeds |
| `rename-reports-both-names` | the result names the old and the new resource |
| `rename-independently-shows-the-old-name-vacant` | a separate read finds nothing at the old name |
| `rename-independently-shows-the-new-name-holding-it` | …and the resource with the reported signature at the new one |
| `rename-onto-an-occupied-destination-is-conflict` | D11 collision policy at the destination |
| `rename-onto-an-occupied-destination-changes-nothing` | …and the source is left where it was |
| `rename-with-a-stale-signature-is-conflict` | the read-compare is the rename's only in-band check |
| `rename-with-a-stale-signature-changes-nothing` | …and nothing moved |
| `rename-of-a-refused-resource-type-is-permission-denied` | D30 §5 covers the rename too |
| `refused-resource-survives-the-rename-denial` | …with the token still working |
| `rename-into-an-unallowlisted-destination-is-permission-denied` | the rename destination is a Target too (D30 §3) |
| `rename-into-an-unallowlisted-destination-changes-nothing` | …and nothing moved |
| `project-import-commits` | an uploaded archive imports into an existing Project and the D16 transaction ends `COMMITTED` |
| `project-import-baseline-is-the-callers-read` | the transaction's baseline A is the fingerprint `project_export` reported to the caller |
| `project-import-verifies-its-own-candidate` | the post-import export C equals the staged candidate B (D16 reconcile) |
| `project-import-reports-the-dispatch` | the committed transaction reports `importDispatched: true` — an import request left the server; the field is false only when nothing was sent |
| `project-export-fingerprint-is-independent` | the fingerprint the server reports for a fresh export equals this harness's own `pcf1` computation |
| `project-import-content-lands` | that independent fingerprint equals the candidate the import reported |
| `project-import-marker-is-present` | the entry the candidate carried is in the Project the Gateway now serves |
| `project-import-of-the-current-content-is-no-change` | re-importing that content is D16's `NO_CHANGE` |
| `project-import-no-change-dispatches-nothing` | …and nothing was dispatched, so `importDispatched` is false |
| `project-import-stale-fingerprint-is-conflict` | the pre-commit fingerprint is a stale Precondition token (D30 §2) |
| `project-import-stale-fingerprint-changes-nothing` | …and the refused import changed nothing |
| `project-import-non-allowlisted-project-is-permission-denied` | D30 §7 for a Project the Target allowlist does not name |
| `project-import-non-allowlisted-project-changes-nothing` | …and that Project is untouched |
| `project-import-invisible-artifact-is-not-found` | D30 §6: an archive another principal owns answers `not_found` |
| `tag-import-source-is-not-empty` | the source export `provision.py` published holds the source Tags |
| `tag-import-reports-no-missing-tag` | the bounded re-export of the destination shows every Tag the document declares |
| `tag-import-destination-serves-every-source-tag` | a *second*, independently downloaded export of the destination serves them too |
| `tag-import-observed-state-covers-the-source-tags` | the Observed state names every source Tag (D30 §6) |
| `tag-import-observed-state-is-relative-to-the-target` | …and they are provider-relative paths under the import path |
| `tag-import-leaves-the-source-path-untouched` | D30 §4: `Abort` creates Tags, so nothing outside the Target changed |
| `tag-import-into-an-occupied-destination-is-conflict` | D11's collision policy, sent as `Abort` and refused as `conflict` |
| `tag-import-conflict-changes-nothing` | …and the refused re-import changed nothing |
| `tag-import-non-allowlisted-path-is-permission-denied` | D30 §7 for a destination path the Target allowlist does not name |
| `tag-import-non-allowlisted-path-creates-nothing` | …and that path holds none of the source Tags |
| `tag-import-invisible-artifact-is-not-found` | D30 §6: a Tag export another principal owns answers `not_found` |
| `tag-import-under-the-allowlisted-prefix-applies` | D30 §1/D08: the allowlisted entry is a path *prefix*, so a destination below it is authorized |
| `tag-import-prefix-destination-serves-every-source-tag` | …and an independent export of that nested destination serves the source Tags |
| `tag-import-reserved-policy-provider-is-permission-denied` | D30 §1: the Runtime Target Policy's provider is reserved |
| `tag-import-reserved-policy-provider-says-which-rule` | …and the error names the reserved-provider rule, not the Target allowlist |
| `pipeline-cancel-config-credential-is-permission-denied` | the cancel's effect is CONTROL, so the config credential never reaches the handler |
| `pipeline-cancel-non-allowlisted-pipeline-is-permission-denied` | D30 §7 for a pipeline the Target allowlist does not name |
| `pipeline-cancel-path-under-the-target-is-permission-denied` | D30 §6: the allowlist holds exact paths, never prefixes, so a path *under* the Target is a different Target |
| `pipeline-cancel-target-parent-path-is-permission-denied` | …and so is the Target's own parent |
| `pipeline-cancel-oversize-path-is-limit-exceeded` | D10: the pipeline path bound is enforced with the requested length and the limit |
| `pipeline-cancel-oversize-event-is-limit-exceeded` | …and so is the alarm event bound |
| `pipeline-cancel-blank-path-is-invalid-argument` | an empty component is refused before anything is dispatched |
| `pipeline-cancel-without-a-run-for-the-event-is-not-found` | the bounded pre-dispatch read refuses a cancel for a run the pipeline does not hold, and dispatches nothing |
| `pipeline-cancel-refusals-change-nothing` | the bounded status read serves the same runs before and after every refusal above |
| `inventory-gate-off-exact` | with the classes disabled every Mutation Tool is gone from discovery |
| `disabled-class-call-is-refused` | …and calling one is refused, never executed |
| `disabled-class-artifact-delete-is-refused` | …including the artifact removal, whose discovery has no Gateway capability to lose |
| `artifact-delete-target-is-served` | the artifact the removal case addresses is served by `artifact_list` first |
| `artifact-delete-removes-the-artifact` | the owning credential removes its own export through the D17 ArtifactStore |
| `artifact-delete-reports-absence` | the result names the identifier it removed |
| `artifact-delete-reports-the-kind` | …and the kind of artifact it removed |
| `artifact-delete-observed-state-is-absence` | the Observed state a removal leaves is `present: false` |
| `artifact-delete-independent-reread-is-not-found` | a separate `artifact_info` answers `not_found` |
| `artifact-delete-listing-no-longer-serves-it` | …and `artifact_list` no longer serves it |
| `artifact-delete-of-an-absent-target-is-not-found` | removing it again is `not_found`, not a success |
| `artifact-delete-invisible-artifact-is-not-found` | D30 §6: an artifact another principal owns answers `not_found` |
| `artifact-delete-invisible-artifact-survives` | …and its owner still sees it |
| `artifact-delete-reader-credential-is-permission-denied` | the effect is CONFIG, so the read-only credential is refused |
| `artifact-delete-denied-call-changes-nothing` | …and the artifact it could not remove is still there |
| `artifact-delete-oversize-identifier-is-invalid-argument` | D10: the identifier bound, refused with the requested length |
| `artifact-delete-malformed-identifier-is-invalid-argument` | …and a traversal-shaped identifier is refused as input |
| `artifact-delete-data-plane-route-is-absent` | D30: `DELETE /artifacts/{id}` is HTTP 405 — the Tool is the only delete path |

The candidate archive the import cases upload is the export the Project already had,
with one SQL comment appended to its first named-query payload. That is the edit the
Gateway stores verbatim: its import rewrites `project.json` and drops archive entries it
does not recognise as resources, so an edit there would make C differ from B for reasons
that have nothing to do with this Tool (the same constraint the G3 transaction case hit
live, recorded in `tests/harness/phase3-live/driver.py`). If a round trip ever mismatches,
the driver writes the per-entry diff of the candidate against the Gateway's re-export into
`observations.json` before it fails, so one run is enough to diagnose it.

The Tag import cases export the source Tags with `tag_config_export` and import that
artifact, so the content is real Gateway state and the bytes dispatched are the bytes
the export produced. `provision.py` publishes the source Tags itself, with
`MergeOverwrite` — this is the harness writing its own fixture, not the Tool: the Tool
always sends `Abort` (D30 §4), and the collision case above is what proves it live. The
import goes to a *destination* path, so the cases also show that the source path is
untouched.

The driver asserts exactly one Target-denial code, `permission_denied` (D30 §7,
Tool-scoped; see `docs/development/phase-4.md`). That is the code both
`config_resource_*`, `project_import` and `tag_config_import` answer; the frozen Phase 3 machinery the G3
harness drives keeps its recorded `operation_disabled` because the code is declared per
operation.

The Tag import cases also pin the two Target rules that are this Tool's alone
(D30 §1): the allowlisted entry is a provider-qualified path *prefix* that matches at
segment boundaries, so the nested destination the cases import into is authorized by the
entry for its parent; and the Runtime Target Policy's own provider is reserved, so an
import addressed to it is refused by provider before the allowlist is consulted. The
second case runs against a deployment whose Target allowlist does not name that provider
either, and asserts the message, so a run tells a reserved-provider refusal from an
ordinary allowlist denial.

The deployment this driver runs against enables the config *and* control mutation
classes, the sensitive exports (the Project cases read their Precondition token from
`project_export`, and the Tag cases take their import document from
`tag_config_export`), artifact upload, and the D16 Project writer with an explicit
`IGNITION_MCP_GATEWAY_ID`. Those gates decide the inventory the driver asserts, so the
read inventory here includes the two sensitive-export Tools. Three credentials are
configured — read-only, read+config and read+control — because D07 assigns scope by
operation effect, and the inventory cases prove each one sees only its own lane.

A fresh CI Gateway serves no Alarm Notification Pipeline *runs*, which is the state the
pipeline cancel cases prove: the class and scope gates, the exact-path Target rule, both
D10 input bounds, and the bounded pre-dispatch read that refuses a run the pipeline does
not hold. Producing a run needs an Alarm Event notifying through a provisioned profile,
which this harness does not provision; the dispatched-and-verified path is proven by the
unit fixture in `packages/ignition-rest-mcp/tests/test_phase4_alarm_pipeline_cancel.py`,
and `docs/development/phase-4.md` records the limitation and what would close it.

The artifact removal cases need no Gateway at all, which is the point of the Tool: D30
dropped the artifact HTTP route, so `artifact_delete` dispatches nothing and its cases
prove the server's own store — the removal and its Observed state, an independent
`artifact_info`/`artifact_list` re-read, the ownership rule of D30 §6, the CONFIG scope,
both D10 input bounds, and the dropped data-plane route. Two of its rules are
fixture-only and `docs/development/phase-4.md` records them: the **Target-allowlist
denial**, because artifact identifiers are generated at removal time so this deployment
must write the explicit `*` (D30 §3), and the **retention-lock `conflict`**, because a
locked RECOVERY artifact comes from a D16 transaction that ended unresolved and no case
in this harness produces one. `packages/ignition-rest-mcp/tests/test_phase4_artifact_delete.py`
pins both, and the same fixture pins the crash-safe `DELETING` recovery.

## Injected transport failures (ticket #20)

The driver's third mode (`--mode fault`) drives a second `ignition-rest` instance whose
Gateway URL points at `fault_proxy.py`: a stdlib-only TCP hop the compose stack runs on
the host network, with a control port that arms one fault at a time. A case is
`arm(mode, route) → call the Tool → judge` against three independent records: what the
caller saw, what the *hop* did (every request it saw, whether it reached the Gateway,
whether the body completed), and what the *server* audited (D18's rows) and persisted
(the D16 transaction row).

| Fault | What it does | Why it is the right shape |
|---|---|---|
| `refuse_after_forward` | answers the first *N* matching requests, then closes its data listener | a refused connect needs the listener itself to go away: a RST after `accept()` is a *successful* connect, which the server correctly classifies as possibly dispatched |
| `drop_mid_body` | reads a few body bytes, then RSTs without dialling the Gateway | the connection dies while the body is being written, and the Gateway never sees the request |
| `drop_after_body` | relays the whole request, reads the Gateway's answer, then RSTs the client | the Gateway applied the change and answered; the caller cannot learn anything from the exchange |
| `delay_response` | relays the request, then holds the answer for *N* seconds | the answer arrives after the deployment's deadline |
| `connect_refused` | closes the data listener immediately | every connect is refused (used where a case needs the hop to be gone for the whole call) |

The proxy answers **one request per connection** and says so on the wire (`Connection:
close`, rewritten on the answer head as well). Without that, a pooled client would send
its next request into a socket the proxy had already closed, and the server would see an
ambiguous boundary where the armed fault meant a refused connect.

| Case | Expectation |
|---|---|
| `fault-not-sent-*` | the write's connect is refused: the caller gets `gateway_unavailable`, the audit result row is `not_sent`, and nothing changed |
| `fault-mid-body-*` | the proxy never forwarded the request, the read-back shows the pre-state, so the call is `conflict`/`not_applied` |
| `fault-after-full-body-*` | the change is in place *and* the caller gets `outcome_unknown` — a read-back alone may not claim this call's success (D30 §2) |
| `fault-deadline-*` | the caller gets the deployment's `timeout`; the audit holds one cancelled result row naming the boundary and the Target; the change may well have applied |
| `fault-cancellation-*` | `notifications/cancelled` is answered with JSON-RPC `-32800`; one attempt, one cancelled result row, no replay |
| `fault-import-not-sent-*` | the D16 row records `importDispatched: false` and the `not_sent` boundary, holds no recovery lock, and the Project is unchanged |
| `fault-import-mid-body-*` | `NOT_APPLIED` on the read-back, with the ambiguous boundary on the row |
| `fault-import-after-full-body-*` | D16's reconciliation: the post-import export equals the staged candidate, so the transaction is `COMMITTED` and the content lands |
| `fault-import-cancellation-*` | the row is left interrupted and the reconcile loop ends it `OUTCOME_UNKNOWN` — never a success, and never a second dispatch |
| `core-collection-update-applies` | an allowlisted update through the proxy succeeds |
| `core-collection-update-moves-the-signature` | …and the Resource signature moved |
| `core-collection-read-count`, `core-collection-is-on-every-read` | the hop saw exactly the update's two reads, both with `collection=core` |
| `core-collection-write-count`, `core-collection-write-names-the-collection-route` | exactly one `PUT` left the server, to the resource type's documented collection route |
| `reserved-provider-config-resource-is-readable` | the reserved policy provider is readable through `config_resource_get` (the refusal is Mutation-only) |
| `reserved-provider-update-is-permission-denied`, `…-delete-…`, `…-create-…` | the `ignition/tag-provider` resource named `IgnitionMCPPolicy` is refused by all three, although the Target allowlist names it |
| `reserved-provider-update-says-which-rule` | the refusal message names the reserved resource, so it cannot be read as an allowlist denial |
| `reserved-provider-*-dispatches-nothing` | the hop's own record holds **no** config-resource request for those calls: a refused Target is decided from its identity |
| `reserved-provider-*-changes-nothing` | the provider's Resource signature is exactly what it was |
| `renaming-a-provider-into-the-reserved-name-is-permission-denied`, `…-says-which-rule`, `…-dispatches-nothing`, `…-changes-nothing` | a rename *into* the reserved name is refused too (both names of a rename are Targets, D30 §3) |
| `renaming-the-reserved-provider-away-…` | …and so is renaming the reserved provider away, even onto an occupied destination: the name rule runs before the collision probe |
| `another-provider-update-applies`, `…-moves-the-signature`, `…-dispatches-one-write` | an ordinary Tag provider is still updated through the same Tool, with exactly one `PUT` |
| `a-name-that-begins-with-the-reserved-name-update-applies`, `…-moves-the-signature` | `IgnitionMCPPolicyStaging` is a different resource: the name matches exactly, never as a substring |

The `core-collection-*` rows are the live form of D30's owner ruling 5. A real Gateway
answers a read that omits the collection exactly as it answers one that names `core`, so
Gateway state alone cannot show what the server sent; the proxy owns the hop the server's
own HTTP client wrote through, and the driver reads its record of the request targets. The
`collection` field of a *change item* is not observable there (the proxy records targets,
not bodies): the unit tests pin it against the recorded Gateway, which keys its resource
state by `(name, collection)` and therefore answers a request that omitted or misnamed the
collection with the wrong resource or none at all. The case runs **after** the fault cases
above, because the fault instance runs a deliberately small tool budget and the `#20` cases
are timing-sensitive: a few extra requests in front of them would perturb evidence this
ticket does not own.

The `reserved-provider-*` rows are the live form of D30's owner ruling 4 (ticket #36). The
refusal is decided from the Target's identity, so Gateway state cannot show what a refused
call did — and, unlike a Refused resource type, the *rule* is about one name inside an
allowed type, so the same run also proves the type stays manageable: an ordinary provider
and one whose name merely begins with the reserved one are updated through the same Tool.
The hop's record is what makes "nothing was dispatched" observable: the cases assert the
proxy saw **no** config-resource request at all for a refused call, while a call that was
accepted shows exactly its one `PUT`. Every Target these cases address — including the
reserved one — is in the deployment's Target allowlist, so a `permission_denied` could not
be the allowlist's, and one case renames onto an *occupied* destination so the refusal is
also provably not the D11 collision. The reserved provider is provisioned by
`provision.py`; `policyProviders.caseFoldedLookup` records how the live Gateway answers a
read of that name with its case folded, which is the measurement behind the rule's
fail-closed case handling.

"Never a replay" is asserted twice, and independently: the audit log holds exactly one
`attempt` row for the call, and the proxy's per-method counter shows exactly one write
left the server.

The fault instance runs with the deployment's smallest useful budgets
(`IGNITION_MCP_TOOL_TIMEOUT_SECONDS`, `IGNITION_MCP_ARTIFACT_TIMEOUT_SECONDS`) and a
short `IGNITION_MCP_PROJECT_RECONCILE_INTERVAL_SECONDS`, so a case costs seconds rather
than minutes; the deadlines themselves are the production rules, only smaller. Its
`--raw-dir` is a subdirectory, so its raw bodies cannot overwrite the other modes'.

## Layout

- `docker-compose.yml` — one Gateway, no MCP Module: the REST plane needs none. It also
  runs the fault proxy (`fault-proxy`) for the injected-failure cases; that service uses
  the host network on purpose, so the data port it closes is a real loopback socket and
  a closed one is a *refused* connect rather than a Docker-proxied one.
- `provision.py` — test-only fixture provisioning through the Gateway's own Native
  REST API: four `ignition/audit-profile` resources (the allowlisted Target, an
  allowlist control, and the two rename sources), each created with an explicit
  `"collection": "core"` item field and read back with `?collection=core`, so the
  fixtures sit where the Tools look for them (D30 owner ruling 5). It waits for the
  required OpenAPI routes first, including the `DELETE` and rename routes the new Tools
  need, so no
  fixture mutation happens before the capability exists. The name the create case
  publishes is deliberately not provisioned. It also confirms the two disposable
  Projects the import cases address are installed and listed, so a missing Project
  fails before the driver runs, and creates the disposable Tag provider the Tag import
  cases use (waiting until it is readable, then importing the source Tags with
  `MergeOverwrite` and verifying the Gateway serves them, retrying the
  freshly-created-provider failure the recorded 8.3.8 run showed). Its
  `tagProvider.convention` block records where the Gateway puts each of the two Tag
  import document shapes (a named root and a provider-root document), read back from a
  provider-root export, so the rule the Tool's verification depends on is live evidence
  in every row rather than an assumption. It also creates the three Tag-provider *config
  resources* ticket #36 is about — the reserved `IgnitionMCPPolicy` provider, an ordinary
  one, and a longer name that only begins with the reserved one — reads each back for its
  Resource signature, and records in `policyProviders.caseFoldedLookup` how the Gateway
  answers a read of the reserved name with its case folded (the measurement behind the
  rule's fail-closed case handling).
- `rest_driver.py` — the live cases, in `--mode gate-on`, `--mode gate-off` and
  `--mode fault` (the D23 injected failures). The two pipeline paths the cancel cases
  address are derived by the workflow from the disposable Projects
  (`project:<Project>:/pipeline:MCP_CI_Notify`), so the Target allowlist entry and the
  paths the driver sends are the same run-unique strings. The fault mode also reads the
  server's own `audit.db` and `state.db` **read-only** for the D18 audit rows and the
  D16 transaction row: those are the server's record of the attempt, and nothing in the
  harness writes to them.
- `fault_proxy.py` — the fault-injecting proxy (#20): a stdlib-only data listener plus a
  control listener (`GET /state`, `POST /fault`, `POST /reset`) that reports every
  request it saw. It runs as a compose service on the host network, and the Docker-free
  rehearsal starts the same class in-process.
- `rehearse_local.py` — Docker-free rehearsal: starts the real server against
  `tests/harness/recorded_gateway.py`, in front of which it also starts `fault_proxy.py`,
  and runs all three driver modes with the same per-Tool Target allowlists the workflow
  configures.

## Rehearsing

```bash
uv run --locked --package ignition-rest-mcp python tests/harness/phase4-live-rest/rehearse_local.py
```

Run this before spending a live CI run. It covers the driver, both deployment
gates and the server wiring; `provision.py` is exercised live only, because it
creates resources through the Gateway's own API.

## Evidence scope

The uploaded artifacts are `provision.json`, `observations.json`, `identity.json`,
`openapi-<version>.json` with its SHA-256, the raw MCP bodies, the server logs and
the Gateway diagnostics for the run.

The captured `/openapi.json` is what feeds the D30 §5 resource-type classification:
the 8.3.9 candidate's inventory under `docs/ignition-8.3.9-openapi/` was derived
from a capture of this harness, and a future version is classified the same way.

No G4 compatibility row is composed here. A G4 row carries the Gateway/Module
tuple, and this harness deliberately deploys no MCP Module; the REST observations
are referenced by the G4 close-out, which composes the rows from the harness that
does deploy the Module.
