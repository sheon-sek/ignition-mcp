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
write a config Mutation makes names `collection=core`: the reads send it as a query parameter, and this
Tool's change item carries it as the field its documented `PUT` request schema declares. A type whose
documented item schema cannot carry that field, or cannot accept `core`, has no way to address a
Target here at all: it is refused with `unsupported_capability` before anything is dispatched, never
sent for the Gateway's own default to place. A caller-supplied `collection` is accepted only when it
is `core`; any other value is `invalid_argument` before anything is read or dispatched, rather than
being resolved to a look-alike in another collection. A Target outside the allowlist is
`permission_denied` (D30 §7).
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
  against the type's documented `POST` request schema (D03) first, and it always names the core
  collection: a type whose documented item schema cannot carry that field, or cannot accept `core`, is
  refused with `unsupported_capability` before anything is dispatched (D30 owner ruling 5).
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
every read and every write names explicitly (D30 owner ruling 5) — the reads and the delete/rename
routes as their `collection` query parameter, and a create/update change item as its documented item
schema's `collection` field, which every committed type declares and accepts; a create or update whose
type cannot name `core` that way is refused with `unsupported_capability` before anything is dispatched,
because the item is the only place those two routes could carry it. A caller-supplied `collection` is
accepted only when it is `core`, and any other value is `invalid_argument` before anything is
dispatched; a Target outside the allowlist is `permission_denied` (D30 §7); and an explicit
Gateway rejection — a 4xx or a 2xx carrying `success=false` with a `problem` — is final: no read-back
may turn it into a success. A success therefore comes only from a claim the Gateway itself made; an
ambiguous dispatch whose read-back merely matches the intended state (another writer could have made
the same change) is `outcome_unknown`, and an ambiguous dispatch that changed nothing is
`not_applied`.

One resource is refused **by name inside an allowed type** (D30 §5, owner ruling 4): the
`ignition/tag-provider` resource named `IgnitionMCPPolicy` is the provider the Runtime Target Policy
lives in, and all four Tools refuse it with `permission_denied` **before** the Target allowlist is
consulted, even under an explicit `*`. It is deliberately not an entry in the Refused resource types
set: `ignition/tag-provider` stays an allowed type and every other Tag provider stays manageable. The
whole name matches, never a substring — `IgnitionMCPPolicyStaging` is a different resource — and case
is folded with surrounding whitespace ignored, fail-closed, because Ignition documents no rule for how
two config resource names compare. A rename covers **both** of its names (D30 §3), so renaming another
provider *into* the reserved name is refused as well as renaming the reserved provider away. The
refusal is Mutation-only: `config_resource_get` still serves the resource, and the denial message names
`ignition/tag-provider/IgnitionMCPPolicy`. Both refusals are decided from the Target's identity, so a
refused call reads nothing and dispatches nothing; the D18 `decision` row records the Target-class
reason (`denied:target-class:reserved-config-resource:ignition/tag-provider/IgnitionMCPPolicy`).

**`project_import`** (D30/D16, Phase 4 milestone 4c) requires scope `ignition.config`, class
`CONFIG_MUTATION`, an operation-allowlist entry and a Target-allowlist entry for the existing
Project (matched exactly and case-sensitively; anything else is `not_found`). It consumes a READY
`project_archive` or `project_export` artifact the same Mutation principal owns, and carries the
`expectedFingerprint` (`pcf1:<64 hex>`) the caller read from `project_export` as its D30 §2
Precondition token. Nothing is staged, backed up or dispatched until that token equals the D16
baseline export A; a mismatch ends the transaction `CONFLICTED` with `conflict`. The whole D16
protocol runs underneath: candidate staged and fingerprinted, durable backup, fresh pre-import
re-export compared with A, exactly-once import (the caller cannot choose `overwrite` — it is always
sent), post-import re-export C. `C == B` commits, `C == A` is `not_applied`, a claimed success that
matches neither is `recovery_required`, and the ambiguous cases are reconciled by that same
comparison and never replayed. The durable transaction row carries `importDispatched` and the
`dispatchBoundary` classification (`not_sent`/`refused`/`claimed`/`attributable`/`unattributable`),
so a restart finishes the transaction instead of re-sending an import.

