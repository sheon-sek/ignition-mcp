# Phase 4 REST live harness (milestone 4c)

Live proof for the REST Mutation Tools — `config_resource_update`,
`config_resource_create`, `config_resource_delete`, `config_resource_rename`,
`project_import` and `tag_config_import` (CONFIG) and `alarm_pipeline_cancel`
(CONTROL) — on disposable CI-owned Gateways. Driven by `.github/workflows/phase4-live-rest.yml`
on pull requests in the trusted repository, under the `phase4-live` GitHub
environment.

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
| `independent-reread-confirms` | a separate `config_resource_get` agrees with the reported signature |
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
| `project-import-reports-the-dispatch` | the committed transaction reports that it dispatched |
| `project-export-fingerprint-is-independent` | the fingerprint the server reports for a fresh export equals this harness's own `pcf1` computation |
| `project-import-content-lands` | that independent fingerprint equals the candidate the import reported |
| `project-import-marker-is-present` | the entry the candidate carried is in the Project the Gateway now serves |
| `project-import-of-the-current-content-is-no-change` | re-importing that content is D16's `NO_CHANGE` |
| `project-import-no-change-dispatches-nothing` | …and nothing was dispatched |
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

## Layout

- `docker-compose.yml` — one Gateway, no MCP Module: the REST plane needs none.
- `provision.py` — test-only fixture provisioning through the Gateway's own Native
  REST API: four `ignition/audit-profile` resources (the allowlisted Target, an
  allowlist control, and the two rename sources). It waits for the required OpenAPI
  routes first, including the `DELETE` and rename routes the new Tools need, so no
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
  in every row rather than an assumption.
- `rest_driver.py` — the live cases, in `--mode gate-on` and `--mode gate-off`. The two
  pipeline paths the cancel cases address are derived by the workflow from the disposable
  Projects (`project:<Project>:/pipeline:MCP_CI_Notify`), so the Target allowlist entry and
  the paths the driver sends are the same run-unique strings.
- `rehearse_local.py` — Docker-free rehearsal: starts the real server against
  `tests/harness/recorded_gateway.py` and runs both driver modes with the same
  per-Tool Target allowlists the workflow configures.

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
