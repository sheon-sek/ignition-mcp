# Phase 5 G5 evidence: 8.3.8 required row

This row closes G5 for the 8.3.8 tuple. It was composed by
`python -m tooling.compat g5 --close tests/compatibility/g5/close-8.3.8.json` from the
class-enabled pass of one green live run:

| Run | Workflow | Branch head | Evaluated merge revision |
|---|---|---|---|
| [35747876956](https://github.com/sheon-sek/ignition-mcp/actions/runs/35747876956) | Phase 4 Live Gateway REST mutation | `ecffb98` | `f857ae48a0e326004bbddad425c3f27115c334ca` |

The live cases, run through the nine Perspective Tools over MCP, are the ones the D26
Phase 5 amendment names: normal edit (including a create), no-op, unrelated-resource
preservation, the Inherited resource refusal, and a change between the caller's read
and the write that the write refuses with `conflict`.

The generic transaction cases cite the G4 row for this tuple, because
`ProjectTransactionService` and `projects/zip_safety.py` did not change in Phase 5.

The row is `VERIFIED`. No tuple is `SUPPORTED`.