**`tag_config_import`** (D30/D11, Phase 4 milestone 4c) requires the same scope and class, and
consumes a READY `tag_config_export` artifact (the JSON Tag export) plus the destination
`provider`/`path`. Its Target is the provider-qualified destination and the allowlist matches at
segment boundaries; the reserved `IgnitionMCPPolicy` provider is refused with `permission_denied`
before the allowlist is consulted, whatever the entry says. It takes **no** Precondition token:
`collisionPolicy=Abort` is always sent and never a parameter, so the import creates Tags and never
overwrites one — a destination already holding a declared Tag is a `conflict`, checked before
dispatch and refused by the Gateway inside the race window. A UDT-definition document is refused
unless the import path is itself the `_types_` subtree (D30 §6). Verification is a bounded
re-export compared with the declared Tag paths: a claimed success is confirmed only when every
declared Tag is there, a partly applied import is `recovery_required` (never a success), and an
ambiguous dispatch is `outcome_unknown` because another writer may have created the same Tags.

**`alarm_pipeline_cancel`** (D30/D12, Phase 4 milestone 4c) requires scope `ignition.control`,
class `CONTROL_MUTATION`, and an exact Alarm Notification Pipeline path (never a prefix, never
`*`) plus the Alarm Event id. Its `path` is bounded at 512 characters and its `alarmEventId` at
128, and control characters in either are `invalid_argument`. Because a cancel's post-state is
absence, the Tool establishes the pre-state with the same bounded `alarm_pipeline_status` read it
verifies with: a read that covers every match and does not name that event is `not_found` and
dispatches nothing, and a read that cannot cover every match is `limit_exceeded` — no partial read
is ever treated as evidence. A success needs the Gateway's own claim *and* a re-read in which the
run is gone; a run that is gone after an ambiguous dispatch is `outcome_unknown`. Cancelling the
pipeline never touches the Alarm Event (D12).

**`artifact_delete`** (D30/D17, Phase 4 milestone 4c) is a `CONFIG_MUTATION` that is destructive
and dispatches nothing to the Gateway — D30 drops the `DELETE /artifacts/{id}` data-plane route, so
this Tool is the only delete path and its discovery depends on the class gate alone, never on a
Gateway capability. Its Target is the exact artifact identifier the caller read, and visibility is
D17's: only the owning principal may delete, or `ignition.admin`; anything else answers `not_found`
(no existence oracle), and only a READY artifact is addressable. A RECOVERY artifact whose
transaction is still retention-locked fails with `conflict` and nothing is unlinked. The removal
commits the `DELETING` state in one transaction with the lock check, then unlinks and fsyncs, then
drops the row, so every crash split point is recovered by the store's `reconcile`; the result
reports the absence as Observed state (`present: false`).

Common rules for all four: a Target outside the allowlist is `permission_denied` (D30 §7), the
class gate is enforced in discovery and again at call time, and an explicit Gateway rejection is
final — no read-back may turn it into a success.

**Perspective reads** (D15, Phase 5) return a Project's Local resources and change nothing.
`perspective_view_list` lists the Local View paths of one Project (bounded, paginated by
`limit`/`offset`), `perspective_view_get` returns one View document, and
`perspective_page_config_get` and `perspective_session_props_get` return the Project's Page
configuration and Session properties documents, answering `not_found` when the Project has
none locally. The three document reads also return the `pcf1` Project fingerprint of the
export they were read from, which is the Precondition token a later write presents. Views are
addressed by Logical resource path (`Pages/Overview`), never by an archive path: a leading
`/`, a backslash, an empty, `.` or `..` segment, a control character, or a character Ignition
refuses in a resource name is `invalid_argument` before anything is exported. All four are
gated on the `project_export` capability, and each exports the Project through the same
bounded capture `project_export` and the D16 transaction use, reads the document from the
private staging copy, and then deletes that copy. A read therefore publishes no artifact,
never returns the archive, and leaves every other entry untouched. Reads return Local
resources only: a View the Project inherits from an ancestor is not in the export and answers
`not_found` rather than being resolved through the inheritance chain.

`perspective_view_validate` (D15) dispatches nothing. It takes the View document as a JSON
object, requires a `root` object whose `type` is a string, and applies the D10 budgets: at most
1 MiB, measured on the document's compact re-serialization, and at most 64 JSON levels.
Unknown component types are accepted, and a passing validation does not promise that an
Ignition import accepts the document.

