# ignition-rest-mcp

External FastMCP capability plane for curated Ignition Native REST operations.

Phase 1 establishes the first read-only vertical slice:

- Tools: `gateway_info`, `gateway_diagnose`
- Resources: `ignition://gateway/capabilities`, `ignition://gateway/openapi-info`
- Operational HTTP: `/health/live`, `/health/ready`, `/metrics`

The server uses FastMCP 4 Streamable HTTP, one shared `httpx.AsyncClient`, a D04 immutable OpenAPI capability registry, server-generated correlation IDs, bounded responses, and low-cardinality metrics.

The caller-facing MCP trust boundary is independent from the deployment-owned Ignition API token. Caller credentials are never forwarded to the Gateway.

## Configuration

Set `IGNITION_MCP_GATEWAY_URL` and `IGNITION_MCP_GATEWAY_API_TOKEN` for the deployment-owned upstream connection. Defaults bind only `127.0.0.1:8000/mcp` in the development profile.

For internal production, explicitly set `IGNITION_MCP_DEPLOYMENT_PROFILE=trusted-internal` and choose `IGNITION_MCP_AUTH_MODE=static-token` with `IGNITION_MCP_STATIC_TOKEN`, or explicitly select `none` on a trusted network. Plain HTTP and authentication are independent choices under D07-A.

For `IGNITION_MCP_DEPLOYMENT_PROFILE=secured`, set `IGNITION_MCP_AUTH_MODE=jwt`, mandatory `IGNITION_MCP_JWT_ISSUER` and `IGNITION_MCP_JWT_AUDIENCE`, and exactly one of `IGNITION_MCP_JWT_JWKS_URI` (HTTPS) or `IGNITION_MCP_JWT_PUBLIC_KEY` (PEM). Phase 1 accepts RS256 with valid signature, expiry and `ignition.read` scope. Verified token subject/client becomes the operation actor. This is resource-server verification, not an OAuth authorization server. JWKS retrieval/rotation is delegated to FastMCP; live IdP rotation has not been integration-tested.

`IGNITION_MCP_GATEWAY_TIMEOUT_SECONDS` defaults to 10 (max 30), and bounds total request elapsed time. Upstream responses are streamed under a 1 MiB JSON ceiling (16 MiB for internal OpenAPI metadata); compressed responses are rejected if the Gateway ignores the requested identity encoding. MCP Tools/Resources default to 256 KiB via `IGNITION_MCP_STRUCTURED_OUTPUT_LIMIT_BYTES` (hard max 1 MiB). Oversize fails explicitly, never silently truncates.

Use `IGNITION_MCP_LOG_FORMAT=json` for structured logs (`auto` chooses JSON outside development). Never log upstream credentials or use a caller token as the Gateway service credential. See the [Phase 1 runbook](../../docs/development/phase-1.md) for verification and limitations.

## Phase 3: storage, artifacts, exports, diagnostics

`IGNITION_MCP_DATA_DIR` is **mandatory in every profile**: an absolute directory for the durable
SQLite stores (WAL; `state.db` + `audit.db` with `synchronous=FULL`), the artifact object store and
the project-writer lock (directories `0700`, files `0600`). Startup fails if it is unset, not
absolute, not writable, or under `/tmp`, `/var/tmp`, `/dev/shm` outside `development`. Subsystem
health degrades gracefully: `/health/ready` reports the failing subsystem while `/health/live`
stays live, the Tool inventory does not change, audited operations fail closed before dispatch and
ordinary reads keep serving while recording the failure.

**Budgets** (all finite, validated at startup): `IGNITION_MCP_TOOL_TIMEOUT_SECONDS` (FAST, 30 max),
`IGNITION_MCP_QUERY_TIMEOUT_SECONDS` (30, ≤120), `IGNITION_MCP_ARTIFACT_TIMEOUT_SECONDS` (120,
≤300), `IGNITION_MCP_AUDIT_MAX_ROWS` (50000), `IGNITION_MCP_AUDIT_MAX_AGE_DAYS` (90),
`IGNITION_MCP_OPERATION_RECORD_MAX_ROWS` (10000), `IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS`
(72), `IGNITION_MCP_RETENTION_INTERVAL_SECONDS` (300), `IGNITION_MCP_RETENTION_BATCH_ROWS` (500),
`IGNITION_MCP_STORAGE_PROBE_INTERVAL_SECONDS` (30).

