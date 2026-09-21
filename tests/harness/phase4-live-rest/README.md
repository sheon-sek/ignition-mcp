# Phase 4 REST live harness (milestone 4c)

Live proof for the REST Mutation Tools — `config_resource_update`,
`config_resource_create`, `config_resource_delete`, `config_resource_rename` and
`project_import` — on disposable CI-owned Gateways. Driven by `.github/workflows/phase4-live-rest.yml`
on pull requests in the trusted repository, under the `phase4-live` GitHub
environment.

## What it proves

The driver (`rest_driver.py`) talks MCP over HTTP to a real `ignition-rest`
server, uses the artifact data plane the way an agent would, and reads the Gateway
only through the server's own read Tools, so every case is something an agent could
observe:

| Case | Expectation |
|---|---|
| `inventory-agent-exact` | with the class enabled, the config-scoped credential sees exactly the read inventory plus the five Mutation Tools |
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
| `inventory-gate-off-exact` | with the class disabled every Mutation Tool is gone from discovery |
| `disabled-class-call-is-refused` | …and calling one is refused, never executed |

The candidate archive the import cases upload is the export the Project already had,
with one SQL comment appended to its first named-query payload. That is the edit the
Gateway stores verbatim: its import rewrites `project.json` and drops archive entries it
does not recognise as resources, so an edit there would make C differ from B for reasons
that have nothing to do with this Tool (the same constraint the G3 transaction case hit
live, recorded in `tests/harness/phase3-live/driver.py`). If a round trip ever mismatches,
the driver writes the per-entry diff of the candidate against the Gateway's re-export into
`observations.json` before it fails, so one run is enough to diagnose it.

The driver asserts exactly one Target-denial code, `permission_denied` (D30 §7,
Tool-scoped; see `docs/development/phase-4.md`). That is the code both
`config_resource_*` and `project_import` answer; the frozen Phase 3 machinery the G3
harness drives keeps its recorded `operation_disabled` because the code is declared per
operation.

The deployment this driver runs against enables the config mutation class, the
sensitive exports (the Project cases read their Precondition token from
`project_export`), artifact upload, and the D16 Project writer with an explicit
`IGNITION_MCP_GATEWAY_ID`. Those gates decide the inventory the driver asserts, so the
read inventory here includes the two sensitive-export Tools.

## Layout

- `docker-compose.yml` — one Gateway, no MCP Module: the REST plane needs none.
- `provision.py` — test-only fixture provisioning through the Gateway's own Native
  REST API: four `ignition/audit-profile` resources (the allowlisted Target, an
  allowlist control, and the two rename sources). It waits for the required OpenAPI
  routes first, including the `DELETE` and rename routes the new Tools need, so no
  fixture mutation happens before the capability exists. The name the create case
  publishes is deliberately not provisioned. It also confirms the two disposable
  Projects the import cases address are installed and listed, so a missing Project
  fails before the driver runs.
- `rest_driver.py` — the live cases, in `--mode gate-on` and `--mode gate-off`.
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