Every document a Perspective read returns passes through the same `redact()` the
`config_resource_*` reads use, so a field named `password`, `apiKey`, `accessToken`,
`clientSecret`, `privateKey` or the like, and an embedded protected credential blob, is
reported as `<redacted>` rather than echoed.

**Perspective writes** (D15/D16, Phase 5) replace one Local resource per call and run the
same D16 Project transaction as `project_import`: `perspective_view_upsert` and
`perspective_view_delete` change one View at a Logical resource path, and
`perspective_page_config_update` and `perspective_session_props_update` replace the
Project's Page configuration and Session properties documents. Each write builds its
candidate by copying the baseline export and patching one resource: the target entry,
plus, when the Project does not hold that resource yet, the sibling `resource.json` an
import needs to keep it. Every other entry reaches the Gateway byte-identical, and each
write takes `expectedFingerprint`, the `pcf1` Project fingerprint the matching get Tool
reported. A stale token is `conflict`; a satisfied transaction is `COMMITTED` or
`NO_CHANGE`; every other D16 terminal state is a Tool error with the D30 §7 code, and a
Gateway rejection is final. A delete of a View the Project does not define locally is
`not_found`.

Three rules decide what a write may touch. Before the transaction starts, the server
reads the Project's own export to see whether the target is Local, and only when it is
not does it walk the ancestor chain (bounded at 16 Projects, refused rather than truncated
if it is deeper or loops), exporting each ancestor and refusing with `invalid_argument`
and reason `inherited_resource` if one defines the target. That walk is why a write never
creates a silent local override (D15). A document that carries the exact value
`<redacted>`, which is what a read returns in place of a secret-named field, is refused
with `invalid_argument` and reason `redacted_value` before anything is exported. All four
are CONFIG Mutations: the Target allowlist, the class gate, the `project_import`
capability and the audit chain apply exactly as they do to `project_import`.

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

`ignition-mcp setup-native` detects, plans, applies, verifies and installs the Module behind an
`ignition-runtime-bundle` deployment across its documented REST and MCP endpoints. It is
code-separated from the server (D25) and ships as the `ignition-mcp` console script. `apply` writes
the planned bundle Project, the MCP Server Config, the Runtime Target Policy and, only when the
matching flags are passed, a dedicated Runtime Security Level and a Runtime API token.
`install-module` writes the Gateway's Module plane from one trusted local `.modl`: it hash-checks
the file, uploads it, accepts its certificate and EULA only under their own flags, installs it, and
restarts the Gateway only when `--restart` says so. Neither command writes anything the operator has
not authorised, and neither downloads anything. `apply` stops on any `BLOCKED` plan line, it never
rolls back, and it ends by running the `verify` sequence; `plan` prints intentions and always ends
with the line `No changes have been applied.`

```bash
ignition-mcp setup-native doctor --bundle-manifest release/ignition-runtime-bundle-0.2.0.manifest.json \
  --bundle-zip release/ignition-runtime-bundle-0.2.0.zip --profile readonly
ignition-mcp setup-native plan   --bundle-manifest ... --server-config-name production --json
ignition-mcp setup-native apply  --bundle-manifest ... --bundle-zip ... --policy-file policy.json \
  --server-config-permissions-file permissions.json --server-config-name production \
  --provision-security-levels --create-runtime-token --runtime-token-file ~/secrets/runtime.token
ignition-mcp setup-native verify --bundle-manifest ... --mcp-url http://127.0.0.1:8000/mcp
ignition-mcp setup-native install-module --file release/MCP-module-1.3.5.2026021307-SNAPSHOT.modl \
  --sha256 "$(sha256sum release/MCP-module-1.3.5.2026021307-SNAPSHOT.modl | cut -d' ' -f1)" \
  --gateway-url https://gw:8088 --gateway-token-file ~/secrets/gateway.token
# The same command, once you have read what it shows you, and when this Gateway may come down:
#   ... --accept-certificate --accept-eula --restart
# Then run `verify` to prove the bundle deployment on top of it.
```

Inputs (every value comes from the manifest file or the command line; the CLI never reads
repo-relative paths such as `contracts/` or `packages/ignition-runtime-bundle/`):

