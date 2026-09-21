# Phase 4 REST live harness (milestone 4c)

Live proof for the first REST Mutation Tool, `config_resource_update`, on
disposable CI-owned Gateways. Driven by `.github/workflows/phase4-live-rest.yml`
on pull requests in the trusted repository, under the `phase4-live` GitHub
environment.

## What it proves

The driver (`rest_driver.py`) talks MCP over HTTP to a real `ignition-rest`
server and reads the Gateway only through the server's own read Tools, so every
case is something an agent could observe:

| Case | Expectation |
|---|---|
| `inventory-agent-exact` | with the class enabled, the config-scoped credential sees exactly the read inventory plus `config_resource_update` |
| `inventory-reader-exact` | the read-only credential sees exactly the read inventory: D07 discovery still filters the Mutation Tool |
| `allowlisted-update-applies` | an allowlisted change returns structured success |
| `update-moves-the-signature` | the Resource signature moved, so the caller has a fresh Precondition token |
| `observed-state-carries-the-change` | the Observed state re-read shows the intended change |
| `independent-reread-confirms` | a separate `config_resource_get` agrees with the reported signature |
| `stale-signature-is-conflict` | re-sending the pre-change signature is a `conflict` |
| `stale-signature-changes-nothing` | the refused change left the resource alone |
| `refused-resource-type-is-permission-denied` | the Gateway's own API token is `permission_denied` under any Target allowlist |
| `refused-resource-still-usable` | the refused resource is untouched and still readable |
| `non-allowlisted-target-is-denied` | a resource the Target allowlist does not name is refused |
| `non-allowlisted-target-changes-nothing` | …and left alone |
| `inventory-gate-off-exact` | with the class disabled the Mutation Tool is gone from discovery |
| `disabled-class-call-is-refused` | …and calling it is refused, never executed |

Two documented deviations from a single denial code live in the driver: the
Target-allowlist layer answers `operation_disabled` today while D30 §7 maps it to
`permission_denied` (recorded in `docs/development/phase-4.md` Open questions), so
that case accepts either code and records which one it saw, requiring only that
the denial happened.

## Layout

- `docker-compose.yml` — one Gateway, no MCP Module: the REST plane needs none.
- `provision.py` — test-only fixture provisioning through the Gateway's own Native
  REST API: two `ignition/audit-profile` resources, one allowlisted and one the
  allowlist does not name. It waits for the required OpenAPI routes first, so no
  fixture mutation happens before the capability exists.
- `rest_driver.py` — the live cases, in `--mode gate-on` and `--mode gate-off`.
- `rehearse_local.py` — Docker-free rehearsal: starts the real server against
  `tests/harness/recorded_gateway.py` and runs both driver modes.

## Rehearsing

```bash
uv run --locked --package ignition-rest-mcp python tests/harness/phase4-live-rest/rehearse_local.py
```

Run this before spending a live CI run. It covers the driver, both deployment
gates and the server wiring; `provision.py` is exercised live only, because it
creates resources through the Gateway's own API.

## Evidence scope

The uploaded artifacts are `provision.json`, `observations.json`, `identity.json`,
the raw MCP bodies, the server logs and the Gateway diagnostics for the run.

No G4 compatibility row is composed here. A G4 row carries the Gateway/Module
tuple, and this harness deliberately deploys no MCP Module; the REST observations
are referenced by the G4 close-out, which composes the rows from the harness that
does deploy the Module.
