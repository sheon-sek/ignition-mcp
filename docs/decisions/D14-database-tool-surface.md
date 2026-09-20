# D14 — Database Tool Surface

**Status:** DECIDED

## Core decision
v1 does **not** expose arbitrary SQL, even through a tool named `readonly`.

Database access is capability-oriented through an explicit approved Named Query registry.

## Public v1 surface
```text
ignition-runtime
├── database_query_list
└── database_query
```

Both are `READ`.

## Why arbitrary read-only SQL is rejected
Do not attempt to establish a security boundary by checking whether SQL starts with `SELECT` or by maintaining a keyword denylist.

A SQL string that appears read-only is not a reliable cross-database proof of no side effects, and generic SQL also makes bounded pre-execution result control difficult.

Database-side permissions remain an important independent safety layer.

## Approved Named Query Registry
`database_query_list` exposes only MCP-approved query contracts, not every Named Query in every Gateway project.

Each approved entry defines server-side:
- public alias;
- description;
- fixed Project / Named Query path;
- result mode;
- accepted parameters and types;
- fixed/approved datasource policy;
- bounded-result policy;
- pagination contract when applicable.

The Agent cannot submit an arbitrary Named Query path.

## `database_query`
Input uses:
- approved query alias;
- validated parameter object;
- D10 pagination controls where the approved query supports them.

The server resolves the alias and decides whether to use `system.db.execQuery` or `system.db.execScalar`.

The caller cannot select arbitrary database execution primitives.

## Named Query parameter policy
v1 approved queries allow only safe Value-style parameters.

Do not approve:
- `QueryString` parameters that directly alter SQL structure;
- caller-controlled dynamic Database parameters.

The effective datasource must resolve to a server-approved fixed connection/default.

## Bounded-result requirement
An approved dataset query must have a result bound that is enforced before/during execution, not after a huge Dataset has already been materialized.

A query may be approved when, for example:
- it has an inherent fixed maximum result size;
- it is aggregate/single-row;
- it has server-controlled limit/pagination parameters.

If pagination parameters such as limit/offset are part of the Named Query, the MCP handler owns their values. The Agent does not get to bypass configured maximums.

A Named Query without a credible bounded-result contract cannot be registered for MCP.

## Database permissions
Where practical, MCP-approved Named Queries should execute through database principals/connections that are themselves read-only.

Security is layered:
```text
Runtime READ profile
→ approved Named Query alias
→ parameter validation
→ Value parameters only
→ fixed approved datasource
→ bounded-result contract
→ database-side least privilege
```

## Writes are not public in v1
Do not expose:
- `system.db.execUpdate`;
- prepared update APIs;
- generic `database_update`;
- generic SQL mutation;
- Store-and-Forward write operations.

Future write capabilities should be explicit business-domain Tools with their own validation, authorization, targets, and verification rather than a generic SQL console.

## Transactions are not public
Do not expose transaction handles across MCP calls:
- begin;
- commit;
- rollback;
- close.

If a future semantic Tool requires a transaction, the entire transaction lifecycle must be contained in one Tool invocation with correct commit/rollback/close cleanup.

## Stored procedures
Do not expose a generic stored-procedure invocation Tool in v1. Stored procedures do not provide an inherent read-only guarantee.

A future procedure may be exposed only as a separately curated semantic capability.

## Datasource configuration
Runtime MCP does not expose datasource configuration APIs.

Database Connection configuration remains owned by the REST configuration plane under D02.

## Timeout note / D10 clarification
`system.db` query APIs do not provide a reliable generic per-call interrupt/timeout contract.

D10 timeout values are request/policy budgets, while true runaway-query protection must also rely on database/JDBC-side timeout configuration.

Do not implement timeout by spawning a worker, abandoning it after N seconds, and leaving orphan queries/threads/connections.

## Result normalization
Normalize Dataset results into stable typed structures such as columns plus rows and page metadata.

Do not stringify every value. Preserve useful scalar/date/decimal/null semantics according to the shared serialization rules.

## Permission summary
| Tool | Server | Class |
|---|---|---|
| database_query_list | runtime | READ |
| database_query | runtime | READ |

```yaml
decision: D14
status: DECIDED
arbitrary_sql_public: false
approved_named_query_registry: true
named_query_querystring_parameters: forbidden
dynamic_datasource_parameter: forbidden
generic_database_writes_v1: false
transaction_handles_public: false
generic_stored_procedure_public: false
```