| Flag | Environment fallback | Meaning |
| --- | --- | --- |
| `--bundle-manifest PATH` | none (required, except `install-module`) | Release manifest JSON: the whole desired state, including the profile inventories and `testedTuples` |
| `--bundle-zip PATH` | none | Bundle ZIP; its SHA-256 must equal `artifact.sha256` or the run fails as a usage error |
| `--profile NAME` | none | `readonly` (default), `operator`, `configurator` or `full` inventory to check against |
| `--gateway-url URL` | `IGNITION_MCP_SETUP_GATEWAY_URL` | Gateway base URL |
| `--mcp-url URL` | `IGNITION_MCP_SETUP_MCP_URL` | Runtime MCP endpoint URL (required by `doctor` and `verify`; they derive it from `--server-config-name` when it is absent) |
| `--gateway-token-file PATH` | `IGNITION_MCP_SETUP_GATEWAY_TOKEN` | Ignition API token; the file must be a regular, non-symlink `0600` file holding one line |
| `--mcp-token-file PATH` | `IGNITION_MCP_SETUP_MCP_TOKEN` | Optional MCP endpoint token: sent as bearer; when shaped like an Ignition API token (`name:key`) also as `X-Ignition-API-Token`, the header the Gateway module endpoint authenticates |
| `--bundle-project NAME` | none | Project the bundle deploys into (default `ignition_runtime`) |
| `--server-config-name NAME` | none | Expected MCP server-config resource: `apply` writes it, `doctor`/`verify` read it |
| `--timeout-seconds N` | none | Per-request HTTP budget (default 10; `initialize` is bounded at 30) |
| `--policy-file PATH` | none | Runtime Target Policy JSON that `apply` writes into the reserved provider (canonicalized, 32 KiB cap) |
| `--server-config-permissions-file PATH` | none | Permissions tree for a Server Config this run creates; never invented by the CLI |
| `--acknowledge-upgrade` | none | Accept a MAJOR or downgrade bundle change (`apply` refuses without it) |
| `--backup-dir PATH` | none | Export the deployed Project into this directory before `apply` overwrites it |
| `--provision-security-levels` | none | Create the dedicated Runtime Security Level for `--profile` (default name `IgnitionMcpRuntime<Profile>`, a child of `Authenticated`); an existing level is never modified |
| `--security-level-name NAME` | none | Override that Security Level's name, or name the existing one a created token should be granted |
| `--create-runtime-token` | none | Create the Runtime API token for this profile, granted exactly that Security Level; an existing token is never overwritten |
| `--runtime-token-file PATH` | none | Where the created token's secret is written: created with mode `0600`, never world-readable, never reported (required with `--create-runtime-token`) |
| `--runtime-token-name NAME` | none | The token resource's name (default: `--server-config-name`) |
| `--runtime-token-insecure-channel` | none | Create the token with `secureChannelRequired=false` (plain-HTTP lab Gateways only) |
| `--allow-insecure-authorize` | none | Permit sending the API token over plain HTTP to a non-loopback Gateway |
| `--json` | none | Machine-readable report on stdout (`plan` still ends with the promise line) |

`install-module` replaces the manifest family with its own two inputs and four switches:

| Flag | Meaning |
| --- | --- |
| `--file PATH` | The trusted local `.modl` to install. Its SHA-256 is compared before anything is sent, and its own `module.xml` supplies the module id and build |
| `--sha256 HEX` | The hash this file must have. A mismatch is a usage error (exit `2`) and no request leaves the machine |
| `--accept-certificate` | Accept the module's signing certificate. Without it the run prints subject, issuer and validity dates and stops before installing |
| `--accept-eula` | Accept the module's EULA. Without it the run prints where the EULA is read and stops before installing |
| `--acknowledge-upgrade` | Accept a module build higher than the installed one. A build *lower* than the Gateway's is always refused, and this flag does not override that |
| `--restart` | Restart the Gateway after the install, wait for it to answer again, and confirm the module id and build are served. Without it the run reports the pending restart and exits `0` |