**Artifact store** (D17): `IGNITION_MCP_ARTIFACT_MAX_BYTES` (256 MiB), `..._TOTAL_BYTES` (1 GiB,
used + reserved), `..._MAX_COUNT` (1000), `..._MIN_FREE_BYTES` (100 MiB), `..._MIN_FREE_RATIO`
(0.05), `..._EXPORT_TTL_HOURS` (24), `..._RECOVERY_TTL_DAYS` (7), `..._STAGING_DEADLINE_SECONDS`
(900), `..._CLEANUP_INTERVAL_SECONDS` (300), `..._CLEANUP_BATCH` (50), and
`IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED` (default false: `POST /artifacts` answers 404 until a
deployment opts in). `GET`/`HEAD /artifacts/{id}` are authenticated at the route level and
principal-scoped: non-visible records answer `not_found` (no existence oracle);
`ignition.admin` sees across principals. `Content-Type`, `Content-Length`, quoted strong `ETag`
(SHA-256), `Repr-Digest: sha-256=:...:`, sanitized `Content-Disposition` and `Cache-Control:
no-store` are identical for both verbs; egress streams in bounded chunks and a client disconnect
is audited.

**Sensitive exports** (D08/D17): `project_export` and `tag_config_export` additionally require
`IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true` (deny by default; enforced in discovery visibility
and at call time with `operation_disabled`; every attempt including denials is audited).

**Mutation policy (machinery only. Phase 3 exposes zero mutation Tools):**
`IGNITION_MCP_CONFIG_MUTATION_ENABLED` / `..._CONTROL_...` / `..._ADMIN_...` all default false;
`IGNITION_MCP_MUTATION_OPERATIONS` is a CSV allowlist (≤100 ids, `*` allowed) and
`IGNITION_MCP_MUTATION_TARGETS` a JSON object mapping operation id → target list (deny-by-default;
an operation with no target list allows nothing). Mutations accept only a `VerifiedPrincipal`
minted by `auth.py` from a just-verified credential; in Phase 3 only `jwt` deployments can carry
`ignition.config`, and `static-token`/`none` are read-only.

**Project writer** (D16, internal): `IGNITION_MCP_PROJECT_WRITER_ENABLED` (false) + mandatory
`IGNITION_MCP_GATEWAY_ID` (≤128 chars `[A-Za-z0-9._:-]`, one stable operator-chosen ID per Gateway,
identical across replicas pointing at the same Gateway) + `IGNITION_MCP_PROJECT_LOCK_TIMEOUT_SECONDS`
(10), `..._LOCK_MAX_ENTRIES` (32), `..._RECONCILE_INTERVAL_SECONDS` (60),
`..._VERIFICATION_TIMEOUT_SECONDS` (60), `IGNITION_MCP_PROJECT_DESIGNER_POLICY`
(`deny`|`warn`|`ignore`, config-only, never caller-overridable). The process holds an exclusive
flock on `<data>/project-writer.lock` while enabled: this prevents two processes sharing one data
directory but cannot prove cross-host exclusivity. **One active Project writer per Gateway
remains an operator obligation** (surfaced by `gateway_diagnose` and `setup-native doctor` as a limitation).

## Operator CLI (`setup-native`)

`ignition-mcp setup-native` detects, plans and verifies an `ignition-runtime-bundle` deployment
across its documented REST and MCP endpoints. It is code-separated from the server (D25) and ships
as the `ignition-mcp` console script. Only the read-only half of D20 exists in Phase 3:
`apply` (Phase 4) and `install-module` (Phase 6) are deliberately **not implemented**, so no
command in this group can create, update or delete anything on a Gateway. `plan` prints
intentions and always ends with the line `No changes have been applied.`

