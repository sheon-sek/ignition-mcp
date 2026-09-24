# ignition-rest-mcp

This package holds two programs:

- `ignition-rest-mcp` is the `ignition-rest` MCP server. It wraps selected Ignition Gateway web API
  operations as MCP Tools, served over Streamable HTTP with FastMCP 4.
- `ignition-mcp` sets up, checks, runs and connects an Ignition MCP deployment. It installs the MCP
  Module, deploys the `ignition-runtime` bundle, starts the REST server, and registers the endpoints
  with an agent client.

To install and use them, read the guides instead of this page:

- [Quick start](../../docs/guide/quick-start.md)
- [Tool catalog](../../docs/guide/tools.md), for what each Tool needs
- [Configuration reference](../../docs/guide/configuration.md), for every setting
- [Operations runbook](../../docs/operations/runbook.md), for upgrades, the policy and exit codes

This page describes the server's behavior in detail: how it authenticates callers, stores data, and
decides the result of each write Tool. The binding rules are in `docs/decisions/`, and each section
names the decisions it follows.

## Server structure

- `server.py` registers every Tool and resource, and serves `/health/live`, `/health/ready` and
  `/metrics`.
- `capabilities/registry.py` reads the Gateway's OpenAPI document at start and on a timer, and derives
  the set of semantic capabilities the Gateway offers (D04). A Tool whose capability is missing is
  hidden from `tools/list`.
- `client/gateway.py` holds the one shared `httpx.AsyncClient`. Responses are streamed with a size
  cap, and compressed responses are refused if the Gateway ignores the request for identity encoding.
- `auth.py` and `authorization.py` verify the caller and check scopes.
- `services/` holds the Tool logic, `artifacts/` the file store, and `projects/` the project
  transaction.
- `operation.py` and `observability/` create a correlation id per call, write structured logs, and
  keep low-cardinality metrics (D18).

The caller's credential never reaches the Gateway. The Gateway only sees the deployment's own API
token.

## Authentication and scopes

The rules follow D07 and D07-A. Plain HTTP and authentication are separate choices.

### Named static tokens

`IGNITION_MCP_STATIC_TOKENS` maps a token name to `{"token": "...", "scopes": [...]}`.

- Scopes come from `ignition.read`, `ignition.config`, `ignition.control` and `ignition.admin`. No
  scope includes another.
- The token's name, never its value, identifies the caller. The audit actor, the operation-record
  actor and the artifact owner are all `static-token:<name>`.
- The server refuses to start on an unknown scope, an empty, repeated or whitespace-only scope or
  token value, a repeated JSON key, a duplicate name, two names with the same value, more than 32
  tokens, a name outside 1 to 64 characters of `[A-Za-z0-9._:-]`, or a value longer than 512
  characters.

`IGNITION_MCP_STATIC_TOKEN` is the older single-token form. It creates one token named
`trusted-internal-static-token` with `ignition.read` only. Its value is used exactly as given, so
leading and trailing spaces are part of it. An empty or whitespace-only value, or setting both
variables, is a configuration error.

### JWT

The `secured` profile requires `IGNITION_MCP_AUTH_MODE=jwt`, with `IGNITION_MCP_JWT_ISSUER`,
`IGNITION_MCP_JWT_AUDIENCE`, and exactly one of `IGNITION_MCP_JWT_JWKS_URI` (HTTPS) or
`IGNITION_MCP_JWT_PUBLIC_KEY` (PEM). The server accepts RS256 tokens with a valid signature and
expiry, and takes the scopes from the token's claims. The token's subject or client becomes the
actor. The server only verifies tokens. It is not an OAuth authorization server. FastMCP fetches and
rotates the JWKS keys. Key rotation with a live identity provider has not been tested.

### How scopes are checked

Every Tool and resource declares the scope it needs, as a `scope:<scope>` tag next to its capability
tag and as `requiredScope` in its contract. Middleware checks the scope twice:

1. It removes components the caller may not use from `tools/list` and `resources/list`.
2. It refuses a call to such a component with `permission_denied` before the handler or the Gateway
   is reached.

