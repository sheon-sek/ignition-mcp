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

For internal production, explicitly set `IGNITION_MCP_DEPLOYMENT_PROFILE=trusted-internal` and choose `IGNITION_MCP_AUTH_MODE=static-token` with `IGNITION_MCP_STATIC_TOKENS`, or explicitly select `none` on a trusted network. Plain HTTP and authentication are independent choices under D07-A.

**Named static tokens** (D07 Phase 4 amendment). `IGNITION_MCP_STATIC_TOKENS` is a JSON object
mapping a token name to `{"token": "...", "scopes": [...]}`. Scopes are drawn from the four
canonical scopes (`ignition.read`, `ignition.config`, `ignition.control`, `ignition.admin`) with
**no hierarchy**, and the token's *name* — never its value — is its Mutation principal: the audit
actor, the operation-record actor and the artifact owner are `static-token:<name>`. Startup fails
closed on an unknown scope, an empty, repeated or whitespace-only scope/token value, a repeated JSON
key, a duplicate name, two names sharing one value, more than 32 tokens, a name outside 1–64
characters of `[A-Za-z0-9._:-]`, or a token value longer than 512 characters.
`IGNITION_MCP_STATIC_TOKEN` remains the single-token form: one token named
`trusted-internal-static-token` (the Phase 1–3 principal name) holding `ignition.read` only, verified
exactly as given — the value is never trimmed, so surrounding spaces are part of the credential. An
empty or whitespace-only value, or setting both variables, is a configuration error.

