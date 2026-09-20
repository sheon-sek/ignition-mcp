# Runtime native-response binding characterization

This harness is the Phase 0 / G0 live-test contract. It does **not** emulate Ignition.

The real test target must be an ephemeral Ignition Gateway with an exact Gateway patch and exact MCP Module version/build. Evidence must record:

1. MCP initialize succeeds.
2. `tools/list` returns the exact fixture Tool inventory and publishes the expected Tool output schema.
3. A successful `tools/call` returns native MCP `structuredContent` matching the schema.
4. A deliberate execution failure returns MCP Tool error semantics with `isError=true`.
5. `resources/list` + `resources/read` work for any published fixture resource.
6. `prompts/list` + `prompts/get` work when a fixture Prompt is published.

Until all Tool-binding checks are live-verified, evidence remains `NATIVE_BINDING_PENDING`, the Runtime product project remains disabled, and production compatibility cannot be claimed.
