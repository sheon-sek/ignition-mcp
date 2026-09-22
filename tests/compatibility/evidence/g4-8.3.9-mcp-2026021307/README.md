# Phase 4 G4 evidence: 8.3.9 compatibility candidate row

This row is the **G4 close-out** for the 8.3.9 candidate and is the exact
counterpart of the [8.3.8 required row](../g4-8.3.8-mcp-2026021307/README.md): the
same live runs, each of which carried both Gateway matrix rows, the same L5 case
matrix, the same inventories and the same recorded limitations. It was composed by
`python -m tooling.compat g4 --close tests/compatibility/g4/close-8.3.9.json
--artifacts <run downloads>`, which picks the candidate's own 8.3.9 harness
document out of each run download instead of the required row's.

The cited live runs were:

| Run | Workflow | Head | What it proves |
|---|---|---|---|
| [35710377211](https://github.com/sheon-sek/ignition-mcp/actions/runs/35710377211) | Phase 4 Live Gateway G4a | `ca27b4b` | milestone 4a on the candidate: the Runtime Target Policy gate, `tag_write`, `alarm_shelve`/`alarm_unshelve`, the operator inventory and the Runtime audit pair |
| [35710377243](https://github.com/sheon-sek/ignition-mcp/actions/runs/35710377243) | Phase 4 Live Gateway G4b | `ca27b4b` | milestone 4b: the fingerprint, `tag_update`, `tag_create` and `tag_copy` |
| [35707687810](https://github.com/sheon-sek/ignition-mcp/actions/runs/35707687810) | Phase 4 Live Gateway G4b | `dfa4d08` | milestone 4b complete: `tag_delete`, `tag_move`, `tag_rename`, the partial-failure batch, the 19-Tool configurator inventory |
| [35713927291](https://github.com/sheon-sek/ignition-mcp/actions/runs/35713927291) | Phase 4 Live Gateway G4b | `0801e51` | milestone 4b on the **final integration head** (`4238653` + #22 + the CI-only workflow change): the same cases and inventories, with the head's own bundle |
| [35687123699](https://github.com/sheon-sek/ignition-mcp/actions/runs/35687123699) | Phase 4 Live Gateway REST mutation | `98f8f74` | the REST inventories and every allowlist, refused-type and precondition refusal, plus the fault proxy's timeout / ambiguous-outcome / cancellation / partial-write cases |

The candidate row is not required, but it is not weaker either: every case the
required row records live is recorded live here too, on Gateway `8.3.9
(b2026082511)`, image `inductiveautomation/ignition:8.3.9`, digest
`sha256:28bd6b320157ec8dbbe465d0cd7c9f0ababfda4ff01c0bab982c0522a7b4eba2`, with
the same MCP Module and the same deployed Runtime Bundle as the required row.

The L5 matrix, the limitations (`limitations`), the unproven case
(`unsatisfiedAcceptance`) and `gateResult: VERIFIED_WITH_LIMITATION` are identical
to the required row's, for the same reasons: Runtime cancellation has no evidence
at all, audit failure (`required` mode) is not live on either Plane, and
`setup-native apply`'s confirmation run on the integration head (`35713927140` on
`0801e51`) failed in the harness — `NameError: _reverify`, fixed in `5be2767` and
not yet re-run — so #21/#22 and that G4 item stay `run-pending` rather than claimed
(see the required row's README).

[evidence.json](evidence.json) is copied byte-for-byte from the generator's
output; the close document it was composed from is
[`tests/compatibility/g4/close-8.3.9.json`](../../g4/close-8.3.9.json).

Gate G4 status for this candidate row is `VERIFIED_WITH_LIMITATION`. The
machine-readable row stays fail-closed with `status: FAILED_NATIVE_BINDING`,
`nativeResponseBinding: UNVERIFIED_LIMITATION` and `d27ExceptionApplied: false`,
because D27's native-binding exception belongs only to the exact 8.3.8 tuple. D21
deployment compatibility remains `UNTESTED`; this row makes no production
compatibility certification and no tuple is `SUPPORTED`.