A refused Tool call also writes an audit `decision` row with the reason
`denied:authz-scope:missing-scope:<scope>`, the token name as actor, the Tool's effect class as
`operation_class`, and the Tool's `destructive` flag. The row shares its correlation id with the
error the caller receives. A refused resource read is logged with the same reason but writes no audit
row, because a resource error has no correlation id to share. A failed audit write never turns a
refusal into an allow.

The credential stays inside `auth.py`. It is not logged, audited, returned in an error or kept on the
verified token object. `auth=none` has no principal and only `ignition.read`, so it cannot read a
CONFIG or ADMIN component or write anything.

## Bounds

`IGNITION_MCP_GATEWAY_TIMEOUT_SECONDS`, 10 by default and 30 at most, limits the total time of one
Gateway request. Gateway JSON responses are capped at 1 MiB, and the internal OpenAPI document at
16 MiB. Tool and resource output is capped by `IGNITION_MCP_STRUCTURED_OUTPUT_LIMIT_BYTES`, 256 KiB by
default and 1 MiB at most. An oversized answer fails with `limit_exceeded` and is never cut short
(D10).

Each Tool has a budget class with its own time limit, checked at start:

| Budget class | Variable | Default | Maximum |
| --- | --- | --- | --- |
| FAST | `IGNITION_MCP_TOOL_TIMEOUT_SECONDS` | 30 | 30 |
| QUERY | `IGNITION_MCP_QUERY_TIMEOUT_SECONDS` | 30 | 120 |
| ARTIFACT | `IGNITION_MCP_ARTIFACT_TIMEOUT_SECONDS` | 120 | 300 |

## Storage and health

`IGNITION_MCP_DATA_DIR` is required in every profile. It must be an absolute, writable path, and
outside `development` it must not be under a temporary folder such as `/tmp`, `/var/tmp` or
`/dev/shm`. It holds:

- two SQLite databases in WAL mode, `state.db` and `audit.db`, with `synchronous=FULL`
- the artifact store
- the Project writer lock file

Folders get mode `0700` and files `0600`. Windows skips these mode checks and logs a warning.

When a storage subsystem fails, the server keeps running in a reduced state:

- `/health/ready` reports the failing subsystem, and `/health/live` stays live.
- The Tool list does not change.
- Audited operations fail before they dispatch anything.
- Plain reads keep working and record the failure.

Records are pruned by age and count: `IGNITION_MCP_AUDIT_MAX_ROWS` (50000),
`IGNITION_MCP_AUDIT_MAX_AGE_DAYS` (90), `IGNITION_MCP_OPERATION_RECORD_MAX_ROWS` (10000) and
`IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS` (72), in passes set by
`IGNITION_MCP_RETENTION_INTERVAL_SECONDS` (300) and `IGNITION_MCP_RETENTION_BATCH_ROWS` (500).
`IGNITION_MCP_STORAGE_PROBE_INTERVAL_SECONDS` (30) sets how often storage health is checked.

## Artifacts

