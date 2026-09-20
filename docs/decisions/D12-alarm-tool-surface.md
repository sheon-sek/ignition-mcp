# D12 — Alarm Tool Surface

**Status:** DECIDED

## Public surface
```text
ignition-runtime
├── alarm_status
├── alarm_journal
├── alarm_shelved_list
├── alarm_acknowledge
├── alarm_shelve
└── alarm_unshelve

ignition-rest
├── alarm_pipeline_list
├── alarm_pipeline_status
└── alarm_pipeline_cancel
```

Alarm configuration resources such as Alarm Journal Settings, General Alarm Settings, Alarm Notification Profiles, and Rosters remain owned by the REST configuration plane through the generic `config_resource_*` layer.

## Alarm event ownership
Runtime MCP owns live Alarm Event behavior because it maps naturally to `system.alarm.*` runtime APIs.

Notification Pipeline runtime status/cancellation is owned by `ignition-rest` when the target Gateway exposes the corresponding Native REST endpoints.

Do not duplicate semantically equivalent pipeline operations in Runtime MCP.

## `alarm_status`
Backend: `system.alarm.queryStatus`.

Expose validated semantic filters rather than a thin passthrough:
- states;
- priorities;
- alarm paths;
- source paths;
- display paths;
- providers;
- property conditions;
- include shelved;
- D10 paging/output controls.

Property conditions use structured MCP objects and are converted internally to Ignition tuple semantics.

Return a canonical current Alarm Event representation. Preserve state and timing semantics such as:
- event ID;
- name/label;
- source/display path;
- priority/state;
- event time;
- active/acknowledged/cleared/shelved state;
- active/clear/ack times;
- acknowledgement identity when available.

Associated Data is not returned wholesale by default.

## `alarm_journal`
Backend: `system.alarm.queryJournal`.

Journal records are historical state transitions, not the same object model as current Alarm Events.

Expose:
- bounded start/end time;
- journal;
- states/priorities;
- alarm/source/display/provider filters;
- property filters;
- include-data/system/shelved options where supported;
- D10 output controls.

Do not claim native pagination where Ignition does not provide reliable continuation. A large query must fail with a bounded-resource error and require a narrower time/filter range rather than materializing an unbounded result and silently truncating it.

## `alarm_acknowledge`
Backend: `system.alarm.acknowledge`.

Input uses explicit Alarm Event UUIDs plus optional note.

Return per-event outcomes and a batch summary. Partial failure is represented explicitly under D06.

Caller-supplied acknowledgement username is forbidden.

If a verified caller identity is available from the official MCP Module, it may be used. Otherwise use an explicit configured MCP service identity. Never fabricate a human actor.

Class: `CONTROL`.
Treat as destructive because acknowledgement can affect alarm workflows and notification behavior.

## Shelving
Expose separate tools:
- `alarm_shelve`
- `alarm_unshelve`

Do not expose overloaded `shelve(timeout=0)` semantics as the public unshelve operation.

`alarm_shelve` accepts exact Alarm Paths and a canonical duration in seconds.

For mutation:
- wildcard paths are forbidden;
- exact targets only;
- batching uses explicit path arrays;
- D08 target allowlists and D10 mutation limits apply.

`alarm_unshelve` likewise accepts exact paths only.

## `alarm_shelved_list`
Expose the dedicated shelved-state view because shelving metadata such as expiration and shelving identity is not equivalent to `alarm_status(includeShelved=true)`.

This read surface is also the canonical bounded verification source for shelve/unshelve mutations where practical.

## Notification Pipeline REST surface
### `alarm_pipeline_list`
List runtime Alarm Notification Pipeline instances/overview through Native REST.

### `alarm_pipeline_status`
Return runtime status for a specific pipeline path/instance. This is runtime status, not pipeline definition authoring.

### `alarm_pipeline_cancel`
Target a specific pipeline path plus Alarm Event ID.

Cancelling a notification pipeline does not clear or acknowledge the Alarm Event itself.

Class: `CONTROL`.
Treat as destructive.

Do not publish a vague `alarm_cancel` tool.

## Not public
Runtime helpers/operations that duplicate REST ownership are not public:
- `system.alarm.cancel`;
- `system.alarm.listPipelines`.

Roster configuration helpers are not public:
- `system.alarm.createRoster`;
- `system.alarm.getRosters`.

Alarm Notification Pipeline definition authoring is deferred to the later typed Project Resource authoring design.

## Permission summary
| Tool | Server | Class |
|---|---|---|
| alarm_status | runtime | READ |
| alarm_journal | runtime | READ |
| alarm_shelved_list | runtime | READ |
| alarm_acknowledge | runtime | CONTROL |
| alarm_shelve | runtime | CONTROL |
| alarm_unshelve | runtime | CONTROL |
| alarm_pipeline_list | REST | READ |
| alarm_pipeline_status | REST | READ |
| alarm_pipeline_cancel | REST | CONTROL |

```yaml
decision: D12
status: DECIDED
runtime_alarm_owner: ignition-runtime
pipeline_runtime_owner: ignition-rest
caller_supplied_ack_username: forbidden
shelve_wildcard_mutation: forbidden
public_unshelve_tool: true
pipeline_definition_authoring_v1: false
```
