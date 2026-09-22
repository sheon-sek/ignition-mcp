# Phase 6: deployment completion and G6

Status: G6 RECORDED on both tuples, waiting for the owner's merge decision (2026-09-23).

Branch: `feature/phase-6`, created from `main` on the owner's instruction. Each ticket ran on its own
branch and worktree (`p6/t1-install-module`, `p6/t2-runbook`, `p6/t3-live`) and merged into
`feature/phase-6`.

Authority: D20, D26 and its Phase 6 amendment (owner ruling, 2026-09-23), D27, D28, D30,
`CONTEXT.md`, then this runbook. If a decided rule cannot be met, the ticket stops and records the
question here. Nobody reinterprets a decision silently.

The operator-facing procedure lives in the [v1 operations runbook](../operations/runbook.md). This
file is the delivery record, not the procedure.

## Goal

Ship the last setup-native command, prove the deployment path end to end against a real Gateway, and
publish the runbook. Concretely: `setup-native install-module`, one live stage that performs a fresh
Module install and a Bundle upgrade, and `docs/operations/runbook.md`. `verify` already performs the
exact Tool, Resource and Prompt inventory check, and `tooling.native.cli release` already produces the
release evidence, so Phase 6 builds neither again.

## Scope set by the amendment

In v1:

1. `ignition-mcp setup-native install-module` (#54), using the Gateway's own module REST flow. No
   download, no certificate or EULA acceptance without its own flag.
2. A live G6 stage (#56) proving one fresh Module install and one Bundle upgrade, recorded as a
   compatibility evidence row.
3. The operations runbook (#55).

Out of v1, tracked in issue #57: the compatibility semantic diff, the full failure suite beyond the
G4 and G5 cases, scheduled soak and leak checks, and the nightly canary.

Still parked, and a known v1 limitation: `alarm_status` and `alarm_journal` under the D12 Phase 2
bounded-execution amendment, with their handlers held in `packages/ignition-runtime-bundle/deferred/`,
and `alarm_acknowledge` under the D12 Phase 4 amendment by the ticket #9 outcome, for the same
unbounded exact-path `queryStatus` reason. G6 item 1 is assessed against every other Tool in the D26
v1 inventory.

## Tickets

1. P6-1 (#54): `setup-native install-module` plus its focused tests.
2. P6-2 (#55): this runbook, the D26 Phase 6 amendment and the `INDEX.md` update. Docs only.
3. P6-3 (#56): the live Module install and Bundle upgrade stage and its G6 evidence. Blocked by #54.

## Gate rules

- G6 never records `SUPPORTED`. The 8.3.8 tuple closes as `VERIFIED_WITH_LIMITATION` under D27, and
  the 8.3.9 tuple carries `FAILED_NATIVE_BINDING`, the status recorded at G3, G4 and G5.
- The Runtime Bundle stays 0.x. D21 and D23 evidence decide any 1.0.0 claim, not the v1 name.
- "Upgrade path" means a Bundle upgrade as `CONTEXT.md` defines it. A Module upgrade is covered by
  `install-module`'s refusal logic and unit tests, because the repository pins one Module build.
- A live stage extends the existing harness pattern rather than adding a workflow, per the delivery
  rules.

## G6 checklist

Closed by the live run recorded in Results. Every item rests on persisted evidence, and Results names
the basis for each.

- [x] 1. The D26 v1 required surface is implemented, except the three parked Alarm Tools
      `alarm_status`, `alarm_journal` and `alarm_acknowledge`, which are recorded as a v1 limitation.
- [x] 2. Every public contract, profile and hard budget passes L0 and L2.
- [x] 3. The `ignition-rest` required capabilities pass L3 on the declared tuples.
- [x] 4. The Runtime Bundle passes a real MCP `initialize`, then lists, reads, gets and calls.
- [x] 5. Tool output binding is resolved from `NATIVE_BINDING_PENDING` to live-verified, recorded as
      `VERIFIED_WITH_LIMITATION` on 8.3.8 under D27 and `FAILED_NATIVE_BINDING` on 8.3.9.
- [x] 6. Mutations pass state verification and the D08 and D18 safety and audit paths.
- [x] 7. Perspective passes the D16 and D17 recovery and concurrency tests.
- [x] 8. setup-native passes fresh install, Bundle upgrade and exact inventory verification on a live
      Gateway. This is Phase 6's own work: #54 and #56.
- [x] 9. The release artifact is reproducible, and it publishes the manifest and the SHA-256. Check
      with two deterministic builds and a byte comparison:

      ```bash
      uv run --no-sync python -m tooling.native.cli release \
        --project-dir packages/ignition-runtime-bundle/project \
        --out-dir dist/release-a --source-revision "$(git rev-parse HEAD)" \
        --evidence-dir tests/compatibility/evidence
      uv run --no-sync python -m tooling.native.cli release \
        --project-dir packages/ignition-runtime-bundle/project \
        --out-dir dist/release-b --source-revision "$(git rev-parse HEAD)" \
        --evidence-dir tests/compatibility/evidence
      cmp dist/release-a/ignition-runtime-bundle-0.7.0.zip dist/release-b/ignition-runtime-bundle-0.7.0.zip
      uv run --no-sync python -m tooling.compat validate --evidence-dir tests/compatibility/evidence
      ```

- [x] 10. Every `SUPPORTED` tuple comes from machine-readable D23 evidence. In v1 the set is empty, so
      the correct result is that no row claims it.

## Current state at Phase 6 open

- G0 through G5 are closed and merged. Evidence rows for each Gate are committed under
  `tests/compatibility/evidence/`, including `g5-8.3.8-mcp-2026021307` and `g5-8.3.9-mcp-2026021307`.
- `packages/ignition-runtime-bundle/BUNDLE_VERSION` is `0.7.0`, and `RESOURCE_SCHEMA_VERSION` is `1`.
- The pinned MCP Module is `com.inductiveautomation.mcp` `1.3.5.2026021307-SNAPSHOT`, build
  `2026021307`, checksum-pinned in `tests/fixtures/modules/`.
- `setup-native` ships `doctor`, `plan`, `apply` and `verify`. `install-module` is refused with exit 2
  until #54 merges. These bullets describe the state at Phase 6 open; Results records the state at
  close.
- Deferred issues stay deferred and do not block G6: #39, #41 and #51.

## Delivery rules

The CLAUDE.md delivery speed rules apply. Agents never push, run only the tests for their own files
plus `ruff`, `mypy` strict and `tooling.contracts.lint`, and stay in their own worktree. The bar is a
working command with correct input and output plus the safety rules: Target allowlist, reserved
provider, `_types_`, Precondition token, no secrets in output. Secondary rigor goes to a tracked
issue. The coordinator pushes once per integration head and chooses which workflows run.

## Results

G6 closed on run [35772449703](https://github.com/sheon-sek/ignition-mcp/actions/runs/35772449703) of
`Phase 4 Live Gateway apply`, conclusion success, on both matrix rows. Both evidence rows record
source revision `c65d233fd3d17eb67fd0fa0732939ca6f9f1ef75`, which is what the stage saw and reported.
The workflow run's trigger commit is `4770b3f`, the merge of the compose fix below, and `c65d233` is
not an object in this repository. The rows keep the reported value.

| Row | `gateResult` | `status` | `nativeResponseBinding` | `compatibilityStatus` |
| --- | --- | --- | --- | --- |
| `g6-8.3.8-mcp-2026021307` | `VERIFIED` | `VERIFIED` | `VERIFIED_WITH_LIMITATION` | `UNTESTED` |
| `g6-8.3.9-mcp-2026021307` | `VERIFIED` | `FAILED_NATIVE_BINDING` | `UNVERIFIED_LIMITATION` | `UNTESTED` |

The rows are committed at `tests/compatibility/evidence/g6-8.3.8-mcp-2026021307/evidence.json` and
`tests/compatibility/evidence/g6-8.3.9-mcp-2026021307/evidence.json`.

The 8.3.8 row applies the D27 exception (`d27ExceptionApplied: true`, `outputSchemaPublished: false`).
The Module publishes `structuredContent` and `isError` but no Tool `outputSchema`, so its binding is
`VERIFIED_WITH_LIMITATION`. The 8.3.9 row carries the same `FAILED_NATIVE_BINDING` status recorded at
G3, G4 and G5. Neither row claims `SUPPORTED`, and both report `compatibilityStatus: UNTESTED`.

Both rows share one bundle and Module identity: bundle `0.7.0` with SHA-256
`bf65b6e64aac9091fa80aaa1cf4251bb85cef4923d7929a565c868141559486c`, MCP Module build `2026021307` with
SHA-256 `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`, Gateway build `2026071409`
from `inductiveautomation/ignition:8.3.8` and Gateway build `2026082511` from
`inductiveautomation/ignition:8.3.9`, and five G6 cases with verdict `LIVE`, corroborated from
`setup-native-apply.json`.

What the stage proved on both Gateways:

- `install-module` installed the pinned `.modl` through the Gateway's module routes under
  `--accept-certificate --accept-eula --acknowledge-upgrade --restart`, and the Gateway served that
  Module build when the wait returned;
- a second `install-module` answered `NO CHANGE` and uploaded nothing;
- a fresh `apply` wrote the five intentions, the Security Level, the Runtime API token, the bundle
  Project, the Server Config and the Runtime Target Policy, and `verify` went green with the exact
  Tool, Resource and Prompt inventory checks;
- a second `plan` reported `NO CHANGE` for all five, the second `apply` wrote nothing, and the
  credential file kept its bytes;
- the Bundle upgrade from `0.6.0` to `0.7.0` planned as `UPDATE bundle-project`, applied under
  `--acknowledge-upgrade`, wrote only `bundle-project`, and verified green.

What each G6 item rests on:

1. The G2, G3, G4 and G5 rows cover the two planes' required surface, and the three parked Alarm Tools
   are recorded as a v1 limitation in the amendment and in the operations runbook.
2. `tooling.contracts.lint`, `tooling/contracts/tests`, and the deterministic double build with `cmp`
   in CI.
3. The G3, G4 and G5 live rows against Native REST on both tuples.
4. The `verify` sequence on both tuples: `initialize`, the three exact inventories, the Text Resource
   and Prompt smokes, and the `bundle_info` call.
5. `nativeResponseBinding: VERIFIED_WITH_LIMITATION` on 8.3.8 under D27, and `UNVERIFIED_LIMITATION`
   with `FAILED_NATIVE_BINDING` on 8.3.9. The same fact reaches the release as
   `nativeResponseBindingStatus` in the manifest, derived from the handlers: `NATIVE_BINDING_PENDING`
   while any handler still carries the marker, and `VERIFIED_WITH_LIMITATION` for the released 0.7.0
   bundle.
6. The G4 rows and their D08 and D18 cases, plus the G6 apply writes, each confirmed by a read-back,
   with the stage's rule that a credential may not reach a command output or the evidence.
7. The G5 rows, `g5-8.3.8-mcp-2026021307` and `g5-8.3.9-mcp-2026021307`.
8. The five `LIVE` G6 cases. This is the item Phase 6 opened for.
9. The double build and byte comparison in the checklist command above, and the fact that both rows
   carry the deployed archive's SHA-256 as `bundleSha256`.
10. `python -m tooling.compat validate --evidence-dir tests/compatibility/evidence` passes at 12 rows
    with `no SUPPORTED claim`, and both G6 rows report `UNTESTED`.

Two failures were fixed on the way:

- Five review blockers on #54, all inside `install-module`, fixed at `0d9fdd8`. The `.modl` is read
  once with hashing in the same pass, so a file that grows under a stat-then-read pair cannot push more
  than `MAX_MODULE_BYTES` into memory. `GatewayRest._request` has one bounded read path, because an
  error status and an allowed 404 were consuming their bodies with the unbounded `response.aread()`.
  The Module id is pinned: `module.xml` must declare `com.inductiveautomation.mcp`, or the run is a
  usage error before a Gateway client exists. The classified outcome and the pre-install identity ride
  into the restart branch, so an acknowledged upgrade with `--restart` still reports `UPGRADE` and
  keeps `installedBefore`. And `modules/healthy` is read as the page it is, following
  `metadata.total`, so an inventory that cannot be completed is a refusal before upload rather than a
  missing Module.
- Run [35771274457](https://github.com/sheon-sek/ignition-mcp/actions/runs/35771274457) failed both
  matrix rows at `Start a fresh Gateway`. The workflow set `COMPOSE_FILE` to the base and override
  pair and then passed that same value as one `-f` argument, and docker compose accepts a file list
  only through the environment variable. Fixed at `4391903`, which drops `-f "$COMPOSE_FILE"` from
  every compose call in `phase4-live-apply.yml` and lets the stage's reload helper split a colon list
  into one `-f` per file. The next run was green.

## Deferred

#39, #41 and #51 stayed deferred and did not block G6. Issue #57 holds the four items the Phase 6
ruling moved out of v1.

## Open questions

None. #54 left two facts for the live run, and run 35772449703 settled both: the upload body is sent
as `application/octet-stream` (`MODULE_UPLOAD_CONTENT_TYPE` in `cli/setup_native/writer.py`), and the
Gateway accepted upload, then certificate and EULA acceptance, then install, on both tuples.