Artifacts are files the server stores, such as exports and pre-import backups (D17). Their limits are
listed in the [Configuration reference](../../docs/guide/configuration.md#limits-and-timing).

- `GET /artifacts/{id}` and `HEAD /artifacts/{id}` require authentication and only serve the caller's
  own artifacts. Anyone else gets `not_found`, so the route does not reveal whether an artifact exists.
  `ignition.admin` sees all artifacts.
- Both verbs send the same `Content-Type`, `Content-Length`, a quoted strong `ETag` (the SHA-256),
  `Repr-Digest: sha-256=:...:`, a sanitized `Content-Disposition` and `Cache-Control: no-store`. The
  body streams in bounded chunks, and a client disconnect is audited.
- `POST /artifacts?kind=project_archive` uploads a project ZIP with `Content-Type: application/zip`
  and an exact `Content-Length`. Until `IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED=true`, it answers HTTP 404
  with code `operation_disabled`.

### Sensitive exports

`project_export` and `tag_config_export` also need `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true`
(D08, D17). The switch is off by default. It is checked when building the Tool list and again at call
time, where it answers `operation_disabled`. Every attempt is audited, including refusals.

## Write Tools

### The write policy

`IGNITION_MCP_CONFIG_MUTATION_ENABLED`, `IGNITION_MCP_CONTROL_MUTATION_ENABLED` and
`IGNITION_MCP_ADMIN_MUTATION_ENABLED` all default to `false`. `IGNITION_MCP_MUTATION_OPERATIONS` is a
comma-separated list of up to 100 Tool names, and `*` allows all. `IGNITION_MCP_MUTATION_TARGETS` is
a JSON object that maps each Tool name to its Target list. A Tool with no list may change nothing.

A write only accepts a `VerifiedPrincipal` that `auth.py` created from a credential it has just
verified. `jwt` takes scopes from the token claims, a named static token from its configured scopes,
and `auth=none` stays read-only. A Tool whose class is switched off is hidden from `tools/list` and
refused at call time.

### Rules shared by every write Tool

These apply to all REST write Tools unless a section below says otherwise.

- A Target outside the allowlist is `permission_denied` (D30 §7).
- The class switch is checked for the Tool list and again at call time.
- An explicit Gateway rejection is final. That covers any 4xx answer, and a 2xx answer that carries
  `success=false` with a `problem`. No read-back can turn it into a success, because a target that
  happens to show the requested values may have been changed by another writer.
- A success comes only from a claim the Gateway itself made, or from an ambiguous dispatch whose
  read-back proves the change.
- An ambiguous dispatch whose read-back only matches the intended state, where another writer could
  have made the same change, is `outcome_unknown`. An ambiguous dispatch that changed nothing is
  `not_applied`.
- Nothing is retried.

### Configuration resources

`config_resource_create`, `config_resource_update`, `config_resource_delete` and
`config_resource_rename` follow D30. They need scope `ignition.config`, class `CONFIG_MUTATION`, an
entry in the operation allowlist and an entry in the Target allowlist.

**Targets and the core collection.** A Target is `<resourceType>/<name>`, or the bare `<resourceType>`
for a singleton, always in the `core` configuration collection (D30 owner ruling 5). The Gateway
selects a resource by collection as well as by name, so every read and every write names
`collection=core`:

- Reads, deletes and renames send it as the `collection` query parameter.
- Creates and updates send it as the `collection` field of the change item, as the type's documented
  request schema declares. If a type's item schema cannot carry that field, or cannot accept `core`,
  the Tool refuses with `unsupported_capability` before dispatching. It never lets the Gateway choose
  a default.
- A caller-supplied `collection` is accepted only when it is `core`. Any other value is
  `invalid_argument` before anything is read or dispatched.

**Refused resource types.** The types in `contracts/shared/refused-resource-types.json` are refused
with `permission_denied` whatever the allowlist says. They are API tokens, security levels, security
properties and zones, user sources, identity providers, OAuth2 clients, secret providers, system
properties, the EAM license, module administration, and the MCP Module's own `server-config`. A type
the file does not classify is refused too.

**The reserved Tag provider.** The `ignition/tag-provider` resource named `IgnitionMCPPolicy` holds
the Runtime Target Policy. All four Tools refuse it with `permission_denied` before they check the
allowlist, even under `*` (D30 §5, owner ruling 4). Rules for the match:

- The whole name must match. `IgnitionMCPPolicyStaging` is a different resource.
- Case is ignored and surrounding spaces are trimmed, because Ignition documents no rule for comparing
  resource names.
- A rename checks both names, so renaming another provider to the reserved name is refused too.
- `config_resource_get` can still read it.
- The refusal reads and dispatches nothing. Its audit row gives the reason
  `denied:target-class:reserved-config-resource:ignition/tag-provider/IgnitionMCPPolicy`.
- `ignition/tag-provider` stays an allowed type, so every other Tag provider can be managed.

**`config_resource_update`** changes one resource.

- The caller passes the `expectedSignature` it read with `config_resource_get`. The Tool compares it
  with a fresh bounded read just before dispatch, then sends it to the Gateway as the native
  `signature`. A stale signature is `conflict`, and nothing is dispatched.
- The change item is validated against the Gateway's own documented `PUT` request schema (D03). The
  Tool takes that schema from the capability snapshot and turns it into a self-contained, immutable
  JSON Schema when the snapshot refreshes. A value the schema forbids is `invalid_argument`, and no
  request leaves the server. A Gateway that documents an update route without a usable request schema
  offers no update for that type.
- `allowInvalidReferences=false` is always sent and is not a parameter. The change body is at most
  262144 bytes.
- The result carries the resource as observed after the change, plus its new signature for the next
  change.

**`config_resource_create`** takes no signature (D30 §2). The target must not exist. The Tool checks
that before dispatch, and an existing target, including one created in the meantime, is `conflict`.
The item is validated against the documented `POST` request schema first. The result carries the new
resource and its signature.

**`config_resource_delete`** sends `expectedSignature` in the native `DELETE` path,
`/data/api/v1/resources/<resourceType>/{name}/{signature}`, or `/{signature}` for a singleton, so the
Gateway enforces it. A bounded read-compare before dispatch still turns a stale signature into
`conflict` without touching the resource. Success means the target is absent, reported as
`present: false`. The route's optional `confirm` flag is never sent, so a delete the Gateway says would
affect other resources is refused. The Tool is `destructive: true` (D26), and its audit rows say so.

**`config_resource_rename`** renames one resource to `newName`. The rename route takes no signature,
so the Precondition token is only a server-side read-compare. A short race window remains between that
read and the dispatch, and the Tool does not claim atomicity (D30 §2). `references=ABORT` is always
sent (D30 §4), and the body is validated against the documented rename schema. Both names are Targets
and both must be allowlisted (D30 §3). The new name must be free before dispatch, and a collision is
`conflict`. Success needs the old name vacant and the new name holding the resource, whose read-back
and signature are returned.

### `project_import`

`project_import` (D30, D16) replaces an existing project.

- It needs scope `ignition.config`, class `CONFIG_MUTATION`, an operation allowlist entry, and a
  Target allowlist entry for the project name. The name matches exactly and with case. Any other
  project is `not_found`.
- It needs the Project writer. See [Project writer](#project-writer).
- Its input is a READY `project_archive` or `project_export` artifact owned by the same caller.
- Its Precondition token is `expectedFingerprint`, a `pcf1:<64 hex>` value read from `project_export`.
  Nothing is staged, backed up or dispatched until that token equals the baseline export, called A. A
  mismatch ends the transaction as `CONFLICTED` with `conflict`.

The D16 transaction then runs:

1. Stage the candidate and fingerprint it, called B.
2. Save a durable backup.
3. Export the project again and compare it with A.
4. Import once. `overwrite` is always sent and is not a parameter.
5. Export again, called C.

`C == B` commits. `C == A` is `not_applied`. A claimed success that matches neither is
`recovery_required`. Ambiguous cases are settled by the same comparison and never re-sent. The
transaction row records `importDispatched` and a `dispatchBoundary` of `not_sent`, `refused`,
`claimed`, `attributable` or `unattributable`, so a restart finishes the transaction instead of
sending the import again.

### `tag_config_import`

`tag_config_import` (D30, D11) creates Tags from a READY `tag_config_export` artifact.

- It needs scope `ignition.config` and class `CONFIG_MUTATION`.
- The caller gives the destination `provider` and `path`. The Target is the provider-qualified
  destination, and allowlist entries match at `/` boundaries.
- The reserved `IgnitionMCPPolicy` provider is refused with `permission_denied` before the allowlist
  is checked.
- It takes no Precondition token. `collisionPolicy=Abort` is always sent, so the import only creates
  Tags. A destination that already holds a declared Tag is `conflict`, checked before dispatch and
  refused by the Gateway if one appears in the meantime.
- A UDT definition document is refused unless the destination is the `_types_` folder (D30 §6).
- The Tool re-exports the destination and compares it with the declared Tag paths. A claimed success
  counts only when every declared Tag is there. A partial import is `recovery_required`. An ambiguous
  dispatch is `outcome_unknown`, because another writer may have created the same Tags.

### `alarm_pipeline_cancel`

`alarm_pipeline_cancel` (D30, D12) stops one running Alarm Notification Pipeline instance.

- It needs scope `ignition.control` and class `CONTROL_MUTATION`.
- The Target is the exact pipeline path. Allowlist entries are exact paths or `*`, never prefixes.
- `path` is at most 512 characters and `alarmEventId` at most 128. Control characters in either are
  `invalid_argument`.
- Because a cancel leaves nothing behind to check, the Tool first reads the pipeline with the same
  bounded `alarm_pipeline_status` read it uses to verify. A read that covers every match and does not
  show the event is `not_found`, and nothing is dispatched. A read that cannot cover every match is
  `limit_exceeded`. A partial read is never used as evidence.
- Success needs the Gateway's own claim and a re-read in which the run is gone. A run that is gone
  after an ambiguous dispatch is `outcome_unknown`.
- Cancelling the pipeline does not change the alarm event itself.

### `artifact_delete`

`artifact_delete` (D30, D17) deletes a stored artifact and sends nothing to the Gateway. It is a
destructive `CONFIG_MUTATION`. There is no `DELETE /artifacts/{id}` route, so this Tool is the only way
to delete an artifact, and its visibility depends only on the class switch.

- The Target is the exact artifact id.
- Only the owner, or an `ignition.admin` caller, may delete. Anyone else gets `not_found`.
- Only a READY artifact can be deleted. A pre-import backup whose transaction is still locked is
  `conflict`, and nothing is removed.
- The store marks the row `DELETING` in the same transaction as the lock check, removes and syncs the
  file, then drops the row. The store's `reconcile` finishes the job after a crash at any point.
- The result reports `present: false`.

## Perspective

### Reads

The Perspective reads (D15) return a project's Local resources and change nothing.

- `perspective_view_list` lists a project's Local View paths, paginated with `limit` and `offset`.
- `perspective_view_get` returns one View document.
- `perspective_page_config_get` and `perspective_session_props_get` return the project's page
  configuration and session properties, or `not_found` when the project has none locally.
- The three document reads also return the project's `pcf1` fingerprint, which a later write uses as
  its Precondition token.

Views are addressed by their Logical resource path, such as `Pages/Overview`, never by an archive
path. A leading `/`, a backslash, an empty, `.` or `..` segment, a control character, or a character
Ignition refuses in a resource name is `invalid_argument` before anything is exported.

All four depend on the `project_export` capability. Each one exports the project with the same bounded
capture that `project_export` uses, reads the document from a private staging copy, and deletes the
copy. A read therefore publishes no artifact and never returns the archive. Only Local resources are
returned. A View inherited from a parent project is not in the export and answers `not_found`.

`perspective_view_validate` dispatches nothing. It takes a View document as a JSON object, requires a
`root` object with a string `type`, and applies the D10 limits: at most 1 MiB, measured on the compact
re-serialization, and at most 64 JSON levels. Unknown component types are accepted. A passing
validation does not promise that Ignition accepts the document.

Every document a Perspective read returns goes through the same `redact()` as the `config_resource_*`
reads. Fields named like `password`, `apiKey`, `accessToken`, `clientSecret` or `privateKey`, and
embedded protected credentials, come back as `<redacted>`.

### Writes

The Perspective writes (D15, D16) each replace one Local resource and run the same project
transaction as `project_import`:

- `perspective_view_upsert` and `perspective_view_delete` change one View at a Logical resource path.
- `perspective_page_config_update` and `perspective_session_props_update` replace the project's page
  configuration and session properties.

Each write copies the baseline export and changes one resource: the target entry, plus the sibling
`resource.json` an import needs when the project does not have that resource yet. Every other entry
reaches the Gateway unchanged. Each write takes `expectedFingerprint`, the `pcf1` fingerprint the
matching read returned.

- A stale fingerprint is `conflict`.
- A transaction that succeeds ends as `COMMITTED` or `NO_CHANGE`. Any other final state is a Tool error
  with the D30 §7 code. A Gateway rejection is final.
- Deleting a View the project does not define locally is `not_found`.

Three rules limit what a write may touch:

- The server first reads the project's own export to see whether the target is Local. Only when it is
  not does it walk the parent chain, at most 16 projects deep, and it refuses a deeper or looping
  chain instead of stopping early. If a parent defines the target, the write is `invalid_argument` with
  reason `inherited_resource`. So a write never creates a silent local override (D15).
- A document that contains the exact value `<redacted>`, which is what a read returns in place of a
  secret, is `invalid_argument` with reason `redacted_value`, before anything is exported.
- All four are CONFIG writes. The Target allowlist, the class switch, the `project_import` capability
  and the audit chain apply as they do to `project_import`.

## Project writer

The Project writer runs the D16 project transaction for `project_import` and the Perspective writes.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IGNITION_MCP_PROJECT_WRITER_ENABLED` | `false` | Turns it on. |
| `IGNITION_MCP_GATEWAY_ID` | none | Required when on. Up to 128 characters of `[A-Za-z0-9._:-]`. Choose one stable id per Gateway, the same on every replica that points at that Gateway. |
| `IGNITION_MCP_PROJECT_LOCK_TIMEOUT_SECONDS` | `10` | Wait for another write to the same project. |
| `IGNITION_MCP_PROJECT_LOCK_MAX_ENTRIES` | `32` | Projects locked at once. |
| `IGNITION_MCP_PROJECT_RECONCILE_INTERVAL_SECONDS` | `60` | How often interrupted transactions are finished. |
| `IGNITION_MCP_PROJECT_VERIFICATION_TIMEOUT_SECONDS` | `60` | Wait for the post-import export. |
| `IGNITION_MCP_PROJECT_DESIGNER_POLICY` | `deny` | `deny`, `warn` or `ignore` open Designer sessions on the project. Set only by the deployment, never by the caller. |

While it is on, the process holds an exclusive lock on `<data>/project-writer.lock`. That stops two
processes that share one data folder, but it cannot stop two machines. Running one Project writer per
Gateway is the operator's job. `gateway_diagnose` reports it as a limitation, and the `rest settings`
line of `ignition-mcp status` names whether the writer is on.

## The `ignition-mcp` command

`ignition-mcp` is separate from the server code (D25). It has five commands: `setup`, `status`,
`start`, `connect` and `reset`. The [quick start](../../docs/guide/quick-start.md) walks through
setup, and the [Configuration reference](../../docs/guide/configuration.md#cli-settings) lists the
flags.

Design points the other pages do not repeat:

- One engine resolves every input from a flag, the saved deployment or the wizard, in that order. It
  writes nothing until the plan is shown, every named risk is accepted and the plan is confirmed.
- The engine generates the Runtime Target Policy and each Server Config's permissions tree from the
  Deployment environment and the role, so the operator writes neither by hand.
- A command is a list of stages. A stage plans with read-only Gateway requests, and only its apply
  half may write. A write before the plan is accepted and confirmed is a bug, not an operator error.
- Each secret lives in one `*.secret` file inside the deployment directory, written with mode `0600`.
  Secrets never appear in output, logs or the one-line command the wizard prints.
- `setup` builds the Runtime bundle from the repository checkout and finds the Module file in
  `tests/fixtures/modules/` or `~/Downloads`, so the CLI does read those repository paths. It installs
  only a local Module file whose SHA-256 matches the pinned build.
- Gateway probes are bounded GET requests with redirects off: 1 MiB per JSON response, and 16 MiB for
  `/openapi.json`, which is hashed and scanned for path keys only. Capability presence comes from the
  OpenAPI path list, so write routes are never called to test them.
- Every write goes through one writer with a documented route constant per operation, never through
  `config_resource_*`.
- Gateway and MCP error bodies are shortened and scrubbed before they are shown.
- A 403 on the MCP endpoint is reported as its cause when the CLI can tell them apart: no token was
  sent, the token's Security Level does not satisfy the Server Config's permissions, or the token
  requires a secure channel and the Gateway URL is `http`.
