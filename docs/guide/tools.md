# Tool catalog and what each Tool needs

> 中文版：[`tools.zh-CN.md`](tools.zh-CN.md)

This page lists every Tool the two servers offer, what it does, and what you have to set up before an
AI agent can see it. If an agent says a Tool does not exist, find the Tool here and check its
requirements row by row.

Both servers hide a Tool when a requirement is missing. The Tool does not show up in the agent's Tool
list at all, so the agent cannot call it by mistake.

- [The REST server (`ignition-rest`)](#the-rest-server-ignition-rest)
- [The Runtime server (`ignition-runtime`)](#the-runtime-server-ignition-runtime)
- [Tools that are not available in v1](#tools-that-are-not-available-in-v1)

The words "scope", "profile", "allowlist" and "Precondition token" are explained in
[How it works](how-it-works.md#words-used-in-these-docs).

## The REST server (`ignition-rest`)

The REST server runs on your own machine and talks to the Gateway over its web API. Setup is in
[Quick start](quick-start.md). Every setting named below is an environment variable, listed
in full in [Configuration reference](configuration.md#rest-server-settings).

### What every REST Tool needs

- The server is running, with `IGNITION_MCP_GATEWAY_URL`, `IGNITION_MCP_GATEWAY_API_TOKEN` and
  `IGNITION_MCP_DATA_DIR` set.
- The Gateway's API description lists the route the Tool uses. The server reads that description at
  start and every 60 seconds after. If your Gateway lacks a module, for example Alarm Notification,
  the Tools that need it stay hidden. The `ignition://gateway/capabilities` resource shows what the
  server found.
- The caller's credential carries the Tool's scope. With `IGNITION_MCP_AUTH_MODE=none`, every caller
  has `ignition.read` only, so only reads work.

### Read Tools

These change nothing. They need scope `ignition.read` and no extra switch, unless the table says
otherwise.

| Tool | What it does | Extra requirement |
| --- | --- | --- |
| `gateway_info` | Returns the Gateway's name, edition, version, redundancy role and time zone. | none |
| `gateway_diagnose` | Checks whether the server can reach and log in to the Gateway, what it learned about the Gateway's API, and whether its own storage works. Use it first when something looks wrong. | none. It shows even when the Gateway is unreachable |
| `operation_diagnose` | Looks up an earlier call by its `correlationId` and shows how far it got. | none. Records are kept 72 hours by default |
| `project_list` | Lists the Gateway's projects. | none |
| `config_resource_search` | Finds configuration resource types by keyword, for example "database". | none |
| `config_resource_describe` | Shows what fields one resource type has. | none |
| `config_resource_names` | Lists the names of the resources of one type. | none |
| `config_resource_list` | Lists the resources of one type with their settings. Passwords and keys are replaced with `<redacted>`. | none |
| `config_resource_get` | Reads one resource and returns the `signature` you need to change it later. | none |
| `audit_query` | Reads entries from one of the Gateway's audit logs. | an audit profile must exist in Ignition |
| `alarm_pipeline_list` | Lists Alarm Notification Pipelines. | the Alarm Notification module on the Gateway |
| `alarm_pipeline_status` | Shows the running instances of one pipeline. | the Alarm Notification module on the Gateway |
| `artifact_list` | Lists the files this server has stored for you, such as exports. | none |
| `artifact_info` | Shows one stored file's size, type and expiry. | none |
| `perspective_view_list` | Lists the Perspective Views of one project. | the Gateway must offer project export |
| `perspective_view_get` | Returns one View's JSON and the project fingerprint you need to change it. | the Gateway must offer project export |
| `perspective_page_config_get` | Returns a project's Perspective page configuration. | the Gateway must offer project export |
| `perspective_session_props_get` | Returns a project's Perspective session properties. | the Gateway must offer project export |
| `perspective_view_validate` | Checks a View JSON document for basic structure. Sends nothing to the Gateway. | the Gateway must offer project export |
| `project_export` | Exports a whole project as a ZIP file that you can download. | `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true` |
| `tag_config_export` | Exports Tag configuration as a JSON file that you can download. | `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true` |

The two export Tools have their own switch because a project or Tag export can contain credentials.
The export result gives a download link on the REST server, `/artifacts/<id>`, that needs the same
credential as the Tool call.

The server also offers two read-only resources: `ignition://gateway/capabilities` lists what the
Gateway supports, and `ignition://gateway/openapi-info` identifies the Gateway API description the
server loaded.

### Write Tools

Every write Tool is off by default. To turn one on, all of these must be true:

1. Authentication is on: `IGNITION_MCP_AUTH_MODE=static-token` or `jwt`. A server without
   authentication cannot write.
2. The caller's token carries the Tool's scope: `ignition.config` for configuration changes,
   `ignition.control` for `alarm_pipeline_cancel`. Having `ignition.admin` does not include the other
   scopes.
3. The Tool's class switch is on: `IGNITION_MCP_CONFIG_MUTATION_ENABLED=true` or
   `IGNITION_MCP_CONTROL_MUTATION_ENABLED=true`.
4. The Tool name is in `IGNITION_MCP_MUTATION_OPERATIONS`, a comma-separated list.
5. The thing it changes, called the Target, is in `IGNITION_MCP_MUTATION_TARGETS` under the Tool's
   name. A Tool with no Target entry can change nothing.
6. The Gateway API token the server uses has write permission on the Gateway.

The table lists what each Tool needs on top of those six.

| Tool | What it does | Scope | Target format in `IGNITION_MCP_MUTATION_TARGETS` | Extra requirement |
| --- | --- | --- | --- | --- |
| `config_resource_create` | Creates one configuration resource. Fails if the name is taken. | `ignition.config` | `<resourceType>/<name>`, for example `ignition/database-connection/MES` | none |
| `config_resource_update` | Changes one resource. Needs the `signature` from `config_resource_get`. | `ignition.config` | `<resourceType>/<name>` | none |
| `config_resource_delete` | Deletes one resource. Needs the `signature`. | `ignition.config` | `<resourceType>/<name>` | none |
| `config_resource_rename` | Renames one resource. | `ignition.config` | both the old and the new `<resourceType>/<name>` | none |
| `project_import` | Replaces an existing project with a ZIP file. Takes a backup first. | `ignition.config` | the project name | the Project writer, see below, plus a ZIP from `project_export` or from an upload |
| `perspective_view_upsert` | Creates or replaces one Perspective View. | `ignition.config` | the project name | the Project writer |
| `perspective_view_delete` | Deletes one Perspective View. | `ignition.config` | the project name | the Project writer |
| `perspective_page_config_update` | Replaces a project's page configuration. | `ignition.config` | the project name | the Project writer |
| `perspective_session_props_update` | Replaces a project's session properties. | `ignition.config` | the project name | the Project writer |
| `tag_config_import` | Creates Tags from a file made by `tag_config_export`. Never overwrites an existing Tag. | `ignition.config` | the destination, for example `[default]Imports` | `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true`, because the input file comes from `tag_config_export` |
| `artifact_delete` | Deletes a file this server stored. Sends nothing to the Gateway. | `ignition.config` | the artifact id | none |
| `alarm_pipeline_cancel` | Stops one running alarm notification. | `ignition.control` | the exact pipeline path | `IGNITION_MCP_CONTROL_MUTATION_ENABLED=true` instead of the config switch |

The Project writer handles the Tools that replace a project's files. Turn it on with both:

```bash
IGNITION_MCP_PROJECT_WRITER_ENABLED=true
IGNITION_MCP_GATEWAY_ID=plant-gateway-1   # any stable name you choose for this Gateway
```

Run only one Project writer per Gateway. The server cannot check this across machines.

`project_import` needs a project ZIP that the server already holds. You get one in either of two ways:

- Call `project_export` first. This needs `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true`.
- Upload a ZIP with `POST /artifacts?kind=project_archive`. This needs
  `IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED=true`.

A Target entry matches in one of three ways:

- An exact name, such as a project name or `<resourceType>/<name>`.
- A path and everything below it, for Tag destinations. `[default]Imports` also covers
  `[default]Imports/Line1`, but not `[default]Imports2`.
- `*`, which allows every Target. Write it only when you mean it.

Some resources can never be changed through these Tools, whatever the allowlist says:
API tokens, security levels, user sources, identity providers, secret providers, module settings, the
MCP Module's own Server Config, and the `IgnitionMCPPolicy` Tag provider. The full list is in
`contracts/shared/refused-resource-types.json`.

A complete example that turns on one configuration change:

```bash
IGNITION_MCP_AUTH_MODE=static-token
IGNITION_MCP_STATIC_TOKENS='{"agent":{"token":"<a long random secret>","scopes":["ignition.read","ignition.config"]}}'
IGNITION_MCP_CONFIG_MUTATION_ENABLED=true
IGNITION_MCP_MUTATION_OPERATIONS=config_resource_update
IGNITION_MCP_MUTATION_TARGETS='{"config_resource_update":["ignition/database-connection/MES"]}'
```

The REST server has no `ignition.admin` Tool in v1. `IGNITION_MCP_ADMIN_MUTATION_ENABLED` exists but
turns nothing on.

## The Runtime server (`ignition-runtime`)

The Runtime server runs inside the Gateway. The official Ignition MCP Module hosts it, and this
repository supplies the Tools as an Ignition project. Setup is in [Quick start](quick-start.md).

### What every Runtime Tool needs

- The MCP Module is installed and running on the Gateway.
- `ignition-mcp setup` deploys the bundle project, the Server Config and the Runtime Target Policy.
- The agent connects to `<gateway-url>/data/mcp/<server-config-name>` with an Ignition API token whose
  security level the Server Config allows.

### Roles decide which Tools exist

Each Assistant role carries a Runtime profile, and the profile decides which Tools the endpoint
offers. The Analysis role uses `readonly`. The Engineer role uses `full`.

| Profile | What it adds | Tool count |
| --- | --- | --- |
| `readonly` | the 13 read Tools | 13 |
| `operator` | `readonly` plus the 3 control Tools | 16 |
| `configurator` | `readonly` plus the 6 Tag configuration Tools | 19 |
| `full` | all of the above | 22 |

`ignition-mcp setup` deploys both roles by default in `dev` and the Analysis role alone in `prod`. To
change which roles a deployment serves, run `setup` again with `--roles`, then run `ignition-mcp
connect` for each role.

### Read Tools, in every profile

| Tool | What it does | Extra requirement |
| --- | --- | --- |
| `bundle_info` | Returns the bundle version, the Gateway version and the MCP Module version. | none |
| `tag_browse` | Lists the Tags and folders under one path. | none |
| `tag_query` | Searches a Tag provider by path, name or type. | none |
| `tag_read` | Reads the current value, quality and timestamp of up to 500 Tags. | none |
| `tag_get_config` | Reads a Tag's configuration and returns the fingerprint you need to change it. | none |
| `udt_type_list` | Lists the UDT types in one Tag provider. | none |
| `udt_type_get` | Reads one UDT type definition. | none |
| `alarm_shelved_list` | Lists the alarms that are shelved right now. | none |
| `historian_browse` | Lists the paths a Historian stores. | a Historian on the Gateway |
| `historian_query_series` | Returns raw history for up to 50 paths, at most 7 days and 25,000 points. | a Historian on the Gateway |
| `historian_query_aggregate` | Returns one value per path over a time range, such as the average or maximum. | a Historian on the Gateway |
| `database_query_list` | Lists the database queries you approved. | the Named Query registry, see below |
| `database_query` | Runs one approved query. | the Named Query registry, see below |

The endpoint also serves three read-only resources: the JSON Schemas for the output of `bundle_info`,
`tag_browse` and `tag_read`.

#### The Named Query registry

The agent can never send SQL. It can only run Named Queries that you list in advance, under a short
name called an alias. The list lives in one environment variable **on the Gateway machine**, not on
your own computer:

```text
IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON
```

Without it, `database_query_list` returns an empty list and `database_query` has nothing to run.
An example with one query:

```json
{
  "schemaVersion": 1,
  "entries": [
    {
      "alias": "line_downtime",
      "description": "Downtime events for one production line, newest first.",
      "project": "MES",
      "path": "Reports/LineDowntime",
      "resultMode": "dataset",
      "datasourcePolicy": "named-query-fixed",
      "parameters": {
        "line": {"type": "string", "required": true, "maxLength": 32}
      },
      "pagination": {
        "mode": "offset",
        "limitParameter": "limit",
        "offsetParameter": "offset",
        "defaultPageSize": 50,
        "hardPageSize": 500,
        "maxOffset": 100000
      }
    }
  ]
}
```

The field rules are in [Configuration reference](configuration.md#named-query-registry). Put the JSON
on one line when you set the variable, then restart the Gateway. With Docker, add it under
`environment:` in the compose file. With a service install, add it to the Gateway service's
environment.

### Control Tools, in `operator` and `full`

| Tool | What it does | Policy allowlist key |
| --- | --- | --- |
| `tag_write` | Writes new values to Tags, then reads them back. 20 writes per call by default, 100 at most. | `tag_write` |
| `alarm_shelve` | Shelves alarms at exact alarm paths for a set number of seconds. | `alarm_shelve` |
| `alarm_unshelve` | Removes shelving from alarms at exact alarm paths. | `alarm_unshelve` |

### Tag configuration Tools, in `configurator` and `full`

| Tool | What it does | Needs a fingerprint | Policy allowlist key |
| --- | --- | --- | --- |
| `tag_create` | Creates Tags. Fails if the path is taken. | no | `tag_create` |
| `tag_update` | Changes existing Tags' configuration. | yes | `tag_update` |
| `tag_copy` | Copies a Tag or folder to a new path. Fails if the destination is taken. | no | `tag_copy`, checked against the destination |
| `tag_delete` | Deletes Tags and folders. | yes | `tag_delete` |
| `tag_move` | Moves Tags and folders to a new parent. | yes | `tag_move`, checked against both the source and the destination |
| `tag_rename` | Renames a Tag or folder inside its own folder. | yes | `tag_rename`, checked against the new path |

Each call takes 20 items by default. The policy can raise that to 100.

"Needs a fingerprint" means the agent must first read the Tag with `tag_get_config` and pass the
fingerprint it got back. If someone changed the Tag in between, the call fails with `conflict` and
changes nothing.

### What every Runtime write Tool needs

A profile only makes a write Tool visible. Before the Tool changes anything it also reads the
**Runtime Target Policy**, a small JSON document that `ignition-mcp setup` stores on the Gateway. The
Tool refuses to run with `operation_disabled` if the policy is missing or broken, and with
`permission_denied` if the target is not in the policy's allowlist for that Tool.

```json
{
  "schemaVersion": 1,
  "serviceIdentity": "ignition-mcp-service",
  "auditMode": "best_effort",
  "allowlists": {
    "tag_write": ["[default]Plant/AHU"],
    "alarm_shelve": ["prov:default:/tag:Plant/AHU"],
    "alarm_unshelve": ["prov:default:/tag:Plant/AHU"],
    "tag_update": ["[default]Plant"]
  },
  "alarmShelveMaxSeconds": 3600
}
```

- A Tag entry covers that path and everything below it. `[default]Plant/AHU` covers
  `[default]Plant/AHU/Temp`, but not `[default]Plant/AHU2`.
- An alarm entry is a provider-qualified alarm path without `*`. It covers that path and the alarm
  paths below it.
- A Tool with no key in `allowlists` can change nothing. `["*"]` allows everything.
- UDT definitions under `[provider]_types_/` are covered by `*` when the Runtime Target Policy's
  `allowlistsWildcardIncludeUdtTypes` is true. Setup defaults it to true when a deployed role uses
  the `full` profile. With the setting false or absent, use an explicit entry such as
  `[default]_types_/Motor`.
- Nothing can ever change the `IgnitionMCPPolicy` Tag provider, where the policy itself lives.

All policy fields are listed in [Configuration reference](configuration.md#runtime-target-policy).

## Tools that are not available in v1

`alarm_status`, `alarm_journal` and `alarm_acknowledge` are written but switched off. Ignition's alarm
query functions have no built-in limit on how many rows they return, so these Tools cannot promise a
bounded answer. Their code is kept in `packages/ignition-runtime-bundle/deferred/` and no profile
lists them.

For alarm information today, use the REST server's `alarm_pipeline_*` Tools for notification
pipelines, or `alarm_shelved_list` for shelved alarms.
