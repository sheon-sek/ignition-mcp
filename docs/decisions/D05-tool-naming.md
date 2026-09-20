# D05 — MCP Server and Tool Naming Convention

**Status:** DECIDED

## Server names
Canonical MCP server names:
- `ignition-rest`
- `ignition-runtime`

Implementation package names may differ, but Agent-facing server aliases use these names.

## Tool naming
Use:
```text
snake_case
<domain>_<action>
<domain>_<object>_<action>
<domain>_<action>_<qualifier>
```

Examples:
- `gateway_info`
- `project_list`
- `database_connection_get`
- `perspective_session_terminate`
- `tag_read`
- `historian_query_raw`

Domain appears first.

## Prefix rules
Do not add `ignition_`, `rest_`, `native_`, `runtime_`, `jython_`, or `system_` to public Tool names. Server name provides the technical namespace; Tool name expresses semantic capability.

## Canonical verbs
- `list`: flat collection
- `get`: exact object/configuration
- `search`: discovery without exact identifier
- `browse`: hierarchical namespace
- `query`: filtered/time-series dataset
- `read`: live/current value
- `write`: live/current value mutation
- `create`, `update`, `delete`, `rename`, `copy`, `move`
- `import`, `export`, `describe`, `status`, `diagnose`
- `acknowledge`, `shelve`, `cancel`, `terminate`, `pause`, `resume`, `restart`

Use singular domain nouns.

## Avoid vague verbs
Do not use as normal public verbs: `manage`, `handle`, `process`, `execute`, `run`, `perform`, `operate`, `do`, `fetch`, `load`, `invoke`, `call`, `modify`, `change`.

Use `update` instead of `modify`.

## Generic config namespace
All generic resource tools use `config_resource_*`.

## Stability
Tool semantic names are stable public contracts. REST paths and `system.*` functions are implementation details.
Tool rename is a breaking change and requires a deprecation path.

## Resource and Prompt identifiers
Canonical public **Tool** names remain `snake_case` exactly as above; this decision is not changed by the updated Skill scaffold.

Resource URIs and Prompt names are public identifiers and must be based on **actual MCP discovery** (`resources/list`, `prompts/list`) against the target Module, not inferred from Designer project folder names or from generic sample titles. The Skill scaffold's `tag-read` folder name/title is a sample, not a project naming rule; production artifacts are verified against real `tools/list`, `resources/list`, and `prompts/list`.

If real discovery ever exposes an identifier that conflicts with this convention, the conflict is resolved by an explicit decision or amendment rather than by silently renaming only one side.

```yaml
decision: D05
status: DECIDED
servers: {external: ignition-rest, runtime: ignition-runtime}
tool_case: snake_case
resource_prompt_identifiers: discovery_verified
ignition_prefix: false
backend_prefix: false
domain_nouns: singular
rename_is_breaking_change: true
```

## Pre-D26 consistency amendment
Added the Resource/Prompt identifier rule. Tool naming, prefix rules, canonical verbs, and stability semantics are unchanged; in particular the public Tool case remains `snake_case` and the Skill's kebab-case sample title is explicitly not adopted as a project naming rule.
