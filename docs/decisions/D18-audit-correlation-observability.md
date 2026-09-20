# D18 — Unified Audit, Correlation and Observability

**Status:** DECIDED

## Related amendment: D07-A — HTTP / Internal Trusted Deployment
The previously stricter D07 production transport/authentication assumption is amended.

External FastMCP and the Artifact HTTP data plane must support deployment profiles appropriate to internal OT/IT networks.

### `trusted-internal`
May use:
```text
HTTP
TLS = off
auth = none | static-token
```

Unauthenticated non-loopback HTTP is allowed only when explicitly configured for this trusted-internal profile.

Static token authentication is a valid production option for simple internal deployments.

### `secured`
May use:
```text
HTTP or HTTPS
TLS recommended across untrusted networks
JWT / OAuth / OIDC / MultiAuth / other supported authentication
```

The project must provide security capabilities without forcing public-Internet-grade infrastructure on every internal deployment.

Authentication, authorization, and TLS are separate configuration concerns.

## Observability model
Keep four concerns distinct:

```text
Correlation
Audit
Logs
Metrics
```

OpenTelemetry/distributed tracing may be supported later but is not a v1 dependency.

## Correlation ID
Every MCP Tool invocation receives one server-generated `correlationId`.

Caller IDs are optional secondary fields such as `clientRequestId`; they do not replace the authoritative server ID.

The same correlation ID flows through all internal layers for that operation:
- Tool handler;
- Gateway HTTP client;
- service layer;
- Project transaction;
- Artifact operations;
- audit;
- logs.

Do not let every layer create unrelated correlation IDs.

Recommended implementation uses a sortable globally unique ID such as ULID/UUIDv7.

## Identity separation
Keep separate:
- `correlationId` → operation;
- `transactionId` → D16 Project mutation transaction;
- `artifactId` → D17 artifact.

Do not overload one identifier for multiple purposes.

## D06 compatibility
D18 does not reintroduce a universal `{ok,result,error,meta}` response envelope.

Tool-specific structured output and D06 Tool Error semantics remain authoritative.

Correlation IDs may appear in error/diagnostic output where useful without forcing all results into one envelope.

## OperationContext
External FastMCP should create one internal operation context containing concepts such as:
- correlation ID;
- optional client request ID;
- server/tool identity;
- actor/service identity;
- start time;
- permission class;
- destructive flag.

Services receive/propagate this context.

## Actor identity
Never fabricate human identity.

If verified authentication context exists, use the verified subject/client.

In trusted-internal/no-auth mode, use an explicit configured service identity.

If the official Runtime MCP Module cannot reliably expose caller identity, Runtime audit uses a configured MCP service identity.

A caller-supplied label may be useful diagnostic metadata, but is not a verified actor.

## Audit vs logs
Audit answers:
- who/service identity;
- what operation;
- target;
- attempt/outcome;
- when;
- correlation.

Logs answer:
- how/why;
- exception details;
- upstream status;
- durations;
- implementation diagnostics.

Ordinary logging is not a substitute for Audit.

## Canonical audit vocabulary
Both servers use equivalent logical fields such as:
- timestamp;
- correlation ID;
- server;
- tool;
- actor;
- operation class;
- destructive flag;
- target type/id;
- phase;
- outcome;
- duration;
- stable error code where applicable.

“Unified” means shared semantics/vocabulary, not a mandatory shared database.

## Audit coverage
Mandatory MCP-level audit includes:
- mutation attempts;
- mutation results;
- denied mutations;
- `outcome_unknown`;
- sensitive exports;
- restricted artifact access.

Ordinary successful reads such as Tag read, Historian query, Alarm status, and approved database reads normally use metrics/logs rather than high-volume Audit records.

Sensitive reads such as full Gateway backup/export may be audited despite being HTTP GET operations.

## Safe audit fields
Do not automatically dump full request/response payloads into Audit.

Each Tool defines safe audit fields.

Never audit/log secrets such as:
- API tokens;
- Authorization headers;
- JWT/OAuth tokens;
- static MCP tokens;
- passwords/client secrets;
- private keys;
- database passwords;
- signed artifact URLs/tokens.

Avoid dumping complete large View JSON, database result sets, or bulk Tag values.

## Mutation phases
Use useful phase semantics such as:
- `decision`;
- `attempt`;
- `result`.

Denied operations may record only the denied decision.

Executed mutations normally record attempt + result.

