# D01 — Repository License and Upstream Reuse Policy

**Status:** DECIDED

## Decision
- Project license: **GPL-3.0**.
- `jsgorana/ignition-mcp` and `WhiskeyHouse/ignition-mcp` are treated identically:
  - selectively reuse proven/compatible code;
  - reuse validated endpoint mappings, request/response handling, parsing, pagination, timeout/error behavior, utilities, tests, and known Ignition quirks where useful;
  - prefer a new target architecture rather than fork-and-patch.
- Neither upstream becomes the primary fork base.

## Meaning of architectural rewrite
Architectural rewrite does not mean rewriting every validated line. Proven low-level behavior may be retained while higher-level structure is redesigned around FastMCP 4 lifecycle, shared async HTTP, capability registry, tool taxonomy, authz, mutation policy, response contracts, Runtime-vs-REST ownership, and removal of WebDev.

```yaml
decision: D01
status: DECIDED
license: GPL-3.0
reuse:
  jsgorana: {selective_reuse: true, architectural_rewrite: true, fork_and_patch: false}
  WhiskeyHouse: {selective_reuse: true, architectural_rewrite: true, fork_and_patch: false}
```
