# Configuration reference

> 中文版：[`configuration.zh-CN.md`](configuration.zh-CN.md)

This page lists every setting, grouped by where you set it. For which Tool needs which setting, see
the [Tool catalog](tools.md).

- [REST server settings](#rest-server-settings): environment variables for `ignition-rest-mcp`.
- [CLI settings](#cli-settings): the flags an `ignition-mcp` command takes.
- [Connecting an agent](#connecting-an-agent): the URL, header and token each MCP server takes.
- [Runtime Target Policy](#runtime-target-policy): the JSON file that allows Runtime writes.
- [Server Config permissions file](#server-config-permissions-file): who may connect to the Runtime server.
- [Named Query registry](#named-query-registry): the database queries the Runtime server may run.

## REST server settings

The REST server reads its settings from environment variables when it starts. It checks every value
and refuses to start if one is wrong, and the error message names the variable. Change a value, then
restart the server.

`ignition-mcp setup` writes these values into the deployment directory and `ignition-mcp start` runs
the server with them. The [quick start](quick-start.md) covers both commands, and the
[runbook](../operations/runbook.md#turn-on-writes) shows the switches for write Tools. You can also
set the variables in a shell: with `export NAME=value` on Linux and macOS, or `$env:NAME = "value"`
in PowerShell.

### Required

| Variable | What to put there |
| --- | --- |
| `IGNITION_MCP_GATEWAY_URL` | The Gateway's web address, for example `http://127.0.0.1:8088`. Default `http://127.0.0.1:8088`. |
| `IGNITION_MCP_GATEWAY_API_TOKEN` | An Ignition API token. The server uses it for every Gateway call. Agents never see it. |
| `IGNITION_MCP_DATA_DIR` | A folder the server keeps its records and stored files in. It must be a full path, for example `/var/lib/ignition-mcp` or `C:\ProgramData\ignition-mcp`. Outside the `development` profile it must not be a temporary folder such as `/tmp`. |

### Where the server listens

| Variable | Default | Meaning |
| --- | --- | --- |
| `IGNITION_MCP_HOST` | `127.0.0.1` | The network address to listen on. `127.0.0.1` means only this computer can connect. |
| `IGNITION_MCP_PORT` | `8000` | The port. |
| `IGNITION_MCP_PATH` | `/mcp` | The URL path of the MCP endpoint. |

With the defaults, agents connect to `http://127.0.0.1:8000/mcp`. The server also answers
`/health/live`, `/health/ready` and `/metrics` on the same port.

### Deployment profile and authentication

`IGNITION_MCP_DEPLOYMENT_PROFILE` sets how strict the server is. `IGNITION_MCP_AUTH_MODE` sets how
callers prove who they are.

| Profile | Use it for | Rules |
| --- | --- | --- |
| `development` (default) | trying it out on one computer | may only listen on `127.0.0.1` or another loopback address |
| `trusted-internal` | a server on a private plant or office network | may listen on any address. May run without authentication, but then it is read-only |
| `secured` | anything exposed more widely | requires `IGNITION_MCP_AUTH_MODE=jwt` |

| Auth mode | What the caller sends | Can write? |
| --- | --- | --- |
| `none` (default) | nothing | no. Every caller gets `ignition.read` only |
| `static-token` | `Authorization: Bearer <token>` | yes, with the scopes you give that token |
| `jwt` | `Authorization: Bearer <JWT from your identity provider>` | yes, with the scopes in the token |

For `static-token`, list the tokens in `IGNITION_MCP_STATIC_TOKENS` as JSON. Each token has a name
and a list of scopes:

```json
{
  "claude-desktop": {"token": "<long random secret>", "scopes": ["ignition.read"]},
  "maintenance-agent": {"token": "<another secret>", "scopes": ["ignition.read", "ignition.config"]}
}
```

- The four scopes are `ignition.read`, `ignition.config`, `ignition.control` and `ignition.admin`.
  None includes another, so list each one you want.
- The token's name, not its secret, appears in audit records as `static-token:<name>`.
- Names use letters, digits and `._:-`, up to 64 characters. Secrets are at most 512 characters. Up
  to 32 tokens.
- `IGNITION_MCP_STATIC_TOKEN` is an older single-token form with `ignition.read` only. Do not set both.

For `jwt`, set `IGNITION_MCP_JWT_ISSUER`, `IGNITION_MCP_JWT_AUDIENCE`, and exactly one of
`IGNITION_MCP_JWT_JWKS_URI` (an `https` address) or `IGNITION_MCP_JWT_PUBLIC_KEY` (a PEM key). The
server accepts RS256 tokens and reads the scopes from the token. It checks tokens. It does not issue
them.

### Turning on writes and exports

All of these default to off. The [Tool catalog](tools.md#write-tools) shows which Tool needs which.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IGNITION_MCP_CONFIG_MUTATION_ENABLED` | `false` | Allows configuration-change Tools. |
| `IGNITION_MCP_CONTROL_MUTATION_ENABLED` | `false` | Allows `alarm_pipeline_cancel`. |
| `IGNITION_MCP_ADMIN_MUTATION_ENABLED` | `false` | No v1 Tool uses it. |
| `IGNITION_MCP_MUTATION_OPERATIONS` | empty | Comma-separated Tool names that may write, for example `config_resource_update,project_import`. `*` allows all. |
| `IGNITION_MCP_MUTATION_TARGETS` | empty | JSON that maps each Tool name to the Targets it may change, for example `{"project_import":["MES"]}`. A Tool with no entry can change nothing. |
| `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED` | `false` | Allows `project_export` and `tag_config_export`. |
| `IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED` | `false` | Allows uploading a project ZIP with `POST /artifacts?kind=project_archive`. |

### Project writer

`project_import` and the four Perspective write Tools need the Project writer.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IGNITION_MCP_PROJECT_WRITER_ENABLED` | `false` | Turns the Project writer on. |
| `IGNITION_MCP_GATEWAY_ID` | empty | A stable name you choose for the Gateway, required when the writer is on. Letters, digits and `._:-`, up to 128 characters. |
| `IGNITION_MCP_PROJECT_DESIGNER_POLICY` | `deny` | What to do when someone has the project open in the Designer: `deny`, `warn` or `ignore`. |
| `IGNITION_MCP_PROJECT_LOCK_TIMEOUT_SECONDS` | `10` | How long a write waits for another write to the same project. |
| `IGNITION_MCP_PROJECT_VERIFICATION_TIMEOUT_SECONDS` | `60` | How long the server waits to confirm an import. |
| `IGNITION_MCP_PROJECT_RECONCILE_INTERVAL_SECONDS` | `60` | How often the server finishes imports that a restart interrupted. |
| `IGNITION_MCP_PROJECT_LOCK_MAX_ENTRIES` | `32` | How many projects can hold a write lock at once. |

### Limits and timing

You rarely need to change these. Each has a maximum the server will not go past.

| Variable | Default | Maximum | Meaning |
| --- | --- | --- | --- |
| `IGNITION_MCP_GATEWAY_TIMEOUT_SECONDS` | `10` | `30` | Time limit for one Gateway request. |
| `IGNITION_MCP_TOOL_TIMEOUT_SECONDS` | `30` | `30` | Time limit for an ordinary Tool call. |
| `IGNITION_MCP_QUERY_TIMEOUT_SECONDS` | `30` | `120` | Time limit for query Tools such as `audit_query`. |
| `IGNITION_MCP_ARTIFACT_TIMEOUT_SECONDS` | `120` | `300` | Time limit for export and import Tools. |
| `IGNITION_MCP_STRUCTURED_OUTPUT_LIMIT_BYTES` | `262144` (256 KiB) | `1048576` | Largest Tool answer. A larger answer fails with `limit_exceeded` instead of being cut short. |
| `IGNITION_MCP_WATCHER_INTERVAL_SECONDS` | `60` | | How often the server re-reads the Gateway's API description. |
| `IGNITION_MCP_AUDIT_MAX_ROWS` | `50000` | | Audit records kept. |
| `IGNITION_MCP_AUDIT_MAX_AGE_DAYS` | `90` | | Age after which audit records are removed. |
| `IGNITION_MCP_OPERATION_RECORD_MAX_ROWS` | `10000` | | Call records kept for `operation_diagnose`. |
| `IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS` | `72` | | Age after which call records are removed. |
| `IGNITION_MCP_RETENTION_INTERVAL_SECONDS` | `300` | | How often old records are removed. |
| `IGNITION_MCP_RETENTION_BATCH_ROWS` | `500` | | Records removed per pass. |
| `IGNITION_MCP_STORAGE_PROBE_INTERVAL_SECONDS` | `30` | | How often the server checks its own storage. |

Stored files, called artifacts, have their own limits:

| Variable | Default | Meaning |
| --- | --- | --- |
| `IGNITION_MCP_ARTIFACT_MAX_BYTES` | 256 MiB | Largest single file. |
| `IGNITION_MCP_ARTIFACT_TOTAL_BYTES` | 1 GiB | All files together. |
| `IGNITION_MCP_ARTIFACT_MAX_COUNT` | `1000` | Number of files. |
| `IGNITION_MCP_ARTIFACT_MIN_FREE_BYTES` | 100 MiB | Free disk space the server leaves alone. |
| `IGNITION_MCP_ARTIFACT_MIN_FREE_RATIO` | `0.05` | Share of the disk the server leaves free. |
| `IGNITION_MCP_ARTIFACT_EXPORT_TTL_HOURS` | `24` | How long an export is kept. |
| `IGNITION_MCP_ARTIFACT_RECOVERY_TTL_DAYS` | `7` | How long a pre-import backup is kept. |
| `IGNITION_MCP_ARTIFACT_STAGING_DEADLINE_SECONDS` | `900` | Time limit for an unfinished upload. |
| `IGNITION_MCP_ARTIFACT_CLEANUP_INTERVAL_SECONDS` | `300` | How often expired files are removed. |
| `IGNITION_MCP_ARTIFACT_CLEANUP_BATCH` | `50` | Files removed per pass. |

### Logging

| Variable | Default | Meaning |
| --- | --- | --- |
| `IGNITION_MCP_LOG_FORMAT` | `auto` | `text`, `json`, or `auto`, which picks `json` outside the `development` profile. |
| `IGNITION_MCP_SERVICE_IDENTITY` | `ignition-rest` | The name recorded for callers when authentication is off. |

## CLI settings

The [quick start](quick-start.md) explains the wizard, the one-line form and the acceptances. The
[runbook](../operations/runbook.md) covers operating a deployment after setup.

| Flag | Commands | Meaning |
| --- | --- | --- |
| `--deployment NAME` | all | The deployment directory under `~/.config/ignition-mcp/deployments/`. Default `default`. |
| `--json` | all | Print the report as one JSON document. Never prompts. |
| `--yes` | all | Accept every named risk except the certificate and the EULA. A one-line run confirms a plan with changes through this flag. |
| `--accept-certificate` | all | Trust the Module certificate. |
| `--accept-eula` | all | Accept the Module EULA. |
| `--gateway-url URL` | `setup`, `status`, `reset` | The Gateway's web address. |
| `--gateway-token-file PATH` | `setup`, `status`, `reset` | A file holding the Gateway API key on one line, mode `0600` on Linux and macOS. `setup` saves the key it used as `gateway-token.secret` in the deployment, and every command reads that file when this flag is absent. |
| `--environment dev\|prod` | `setup` | The Deployment environment. Default `dev`. |
| `--roles LIST` | `setup` | The Assistant roles to deploy. Default `analysis,engineer` in `dev` and `analysis` in `prod`. |
| `--module-file PATH` | `setup` | The MCP Module `.modl` file. |
| `--recreate-tokens` | `setup` | Delete and recreate every managed token whose local secret file is lost. |
| `--provision-security-levels` | `setup` | In `prod`, create the roles' missing Security Levels. |
| `--dry-run` | `setup` | Show the plan and stop before any write. |
| `--bind HOST:PORT` | `start` | The address the REST server listens on. Default `127.0.0.1:8000`. |
| `--client claude\|codex\|none` | `connect` | The client to register the role with. |

## Connecting an agent

Each role has two MCP servers, and they authenticate differently. `ignition-mcp connect <role>`
writes both entries for Claude Code or Codex. To configure a client by hand, or to check what
`connect` wrote, use the settings below. Both servers use the Streamable HTTP transport.

| Server | URL | Header | Token |
| --- | --- | --- | --- |
| Runtime (`ignition-runtime-<role>`) | `<gateway-url>/data/mcp/<role>` | `X-Ignition-API-Token: <token>` | the role's Ignition API token, in `runtime-<role>.secret` |
| REST (`ignition-rest-<role>`) | `http://<bind>/mcp`, default `http://127.0.0.1:8000/mcp` | `Authorization: Bearer <token>` | the role's static token, in `rest-<role>-token.secret` |

The secret files are in the deployment directory, `~/.config/ignition-mcp/deployments/<name>/`. Each
holds one `<name>:<key>` line, and the whole line is the token.

### Runtime server

The MCP Module reads the Ignition API token from the `X-Ignition-API-Token` header only. It does not
accept `Authorization: Bearer`, so a client that offers only a bearer token setting cannot connect to
the Runtime server. Put the whole `<name>:<key>` line in the header, with no `Bearer` prefix.

Claude Code:

```bash
claude mcp add --transport http ignition-runtime-analysis \
  http://127.0.0.1:8088/data/mcp/analysis --scope user \
  --header "X-Ignition-API-Token: ignition-mcp-analysis:<key>"
```

Codex, in `~/.codex/config.toml`. `codex mcp add` cannot set a custom header, so edit the file:

```toml
[mcp_servers.ignition-runtime-analysis]
url = "http://127.0.0.1:8088/data/mcp/analysis"

[mcp_servers.ignition-runtime-analysis.http_headers]
"X-Ignition-API-Token" = "ignition-mcp-analysis:<key>"
```

A client that takes a JSON server list, such as `claude mcp add-json`:

```json
{
  "type": "http",
  "url": "http://127.0.0.1:8088/data/mcp/analysis",
  "headers": {"X-Ignition-API-Token": "ignition-mcp-analysis:<key>"}
}
```

### REST server

The REST server reads the standard `Authorization: Bearer <token>` header. Which tokens it accepts
depends on its auth mode; see [Deployment profile and authentication](#deployment-profile-and-authentication).
A deployment `setup` created uses `static-token`, with one token per role. `start` must be running.

Claude Code:

```bash
claude mcp add --transport http ignition-rest-analysis \
  http://127.0.0.1:8000/mcp --scope user \
  --header "Authorization: Bearer ignition-mcp-analysis:<key>"
```

Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.ignition-rest-analysis]
url = "http://127.0.0.1:8000/mcp"

[mcp_servers.ignition-rest-analysis.http_headers]
"Authorization" = "Bearer ignition-mcp-analysis:<key>"
```

A JSON server list:

```json
{
  "type": "http",
  "url": "http://127.0.0.1:8000/mcp",
  "headers": {"Authorization": "Bearer ignition-mcp-analysis:<key>"}
}
```

With `IGNITION_MCP_AUTH_MODE=none` the REST server needs no header, and every caller is read-only.

## Runtime Target Policy

The policy decides which Tags and alarms the Runtime write Tools may change. `ignition-mcp setup`
generates it from the Deployment environment and the roles, stores it in the deployment directory as
`runtime-policy.json`, and writes it to the Gateway's `IgnitionMCPPolicy` Tag provider. In `dev` every
Runtime Mutation Tool gets `*`; in `prod` every allowlist is empty. Agents cannot change it. The table
below describes the fields of the generated document.

| Field | Required | Meaning |
| --- | --- | --- |
| `schemaVersion` | yes | Always `1`. |
| `serviceIdentity` | yes | The name recorded as the actor in Ignition's audit log for every Runtime write. |
| `auditMode` | yes | `best_effort` records an audit entry when it can. `required` refuses to write when the audit profile is unavailable. `off` records nothing. |
| `auditProfile` | no | The Ignition audit profile to write to. Needed for `required`. |
| `allowlists` | yes | An object with one key per Tool name. Each value is a list of paths that Tool may change. |
| `alarmShelveMaxSeconds` | no | The longest shelve `alarm_shelve` accepts. At most 86400, one day. |
| `tagWriteMaxWrites` | no | Writes per `tag_write` call, 1 to 100. Default 20. |
| `tagCreateMaxItems`, `tagUpdateMaxItems`, `tagCopyMaxItems`, `tagDeleteMaxItems`, `tagMoveMaxItems`, `tagRenameMaxItems` | no | Items per call for each Tag Tool, 1 to 100. Default 20. |
| `alarmMaxPaths` | no | Alarm paths per `alarm_shelve` or `alarm_unshelve` call, 1 to 100. |

The file may be at most 32 KiB. How the allowlist entries match is explained in the
[Tool catalog](tools.md#what-every-runtime-write-tool-needs).

A policy with empty `allowlists` is valid. It keeps every Runtime write switched off, which is a safe
start.

## Server Config permissions file

The Server Config is the MCP Module's record of one MCP endpoint. Its permissions tree lists the
Ignition security levels a caller must have to use the endpoint. `ignition-mcp setup` generates the
tree from the role and keeps it in step with the Gateway, so you never write one. This is what
`setup` generates for the Analysis Assistant:

```json
{
  "type": "AllOf",
  "securityLevels": [
    {
      "name": "Authenticated",
      "children": [
        {"name": "IgnitionMcpAnalysis", "children": []}
      ]
    }
  ]
}
```

- `type` is `AllOf` or `AnyOf`.
- `securityLevels` follows Ignition's security level tree. The example requires the
  `Authenticated/IgnitionMcpAnalysis` level, which `setup` creates for the Analysis role. The
  Engineer role uses `Authenticated/IgnitionMcpEngineer`.
- The token the agent connects with must carry that level. `setup` creates one Runtime token per
  deployed role.
- A tree someone changed by hand is reported as `CHANGED`, never `NO CHANGE`, and `setup` restores
  the generated tree after confirmation.

## Named Query registry

The Runtime `database_query` Tool can only run the Named Queries listed here. Set the JSON as the
environment variable `IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON` **on the Gateway**, then restart the
Gateway. An example is in the [Tool catalog](tools.md#the-named-query-registry).

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Always `1`. |
| `entries` | Up to 100 queries. The whole value may be at most 32 KiB. |
| `entries[].alias` | The name the agent uses: lowercase letters, digits and `_`, starting with a letter, up to 64 characters. |
| `entries[].description` | Tells the agent what the query returns. Up to 512 characters. |
| `entries[].project` | The Ignition project that holds the Named Query. |
| `entries[].path` | The Named Query's path inside that project, such as `Reports/LineDowntime`. |
| `entries[].resultMode` | `dataset` for a table, `scalar` for a single value. |
| `entries[].datasourcePolicy` | Always `named-query-fixed`. The query uses the database set in the Named Query. |
| `entries[].parameters` | The values the agent may pass. Each has a `type` (`string`, `integer`, `number`, `boolean` or `datetime`), and optionally `required`, `minimum`, `maximum` and, for strings, `maxLength`. Up to 32. |
| `entries[].pagination` | How many rows come back. See below. |

`pagination` takes one of three forms:

- `{"mode": "none"}` is required for `scalar` queries.
- `{"mode": "fixed", "maxRows": 200}` returns at most `maxRows` rows, up to 2000.
- `{"mode": "offset", "limitParameter": "limit", "offsetParameter": "offset", "defaultPageSize": 50, "hardPageSize": 500, "maxOffset": 100000}`
  pages through the result. The Named Query must declare the two parameters named in
  `limitParameter` and `offsetParameter` and use them in its SQL. `hardPageSize` is at most 2000.

If the variable is missing, the registry is empty. If it is malformed, both database Tools fail with
an error instead of guessing.
