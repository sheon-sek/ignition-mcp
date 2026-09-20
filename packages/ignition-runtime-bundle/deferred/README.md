# Deferred Runtime Tools (not shipped in the bundle ZIP)

These handlers are **disabled** in every current profile per the [D12 Phase 2 bounded-execution amendment](../../../docs/decisions/D12-alarm-tool-surface.md#phase-2-bounded-execution-amendment).

`system.alarm.queryStatus` and `system.alarm.queryJournal` expose no native row limit, reliable continuation, or interruptible timeout on the characterized Ignition 8.3 API. The handlers therefore cannot provide the pre/during-execution cardinality and memory bound required by D10/D12, so their resources are excluded from `project/` and cannot be discovered or invoked by the Runtime MCP server.

Re-enabling either Tool requires, at minimum:

1. a credible bounded mechanism verified against a real Gateway (native limit/continuation or an independently enforced bounded alarm backend);
2. restoration of `contracts/profiles/*.yaml`, `tooling/contracts/lint.py`, and the Tool directories under `project/com.inductiveautomation.mcp/tools/`;
3. a fresh exact-tuple D23/D26/G2 live-evidence run — these contracts are not exempt merely because their code exists here.

The preserved code still carries the bounded-input, canonical-error, and D28 work completed during the pre-G2 audit.