The Gateway half is shared: `--gateway-url`, `--gateway-token-file`, `--timeout-seconds`,
`--allow-insecure-authorize` and `--json` behave as above. `install-module` reads no manifest, checks
no compatibility matrix (that is `doctor`'s report), and its restart wait is bounded at 600 s polled
every 5 s.

Tokens are never echoed: they stay out of reports, and any Gateway or MCP error body is truncated
and scrubbed. The credential `apply` creates follows the same rule — the Gateway's raw API key is
written once to the operator's `--runtime-token-file` (mode `0600`, created with `0600`) and never
appears in a report, a log or the plan text; a second run proves ownership by hashing the file's
secret and comparing it with the token hash the Gateway serves, so re-running is a `NO CHANGE` run
rather than a rotation. Plain HTTP to a non-loopback Gateway is refused until
`--allow-insecure-authorize` is passed. Gateway probes are bounded GETs with redirects disabled
(1 MiB per JSON response, 16 MiB for `/openapi.json`, which is hashed and scanned for path keys
only); MCP capability presence is read from the OpenAPI path inventory, so write routes are never
called. `initialize` gets exactly one attempt; `doctor` and `verify` diagnose and do not wait for a
starting Gateway. That readiness waiting belongs to the live harness.

Commands: `doctor` runs the ordered read-only checks (`gateway-info`, `openapi-sha256`,
`module-installed`, `capabilities.*`, `bundle-project` ownership classification,
`server-config-presence`, `mcp-initialize`, exact `inventory-tools`/`inventory-resources`/
`inventory-prompts`, `bundle-info`, `compatibility`), `plan` reports `CREATE` / `UPDATE` /
`NO CHANGE` / `BLOCKED` intentions including the D21 change class (`patch`, `minor`, `major`,
`downgrade`; `apply` needs an explicit acknowledgement for the last two), `apply` writes them in
D20's order (Security Level, credential, Project, Server Config, Runtime Target Policy), each
confirmed by a read-back, and ends by running the `verify` sequence, and `verify` runs that
acceptance sequence on its own (reachable → `initialize` → exact inventories → `resources/read`
and `prompts/get` smokes → `bundle_info`). A Server Config this run announced must end up
serving the profile's Tools: the Module resolves a Server Config's Tool list from its provider
registry when the resource is written and registers a Project's provider on the Project's own
thread, so an endpoint it built before that registration lands answers `initialize` with no
capability at all. When that happens, `apply` re-announces the same document (bounded, at most
three times, and it reports each `refreshes[]` entry) and only then judges `verify`; that is
still a `NO CHANGE` deployment afterwards. Compatibility is mapped deterministically from
`testedTuples` and is never upgraded: an incomplete identity or an unmatched tuple yields
`UNKNOWN`/`UNTESTED`.

`apply` (Phase 4, D20) observes the Gateway exactly as `plan` does, refuses to write while any line
is `BLOCKED`, then executes the printed intentions in D20's order — the opt-in Runtime Security
Level and API token (only when those flags are passed), the bundle Project (`CREATE`, or an `UPDATE`
of a MANAGED Project), the MCP Server Config for the selected profile, and the Runtime Target
Policy in the reserved `IgnitionMCPPolicy` provider. Every write goes through one curated writer
with a documented route constant per operation — never through `config_resource_*` — and the guards
run before dispatch: a name must match the CLI's grammar, a Server Config Tool list must be
explicit (never `*`), a Server Config is never written without a permissions tree, and the policy is
validated against `contracts/shared/runtime-target-policy.schema.json` and refused above the
product-enforced 32 KiB cap before anything is written. A `CREATE` writes the Server Config
disabled, reads it back and then enables it with the signature that read returned; an `UPDATE`
reconciles the Tool list in one write and preserves the observed `enabled` and every other
operator-held field. Every action is confirmed by a read-back (the policy through a `/tags/export`
read-back, repaired once if it disagrees), and the run ends by executing `verify` and embedding that
report in its own. Without `--backup-dir` there is no local copy and no rollback: the Gateway's own
configuration backup remains the operator's safety net.

Exit codes: `0` success with no `FAIL` (and no `BLOCKED` for `plan`), `1` a failed check or
transport error, `2` usage error (bad flags, rejected manifest, unreadable artifact, credential
file with a loose mode), `3` nothing written because an explicit operator acknowledgement is
missing: a `BLOCKED` plan line, an unacknowledged bundle change, or a module upgrade, certificate
or EULA this run was not told to accept.