A missing result after an attempt becomes diagnosable evidence of interruption/unknown outcome.

## External AuditSink
External FastMCP uses an `AuditSink` abstraction.

v1 default may use a lightweight durable SQLite-backed sink.

It does not require PostgreSQL/Elastic/Kafka.

Audit storage must be bounded through configurable retention/size/row policies and cleanup in bounded batches.

## Runtime audit
Runtime mutation Tools use Ignition-native audit support such as `system.util.audit`.

“Shared” here means shared audit semantics and a shared implementation pattern, **not** a mandatory shared Project Library. Actual packaging follows D25: v1 Runtime Tools are self-contained and no large shared Project Library is pre-created until its resource format is verified. A verified shared project helper may be introduced later if real repetition justifies it, but D18 does not require one.

Do not expose `system.util.audit` as a generic public `audit_write` Tool.

### Runtime audit mode
Support:
```text
best_effort   # default
required
off
```

`best_effort`:
- audit failure is logged;
- an already-successful mutation remains success.

`required`:
- mutation should fail during preflight if required audit cannot be provided.

`off`:
- allowed for development or explicitly trusted deployment;
- ordinary operational logging remains active.

Do not report a successful mutation as failed merely because a post-mutation audit write failed; doing so could cause unsafe retries.

## Ignition REST audit
Ignition Native REST mutation auditing remains valuable but does not replace MCP-level audit.

Ignition sees the Gateway API operation/service identity; MCP additionally knows semantic Tool name, caller/client context, correlation ID, transaction ID, verification outcome, etc.

## Text Resources and Prompts
Static MCP Text Resources and Prompts are not mutation surfaces, so Tool audit semantics are not forced onto them.

Resource and Prompt retrieval/generation errors may be logged for diagnostics under the ordinary logging rules; they do not create audit records unless an actual sensitive-export or restricted-artifact rule from the audit coverage list applies.

## Audit query/write public surface
v1 does not expose:
- generic `audit_write`;
- unbounded Runtime `audit_query`.

Runtime Audit query APIs that lack reliable bounded native pagination are deferred under D10.

Whether MCP-local audit records receive a bounded public query Tool is deferred to D19 diagnostics/public surface.

D19's `generic_mcp_audit_query_v1=false` therefore concerns MCP-local/generic audit browsing only. It does not revoke D02: when Ignition Native REST exposes an audit-log query, that Gateway audit query remains owned by `ignition-rest`.

## Structured logging
External production logging should support structured records, preferably JSON.

Development may use human-readable formatting.

Runtime uses Ignition Gateway logging through `system.util.getLogger` and consistent logger namespaces.

Exceptions retain root cause/stack internally while MCP errors expose stable safe error codes/messages under D06.

## Metrics
Keep metric labels low-cardinality.

Useful dimensions include:
- server;
- tool;
- outcome;
- stable error code;
- HTTP method/category.

Do not use:
- correlation ID;
- artifact ID;
- actor;
- project name;
- Tag path

as ordinary metric labels.

Metrics/Prometheus/OTLP exporters are optional integration points, not deployment prerequisites.

## Distributed tracing
OpenTelemetry/distributed tracing is deferred as an optional extension.

v1 baseline:
```text
correlationId
+ structured logs
+ bounded audit
+ low-cardinality metrics
```

```yaml
decision: D18
status: DECIDED
d07_amendment: D07-A
plain_http_production_supported: true
trusted_internal_auth_none_allowed: true
trusted_internal_static_token_allowed: true
tls_mandatory: false
correlation_id_server_generated: true
universal_response_envelope: false
runtime_audit_default: best_effort
runtime_shared_project_library_required: false
resource_prompt_tool_audit_semantics_forced: false
generic_audit_write_public: false
runtime_audit_query_v1: false
otel_required_v1: false
```

## Pre-D26 consistency amendment (C02)
D18 previously described Runtime audit as going through “shared Jython helpers”, while D25 requires v1 Runtime Tools to be self-contained until a shared Project Library resource format is verified. The Runtime-audit section now says explicitly that “shared” means shared semantics and pattern, that packaging follows D25, and that a verified shared helper is optional and may be added later. Two clarifications were added: Resource/Prompt retrieval errors are ordinary logging concerns rather than forced Tool audit records, and D19's `generic_mcp_audit_query_v1=false` does not revoke D02's Gateway Native REST audit-query ownership. Audit architecture, modes, correlation, and metrics are otherwise unchanged.
