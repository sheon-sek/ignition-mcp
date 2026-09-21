# Phase 4 REST live harness (milestone 4c)

Live proof for the REST Mutation Tools — `config_resource_update`,
`config_resource_create`, `config_resource_delete` and `config_resource_rename` — on
disposable CI-owned Gateways. Driven by `.github/workflows/phase4-live-rest.yml`
on pull requests in the trusted repository, under the `phase4-live` GitHub
environment.

## What it proves

The driver (`rest_driver.py`) talks MCP over HTTP to a real `ignition-rest`
server and reads the Gateway only through the server's own read Tools, so every
case is something an agent could observe:

| Case | Expectation |
|---|---|
| `inventory-agent-exact` | with the class enabled, the config-scoped credential sees exactly the read inventory plus the four Mutation Tools |
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
| `inventory-gate-off-exact` | with the class disabled every Mutation Tool is gone from discovery |
| `disabled-class-call-is-refused` | …and calling one is refused, never executed |

The driver asserts exactly one Target-denial code, `permission_denied` (D30 §7,
Tool-scoped; see `docs/development/phase-4.md`).

## Layout

- `docker-compose.yml` — one Gateway, no MCP Module: the REST plane needs none.
- `provision.py` — test-only fixture provisioning through the Gateway's own Native
  REST API: four `ignition/audit-profile` resources (the allowlisted Target, an
  allowlist control, and the two rename sources). It waits for the required OpenAPI
  routes first, including the `DELETE` and rename routes the new Tools need, so no
  fixture mutation happens before the capability exists. The name the create case
  publishes is deliberately not provisioned.
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
