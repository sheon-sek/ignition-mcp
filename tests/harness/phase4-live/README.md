# Phase 4 live Gateway harness — tickets #6–#12 and #21

Ephemeral CI-owned evidence for the facts Phase 4 milestone 4a depends on:

1. **Runtime Target Policy storage** (D30 §1, issue #6). Where can a
   deployment-owned JSON policy document live on a Gateway, outside the Runtime
   Bundle, so that
   - a Runtime Tool handler (`onToolCalled.py`) reads it at bounded cost,
   - the Runtime MCP server cannot write it,
   - `setup-native apply` can later write it through Native REST?
2. **Exact-path `system.alarm.queryStatus` bound** (D12 Phase 4 amendment,
   issue #6). Whether querying one exact Alarm path is bounded before or during
   execution, to the standard the Phase 2 amendment set.
3. **`tag_write` end to end** (issue #7). The shipped Runtime Bundle, deployed
   with the `operator` profile, is driven through the Module: an allowlisted
   write, the Target allowlist refusal at a segment boundary, a Preflight that
   executes nothing, the reserved policy provider under an explicit `*`, the
   missing-policy fail-closed case, the Runtime audit rows, and the exact
   operator inventory.
4. **`alarm_shelve` and `alarm_unshelve` end to end** (issue #8). The same
   shipped bundle: an allowlisted shelve of the run's exact Alarm path confirmed
   through `alarm_shelved_list`, the deployment shelve cap below the D12 24 h
   hard maximum, the wildcard and segment-boundary refusals, a Preflight that
   shelves nothing, the missing-policy refusals, and the matching unshelve.
5. **The Tag config fingerprint and `tag_update` end to end** (issue #10). The
   shipped bundle, deployed with the `configurator` profile: the fingerprint a
   caller reads is the token the handler compares and is the documented rule over
   the published configuration, an allowlisted merge-update applies to a Tag and
   to a Folder, a stale token is `conflict` and changes nothing, a missing target
   is `not_found` and really is absent from the provider's own export, the
   segment-boundary and UDT-definition refusals hold, the reserved policy
   provider is refused under an explicit `*`, the CONTROL profile does not serve
   the CONFIG Tool, and the Runtime audit rows carry the Service identity.

The harness provisions an exact-patch Ignition Gateway (8.3.8 required row,
8.3.9 compatibility candidate), installs the checksum-pinned official MCP Module
plus:

- a **CI-only probe project** (`project/`) hosting three harness Tools:
  - `policy_probe` reads every candidate storage location and reports bounded-cost
    measurements (Tag value read, Tag config read, `system.config.getResource`,
    handler-scope write attempt, handler scope inventory, process environment);
  - `alarm_probe` builds a disposable Alarm fixture, activates it, and measures
    `system.alarm.queryStatus` matching semantics, cardinality and cost for exact,
    sibling, folder, partial-leaf and wildcard path patterns, plus activate/clear
    cycles without acknowledgement;
  - `tag_fixture_probe` creates the disposable Tag targets the `tag_write` cases
    write to (the allowlisted root, a nested descendant, and a sibling root whose
    path only shares a string prefix);
- the **shipped Runtime Bundle** (`packages/ignition-runtime-bundle/project`,
  built deterministically and deployed as the `ignition_runtime` project) under a
  Server Config per profile it verifies — `phase4-operator` for the CONTROL Tools
  (tickets #7/#8) and `phase4-configurator` for the CONFIG Tool (ticket #10) — so
  the Mutation cases run the product handler, not a probe copy, and each
  deployment's `tools/list` equals its profile contract exactly.

None of the probe Tools ship in a product artifact: the probe project is a test
fixture, exactly like `tests/harness/runtime-binding/project`.

## Layout

- `docker-compose.yml`: Gateway-only compose. `GATEWAY_IMAGE` must be an exact
  patch tag, `GATEWAY_MODULES_ENABLED` is the minimal whitelist
  (`com.inductiveautomation.mcp`), and the host port is **8093** so a developer
  workstation's real Gateway on 8088 is never bound or called.
- `project/`: the probe project (D29/D21 profile shape, validated by
  `tooling.native.cli validate`). Deployed by directory copy, like Phase 0.
- `gateway-config/`: the `phase4-policy-probe` (probe project), `phase4-operator`
  and `phase4-configurator` (shipped bundle) MCP server-config resources.
- `policy_document.py`: the deterministic policy document, its companion length
  Tag, its Tag provider resource body and its Tag import document, plus the
  policy SHA-256 that both the REST read-back and the live handler read are
  compared against, and the harness-only oversize pair that proves the gate skips
  an over-cap value (in the same provider, so it goes through the same admission
  path; a deployment's provider holds only the policy Tag and its length Tag).
  It also holds the ticket #7 fixtures: the audit profile, the Tag targets, the
  `tag_write` policy (shape-identical to the ticket #6 document plus its
  `auditProfile`), and the explicit `*` variant used for the reserved-provider
  refusal proof. The ticket #10 documents are the same shape with the
  `tag_update` key: the Tag allowlist, the same allowlist plus an explicit
  `_types_` entry for the UDT-definition case, and the explicit `*` variant; the
  Tag CONFIG Mutation also names the properties the positive case merges
  (`documentation` and `engUnits`) and the Folder target.
- `gateway_rest.py`: bounded stdlib Native REST client.
- `mcp_client.py`: bounded stdlib MCP Streamable-HTTP client for the Module.
- `driver.py`: the characterization driver (stages below).
- `wait_for_gateway.py`: the single readiness waiter both readiness points use
  (Native REST `/data/api/v1/gateway-info` plus an MCP `initialize` for every
  `--mcp-url`). It applies the same exact-origin and MCP-path check as the driver
  before either request (exit 3 on a refused origin, 2 when an origin never
  answers).
- `characterization.json` and `characterization-config.json`: the structural facts
  each Gateway version is expected to show, one file per milestone (4a and 4b).
  Drift is reported and recorded, never hidden.
- `rehearse_local.py`: runs one milestone's driver stages against the recorded
  Gateway fake (`--stages 4a` by default, `--stages 4b` for ticket #10).
- `apply_stage.py` (tickets #21 and #22, milestone 4d): drives the shipped
  `ignition-mcp setup-native` CLI — `plan` -> `apply` -> `verify`, then a second
  `plan`/`apply` that must be a `NO CHANGE` run that writes nothing — against the
  Gateway row this workflow provisioned. It uses run-unique names for the Project
  and the Server Config (so the first run really creates both), the deterministic
  release the workflow built, the ticket #6 policy document and the harness's own
  permissions tree. Ticket #22's opt-in flags are always passed, so the same run
  also creates the dedicated Runtime Security Level for its profile and a Runtime
  API token granted exactly that level; both are read back over Native REST with the
  stage's own admin token, the token's stored hash must equal the secret the CLI
  wrote, the credential file must be `0600`, and the secret is judged absent from
  every command output and from the evidence (which is redacted rather than uploaded
  if it ever appears). A verify that fails right after the write is re-run, bounded
  and read-only, because a Gateway whose Module has not yet picked the new Project
  or Server Config up answers the endpoint before it can serve it; a *write* is
  never re-run. `--expected-origin` refuses any Gateway but the compose one.
- `rehearse_apply.py`: builds the release into a temporary directory, starts the
  recorded Gateway fake and runs `apply_stage.py` against it, so the stage is
  rehearsed — writes, read-backs, idempotency — before a live Gateway is spent.
- `../recorded_gateway.py`: replays the recorded Native REST and MCP bodies,
  including this ticket's `phase4/` fixtures.

## Driver stages

```bash
uv run --no-sync python tests/harness/phase4-live/driver.py tag-write-no-policy
uv run --no-sync python tests/harness/phase4-live/driver.py policy-provision
uv run --no-sync python tests/harness/phase4-live/driver.py policy-read --label before-restart
#   ... restart the Gateway ...
uv run --no-sync python tests/harness/phase4-live/driver.py policy-read --label after-restart
uv run --no-sync python tests/harness/phase4-live/driver.py alarm
uv run --no-sync python tests/harness/phase4-live/driver.py tag-write-setup
uv run --no-sync python tests/harness/phase4-live/driver.py tag-write
uv run --no-sync python tests/harness/phase4-live/driver.py alarm-no-policy
uv run --no-sync python tests/harness/phase4-live/driver.py alarm-shelve
uv run --no-sync python tests/harness/phase4-live/driver.py summarize
```

Milestone 4b (ticket #10) is its own workflow and its own stage set; pass
`--stages 4b` to `summarize` (or set `P4_MILESTONE=4b`) so it merges the 4b
records, checks this milestone's expectations and writes the 4b verdict shape:

```bash
uv run --no-sync python tests/harness/phase4-live/driver.py tag-update-no-policy
uv run --no-sync python tests/harness/phase4-live/driver.py policy-provision
uv run --no-sync python tests/harness/phase4-live/driver.py tag-update-setup
uv run --no-sync python tests/harness/phase4-live/driver.py tag-update
uv run --no-sync python tests/harness/phase4-live/driver.py summarize --stages 4b
```

Every stage first verifies — **before the first request** — that every URL is
exactly the disposable origin `http://127.0.0.1:8093` with one of the hosted MCP
paths, that the alarm root is run-unique, and that the CI marker names this
marker, this environment, this trusted repository, this run id, this Gateway
build, this `gatewayId` (whose label is the milestone's own, `g4a` or `g4b`),
this policy provider, this alarm root, this Runtime project and this audit
profile. Redirects are refused by the REST and MCP
clients, so a rewritten endpoint cannot bounce the identity check elsewhere, and
`--base-url http://127.0.0.1:8088` (a real workstation Gateway) is rejected with
no network call at all. Exit codes: `0` characterized as expected, `3`
characterized but drifted from `characterization.json`, `2` a stage could not be
characterized. The workflow fails on `3` now that the expectations are frozen.

`tag-write-no-policy` runs before any provisioning and requires the shipped
handler to refuse with `operation_disabled` on a Gateway whose policy provider
does not exist yet.

`policy-provision` writes the policy the way `setup-native apply` will: create
the dedicated Tag provider through
`POST /data/api/v1/resources/ignition/tag-provider`, import the Tag document
through `POST /data/api/v1/tags/import`, prove the D30 `Abort` collision policy,
prove the idempotent `MergeOverwrite` update path, and read the document back
through `GET /data/api/v1/tags/export`.

`policy-read` verifies that the *running* provider actually serves the policy
Tag through the gate the storage recommendation depends on — the companion
length Tag, the enforced `IgnitionMcpPolicyMaxBytes` cap, and the length-equals-
value check —, not just that its config holds it: it probes, and while the probe reports
anything other than a Good read of the applied document it re-imports
(idempotently) and probes again under a 240 s deadline. Two recorded 8.3.8
provider-startup failures motivate that loop — a first import rejected while the
provider starts (`Bad 776 … cleanPath is null`), and an accepted import whose
Tags the running provider never serves. Both are recorded under
`tests/fixtures/recorded/gateway-8.3/phase4/`.

`tag-write-setup` is test-only provisioning: it creates the disposable Tag
targets through `tag_fixture_probe`, creates the `MCP_CI_AUDIT` local audit
profile through Native REST, and installs the `tag_write` policy
(`MergeOverwrite`) whose `auditProfile` the D18 `best_effort` mode then names.
Like `setup-native apply`, it treats an accepted import as unproven until a
Tool handler reads the served document back, and it retries under a deadline.

`tag-write` drives the shipped Tool: the 14-Tool operator inventory, an
allowlisted batch (four items, one of them a missing path so a Bad Native
outcome is exercised), the audit rows for that call's correlation ID read back
through `GET /data/api/v1/audit/log/<profile>`, the segment-boundary refusal (a
sibling root whose path only shares a string prefix), a Preflight that rejects a
whole batch without executing its first item, and — after installing the
explicit `*` policy — the reserved-provider refusal, with the target Tag and the
policy document both re-read to prove nothing was written.

`tag-update-setup` is the ticket #10 provisioning: the same disposable Tag
targets, the audit profile, and the `tag_update` policy installed only when a
handler read-back serves it.

`tag-update` drives the shipped Tool: the configurator inventory against
`contracts/profiles/configurator.yaml` and the operator inventory against its own
contract (CONTROL must not serve the CONFIG Tool), the fingerprint recomputed
from the published configuration with the repository's own Python copy of the
rule, an allowlisted merge-update on a Tag and on a Folder each confirmed by an
independent `tag_get_config` re-read, the stale token refused with `conflict` and
changing nothing, a missing target refused with `not_found` and proven absent
through `GET /data/api/v1/tags/export`, the segment-boundary refusal, the D30 §6
`_types_` refusals and the explicit-entry pass-through, the reserved-provider
refusal under an explicit `*`, a whole-batch Preflight refusal, and the Runtime
audit rows for the call's correlation ID.

## Apply stage (ticket #21)

```bash
uv run --no-sync python tests/harness/phase4-live/rehearse_apply.py
uv run --no-sync python tests/harness/phase4-live/apply_stage.py \
  --base-url http://127.0.0.1:8093 --expected-origin 127.0.0.1:8093 \
  --api-token "$CI_API_TOKEN" \
  --bundle-manifest dist/release/ignition-runtime-bundle-<version>.manifest.json \
  --bundle-zip dist/release/ignition-runtime-bundle-<version>.zip \
  --evidence-dir artifacts/apply-<version> --source-revision "$(git rev-parse HEAD)" \
  --project ignition_runtime_apply_<run> --server-config phase4-apply-runtime
```

The stage is the product under test, not a copy of it: the Workflow
`.github/workflows/phase4-live-apply.yml` (milestone 4d, its own Gateway row and
its own `phase4-live` environment reuse) runs it after the release build. It
never shares a Gateway with the 4a/4b Mutation rows, because it writes deployment
state — a Project, a Server Config and the reserved policy provider.

## Rehearsal

Run this before spending a live run:

```bash
uv run --no-sync python tests/harness/phase4-live/rehearse_local.py            # 4a
uv run --no-sync python tests/harness/phase4-live/rehearse_local.py --stages 4b
```

It starts the recorded Gateway fake, writes a synthetic CI marker and runs one
milestone's stages over it, so driver wiring, fact derivation, the drift check and
the verdict are exercised without a Gateway. Rehearsal output is never evidence.
The fake serves the Tag configuration the live run recorded and answers the node a
Gateway synthesizes for a path that is not there, so the missing-target case
models the Gateway rather than the rehearsal's convenience.

## Live run

`.github/workflows/phase4-live-g4a.yml` (milestone 4a) and
`.github/workflows/phase4-live-g4b.yml` (milestone 4b) run from trusted pull
requests only, under the `phase4-live` GitHub environment. Like `phase3-live`, that environment
exists **without protection rules** (owner-accepted deviation); the compensating
controls are the trusted-repo guard, no repository or environment secrets in the
job, compose-localhost endpoints only, run-unique Alarm paths, and the
driver-enforced marker plus Gateway-identity check. Evidence is uploaded per
Gateway version as `phase4-g4a-<version>-<run id>` and
`phase4-g4b-<version>-<run id>`.

## Recorded fixtures

`characterization.json`'s `runtimeWritePrevention` verdict sentence is generated
from `policy_document.RESERVED_PROVIDER_REFUSALS`, so the sentence a later
implementer reads in `evidence.json` cannot drift from the documented rule.

`tests/fixtures/recorded/gateway-8.3/phase4/` holds the bodies this harness
replays and the handler reports the live run produced, with their run ids
recorded in `tests/fixtures/recorded/gateway-8.3/provenance.json`.

The Alarm fixture is run-unique on a live Gateway, so the recorded Alarm bodies
templated the two run-scoped values: `__ALARM_ROOT__` for the Alarm root (also
substituted into the recorded `alarm_probe` report, which names its own run's
root) and `__CORRELATION__` for the Runtime correlation ID the audit rows carry.
Everything else in those bodies is the live payload. The ticket #10 refusal bodies
templated the same correlation ID plus the run's own Tag paths
(`__TARGET__`, `__TEXT_TARGET__`, `__FOLDER__`, `__SIBLING__`, `__MISSING__`,
`__UDT__`) and, for the stale fingerprint, the expected and observed tokens.

Two ticket #10 bodies deserve their own note:
`phase4/tag-get-config-missing-template.json` is the node a live Gateway answers
for a configuration read of a path that is not there (`tagType: Unknown`, a
native `BasicTagPath`, and the same fingerprint on 8.3.8 and 8.3.9), which is why
`tag_update` checks existence with `system.tag.exists`; and
`phase4/tag-config.json` is the configuration the fake serves — the live read of
the fixture Tag, plus the Folder node with the property the live merge added
removed again, so it is that target's pre-update state.

## Safety

- One origin, checked locally before any request: `http://127.0.0.1:8093`, plus
  the phase4 MCP path. The rehearsal fake binds the same port so the rehearsal
  exercises the real guard.
- Redirects are refused by both clients (an `HTTPRedirectHandler` that raises),
  so the guard cannot be moved to another host.
- The CI marker must name the marker string, environment, trusted repository,
  run id, Gateway version/build, `gatewayId`, `policyProvider` and `alarmRoot`;
  the live Gateway identity is then checked against the same marker.
- Run-unique Alarm paths (`mcp_p4_<run id>`), and the policy provider name is a
  fixed repo constant so the characterization describes the recommended layout.

## Limitations

- The probe project is test-only and must never be deployed as a product.
- The probe handlers are harness code: they are not the shipped Runtime Target
  Policy reader (ticket #7) and their output carries no contract.
- A passing run characterizes behavior for the exact Gateway build and Module
  build recorded in its evidence row. It supports no compatibility claim and
  never promotes a tuple to `SUPPORTED`.
