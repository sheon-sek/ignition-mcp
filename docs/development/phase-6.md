# Phase 6: deployment completion and G6

Status: Phase 6 open, G6 checklist open (2026-09-23).

Branch: `feature/phase-6`, created from `main` on the owner's instruction. Each ticket runs on its own
branch and worktree (`p6/t1-install-module`, `p6/t2-runbook`, and the T3 branch when it opens) and
merges into `feature/phase-6`.

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
bounded-execution amendment. G6 item 1 is assessed against every other Tool in the D26 v1 inventory.

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

Open. Close it only against a real Gateway run with persisted evidence.

- [ ] 1. The D26 v1 required surface is implemented, except `alarm_status` and `alarm_journal`, which
      stay parked under D12 and are recorded as a v1 limitation.
- [ ] 2. Every public contract, profile and hard budget passes L0 and L2.
- [ ] 3. The `ignition-rest` required capabilities pass L3 on the declared tuples.
- [ ] 4. The Runtime Bundle passes a real MCP `initialize`, then lists, reads, gets and calls.
- [ ] 5. Tool output binding is resolved from `NATIVE_BINDING_PENDING` to live-verified, recorded as
      `VERIFIED_WITH_LIMITATION` on 8.3.8 under D27 and `FAILED_NATIVE_BINDING` on 8.3.9.
- [ ] 6. Mutations pass state verification and the D08 and D18 safety and audit paths.
- [ ] 7. Perspective passes the D16 and D17 recovery and concurrency tests.
- [ ] 8. setup-native passes fresh install, Bundle upgrade and exact inventory verification on a live
      Gateway. This is Phase 6's own work: #54 and #56.
- [ ] 9. The release artifact is reproducible, and it publishes the manifest and the SHA-256. Check
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

- [ ] 10. Every `SUPPORTED` tuple comes from machine-readable D23 evidence. In v1 the set is empty, so
      the correct result is that no row claims it.

## Current state at Phase 6 open

- G0 through G5 are closed and merged. Evidence rows for each Gate are committed under
  `tests/compatibility/evidence/`, including `g5-8.3.8-mcp-2026021307` and `g5-8.3.9-mcp-2026021307`.
- `packages/ignition-runtime-bundle/BUNDLE_VERSION` is `0.7.0`, and `RESOURCE_SCHEMA_VERSION` is `1`.
- The pinned MCP Module is `com.inductiveautomation.mcp` `1.3.5.2026021307-SNAPSHOT`, build
  `2026021307`, checksum-pinned in `tests/fixtures/modules/`.
- `setup-native` ships `doctor`, `plan`, `apply` and `verify`. `install-module` is refused with exit 2
  until #54 merges.
- Deferred issues stay deferred and do not block G6: #39, #41 and #51.

## Delivery rules

The CLAUDE.md delivery speed rules apply. Agents never push, run only the tests for their own files
plus `ruff`, `mypy` strict and `tooling.contracts.lint`, and stay in their own worktree. The bar is a
working command with correct input and output plus the safety rules: Target allowlist, reserved
provider, `_types_`, Precondition token, no secrets in output. Secondary rigor goes to a tracked
issue. The coordinator pushes once per integration head and chooses which workflows run.

## Results

To be recorded when G6 closes: the run link, the branch head, the two G6 evidence rows, and the
VERIFIED or VERIFIED_WITH_LIMITATION verdict for each tuple.

## Open questions

1. #54 records two facts that only a live run settles: the exact upload content type, and whether the
   Gateway enforces the upload, accept, install order. The runbook documents the sequence the command
   performs, and the live stage in #56 confirms or corrects it.
2. G6 item 1 says the D26 v1 surface is implemented. D12's alarm inventory also lists
   `alarm_acknowledge`, which has no contract in `contracts/tools/runtime/` and no handler under
   `project/com.inductiveautomation.mcp/tools/`. The D12 amendment parks it unless a bounded exact-path
   `queryStatus` is proven. The owner needs to record whether item 1 is assessed with or without
   `alarm_acknowledge` before G6 can close.