Every Tool and Resource declares the scope it needs — a `scope:<scope>` tag next to its capability
tag, mirrored by `requiredScope` in its contract. Authorization is centralized in middleware and
runs twice (D07): an unauthorized component is filtered out of `tools/list`/`resources/list`, and a
call to it is refused with `permission_denied` *before* the handler or the Gateway is reached. A
refused **Tool** call is also a durable audit `decision` row
(`denied:authz-scope:missing-scope:<scope>`, the token name as actor, the Tool's effect class as
`operation_class`, and the Tool's declared `destructive` flag) sharing one correlation ID with the
caller's error envelope; a refused **Resource** read is logged with the same reason but writes no
audit row, because a Resource denial carries no D06 envelope to share a correlation ID with. A
denial is never upgraded to an allow because the audit write failed.
The credential itself never leaves `auth.py`: it is not logged, not audited, not returned in an error,
and not retained on the verified token. `auth=none` has no principal and stays `ignition.read` only,
so it can neither read a CONFIG/ADMIN surface nor mutate anything.

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

**Mutation policy:** `IGNITION_MCP_CONFIG_MUTATION_ENABLED` / `..._CONTROL_...` / `..._ADMIN_...` all
default false; `IGNITION_MCP_MUTATION_OPERATIONS` is a CSV allowlist (≤100 ids, `*` allowed) and
`IGNITION_MCP_MUTATION_TARGETS` a JSON object mapping operation id → target list (deny-by-default;
an operation with no target list allows nothing). Mutations accept only a `VerifiedPrincipal`
minted by `auth.py` from a just-verified credential; `jwt` takes its scopes from the verified token
claims and a named static token from its configured scope set, while `auth=none` stays read-only.
A Tool whose class is disabled is hidden from `tools/list` as well as refused at call time.

**`config_resource_update`** (D30, Phase 4 milestone 4c) is the first REST Mutation Tool. It
requires scope `ignition.config`, class `CONFIG_MUTATION`, an operation-allowlist entry and a
Target-allowlist entry. The Target of a config change is the exact `<resourceType>/<name>`, or the
bare `<resourceType>` for a singleton, always in the **`core`** configuration collection (D30 owner
ruling 5). The Gateway selects a resource by collection as well as by name, so every read and every
write a config Mutation makes names `collection=core`: the reads, the delete and the rename routes
send it as a query parameter, and a create/update change item carries it as the field its documented
request schema declares. A caller-supplied `collection` is accepted only when it is `core`; any other
value is `invalid_argument` before anything is read or dispatched, rather than being resolved to a
look-alike in another collection. A Target outside the allowlist is `permission_denied` (D30 §7).
`*` is still required to allow everything. The caller passes the `expectedSignature` it read from
`config_resource_get`, which is
compared against a bounded re-read immediately before dispatch and then sent to the Gateway as its
native `signature` — a stale token fails with `conflict` and dispatches nothing. The change item is
validated against the target Gateway's own documented `PUT` request schema (D03), taken from the
capability snapshot and bundled into a self-contained, deeply immutable JSON Schema at refresh time
(a holder of the snapshot cannot change the rules a later write is validated against): a value the
type's schema forbids is `invalid_argument` without a request leaving the server, and a Gateway that
documents an update route without a usable request schema exposes no update for it at all. An explicit
Gateway rejection (a 4xx, or a 2xx carrying `success=false` with a `problem`) is the result of the
call — `conflict` for a signature mismatch — and no read-back can turn it into a success, because a
resource that happens to show the requested values may have been changed by another writer. A
success therefore comes only from a claim the Gateway itself made, or from a genuinely ambiguous
dispatch whose read-back establishes the change; where attribution cannot be established (an
ambiguous dispatch whose read-back merely matches, with a competing writer possible) the result is
`outcome_unknown`.
`allowInvalidReferences=false` is always sent and is not a parameter, and the change body is bounded
(262144 bytes). The result carries the resource as Observed state plus the new Resource signature for
the caller's next change. The **Refused resource types** in
`contracts/shared/refused-resource-types.json` (API tokens, security levels/properties/zones, user
sources, identity providers, OAuth2 clients, secret providers, system properties, EAM license and
module administration, and the MCP Module's own `server-config`) are refused with
`permission_denied` whatever the Target allowlist says; a resource type that is not classified is
refused too.

**`config_resource_create`, `config_resource_delete` and `config_resource_rename`** (D30, Phase 4
milestone 4c) require the same scope, class and allowlists as the update Tool, and each has its own
Precondition rule:

- `config_resource_create` takes **no** Precondition token (D30 §2). The D11 collision policy
  decides instead: the Target must be absent, which is checked against the Gateway before anything
  is dispatched, and an existing target — including one that appears in the race window — is a
  `conflict`. The published resource and its signature are the Observed state. The item is validated
  against the type's documented `POST` request schema (D03) first.
- `config_resource_delete` carries `expectedSignature` **in the native `DELETE` path**
  (`/data/api/v1/resources/<resourceType>/{name}/{signature}`, or `/{signature}` for a singleton), so
  the Gateway enforces the token itself, and a bounded read-compare before dispatch still turns a
  stale token into `conflict` without touching the resource. Its verification is the Target's
  **absence**: the result reports the Observed state a delete leaves, `present: false`. The route's
  optional `confirm` flag is **never sent**, so a caller cannot authorize a delete the Gateway says
  would affect other resources — such a delete is refused like any other rejection. This Tool is
  `destructive: true` (D26), and its audit rows say so.
- `config_resource_rename` renames one resource to `newName`. Its endpoint takes no signature, so the
  Precondition token is a **server-side read-compare** only: the race window between that read and
  the dispatch remains and no atomicity is claimed (D30 §2). `references=ABORT` is always sent (D30
  §4) and the body is validated against the documented rename schema (D03). Two names are Targets
  (D30 §3) — the resource being renamed and the one the rename produces — so **both must be
  allowlisted**. The destination must be absent before dispatch (D11); a collision is a `conflict`.
  Verification needs both names: the old one vacant *and* the new one holding the resource, whose
  read-back and signature are returned.

Common rules for all four: a Target is `<resourceType>/<name>` (or the bare `<resourceType>` for a
singleton) in the **`core`** collection, which is the only collection these Tools address and which
every read and every write names explicitly (D30 owner ruling 5); a caller-supplied `collection` is
accepted only when it is `core`, and any other value is `invalid_argument` before anything is
dispatched; a Target outside the allowlist is `permission_denied` (D30 §7); and an explicit
Gateway rejection — a 4xx or a 2xx carrying `success=false` with a `problem` — is final: no read-back
may turn it into a success. A success therefore comes only from a claim the Gateway itself made; an
ambiguous dispatch whose read-back merely matches the intended state (another writer could have made
the same change) is `outcome_unknown`, and an ambiguous dispatch that changed nothing is
`not_applied`.

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
