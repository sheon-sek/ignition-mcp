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
| [35713927291](https://github.com/sheon-sek/ignition-mcp/actions/runs/35713927291) | Phase 4 Live Gateway G4b | `0801e51` | milestone 4b on the **final integration head** (`4238653` + #22 `e34884a` + the CI-only change that stops the live workflows re-running the unit suite): the same nine CONFIG/CONTROL cases, the 19-Tool configurator inventory and the operator/configurator split, with the head's own bundle |
| [35715927983](https://github.com/sheon-sek/ignition-mcp/actions/runs/35715927983) | Phase 4 Live Gateway apply | `4b5b0df` | milestone 4d: the D20 acceptance sequence on a disposable Gateway — `plan` → `apply` (bundle Project, the profile's Server Config, the Runtime Target Policy) → `verify`, with the **applied endpoint serving the profile's inventory** on both rows (`ok: true`, 0 refreshes and 0 verify retries needed) |
| [35687123699](https://github.com/sheon-sek/ignition-mcp/actions/runs/35687123699) | Phase 4 Live Gateway REST mutation | `98f8f74` | milestone 4c: the exact REST inventories (class enabled/disabled, agent/reader/operator credentials), every REST Mutation Tool's allowlist, refused-type and precondition refusals, and the fault proxy's timeout / ambiguous-outcome / cancellation / partial-write cases |

Each run reported `drift: {}` with every stage `ok` (the four Gateway runs), every REST
case `ok` (the REST run), or a green apply report (the apply run). The row also records the
pull-request test merge revision each run evaluated: `7875bb698022153e0eb06290bc8952024dde0219`
for the two `ca27b4b` runs, `d95a2d2e004abbddca50b6dbc3db87b53c2e40b2` for `dfa4d08`,
`e8e4d169abf73412a4fdc9b684be80216cf7c5e5` for `0801e51`,
`2bc81f682e7284cc64aba20b5775b1a439703add` for `4b5b0df` and
`01fb482a7307084f03d12c29bc679df70c7d8532` for the REST run.

The exact deployed identities were:

- Gateway `8.3.8 (b2026071409)`, image `inductiveautomation/ignition:8.3.8`, digest
  `sha256:560d0c069911476ff65aa7fd0fefab0530897e81c2a6cc5ea1410026126ed8ce`.
- MCP Module `1.3.5-SNAPSHOT (b2026021307)`, artifact version
  `1.3.5.2026021307-SNAPSHOT`, SHA-256
  `b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365`.
- Runtime Bundle `0.7.0`. The row's `bundleSha256` is the deterministic `build` ZIP the
  milestone-4b runs deploy,
  `d5382418c0d1b3f28ce89bc789373f9ee5020ba9daac9fe6f8bd158b3459ffd5`, independently
  reproduced locally from the merged tree. Each run records the artifact it deployed: the
  `ca27b4b` runs `0.6.0` (`d8d6c794…`), `dfa4d08` `0.7.0` (`5cdca831…`, before the ticket
  #7/#8 handler fixes the final head carries) and the apply run the `release`-built ZIP of
  the same version (`c95bd02e…`, which stamps its source revision).

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
*not* proven separate from it in `limitations` and `unsatisfiedAcceptance`.

**`setup-native apply` is verified.** Run
[35715927983](https://github.com/sheon-sek/ignition-mcp/actions/runs/35715927983) (`4b5b0df`,
the fix that makes the applied endpoint serve the profile's Tools) completed the D20 sequence
on a disposable Gateway on both rows — `plan`, then `apply` writing the bundle Project, the
Server Config and the Runtime Target Policy, then `verify` — with the applied endpoint serving
its inventory, `ok: true` and neither a Server Config refresh nor a verify retry needed. The
runs before it are why the row is careful about this item: `35713927140` died in the harness
(the `__main__` guard above its helpers, fixed in `5be2767`) and `35714215320` recorded the
Module's deferred provider pickup, which `4b5b0df` handles; both are recorded under ticket #21
in the runbook and below.

What the row still does not claim, in `limitations` and `unsatisfiedAcceptance`:

- **Runtime cancellation has no evidence at all.** There is no live case and no recorded
  fixture, because the Jython handlers have no cancellation path to observe. D30's
  Consequences say the Runtime timeout, ambiguous-outcome and cancellation cases are "proven
  with recorded fixtures only"; for cancellation that sentence is not satisfied.
- **Audit failure (`required` mode) is not live on either Plane.** The REST plane proves the
  D18 fail-closed branch by unit tests that raise `AuditWriteError`, and every live Runtime
  stage installs its policy with `auditMode=best_effort`. D26's G4 acceptance text asks for
  this case live on both Planes.
- **The Module's provider pickup is a recorded hazard, not a closed one.** A Server Config
  created live can be built before the Module has registered the Project's provider (its
  registration rides the Project collection's notification queue), leaving an endpoint that
  answers `initialize` with no capabilities at all. `apply` now re-announces the document
  while that is the case (bounded: 3 attempts, 2 s apart, `refreshes[]`) and the apply stage
  keeps one Gateway reload as a last-resort fallback, but a `config_resource_*` or
  file-copied deployment shares the window (ticket #21; live run `35714215320`).

The first two are recorded as owner questions in the Phase 4 runbook; the D30-sanctioned
Runtime fixture-only cases (timeout, ambiguous outcome) are limitations with a ruling behind
them, not gaps.

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
