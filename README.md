# Ignition MCP

A dual-server MCP ecosystem for Inductive Automation Ignition.

- `ignition-rest`: external FastMCP server for curated Ignition Native REST capabilities.
- `ignition-runtime`: the Ignition Official MCP Module hosting the Runtime MCP Bundle (`system.*`).

The architecture and implementation order are frozen by `docs/decisions/D01-D26`. Implementation is dependency-first and phase-gated.

## Current implementation status

Phase 0 is repository foundation plus Native MCP response-binding characterization. The Runtime Bundle is intentionally disabled while `NATIVE_BINDING_PENDING` remains unresolved by a real Gateway + exact MCP Module test.

See `docs/development/phase-0.md`.
