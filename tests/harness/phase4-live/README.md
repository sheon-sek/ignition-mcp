# Phase 4 live Gateway harness — ticket #6 characterization

Ephemeral CI-owned evidence for the two facts Phase 4 milestone 4a depends on
(issue #6, blockers of tickets #7 and #9):

1. **Runtime Target Policy storage** (D30 §1). Where can a deployment-owned JSON
   policy document live on a Gateway, outside the Runtime Bundle, so that
   - a Runtime Tool handler (`onToolCalled.py`) reads it at bounded cost,
   - the Runtime MCP server cannot write it,
   - `setup-native apply` can later write it through Native REST?
2. **Exact-path `system.alarm.queryStatus` bound** (D12 Phase 4 amendment).
   Whether querying one exact Alarm path is bounded before or during execution,
   to the standard the Phase 2 amendment set.

The harness provisions an exact-patch Ignition Gateway (8.3.8 required row,
8.3.9 compatibility candidate), installs the checksum-pinned official MCP Module
and a **CI-only probe project** (`project/`) that hosts two harness Tools:

- `policy_probe` reads every candidate storage location and reports bounded-cost
  measurements (Tag value read, Tag config read, `system.config.getResource`,
  handler-scope write attempt, handler scope inventory, process environment);
- `alarm_probe` builds a disposable Alarm fixture, activates it, and measures
  `system.alarm.queryStatus` matching semantics, cardinality and cost for exact,
  sibling, folder, partial-leaf and wildcard path patterns, plus activate/clear
  cycles without acknowledgement.

Neither probe Tool ships in a product artifact: the probe project is a test
fixture, exactly like `tests/harness/runtime-binding/project`.

## Layout

- `docker-compose.yml`: Gateway-only compose. `GATEWAY_IMAGE` must be an exact
  patch tag, `GATEWAY_MODULES_ENABLED` is the minimal whitelist
  (`com.inductiveautomation.mcp`), and the host port is **8093** so a developer
  workstation's real Gateway on 8088 is never bound or called.
- `project/`: the probe project (D29/D21 profile shape, validated by
  `tooling.native.cli validate`). Deployed by directory copy, like Phase 0.
- `gateway-config/`: the `phase4-policy-probe` MCP server-config resource.
- `policy_document.py`: the deterministic policy document, its Tag provider
  resource body and its Tag import document, plus the policy SHA-256 that both
  the REST read-back and the live handler read are compared against.
- `gateway_rest.py`: bounded stdlib Native REST client.
- `mcp_client.py`: bounded stdlib MCP Streamable-HTTP client for the Module.
- `driver.py`: the characterization driver (stages below).
- `characterization.json`: the structural facts each Gateway version is expected
  to show. Drift is reported and recorded, never hidden.
- `rehearse_local.py`: runs the whole driver against the recorded Gateway fake.
- `../recorded_gateway.py`: replays the recorded Native REST and MCP bodies,
  including this ticket's `phase4/` fixtures.

## Driver stages

```bash
uv run --no-sync python tests/harness/phase4-live/driver.py policy-provision
uv run --no-sync python tests/harness/phase4-live/driver.py policy-read --label before-restart
#   ... restart the Gateway ...
uv run --no-sync python tests/harness/phase4-live/driver.py policy-read --label after-restart
uv run --no-sync python tests/harness/phase4-live/driver.py alarm
uv run --no-sync python tests/harness/phase4-live/driver.py summarize
```

Every stage first verifies the L5 CI marker, the trusted repository, the run id
and the **live Gateway identity**, and fails closed otherwise. Exit codes:
`0` characterized as expected, `3` characterized but drifted from
`characterization.json`, `2` a stage could not be characterized.

`policy-provision` writes the policy the way `setup-native apply` will: create
the dedicated Tag provider through
`POST /data/api/v1/resources/ignition/tag-provider`, import the Tag document
through `POST /data/api/v1/tags/import`, prove the D30 `Abort` collision policy,
prove the idempotent `MergeOverwrite` update path, and read the document back
through `GET /data/api/v1/tags/export`.

`policy-read` verifies that the *running* provider actually serves the policy
Tag, not just that its config holds it: it probes, and while the probe reports
anything other than a Good read of the applied document it re-imports
(idempotently) and probes again under a 240 s deadline. Two recorded 8.3.8
provider-startup failures motivate that loop — a first import rejected while the
provider starts (`Bad 776 … cleanPath is null`), and an accepted import whose
Tags the running provider never serves. Both are recorded under
`tests/fixtures/recorded/gateway-8.3/phase4/`.

## Rehearsal

Run this before spending a live run:

```bash
uv run --no-sync python tests/harness/phase4-live/rehearse_local.py
```

It starts the recorded Gateway fake, writes a synthetic CI marker and runs all
five stages over it, so driver wiring, fact derivation, the drift check and the
verdict are exercised without a Gateway. Rehearsal output is never evidence.

## Live run

`.github/workflows/phase4-live-g4a.yml` runs from trusted pull requests only,
under the `phase4-live` GitHub environment. Like `phase3-live`, that environment
exists **without protection rules** (owner-accepted deviation); the compensating
controls are the trusted-repo guard, no repository or environment secrets in the
job, compose-localhost endpoints only, run-unique Alarm paths, and the
driver-enforced marker plus Gateway-identity check. Evidence is uploaded per
Gateway version as `phase4-g4a-<version>-<run id>`.

## Recorded fixtures

`tests/fixtures/recorded/gateway-8.3/phase4/` holds the bodies this harness
replays and the handler reports the live run produced, with their run ids
recorded in `tests/fixtures/recorded/gateway-8.3/provenance.json`.

## Limitations

- The probe project is test-only and must never be deployed as a product.
- The probe handlers are harness code: they are not the shipped Runtime Target
  Policy reader (ticket #7) and their output carries no contract.
- A passing run characterizes behavior for the exact Gateway build and Module
  build recorded in its evidence row. It supports no compatibility claim and
  never promotes a tuple to `SUPPORTED`.
