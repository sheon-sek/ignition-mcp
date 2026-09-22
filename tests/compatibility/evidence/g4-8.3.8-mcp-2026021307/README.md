# Phase 4 G4 evidence: 8.3.8 required row

This row is the **G4 close-out** for the 8.3.8 tuple. Phase 4 spans several live
workflows, milestones and heads, so the row cites every run it is composed from
instead of one run's artifact. It was composed by
`python -m tooling.compat g4 --close tests/compatibility/g4/close-8.3.8.json
--artifacts <run downloads>`, which re-reads each cited run's own harness artifact
before it writes anything: a run may only be cited when its document proves it ran
green, matched the row's Gateway/Module identity and deployed the bundle the row
declares.

The cited live runs were:

| Run | Workflow | Head | What it proves |
|---|---|---|---|
| [35710377211](https://github.com/sheon-sek/ignition-mcp/actions/runs/35710377211) | Phase 4 Live Gateway G4a | `ca27b4b` | milestone 4a: the Runtime Target Policy gate (including the 32 KiB read gate), `tag_write`, `alarm_shelve`/`alarm_unshelve`, the operator inventory (16 Tools), the whole-batch Preflight refusal, the reserved provider under `*` and the Runtime audit pair |
| [35710377243](https://github.com/sheon-sek/ignition-mcp/actions/runs/35710377243) | Phase 4 Live Gateway G4b | `ca27b4b` | milestone 4b: the Tag config fingerprint, `tag_update`, `tag_create` and `tag_copy`, with the configurator Server Config at that head |
| [35707687810](https://github.com/sheon-sek/ignition-mcp/actions/runs/35707687810) | Phase 4 Live Gateway G4b | `dfa4d08` | milestone 4b complete (ticket #12): `tag_delete`, `tag_move`, `tag_rename`, the partial-failure batch, the full 19-Tool configurator inventory |
| [35687123699](https://github.com/sheon-sek/ignition-mcp/actions/runs/35687123699) | Phase 4 Live Gateway REST mutation | `98f8f74` | milestone 4c: the exact REST inventories (class enabled/disabled, agent/reader/operator credentials), every REST Mutation Tool's allowlist, refused-type and precondition refusals, and the fault proxy's timeout / ambiguous-outcome / cancellation / partial-write cases |

Each run reported `drift: {}` with every stage `ok` (the three Gateway runs), or
`"passed": true` with every one of its 217 REST cases `ok` (the REST run). The row
also records the pull-request test merge revision each Gateway run evaluated:
`7875bb698022153e0eb06290bc8952024dde0219` for the two `ca27b4b` runs,
`d95a2d2e004abbddca50b6dbc3db87b53c2e40b2` for `dfa4d08` and
`01fb482a7307084f03d12c29bc679df70c7d8532` for the REST run.

The exact deployed identities were:

- Gateway `8.3.8 (b2026071409)`, image `inductiveautomation/ignition:8.3.8`, digest
  `sha256:560d0c069911476ff65aa7fd0fefab0530897e81c2a6cc5ea1410026126ed8ce`.
- MCP Module `1.3.5-SNAPSHOT (b2026021307)`, artifact version
  `1.3.5.2026021307-SNAPSHOT`, SHA-256
  `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`.
- Runtime Bundle `0.7.0`, deployed ZIP SHA-256
  `5cdca831b1a421ecf83a86d2ad889a76f757da9ed2451e8a11b51e8dacd743a6` — the
  deterministic `build` output the milestone 4b runs deployed, independently
  reproduced from head `dfa4d08`. The `ca27b4b` runs deployed the previous release
  `0.6.0` (`d8d6c794…`), which is why the row records the bundle each run deployed
  instead of only the newest one.

## The L5 failure suite

`l5` is the machine-readable form of the runbook's
[L5 case matrix](../../../docs/development/phase-4.md#l5-failure-suite--the-g4-case-matrix).
Every one of D26's eight cases is recorded on both Planes with the class of
evidence behind it (`LIVE`, `SYSTEMATIC`, `FIXTURE` or `NONE`), its source, and —
where it is not live — the limitation:

| D26 case | REST | Runtime |
|---|---|---|
| partial failure | **LIVE** | **LIVE** |
| timeout | **LIVE** (fault proxy) | FIXTURE (D30 ruling) |
| ambiguous outcome | **LIVE** (fault proxy) | FIXTURE (D30 ruling) |
| permission denied | **LIVE** | **LIVE** |
| oversize | **LIVE** | **LIVE** |
| concurrent modification | **LIVE** | **LIVE** |
| audit failure | FIXTURE (unit) | FIXTURE |
| cancellation | **LIVE** (fault proxy) | **NONE** |

`gateResult` is therefore `VERIFIED_WITH_LIMITATION`, and the row keeps what is
*not* proven separate from it in `limitations` and `unsatisfiedAcceptance`. Two
things are not claimed:

- **Runtime cancellation has no evidence at all.** There is no live case and no
  recorded fixture, because the Jython handlers have no cancellation path to
  observe. D30's Consequences say the Runtime timeout, ambiguous-outcome and
  cancellation cases are "proven with recorded fixtures only"; for cancellation
  that sentence is not satisfied.
- **Audit failure (`required` mode) is not live on either Plane.** The REST plane
  proves the D18 fail-closed branch by unit tests that raise `AuditWriteError`,
  and every live Runtime stage installs its policy with `auditMode=best_effort`.
  D26's G4 acceptance text asks for this case live on both Planes.

Both are recorded as owner questions in the Phase 4 runbook rather than papered
over. The D30-sanctioned Runtime fixture-only cases (timeout, ambiguous outcome)
are limitations with a ruling behind them, not gaps.

## Inventories and default state

`inventories` records the exact Tool lists: the four Runtime profiles (13
readonly / 16 operator / 19 configurator / 22 full, each byte-equal to
`contracts/profiles/*.yaml` and to `tooling/contracts/lint.py` — the validator
refuses any drift), what verified each of them, and the REST inventories with the
mutation classes enabled and disabled (16 read Tools with both classes off, 23
with `CONFIG_MUTATION` on, 17 with `CONTROL_MUTATION` on, 24 with both, and 14
with the sensitive-export gate off). `mutationsDisabledByDefault` and
`unsafeAutomaticRetryAbsent` are explicit booleans, not prose: no deployment
enables a Mutation by default, and the fault cases assert one dispatch per call
against the proxy's own counters.

[evidence.json](evidence.json) is copied byte-for-byte from the generator's
output. The close document it was composed from is
[`tests/compatibility/g4/close-8.3.8.json`](../../g4/close-8.3.8.json); the
artifacts themselves (logs, Gateway diagnostics, raw MCP bodies, tokens) stay
outside the repository.

Gate G4 status for this required row is `VERIFIED_WITH_LIMITATION`. Native
response binding is `VERIFIED_WITH_LIMITATION` with the exact-tuple D27 exception
applied; D21 deployment compatibility remains `UNTESTED`; this row makes no
production compatibility certification and no tuple is `SUPPORTED`.
