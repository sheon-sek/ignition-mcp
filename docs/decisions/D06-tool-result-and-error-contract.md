# D06 — Tool Result and Error Contract

**Status:** DECIDED

## Decision
The two servers share response semantics, error taxonomy, metadata conventions, pagination conventions, and batch conventions, but do **not** force every Tool into `{ok,result,error}`.

Use MCP structured output and MCP Tool error semantics.

## Scope
D06 is a **Tool** contract. Structured output, `outputSchema`, MCP Tool Error semantics, the stable error taxonomy, and batch/partial-failure rules apply to Tools.

MCP Text Resources and Prompts use their own native MCP semantics (`resources/read`, `prompts/get`) and are not wrapped into, and not required to satisfy, this Tool result/error contract.

## Success
Successful tools return their domain model directly with explicit output schema.

Do not wrap every result in:
```json
{"ok": true, "result": {}}
```

## Execution failure
Tool execution failures use MCP Tool Error semantics rather than a successful Tool result whose JSON says `"ok": false`.

## Stable error taxonomy
- `invalid_argument`
- `unsupported_capability`
- `permission_denied`
- `operation_disabled`
- `not_found`
- `conflict`
- `limit_exceeded`
- `rate_limited`
- `timeout`
- `outcome_unknown`
- `gateway_unavailable`
- `upstream_error`
- `schema_mismatch`
- `internal_error`

`outcome_unknown` is specifically for mutations that may have reached Ignition but whose final outcome cannot be confirmed. Do not blindly retry; verify state first.

## Error exposure
Expose:
- stable code;
- safe message;
- remediation when useful;
- correlation ID.

Do not expose:
- stack trace;
- secrets/tokens/passwords;
- sensitive raw upstream responses.

Full exception cause/stack/context stays in server logs.

## Domain negative state is not Tool failure
Examples:
- Bad/Uncertain Tag Quality;
- degraded Gateway;
- disconnected DB connection;
- active alarm.

## Batch partial failure
A batch that executes with mixed per-item outcomes remains a successful Tool execution and returns per-item results plus summary. Do not discard valid outcomes.

## Metadata
Normal metadata remains lightweight: correlation ID, pagination/continuation, explicit truncation when applicable. Backend path/native function/OpenAPI hash belong in diagnostics/logs, not every response.

## Shared contracts
Standardize:
- error taxonomy;
- metadata;
- pagination;
- batch-result conventions.

Do not standardize every Tool into one top-level result shape.

```yaml
decision: D06
status: DECIDED
applies_to: mcp_tools
structured_output: true
output_schema_required: true
universal_ok_wrapper: false
mcp_tool_error_for_execution_failure: true
partial_failure_is_tool_error: false
correlation_id_required: true
```

## Pre-D26 consistency amendment
Added the “Scope” section so that D06's result/error/outputSchema rules are explicitly Tool rules. Text Resources and Prompts keep their native MCP semantics. No Tool contract behavior was changed.


## D27 amendment — Runtime native outputSchema discovery

D27 records live evidence that the pinned official Runtime MCP Module build exposes real `structuredContent` and `isError` but does not publish Tool `outputSchema`. For the exact D27 tuple only, the repo-owned JSON Schema remains the mandatory semantic Tool output contract while native `tools/list.outputSchema` may be absent. No text-only fallback is permitted. Future Module/Gateway tuples must be re-characterized.
