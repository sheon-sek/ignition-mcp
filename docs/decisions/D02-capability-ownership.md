# D02 — MCP Capability Ownership

**Status:** DECIDED

## Architecture
```text
AI Agent / MCP Client
├── ignition-rest
│   └── External FastMCP Server
│       └── Ignition Native REST API
└── ignition-runtime
    └── Ignition Official MCP Module
        └── system.* runtime functions
```

## Canonical ownership rules
1. If official Native REST semantically and completely represents an operation, **`ignition-rest` is canonical owner**.
2. `ignition-runtime` owns Gateway runtime operations that require `system.*` and are not equivalently exposed by Native REST.
3. Ownership is by **operation semantics**, not broad domain/module.
4. Equivalent public operations are not duplicated across servers.
5. If REST later fully covers an existing Runtime operation, **REST wins**; Runtime version is deprecated/removed through normal compatibility policy.
6. Servers do not proxy through each other.
7. No automatic cross-server fallback.
8. Unsupported means unsupported; never silently switch backend.

## Scope of the ownership rules
The ownership/no-duplication rules govern **operation semantics and Tools**.

MCP Text Resources and Prompts are supporting primitives, not a second operation surface. Publishing a Tool's schema or documentation as a static Runtime Text Resource, or shipping a Prompt that references Tools, does not create duplicate operation ownership and does not violate the no-duplicate-operation rule, even when the Resource or Prompt describes a capability owned by `ignition-rest`.

## Configuration plane vs runtime plane
Typical REST ownership: Gateway info/health, module status, projects, configuration resources, DB/OPC configuration, historian provider configuration, alarm configuration, REST-exposed sessions/reporting/SFC, audit query, bulk Tag config import/export when Native REST supports it.

Typical Runtime ownership: live Tag browse/read/write, granular Tag configuration, active alarms/journal/ack/shelve, historian data queries, restricted runtime DB query.

## Audit
Public audit log query belongs to REST when Native REST exposes it. `system.util.audit(...)` is used by Runtime tools to record Runtime mutations rather than creating a duplicate public audit-query surface.

## Forbidden
- arbitrary Jython execution;
- OS command execution;
- duplicated equivalent tools across servers.

```yaml
decision: D02
status: DECIDED
principles:
  - native-rest-first
  - ownership-by-operation-semantics
  - no-duplicate-semantic-tools
  - no-cross-server-proxy
  - no-cross-server-fallback
  - rest-wins-equivalent-operation
  - ownership-scope-is-operation-semantics-and-tools
  - supporting-text-resources-and-prompts-are-not-duplicate-operations
```

## Pre-D26 consistency amendment
Added the “Scope of the ownership rules” clarification so that Runtime Text Resources and Prompts are not misread as duplicate operation ownership when they document or reference Tool contracts. No ownership rule, plane, or forbidden-operation list was changed.