```bash
ignition-mcp setup-native doctor --bundle-manifest release/ignition-runtime-bundle-0.2.0.manifest.json \
  --bundle-zip release/ignition-runtime-bundle-0.2.0.zip --profile readonly
ignition-mcp setup-native plan   --bundle-manifest ... --server-config-name production --json
ignition-mcp setup-native verify --bundle-manifest ... --mcp-url http://127.0.0.1:8000/mcp
```

Inputs (every value comes from the manifest file or the command line; the CLI never reads
repo-relative paths such as `contracts/` or `packages/ignition-runtime-bundle/`):

| Flag | Environment fallback | Meaning |
| --- | --- | --- |
| `--bundle-manifest PATH` | none (required) | Release manifest JSON: the whole desired state, including the profile inventories and `testedTuples` |
| `--bundle-zip PATH` | none | Bundle ZIP; its SHA-256 must equal `artifact.sha256` or the run fails as a usage error |
| `--profile NAME` | none | `readonly` (default), `operator`, `configurator` or `full` inventory to check against |
| `--gateway-url URL` | `IGNITION_MCP_SETUP_GATEWAY_URL` | Gateway base URL |
| `--mcp-url URL` | `IGNITION_MCP_SETUP_MCP_URL` | Runtime MCP endpoint URL (required by `doctor` and `verify`; `plan` never touches that plane) |
| `--gateway-token-file PATH` | `IGNITION_MCP_SETUP_GATEWAY_TOKEN` | Ignition API token; the file must be a regular, non-symlink `0600` file holding one line |
| `--mcp-token-file PATH` | `IGNITION_MCP_SETUP_MCP_TOKEN` | Optional MCP endpoint token: sent as bearer; when shaped like an Ignition API token (`name:key`) also as `X-Ignition-API-Token`, the header the Gateway module endpoint authenticates |
| `--bundle-project NAME` | none | Project the bundle deploys into (default `ignition_runtime`) |
| `--server-config-name NAME` | none | Expected MCP server-config resource; presence only, never written |
| `--timeout-seconds N` | none | Per-request HTTP budget (default 10; `initialize` is bounded at 30) |
| `--allow-insecure-authorize` | none | Permit sending the API token over plain HTTP to a non-loopback Gateway |
| `--json` | none | Machine-readable report on stdout (`plan` still ends with the promise line) |

Tokens are never echoed: they stay out of reports, and any Gateway or MCP error body is truncated
and scrubbed. Plain HTTP to a non-loopback Gateway is refused until `--allow-insecure-authorize` is
passed. Gateway probes are bounded GETs with redirects disabled (1 MiB per JSON response, 16 MiB
for `/openapi.json`, which is hashed and scanned for path keys only); MCP capability presence is
read from the OpenAPI path inventory, so write routes are never called. `initialize` gets exactly
one attempt; `doctor` and `verify` diagnose and do not wait for a starting Gateway. That
readiness waiting belongs to the live harness.

Commands: `doctor` runs the ordered read-only checks (`gateway-info`, `openapi-sha256`,
`module-installed`, `capabilities.*`, `bundle-project` ownership classification,
`server-config-presence`, `mcp-initialize`, exact `inventory-tools`/`inventory-resources`/
`inventory-prompts`, `bundle-info`, `compatibility`), `plan` reports `CREATE` / `UPDATE` /
`NO CHANGE` / `BLOCKED` intentions including the D21 change class (`patch`, `minor`, `major`,
`downgrade`; `apply` needs an explicit acknowledgement for the last two), and `verify` runs the D20
acceptance sequence (reachable → `initialize` → exact inventories → `resources/read` and
`prompts/get` smokes → `bundle_info`). Compatibility is mapped deterministically from
`testedTuples` and is never upgraded: an incomplete identity or an unmatched tuple yields
`UNKNOWN`/`UNTESTED`.

Exit codes: `0` success with no `FAIL` (and no `BLOCKED` for `plan`), `1` a failed check or
transport error, `2` usage error (bad flags, rejected manifest, unreadable artifact, credential
file with a loose mode), `3` `plan` reports at least one `BLOCKED` action.
