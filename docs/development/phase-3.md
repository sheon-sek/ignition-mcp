# Phase 3 — Artifact / Project / diagnostics / deployment foundations

Status: **PLANNED** — plan approved by Codex review (4 rounds); implementation not started.

Branch: `feature/phase-3-artifact-project-foundations`.

Base: `c64b597` (main after the Phase 2 merge `60818c6` + AGENTS.md unification).

This phase implements only D26 Phase 3 / G3. It must not absorb Phase 4+ work. Phases 0–2 (G0/G1/G2) are closed and frozen; nothing in this phase may weaken a G0–G2 guarantee or edit their evidence.

## Authority

1. D01–D28 plus recorded amendments (primarily D06, D07, D08, D10, D11, D15, D16, D17, D18, D19, D20, D21, D23, D25, D26);
2. repo-owned `contracts/` + `tooling/` + live compatibility evidence;
3. current implementation and tests;
4. this runbook.

If implementation reveals that a decided rule cannot be met as written, **stop that slice**, record the finding under "Open questions", and ask the owner. Never silently reinterpret a decision. An amendment is valid only after owner approval and only as an explicit amendment section in the affected decision file.

Frozen items this plan explicitly does **not** change:

- D07 scopes are exactly `ignition.read`, `ignition.config`, `ignition.control`, `ignition.admin`, no hierarchy. No new scope is introduced.
- D06 error taxonomy is the 14 codes in `contracts/shared/error-codes.json`. **No code is added in Phase 3.** Mappings used: failed D16 precondition → `conflict`; ambiguous dispatch → `outcome_unknown`; unknown/expired/non-READY artifact → `not_found`; deployment-disabled capability → `operation_disabled`; missing Gateway capability → `unsupported_capability`; bound exceeded → `limit_exceeded`; deadline → `timeout`; local subsystem failure → `internal_error` (safe message only). Transaction-domain states such as `CONFLICTED` are data, not error codes.
- D10 budgets from `contracts/shared/budget-classes.json` (FAST 10/30 s, QUERY 30/120 s, ARTIFACT 120/300 s, collections 100/500).

## Goal (D26)

Before any Project/Perspective overwrite or any mutation Tool is exposed, build recoverable, diagnosable infrastructure, and close G3: the full D08 + D18 safety chain exists and is proven, while **no mutation Tool is exposed in Phase 3**.

## Scope

### New public `ignition-rest` Tools (all non-mutating, scope `ignition.read`)

| Tool | Budget class | Notes |
|---|---|---|
| `artifact_list` | FAST | metadata only, D10 pagination, principal-scoped |
| `artifact_info` | FAST | metadata for one artifact ID, principal-scoped |
| `project_export` | ARTIFACT | sensitive export: deployment-gated, CONFIDENTIAL, audited; returns `ArtifactRef` + D16 fingerprint |
| `tag_config_export` | ARTIFACT | sensitive export: deployment-gated, CONFIDENTIAL, audited; D11 keeps it READ and REST-owned |
| `operation_diagnose` | FAST | exact `correlationId` only (D19) |

Sensitive-export policy (within D07/D08/D17, no new scope): `project_export` and `tag_config_export` require scope `ignition.read` **and** the deployment gate `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true` (default `false`, D08 deny-by-default; D17 permits sensitive exports to need more than ordinary read). The gate is enforced centrally in the same place as capability gating (discovery visibility via `_apply_visibility`) **and** at call time (`operation_disabled`), never only inside the handlers. Every attempt, including denials, writes audit rows.

Principal-scoped access (D17 "do not rely on an unguessable ID"; mirrors D19's same-actor rule, using the existing `ignition.admin` scope): every artifact records its owning principal. `artifact_list`, `artifact_info`, `GET`/`HEAD /artifacts/{id}` and `operation_diagnose` return only records of the same verified principal; any other principal's records require `ignition.admin`. `auth=none` (trusted-internal/development) and static-token modes are each a single trust domain (the configured service identity / the static-token client). Non-visible records answer `not_found` (no existence oracle).

### New operational HTTP (artifact data plane, D17)

- `GET /artifacts/{artifactId}` and `HEAD /artifacts/{artifactId}`.
- `POST /artifacts` — streaming ingress, **deployment-disabled by default**.

`DELETE /artifacts/{id}` and the `artifact_delete` Tool are **Phase 4** (D26 lists `artifact_delete` as a Phase 4 mutation). Phase 3 deletion happens only through bounded TTL cleanup.

### Internal machinery (no public surface)

- D18 central invocation lifecycle, `AuditSink`, operation records.
- D17 `ArtifactStore` + `LocalArtifactStore`.
- D15/D16 ZIP safety validator and Project logical fingerprint.
- D08 mutation safety chain + guarded mutation executor (the only code allowed to dispatch a Gateway write).
- D16 Project transaction machinery (writer lock, Designer-session policy, durable recovery artifact, pre-import re-export comparison, exactly-once import through the guarded executor, post-import reconcile/verification, persisted transaction records).

### Operator tooling

- Deterministic Runtime Bundle release artifacts: ZIP + manifest + SHA-256 (D21).
- `ignition-mcp setup-native doctor | plan | verify` (D20). `apply` is Phase 4, `install-module` Phase 6.
- Compatibility evidence plumbing (D21/D23).

### Inventory after Phase 3

- `ignition-rest` effective Tools = Phase 2 set ∪ {`artifact_list`, `artifact_info`, `operation_diagnose`} ∪ ({`project_export`, `tag_config_export`} only when the sensitive-export gate is on **and** the matching capability is present). `gateway_info`/`gateway_diagnose` retained. The tests enumerate the exact inventory for both gate settings.
- `ignition-rest` mutation Tools: **zero** in every configuration.
- `ignition-runtime` Tools: **unchanged** (exact 13). `bundle_info` gains the additive `bundleSourceRevision` output field and the project gets an ownership marker (slice 10), so `BUNDLE_VERSION` goes 0.1.0 → 0.2.0 (D21 MINOR).

## Explicit exclusions

Phase 3 does **not** implement or expose: any mutation Tool (`project_import`, `tag_config_import`, `artifact_delete`, `config_resource_*` writes, `alarm_pipeline_cancel`, Runtime CONTROL/CONFIG); `DELETE /artifacts/{id}`; `setup-native apply`, Security Level/token provisioning, `install-module`; any Perspective work; MCP binary Resources, Base64 bodies, `artifact_download`/`artifact_upload` Tools, URL ingress; generic/time-range audit browsing; re-enabling `alarm_status`/`alarm_journal`; any production candidate builder or generic project-resource editor (D15); promoting any tuple to `SUPPORTED`.

## Cross-cutting implementation rules

- Everything is bounded (D10): artifact size/total/count/free-disk, list page size **and offset**, filter lengths, record/audit rows and ages, cleanup batch sizes and deadlines, ZIP entry count/expanded size/ratio, lock registry size, every elapsed deadline. Exceeding a bound fails explicitly; never silent truncation.
- Binary bodies never materialize whole in memory: bounded chunks, incremental size + SHA-256, no Base64. This includes transaction intermediates A, B, A′, C.
- Filesystem/SQLite work runs off the event loop; cancellation removes staging data and never publishes an unvalidated artifact.
- Storage keys are generated opaque IDs; never derive paths from user/Gateway filenames or project names.
- Secrets never reach logs, audit, operation records, metrics, artifact metadata, error text or evidence.
- Metrics stay low-cardinality (no artifact ID, correlation ID, actor, project name, tag path labels).
- D25 layout: new domain packages under `packages/ignition-rest-mcp/src/ignition_rest_mcp/`: `invocation/` (or extend `operation.py`), `audit/`, `storage/` (shared SQLite + data-dir), `artifacts/`, `projects/`, `safety/`, `cli/setup_native/`. No `utils/`/`common/`. `client/` stays transport-only; no transaction or artifact-lifecycle logic in `GatewayClient`.
- No new runtime dependency unless unavoidable (stdlib `sqlite3`, `zipfile`, `hashlib`, `fcntl`); any addition pinned and justified in the PR.
- mypy strict, ruff clean; each slice ships focused tests (contract shape, bounds, error mapping, cancellation) per the D26 per-Tool completion order.

## Storage enablement (decided for Phase 3)

`IGNITION_MCP_DATA_DIR` becomes **mandatory in every deployment profile** (D17 local persistent store, D18 durable audit). Startup validation fails (`ConfigurationError`) if it is unset, not a directory, not writable, or under a temporary filesystem path (`/tmp`, `/var/tmp`, `/dev/shm`) in `trusted-internal`/`secured`. The artifact store, audit sink, operation records and transaction records live below it (SQLite, WAL, files `0600`, directories `0700`).

Runtime degradation: if a subsystem becomes unavailable or its database is corrupt after startup, `/health/ready` reports not-ready with the failing subsystem, `/health/live` stays live, `gateway_diagnose` reports it, and Tool inventory does **not** change (no list_changed churn). Call-time behavior: artifact Tools, exports and `operation_diagnose` fail `internal_error`; ordinary Gateway reads continue and log + count the operation-record write failure (operation records are diagnostics, not audit, for ordinary reads per D18); any audited operation (sensitive export, future mutation) fails closed **before** Gateway dispatch if its pre-dispatch audit row cannot be written. Subsystems re-probe on an interval and readiness recovers automatically. Existing G0/G1/G2 harnesses and workflow env gain `IGNITION_MCP_DATA_DIR` pointing to a runner-owned directory; that is a config-only change to keep them green.

## Slices (dependency-first; each slice ends with every validation command green)

Commit subjects follow Conventional Commits (`feat(artifacts): …`, `test(projects): …`).

### Slice 0 — Runbook, inventory freeze, contracts

1. Commit this runbook; update only the "Resume point" text of `docs/decisions/INDEX.md` to Phase 3 in progress.
2. Tool contracts `contracts/tools/rest/{artifact_list,artifact_info,project_export,tag_config_export,operation_diagnose}.contract.json` (permission READ, mutation class none, budget class per table, sensitivity and deployment gate where relevant, `outputSchema` path).
3. Output schemas `contracts/schemas/{artifact-list,artifact-info,project-export,tag-config-export,operation-diagnose}.output.schema.json`.
4. Extend `contracts/shared/artifact-ref.schema.json` **additively** (after grepping every consumer): `kind`, `filename`, `sensitivity` (`INTERNAL|CONFIDENTIAL|RESTRICTED`), `retentionClass` (`EPHEMERAL|EXPORT|RECOVERY`), `createdAt`, `expiresAt`, optional `download` (`{"path": "/artifacts/<id>"}` relative data-plane path; never a token). The owning principal is internal metadata, not part of `ArtifactRef`.
5. Extend `contracts/shared/operation-record.schema.json` additively for D19 output (start/complete timestamps, status incl. `in_progress`/`interrupted`, bounded phase summary, `transactionId`, `downstreamCorrelation`); no secrets, stack traces, raw bodies, or principal.
6. Add `contracts/shared/project-transaction-states.json` (states, terminal states, lock-release set — see slice 7) and `contracts/shared/artifact-classes.json` (sensitivity + retention classes).
7. Update `tooling/contracts/lint.py` REST inventories/expectations and tests; Runtime lists unchanged; error-code list unchanged.

### Slice 1 — Storage foundation, central invocation lifecycle, AuditSink, operation records (D18/D19)

1. `storage/`: data-dir validation, shared SQLite helpers (WAL, `busy_timeout` bounded, schema version table with forward-only migrations, integrity probe), all executed off-loop.
2. `OperationContext`: sortable UUIDv7 correlation IDs (stdlib implementation, monotonic-sort test), optional `client_request_id`, `transaction_id`, budget class, permission class, destructive flag, and a **safe principal key** (verified subject/client_id, or the configured service identity / static-token client for single-trust-domain modes). Propagate `X-Correlation-ID` as today.
3. **One central invocation lifecycle** replaces the scattered wrappers: every registered Tool — including today's bespoke `gateway_info`, `gateway_diagnose`, `config_resource_search` wrappers — goes through a single `invoke(tool, budget_class, handler)` that creates the context, writes the operation record, applies the budget-class deadline (FAST/QUERY/ARTIFACT from settings, never above the D10 hard limit), enforces the output budget, maps errors, emits metrics, and finalizes the record. A static test asserts every `@mcp.tool` handler routes through it. Output contracts of existing Tools are unchanged (their tests must still pass untouched, except for new fields that are explicitly additive).
4. Operation records: write `in_progress` at start, phases appended (bounded count), completion with outcome/error code. On startup, stale `in_progress` rows older than the maximum ARTIFACT hard deadline are marked `interrupted` (diagnosable, never re-executed). Retention (`max rows`, `max age`) is enforced by a background task in bounded batches with a deadline and never removes `in_progress` rows.
5. `audit/`: `AuditSink` protocol + `SqliteAuditSink` (`synchronous=FULL`). Canonical D18 fields; per-Tool **allowlisted** safe fields (not a denylist). Bounded retention like operation records.
6. Audit phase ordering (used by sensitive exports now and by the safety chain in slice 6):
   - `decision` row written before any Gateway call. If it cannot be written: for an allowed operation, fail closed (`internal_error`) with no dispatch; for a denial, still deny and log the audit failure.
   - `attempt` row durably committed **before** dispatch; failure ⇒ no dispatch, fail closed.
   - `result` row after verification. If it fails after a known outcome: return the true outcome (never convert a committed mutation or completed export into a failure that invites retry), mark the operation record `auditResultMissing=true`, log at error level, increment a metric. If the operation record also fails, log only. A missing `result` after an `attempt` is the D18 interruption signal surfaced by `operation_diagnose`.
7. Tests for every registration style (success, Tool error, timeout, cancellation, output-budget failure), each audit phase failing, restart with in-progress rows, retention bounds.

### Slice 2 — ArtifactStore + LocalArtifactStore (D17)

1. `ArtifactStore` protocol: `create` (async writer with declared kind/sensitivity/retention/owner and optional validator), `open_read`, `stat`, `exists`, `list` (bounded, principal filter, kind filter), `cleanup_expired`, internal `delete`, retention-lock/unlock, `reconcile`.
2. Layout under `<data>/artifacts/`: `staging/`, `objects/<2-char shard>/<id>`, metadata table in the shared SQLite DB.
3. Durable create sequence (each step's crash recovery in brackets):
   1. INSERT row `STAGING` with quota reservation (`reservedBytes` = declared size or max) and staging deadline; commit. [recover: row past deadline ⇒ delete staging file if present, delete row, release reservation]
   2. Open `staging/<id>.part` with `O_EXCL`, `0600`; stream with incremental size + SHA-256 and in-flight cap (quota rechecked as bytes arrive); `fsync` file. [same as above]
   3. Run validator (type/ZIP/JSON). Failure ⇒ unlink + delete row.
   4. UPDATE row `PUBLISHING` with final size + sha256; commit. [recover: object present in `objects/` with matching size and hash (bounded rehash) ⇒ READY; else staging file present ⇒ discard; delete row]
   5. `os.replace` staging → object; `fsync` object dir and staging dir. [recovered by step-4 rule]
   6. UPDATE row `READY`, release reservation into used bytes; commit.
   - Delete/cleanup: UPDATE `DELETING`; commit; unlink object; fsync dir; delete row. [recover: `DELETING` ⇒ finish unlink + row delete]
   - Unreferenced files in `objects/` or `staging/` with no row ⇒ removed by reconciliation. `READY` row whose object is missing ⇒ `LOST` (never listed; reported by diagnostics).
   - Reconciliation runs at startup and periodically, in bounded batches with a deadline, and before quota computation is trusted.
4. Quotas (finite defaults, validated; `0`/negative/`unlimited` rejected): max artifact bytes, max total bytes (used + reserved), max count, minimum free bytes and ratio. Checked before transfer when size is known and continuously during streaming.
5. Retention: EPHEMERAL (owning request/transaction lifetime, deleted at its end), EXPORT (default 24 h), RECOVERY (default 7 d, clock starts only when the owning transaction reaches a lock-releasing terminal state). Retention-locked artifacts are skipped by cleanup and refused by internal delete.
6. Metadata: ID, kind, state, sanitized display filename, media type, size, sha256, sensitivity, retention class, created/expires, owner principal, Gateway identity (slice 7 definition), project where relevant, correlation/transaction IDs. No secrets.
7. Reads during cleanup: `open_read` checks `READY` and opens the fd in one guarded step; an already-open fd keeps streaming after unlink (POSIX); after state leaves `READY`, new opens get `not_found`.
8. Tests: simulated crash at every numbered split point, cancellation at each await, quota exhaustion mid-stream, size/hash mismatch, orphan/`LOST` reconciliation, retention lock, concurrent creates vs quota, IDs-only paths, bounded reconcile batches.

### Slice 3 — ZIP safety + D16 Project fingerprint (D15/D16)

1. `projects/zip_safety.py`: streamed central-directory + entry validation; reject absolute paths, `..`/`.`/empty segments, backslashes, drive letters, symlinks and non-regular file types (external attrs), duplicates (exact and after NFC + casefold collision), encrypted entries, invalid UTF-8 names (entries without the UTF-8 flag must decode as ASCII), entry-count cap, per-entry and total expanded-size caps, compression-ratio cap. Never `extractall`.
2. `projects/fingerprint.py`, algorithm `project-content-v1`, output `pcf1:<64 hex>`:
   - input archive must pass `zip_safety`;
   - entries = all non-directory entries (names ending `/` are directories and excluded; a directory entry carrying data is rejected);
   - canonical path = the validated UTF-8 entry name exactly as stored (no Unicode normalization, no case folding); sort by path UTF-8 bytes ascending;
   - digest = SHA-256 over: ASCII `project-content-v1\n`, then per entry `u64be(len(path_bytes)) ‖ path_bytes ‖ u64be(uncompressed_len) ‖ uncompressed_bytes`;
   - no content normalization, nothing excluded (D16). Any future normalization requires an owner-approved amendment.
3. Tests: invariance under entry order, compression method/level, timestamps, extra fields and comments; sensitivity to a single content byte, a path change, an added/removed empty file; every rejection class with crafted fixtures; golden vector committed for reproducibility.

### Slice 4 — Artifact HTTP data plane (D17)

1. Routes next to `/health/*`. FastMCP 4.0.5 custom routes are **not** wrapped by the MCP `RequireAuthMiddleware`, so the artifact routes get explicit route-level authentication using the configured verifier, then the principal-scoped authorization rule above. `/health/*` and `/metrics` behavior is unchanged.
2. `GET`/`HEAD /artifacts/{id}`: ID syntax validated before storage access; headers identical for both: `Content-Type`, `Content-Length`, `ETag: "<sha256hex>"` (quoted strong ETag), `Repr-Digest: sha-256=:<base64>:` (RFC 9530), sanitized `Content-Disposition` (ASCII fallback + RFC 6266 `filename*`), `Cache-Control: no-store`. Streaming egress in bounded chunks; client disconnect stops the stream. Artifact *access* = any authorized `GET` or `HEAD` that returns an artifact's metadata or body. Every access to a CONFIDENTIAL or RESTRICTED artifact writes an audit row (D18 sensitive export / restricted artifact access), for both `GET` and `HEAD`; denied attempts write a `decision` row. `artifact_info`/`artifact_list` on CONFIDENTIAL/RESTRICTED artifacts are metadata-only Tool reads covered by operation records (Phase 3 produces no RESTRICTED artifacts; if one ever exists, `artifact_info` on it is audited too).
3. `POST /artifacts` (disabled unless `IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED=true`, else 404/disabled): requires kind from an allowlist (Phase 3: `project_archive` only), `Content-Type: application/zip`, `Content-Length` required and prechecked against quota, streaming with in-flight cap, ZIP safety validation before READY, CONFIDENTIAL/EXPORT retention, owner = caller principal. Returns `ArtifactRef` JSON. Nothing consumes uploads until Phase 4.
4. **Data-plane audit ordering** (these custom routes are outside the Tool invocation lifecycle, so they implement the same slice 1 ordering themselves): for an allowed `GET`/`HEAD` of a CONFIDENTIAL/RESTRICTED artifact, the access (`attempt`) audit row is committed **before** any status line, sensitive header or body byte is sent; if it cannot be written the route fails closed with `503` + a generic JSON error and no artifact metadata/data. Denials write a `decision` row; if that write fails the request is still denied (logged). After streaming, a `result` row records `completed` or `client_disconnected` + bytes sent; a failed `result` write is logged + counted and never alters the already-sent response. When the audit sink or artifact store is unhealthy, these routes return `503` for CONFIDENTIAL/RESTRICTED artifacts (INTERNAL artifacts do not require the audit row), and `/health/ready` is already not-ready per Storage enablement. The same fail-closed rule applies to any audited `artifact_info` call (RESTRICTED) — `internal_error` before returning metadata.
5. Tests: every auth mode, cross-principal denial vs admin, audit-sink failure for GET, HEAD, cross-principal denial, disconnect after streaming starts, and RESTRICTED `artifact_info`; HEAD/GET header parity, disconnect mid-download, cleanup racing an open download, upload disabled by default, oversize/lying `Content-Length`, bad ZIP classes.

### Slice 5 — Gateway streaming download + `project_export` + `tag_config_export`

1. `GatewayClient.stream_get_to(path, params, sink, limit_bytes, deadline_seconds, context)`: transport-only streamed GET feeding an async sink, byte cap, per-request timeout derived from the ARTIFACT budget (settings `IGNITION_MCP_ARTIFACT_TIMEOUT_SECONDS`, default 120, validated ≤ 300; the existing FAST Gateway timeout stays ≤ 30), identity encoding enforced.
2. Registry capabilities `project_export` (`GET /data/api/v1/projects/export/{name}`) and `tag_config_export` (`GET /data/api/v1/tags/export`); both added to the `_apply_visibility` gating map combined with the sensitive-export gate.
3. `project_export(projectName)`: nonempty, bounded length (≤ 256 UTF-8 bytes), no invented charset; path segment encoded with `quote(name, safe="")`. Stream into a CONFIDENTIAL EXPORT artifact with the ZIP-safety validator, compute `pcf1` fingerprint during/after staging via bounded streaming, return `ArtifactRef` + `fingerprint` + `projectName`. 404 → `not_found`, 403 → `permission_denied`.
4. `tag_config_export(provider, path?, recursive?, includeUdts?)`: `type` fixed to `json` (no caller-selectable XML); bounded provider/path lengths; CONFIDENTIAL EXPORT artifact; validator = well-formed JSON using an incremental parser if stdlib-feasible, otherwise a documented maximum size under which a full parse is allowed (larger fails `limit_exceeded`).
5. Audit `decision` + `attempt` before the Gateway call and `result` after, per slice 1 ordering.
6. Tests with a fake transport: byte cap, deadline, disconnect mid-body (no READY artifact), non-ZIP / invalid JSON body, fingerprint present, capability absence and gate off both hide the Tool and fail at call time, audit rows for allowed and denied calls.

### Slice 6 — D08 mutation safety chain + guarded mutation executor (G3 core)

Built **before** any code that can dispatch a Gateway write.

1. `GatewayClient.dispatch_write(...)` is a transport primitive with **no retry** that returns a typed outcome recording the dispatch boundary:
   - `NOT_SENT` — DNS/connect/TLS/pool timeout before the request started (httpx `ConnectError`, `ConnectTimeout`, `PoolTimeout`, TLS errors): known not attempted.
   - `SENT_PARTIAL` — failure while writing the body (`WriteError`, `WriteTimeout`): may have reached Ignition ⇒ ambiguous.
   - `SENT_COMPLETE_NO_RESPONSE` — body fully sent, then `ReadTimeout`/`ReadError`/`RemoteProtocolError`/deadline/cancellation: ambiguous.
   - `RESPONDED` with status + bounded parsed body: 2xx claimed success (still verified); 4xx rejected (still verified where state is observable); 5xx ambiguous.
   Only possibly-dispatched outcomes map to `outcome_unknown`; `NOT_SENT` maps to its ordinary transport error. Request bodies stream from an `ArtifactStore` reader.
2. `safety/policy.py`: deployment mutation-class enablement (`CONFIG_MUTATION`, `CONTROL_MUTATION`, `ADMIN_MUTATION`, all default disabled), per-operation allowlist, target allowlists (deny by default; empty = none; all requires explicit `*`), capability hook, precondition/concurrency hook; D07 scope mapping by effect (config → `ignition.config`, control → `ignition.control`, admin → `ignition.admin`).
3. **Principal source for mutations**: the executor accepts only a `VerifiedPrincipal` (subject/client, auth mode, scopes) that can be constructed solely by the authentication module (`auth.py`) from a credential it has just verified — never from caller-supplied strings. Enforcement: module-private construction token + structural test that no other module constructs it; a forged/duck-typed principal is rejected by the executor type check. Phase 3 scope grants per auth mode: `jwt` ⇒ scopes from the verified token claims (so `ignition.config` is possible only via a signed token issued by the deployment's trusted issuer); `static-token` and `auth=none` ⇒ `ignition.read` only (current behavior), so every mutation from those modes is denied at the authz layer. How static-token / `auth=none` deployments may be granted mutation scopes is **deferred to Phase 4** and must be decided with the owner then (recorded under Open questions). No public entry point produces a mutation principal in Phase 3; tests and the live harness obtain one by presenting a JWT signed with a key the harness configured as the server's JWT public key.
4. `safety/executor.py` — the guarded mutation executor, the **only** caller of `dispatch_write`: input validation → authn → authz scope → deployment class → operation allowlist → target allowlist → capability → precondition/concurrency → audit `decision` → audit `attempt` → dispatch exactly once → bounded verification (attempts/time/cancellation) → audit `result`. Denials audit only `decision`. No automatic replay anywhere.
5. Structural tests (AST/import scan) that fail if any module other than `safety/executor.py` references `dispatch_write`, if any registered Tool declares a mutation class, or if any internal service performs a non-GET Gateway request outside the executor. A test asserts the Phase 3 effective inventories contain zero mutation Tools.
6. Tests: missing principal, READ-only principal, forged principal, static-token and `auth=none` principals all denied before dispatch; each deny layer independently, empty vs `*` allowlists, no single-flag bypass, each dispatch-boundary outcome (before dispatch, during body, after full body, while reading response), cancellation at each point, verification timeout ⇒ `outcome_unknown` only for possibly-dispatched outcomes (a `NOT_SENT` + verification timeout stays a known non-attempt), audit phase failures per slice 1, no retry under any failure.

### Slice 7 — D16 Project transaction machinery (internal, via the guarded executor only)

1. **Gateway identity**: `IGNITION_MCP_GATEWAY_ID` — explicit, nonempty, bounded (≤ 128 chars, `[A-Za-z0-9._:-]`), stable operator-chosen identifier. **Required** whenever `IGNITION_MCP_PROJECT_WRITER_ENABLED=true` (startup fails without it). In read-only deployments, when unset, artifacts/diagnostics record a derived display value (normalized Gateway base URL) marked `derived`, which is never used as a lock key. The environment fingerprint is never a lock key. README guidance: one ID per Gateway, identical across all replicas that point at the same Gateway. Config tests cover writer-enabled-without-ID and invalid IDs.
2. **Project name canonicalization**: the name must exactly match (case-sensitive) an entry from a bounded `project_list` lookup at transaction start; no case folding.
3. **Writer lock**: per-(Gateway ID, project) `asyncio.Lock` registry with bounded acquire timeout (`conflict` on contention timeout), a cap on concurrently locked projects (`limit_exceeded`), and removal of idle entries. Process-level single-writer guard: an exclusive non-blocking `fcntl.flock` on `<data>/project-writer.lock` while `IGNITION_MCP_PROJECT_WRITER_ENABLED=true` (default `false`; startup fails if the lock is held). This prevents two processes sharing one data dir; it cannot prove cross-host exclusivity, so D16's "one active Project writer per Gateway" remains an operator obligation, documented in the README and surfaced by `gateway_diagnose`/`setup-native doctor` as a limitation.
4. **Designer-session policy** `deny|warn|ignore` from config only (default `deny`, not caller-overridable): bounded paginated `GET /data/api/v1/designers`, filtered to the project; capability-gated; unknown shape fails closed.
5. **Candidate builder contract**: `async build(baseline: ArtifactReader, out: ArtifactWriter) -> None` — reads A only as a bounded stream from the store and writes B only through a store-managed EPHEMERAL writer (validator = ZIP safety); it can never hold whole archives. Phase 3 ships **no** production builder; the only builders live in tests and `tests/harness/phase3-live/` (D15: no generic resource editor).
6. **State machine** (states persisted with timestamps in a transaction table):
   `PREPARING → BASELINE_CAPTURED → CANDIDATE_VALIDATED → BACKUP_PERSISTED → CONCURRENCY_VERIFIED → IMPORT_SENT → VERIFYING → COMMITTED`
   Terminal: `COMMITTED`, `NO_CHANGE`, `CONFLICTED`, `FAILED_PRE_IMPORT`, `NOT_APPLIED`, `OUTCOME_UNKNOWN`, `RECOVERY_REQUIRED`.
   Order (D16): lock → Designer policy → export A (EPHEMERAL, fingerprint A) → build B → `fingerprint(B)==fingerprint(A)` ⇒ `NO_CHANGE` (no backup, no import) → validate B → promote A to RECOVERY + retention lock (failure ⇒ `FAILED_PRE_IMPORT`, abort) → export A′ → `fingerprint(A′)≠fingerprint(A)` ⇒ `CONFLICTED`, `importAttempted=false`, error `conflict` → import B once via the guarded executor (`POST /data/api/v1/projects/import/{name}?overwrite=true`) → export C → reconcile:
   - `C == B` ⇒ `COMMITTED` (after an ambiguous or rejected dispatch this is "recovered success", recorded as such);
   - `C == A` ⇒ `NOT_APPLIED` (normal 4xx rejection or ambiguous dispatch that did not land);
   - otherwise, or C cannot be exported within budget ⇒ `OUTCOME_UNKNOWN` (dispatch was ambiguous) or `RECOVERY_REQUIRED` (dispatch claimed success but state is wrong).
   - a `NOT_SENT` dispatch is a known non-attempt (the audit `attempt` row is written before dispatch and is not evidence that execution occurred): the transaction ends `NOT_APPLIED` with `importDispatched=false` and the ordinary transport error, **never** `OUTCOME_UNKNOWN`. A diagnostic export C is still attempted; if C ≠ A, the record additionally reports `externalDriftDetected=true` (diagnostic only, recovery lock released as for `NOT_APPLIED`).
   - `OUTCOME_UNKNOWN` is reachable **only** from possibly-dispatched outcomes (`SENT_PARTIAL`, `SENT_COMPLETE_NO_RESPONSE`, 5xx).
7. **Recovery-lock release set**: released (7-day RECOVERY TTL starts) on `COMMITTED`, `NOT_APPLIED`, `CONFLICTED`, `FAILED_PRE_IMPORT`; `NO_CHANGE` never created one. Kept locked on `OUTCOME_UNKNOWN`, `RECOVERY_REQUIRED` and any non-terminal state. On `OUTCOME_UNKNOWN`/`RECOVERY_REQUIRED`, B and C are also promoted to locked RECOVERY for diagnosis; otherwise A′, B, C (EPHEMERAL) are deleted when the transaction ends.
8. **Restart recovery**: non-terminal rows before `IMPORT_SENT` ⇒ `FAILED_PRE_IMPORT` (interrupted; nothing was dispatched). Rows in `IMPORT_SENT`/`VERIFYING` may have dispatched, so a bounded background reconciler (only when the project writer is enabled, bounded batch + ARTIFACT deadline per transaction) reacquires the (Gateway ID, project) writer lock and performs the D16 read-only reconciliation — export C, then `C == B` ⇒ `COMMITTED` (recovered), `C == A` ⇒ `NOT_APPLIED`, claimed-success response recorded but `C` matches neither ⇒ `RECOVERY_REQUIRED`, otherwise or C unobtainable ⇒ `OUTCOME_UNKNOWN`. Reconciliation is a bounded export, never a re-import; the import is never replayed. Locks and EPHEMERAL artifacts are cleaned per the release set.
9. No automatic rollback, merge or replay (D16).
10. Tests with a fake Gateway for every branch: no-op, commit, backup failure abort, conflict, each dispatch-boundary outcome reconciled to COMMITTED/NOT_APPLIED/OUTCOME_UNKNOWN, verification mismatch ⇒ RECOVERY_REQUIRED, lock contention/timeout and registry cap, flock held, Designer deny/warn/ignore, cancellation and simulated restart at every persisted state (including restart reconciliation for each C branch: C==B, C==A, mismatch after claimed success, and C unavailable), intermediate artifact cleanup and retention locks.

### Slice 8 — `artifact_list`, `artifact_info`, `operation_diagnose`, diagnostics integration

1. `artifact_list(kind?, limit, offset)`: `limit` default 100, hard 500; `offset` ≥ 0 and bounded (≤ max artifact count); `kind` from the enum; READY artifacts visible to the principal only; D10 page metadata and `nextOffset`.
2. `artifact_info(artifactId)`: ID syntax validated; `not_found` for unknown/expired/non-READY/not-visible.
3. `operation_diagnose(correlationId)`: UUIDv7 syntax validated; exact match; principal rule above; returns tool, start/completion timestamps, status/outcome, bounded phase summary, `transactionId`, stable error code, downstream correlation, `auditResultMissing` when set. No secrets, stack traces, raw bodies or logs.
4. `gateway_diagnose`: additive optional fields for data dir, artifact store, audit sink, operation records, project-writer enablement and single-writer limitation; output schema updated additively. `/health/ready` reflects subsystem health (see Storage enablement).
5. Metrics: artifact bytes/count gauges, cleanup/reconcile counters, audit/record write-failure counters, transaction terminal-state counters — low-cardinality only.

### Slice 9 — Compatibility evidence schema + deterministic Runtime Bundle release artifacts (D21/D23)

0. First, `tooling/compat/`: evidence-row JSON Schema, a parser, and a validator CLI (`python -m tooling.compat validate --evidence-dir tests/compatibility/evidence`) that rejects any `SUPPORTED` in evidence or manifests during Phase 3 and enforces the D27 exact-tuple rules. Existing G0–G2 evidence is validated read-only and never rewritten; if it does not fit the schema, the schema accepts a documented legacy form. Live evidence *generation* stays in slice 11.
0b. **Single Runtime bundle identity source** (D21). `packages/ignition-runtime-bundle/BUNDLE_VERSION` is the only authoritative version. The native validator fails unless the `bundle_info` handler's `bundleVersion` literal and the project ownership marker (slice 10 step 1, implemented in this slice) both equal it. D21 also requires `bundle_info` to return source revision/build info, which the current contract lacks: add an **optional** `bundleSourceRevision` output property (40-hex git SHA, or the literal `UNSTAMPED`) to `contracts/schemas/bundle-info.output.schema.json` (not in `required`, so the contract change is additive; the 0.2.0 handler always emits it and the G3 live smoke asserts its presence; legacy 0.1.0 evidence remains valid against the unchanged required set), re-run `tooling.native.sync_schemas` so the published Text Resource matches, and update the handler to return a placeholder token `__BUNDLE_SOURCE_REVISION__`. Stamping is deterministic and build-time: `release --source-revision <sha>` replaces the token with the SHA; `build --output` (used by existing harnesses) stamps `UNSTAMPED` unless `--source-revision` is given; the validator rejects a ZIP that still contains the raw token, substitution happens in the common final-archive construction path (`tooling.native.archive.build_project`, used by CLI `build`, `release` and direct library callers alike; a `source_revision` argument defaulting to `UNSTAMPED`), the source tree is never modified, the handler must contain the placeholder exactly once (source validation accepts it; final-archive validation rejects it), tests prove CLI build, release and direct `build_project` output contain `UNSTAMPED`/the requested SHA and never the raw token, and the final **stamped** ZIP is what gets validated (unpacked, D23 L2), hashed, recorded in the manifest, and deployed. D21 classification: a new output field plus the marker is a **MINOR** change → `BUNDLE_VERSION` 0.1.0 → 0.2.0 (0.x rules; additive, no breaking change). Existing G1/G2 probes must keep passing on the branch head against the updated schema; the G3 harness smoke-calls `bundle_info` over real MCP and asserts `bundleVersion` == manifest and `bundleSourceRevision` == the stamped SHA. setup-native doctor/verify compare `bundle_info` (version + source revision) with the selected manifest and the `--bundle-zip` SHA-256; any mismatch fails verification.
1. New CLI command (existing `validate` and `build --output` keep their interface for harness compatibility; `build` only gains the optional `--source-revision` and `UNSTAMPED` default stamping from step 0b):
   `python -m tooling.native.cli release --project-dir packages/ignition-runtime-bundle/project --out-dir dist --source-revision <40-hex git SHA> --evidence-dir tests/compatibility/evidence`
2. Outputs (atomic temp-then-`os.replace`, overwriting only files it owns): `ignition-runtime-bundle-<bundleVersion>.zip` (existing deterministic builder), `…manifest.json`, `…sha256` (`<hex>␠␠<zip filename>\n`, `sha256sum -c` compatible).
3. Manifest (sorted keys, no timestamps, UTF-8, trailing newline): `schemaVersion`, `bundleVersion` (from `BUNDLE_VERSION`), `artifact` {filename, sha256, size}, `sourceRevision` (explicit arg, validated), `resourceSchemaVersion` (new source file `packages/ignition-runtime-bundle/RESOURCE_SCHEMA_VERSION`, starting at `1`), `nativeResponseBindingStatus`, per-tool requirements (from Runtime contracts), Tool/Resource/Prompt inventories (from source, cross-checked against `contracts/`), **profile inventories** (tools/resources/prompts per profile from `contracts/profiles/*.yaml`, validated), and `testedTuples` generated **only** from evidence that passes the slice 9 step 0 validator, which `release` invokes itself before building the manifest (never caller-authored).
4. Manifest JSON Schema under `contracts/` (e.g. `contracts/shared/bundle-manifest.schema.json`); the release command validates its own output.
5. CI: `release` twice from a clean checkout into two dirs and `cmp` all three files; unpack the ZIP and re-run structural validation on the unpacked tree (D23 L2). Update `.github/workflows/ci.yml` and the Commands section of `AGENTS.md`.

### Slice 10 — setup-native doctor / plan / verify (D20/D21)

1. **Managed-project ownership marker** (source change implemented in slice 9 step 0b; consumed here) (D20 `managed_project_precondition`, no blind takeover). The marker lives in an existing Designer project field, not a new resource inside the ZIP (D21 forbids inventing an unverified manifest resource): `project.json` `description` gains a trailing machine-readable line `ignition-mcp-managed: product=ignition-runtime-bundle; bundle=<bundleVersion>`. The native validator enforces it, `release` checks it against `BUNDLE_VERSION`, covered by the MINOR bump in slice 9 step 0b; implement the marker together with that step so a single version bump covers both. doctor observes it via `GET /data/api/v1/projects/find/{name}` (capability-gated). Classification: marker present and well-formed ⇒ `MANAGED` (with its bundleVersion); name present but no marker ⇒ `UNMANAGED_SAME_NAME`; marker present but malformed or product mismatch ⇒ `MARKER_INVALID`; absent ⇒ `ABSENT`. `plan` emits `BLOCKED: refuse takeover of unmanaged project <name>` (and nonzero exit) for `UNMANAGED_SAME_NAME`/`MARKER_INVALID`, `CREATE` for `ABSENT`, `UPDATE` (with D21 upgrade class) or `NO CHANGE` only for `MANAGED`. The first live G3 run must confirm the Gateway round-trips `description` through import and `projects/find`; if it does not, stop and record an Open question rather than choosing another marker silently.
2. `cli/setup_native/` plus console script `ignition-mcp` (`ignition-mcp setup-native doctor|plan|verify`), code-separated from the server (D25). Inputs: `--bundle-manifest <path>` (required) and `--bundle-zip <path>` (plan/verify hash check), `--profile` (default `readonly`), Gateway URL, Runtime MCP endpoint URL; tokens from env or a `0600` file, never echoed or logged. The CLI never reads repo-relative paths (`contracts/`, `packages/ignition-runtime-bundle/`); everything comes from the manifest.
3. `doctor` (read-only): Gateway reachable/version, `/openapi.json`, MCP Module installed/version/build/health, capabilities (server-config resource, project import, security level, API token), bundle project presence + standalone/`inheritable=false`, expected server config, MCP initialize, exact `tools/list`/`resources/list`/`prompts/list` vs the manifest profile inventory, `bundle_info`, tuple compatibility from `testedTuples` with a deterministic D21 mapping: any of gatewayVersion / mcpModuleVersion / mcpModuleBuild / bundleVersion missing or unparseable ⇒ `UNKNOWN`; complete exact tuple with a matching evidence row ⇒ that row's recorded status (never upgraded; Phase 3 evidence cannot carry `SUPPORTED`); complete exact tuple with no evidence row ⇒ `UNTESTED`; a row recording known incompatibility ⇒ `INCOMPATIBLE`, and the project-writer single-instance limitation.
4. `plan` (read-only): declarative lines from the closed vocabulary `CREATE | UPDATE | NO CHANGE | BLOCKED` (text form `<ACTION> <kind> <name>: <reason>`; `--json` form `{"actions":[{"action","kind","name","reason"}],"applied":false}`; exit code 0 when no action is BLOCKED, 3 when any is BLOCKED, 1 on doctor-level errors) for bundle project, server config (explicit Tool inventory, never `*`), security level and runtime token (detect only), D21 upgrade class (patch/minor/major/downgrade) from bundle versions; always ends with `No changes have been applied.`
5. `verify`: D20 sequence — endpoint reachable → initialize → exact tools/resources/prompts (superset or subset = failure) → `resources/read` smoke where present → `prompts/get` smoke where present → `bundle_info`; JSON report + documented exit codes.
6. Tests: fake Gateway + fake MCP endpoint (managed / unmanaged same-name / malformed marker / version-upgrade plan cases, inventory superset/subset, missing module, UNKNOWN (incomplete identity) vs UNTESTED (complete tuple, no evidence) mapping, plan NO CHANGE vs CREATE, secrets never in output); plus an installed-wheel test (`uv build`, install into a temp venv outside the checkout, run doctor/plan/verify against the fakes) proving no monorepo path dependency.

### Slice 11 — Compatibility evidence plumbing + G3 live harness

1. `tooling/compat/` evidence **generator** (schema/validator already exist from slice 9) used by harnesses to write schema-valid `tests/compatibility/evidence/g3-<gateway>-mcp-<build>/`.
2. `tests/harness/phase3-live/` reusing the G2 fixture approach: CI-owned ephemeral official images (8.3.8 required row, 8.3.9 candidate row), checksum-pinned MCP Module, exact module whitelist, bounded diagnostics with `timeout` + detached stdin, symlink-pruned log copies, unconditional teardown (Phase 2 lessons). Deploy the exact `release`-built ZIP and record its SHA-256 + manifest. Harness provisioning remains test-only (not a second installer).
3. **L5 mutation-safety guards** (D23): workflow job uses the GitHub environment `phase3-live` (created without protection rules by owner decision, see Open questions); Gateway endpoints restricted to localhost / the compose network; before any import, the harness verifies an explicit CI marker and expected Gateway identity (e.g. marker written during provisioning + gateway-info identity match) and a run-unique disposable project name (`mcp_g3_<run_id>_<attempt>`); any missing guard fails closed before the import.
4. Live checks:
   - `setup-native doctor` and `verify` pass; `plan` reports only NO CHANGE for provisioned items.
   - `project_export` (gate on) → READY artifact; `GET` body SHA-256 == metadata == `ETag`; `HEAD` parity; `artifact_list`/`artifact_info` see it.
   - Fingerprint stability: export twice with no change ⇒ identical `pcf1`. An unstable row is recorded honestly and marks Project mutation as not concurrency-safe on that row (no ad-hoc normalization).
   - `tag_config_export` of the fixture provider/path ⇒ JSON artifact.
   - `operation_diagnose` for a prior call's correlation ID (same principal) ⇒ record; unknown ID ⇒ `not_found`.
   - Transaction machinery driven by an in-process harness driver through the internal service and guarded executor, with the server configured for `jwt` auth using a harness-generated key pair, `CONFIG_MUTATION` enabled, and operation/target allowlists naming only the project-import operation and the disposable project. Authn/authz layers are proven live first: an invalid JWT, a JWT with only `ignition.read`, and a valid `ignition.config` JWT targeting a non-allowlisted project are each denied before dispatch (audit `decision` rows present, Gateway project unchanged). Then with a valid `ignition.config` JWT: `NO_CHANGE`; normal edit ⇒ `COMMITTED` verified by re-export; external change injected between A and A′ ⇒ `CONFLICTED` with no import; recovery artifact persisted + retention-locked; audit `decision/attempt/result` rows present.
   - Exact REST inventory for gate on and gate off (zero mutation Tools); unchanged exact 13-Tool Runtime inventory.
5. Workflow `.github/workflows/phase3-live-g3.yml` via `pull_request` with the trusted-repo guard (feature-branch workflows cannot be dispatched) **and** the protected environment. Evidence persisted under `tests/compatibility/evidence/`.
6. Owner prerequisite: create the `phase3-live` environment in the GitHub repository settings before the first live run. **Done 2026-09-21** (verified via `gh api repos/sheon-sek/ignition-mcp/environments/phase3-live`: exists; `protection_rules: []`, `deployment_branch_policy: null`, `can_admins_bypass: true`).

### Slice 12 — G3 close

1. Update this runbook with results, evidence links, run IDs, ZIP SHA-256, lessons.
2. Update package README (every new `IGNITION_MCP_*` variable, single-writer obligation, artifact data plane), bundle README (release artifacts), `AGENTS.md` Commands if changed, `docs/decisions/INDEX.md` resume point.
3. Open the PR (behavior + contract impact, decisions referenced, validation commands, live evidence). Do not merge without the owner.

## Configuration added (finite, validated, fail-closed)

`IGNITION_MCP_DATA_DIR` (mandatory), `IGNITION_MCP_ARTIFACT_TIMEOUT_SECONDS` (120, ≤300), `IGNITION_MCP_QUERY_TIMEOUT_SECONDS` if needed (30, ≤120), `IGNITION_MCP_ARTIFACT_MAX_BYTES`, `…_TOTAL_BYTES`, `…_MAX_COUNT`, `…_MIN_FREE_BYTES`, `…_MIN_FREE_RATIO`, `…_EXPORT_TTL_HOURS` (24), `…_RECOVERY_TTL_DAYS` (7), `…_STAGING_DEADLINE_SECONDS`, `…_CLEANUP_INTERVAL_SECONDS`, `…_CLEANUP_BATCH`, `IGNITION_MCP_ARTIFACT_UPLOAD_ENABLED` (false), `IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED` (false), `IGNITION_MCP_AUDIT_MAX_ROWS`, `IGNITION_MCP_AUDIT_MAX_AGE_DAYS`, `IGNITION_MCP_OPERATION_RECORD_MAX_ROWS`, `IGNITION_MCP_OPERATION_RECORD_MAX_AGE_HOURS`, `IGNITION_MCP_GATEWAY_ID`, `IGNITION_MCP_PROJECT_WRITER_ENABLED` (false), `IGNITION_MCP_PROJECT_LOCK_TIMEOUT_SECONDS`, `IGNITION_MCP_PROJECT_LOCK_MAX`, `IGNITION_MCP_DESIGNER_SESSION_POLICY` (`deny`), `IGNITION_MCP_MUTATION_CLASSES` (empty), operation/target allowlist variables (empty = none; `*` explicit). Exact names may be adjusted within slices 1–7 but must be documented in the package README and validated in `config.py`.

## Validation commands

Every slice:

```bash
uv lock --check
uvx --from ruff==0.16.8 ruff check .
uv run --locked --package ignition-rest-mcp --with mypy==2.3.1 mypy packages/ignition-rest-mcp/src tooling
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling packages/ignition-rest-mcp/tests
uv run --no-sync python -m tooling.contracts.lint
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli build --project-dir packages/ignition-runtime-bundle/project --output dist/runtime.zip
uv build --package ignition-rest-mcp
```

From slice 9 onward also:

```bash
uv run --no-sync python -m tooling.compat validate --evidence-dir tests/compatibility/evidence
uv run --no-sync python -m tooling.native.cli release --project-dir packages/ignition-runtime-bundle/project --out-dir dist/release-a --source-revision "$(git rev-parse HEAD)" --evidence-dir tests/compatibility/evidence
uv run --no-sync python -m tooling.native.cli release --project-dir packages/ignition-runtime-bundle/project --out-dir dist/release-b --source-revision "$(git rev-parse HEAD)" --evidence-dir tests/compatibility/evidence
for f in dist/release-a/*; do cmp "$f" "dist/release-b/$(basename "$f")"; done
```

Live harness per slice 11.

## G3 acceptance

G3 closes only when all are true:

- D08 + D18 chain implemented and unit-proven layer by layer: authn/authz ∩ deployment mutation-class enablement ∩ operation allowlist ∩ target allowlist ∩ capability ∩ precondition/concurrency ∩ exactly-once dispatch ∩ bounded verification ∩ audit/correlation; structural tests prove no write path bypasses the guarded executor;
- ambiguous dispatch ⇒ `outcome_unknown`, never replayed; dispatch-boundary cases all tested;
- zero mutation Tools in every effective inventory;
- ArtifactStore atomic publish, crash recovery at every split point, quotas, TTL cleanup, recovery retention lock, metadata persistence proven (unit) and exercised live (export + download integrity);
- ZIP safety + `pcf1` fingerprint with golden vector; fingerprint stable on each live row, or instability recorded honestly;
- D16 transaction machinery exercised live on a disposable project (no-op, commit, conflict) under L5 guards, with recovery artifact persisted and audit phases present;
- central invocation lifecycle covers every Tool; AuditSink + operation records persisted and bounded; `operation_diagnose` live-verified;
- sensitive-export gate and principal-scoped artifact access enforced in discovery and at call time;
- `setup-native doctor/plan/verify` live-verified and wheel-installed-tested; exact inventories;
- deterministic Runtime release ZIP + manifest + SHA-256 built twice identically in CI; deployed ZIP hash recorded;
- 8.3.8 required and 8.3.9 candidate rows produce schema-valid evidence; no tuple is `SUPPORTED`;
- all CI commands green; G0/G1/G2 workflows still pass on the branch head.

Phase 3 ends at G3. Phase 4 starts only on a new user-directed feature branch.

## Open questions

(Record here any decided rule that cannot be met as written; stop the affected slice and ask the owner.)

- **Owner-accepted deviation (2026-09-21): `phase3-live` has no protection rules.** D23 places L5 mutation jobs in a *protected* environment. The owner reviewed the environment and chose to keep it without required reviewers, wait timer or deployment-branch restriction (repository is public; admins may bypass). Compensating controls that remain mandatory: the `pull_request` trusted-repo job guard (no fork execution), no repository/environment secrets consumed by the G3 job, localhost/compose-network-only Gateway endpoints, CI-marker + expected-Gateway-identity check and run-unique disposable project immediately before any import, and fail-closed on any missing guard. G3 evidence must record this deviation; adding required reviewers later needs no plan change.

- **Deferred to Phase 4 (not a Phase 3 blocker):** how `static-token` and `auth=none` deployments may obtain `ignition.config` / `ignition.control` / `ignition.admin` for mutations. Phase 3 keeps them read-only; D07 is unchanged.

## Execution log

Commit SHAs are recorded by the next slice's commit (a commit cannot contain its own SHA); Slice 12 fills any final row.

| Slice | Commit | Validation |
|---|---|---|
| 0 | `a9f7110` | full validation suite green |
| 1 | `6cdb56b` | full validation suite green (162 tests) |
| 1+ | `3663017` | owner/planner doc commit: phase3-live environment created (no protection rules, owner-accepted deviation recorded) |
| 2 | `dcaf42d` | full validation suite green (201 tests) |
| 3 | `c17d1b5` | full validation suite green (253 tests) |
| 4 | `cb0ca2e` | full validation suite green (270 tests) |
| 5 | `5b03c04` | full validation suite green (288 tests) |
| 6 | `eac3b5a` | full validation suite green (328 tests; executor claimed-state semantics tightened to plan 7.6 during test iteration) |
| 7 | `6e7035f` | full validation suite green (360 tests) |
| 8 | `a81ba12` | full validation suite green (370 tests) |

## Handoff status (2026-09-21, context-window boundary — continue from here in a fresh omp)

Binding inputs for the continuing agent: this runbook (execute slices strictly in order),
`/tmp/claude-1000/-home-sheon-Projects-ignition-mcp-2/4166e223-c7f6-496c-9193-7924c6d66e51/scratchpad/omp-brief.txt`
(execution rules 1–7), `AGENTS.md`, and the cited decisions. Branch
`feature/phase-3-artifact-project-foundations`; never work on main; never merge.

### Done and committed (each ended with the FULL validation suite green)

Slice 0 `a9f7110` contracts/INDEX · 1 `6cdb56b` storage+lifecycle+audit · 2 `dcaf42d` ArtifactStore ·
3 `c17d1b5` zip_safety+pcf1 (golden vector committed) · 4 `cb0ca2e` HTTP data plane · 5 `5b03c04` streamed
download + exports · 6 `eac3b5a` D08 chain + executor (structural tests: dispatch_write/VerifiedPrincipal
confined; zero mutation tools; 16-Tool inventory) · 7 `6e7035f` D16 transactions (all states, restart
reconcile) · 8 `a81ba12` artifact/diagnose Tools + storage diagnostics · 9 `40ecc81` tooling/compat +
deterministic release (BUNDLE_VERSION 0.2.0, RESOURCE_SCHEMA_VERSION 1, marker line in project.json,
__BUNDLE_SOURCE_REVISION__ token stamped at archive time; testedTuples never SUPPORTED).

### UNCOMMITTED — Slice 10 (verification REQUIRED before commit)

`cli/setup_native/` (doctor|plan|verify, no apply/install-module), package pyproject console script
`ignition-mcp`, README section, `tests/test_phase3_setup_native.py`, `tests/test_phase3_setup_native_wheel.py`
were produced by a subagent and are NOT yet independently verified. Steps:
1. Read `cli/setup_native/*` critically; then run the ENTIRE block below. Fix failures.
2. Confirm: manifest-only inputs (no repo-relative runtime paths), 0600 token-file enforcement, secrets
   never in output, exit codes 0/1/2/3, plan ends with `No changes have been applied.`, BLOCKED on
   UNMANAGED_SAME_NAME/MARKER_INVALID, D21 UNKNOWN-vs-UNTESTED mapping, exact inventory (superset=FAIL).
3. Commit `feat(cli): setup-native doctor/plan/verify (read-only, manifest-driven)`; append
   `| 9 | 40ecc81 | … |` and `| 10 | … | … |` rows to the Execution log table.

### Remaining: Slice 11 then 12

Slice 11: `tests/harness/phase3-live/` (compose modeled on phase2-live: exact images 8.3.8 required +
8.3.9 candidate, module checksum-pinned, minimal GATEWAY_MODULES_ENABLED whitelist, bounded diagnostics
with `timeout` + detached stdin, symlink pruning, unconditional teardown). Deploy the exact
`release`-built ZIP (record its SHA-256 + manifest in evidence); ignition-rest in `jwt` auth
(harness-generated RS256 keypair, env IGNITION_MCP_JWT_PUBLIC_KEY PEM, issuer/audience),
IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true, IGNITION_MCP_DATA_DIR=$RUNNER_TEMP/…,
CONFIG_MUTATION enabled with operation/target allowlists naming ONLY project_import + the run-unique
disposable project `mcp_g3_<run_id>_<attempt>`, PROJECT_WRITER_ENABLED=true + GATEWAY_ID.
In-process driver exercises: doctor/plan/verify, project_export→GET/HEAD integrity+parity,
artifact_list/info, operation_diagnose same-principal + unknown-ID not_found, fingerprint stability
(2 exports no change), tag_config_export, authz denials (invalid JWT / read-only JWT / config JWT to
non-allowlisted project → decision rows, Gateway unchanged), then transactions NO_CHANGE / COMMITTED /
CONFLICTED (+ recovery lock + audit triples). Add `tooling/compat` evidence GENERATOR writing schema-valid
`tests/compatibility/evidence/g3-<gateway>-mcp-<build>/` incl. ownerAcceptedDeviations
["phase3-live-environment-protection"], fingerprintStability, deployedBundleSha256.
Workflow `.github/workflows/phase3-live-g3.yml`: `pull_request` + trusted-repo job guard + `environment:
phase3-live` (exists, NO protection rules — owner-accepted deviation, keep; record in evidence).
Push branch, `gh pr create --draft` (gh is authenticated as sheon-sek; repo
github.com/sheon-sek/ignition-mcp; LOCAL docker daemon is NOT running — live runs only on GH Actions; a
user's real Ignition occupies 127.0.0.1:8088 on this machine — never bind/assume it in tests).
Monitor run; commit resulting evidence rows; never merge.

Slice 12: runbook results (evidence links, run IDs, ZIP SHA-256, lessons), package README ALL new
IGNITION_MCP_* vars (DATA_DIR mandatory; TOOL/QUERY/ARTIFACT timeouts; AUDIT_*/OPERATION_RECORD_*/
RETENTION_*/STORAGE_PROBE_*; ARTIFACT_MAX_BYTES/TOTAL_BYTES/MAX_COUNT/MIN_FREE_BYTES/MIN_FREE_RATIO/
EXPORT_TTL_HOURS/RECOVERY_TTL_DAYS/STAGING_DEADLINE_SECONDS/CLEANUP_INTERVAL_SECONDS/CLEANUP_BATCH/
UPLOAD_ENABLED; SENSITIVE_EXPORTS_ENABLED; CONFIG/CONTROL/ADMIN_MUTATION_ENABLED; MUTATION_OPERATIONS;
MUTATION_TARGETS; GATEWAY_ID; PROJECT_WRITER_ENABLED; PROJECT_LOCK_TIMEOUT_SECONDS/
PROJECT_LOCK_MAX_ENTRIES/PROJECT_RECONCILE_INTERVAL_SECONDS/PROJECT_VERIFICATION_TIMEOUT_SECONDS/
PROJECT_DESIGNER_POLICY; setup-native: IGNITION_MCP_SETUP_GATEWAY_URL/MCP_URL/GATEWAY_TOKEN/MCP_TOKEN),
single-writer obligation, data plane, bundle README release artifacts, INDEX resume point → G3 closed,
final Execution-log rows, draft PR body per brief rule 5.

### Validation command block (run after EVERY slice; slice 9+ adds the last four)

```bash
uv lock --check
uvx --from ruff==0.16.8 ruff check .
uv run --locked --package ignition-rest-mcp --with mypy==2.3.1 mypy packages/ignition-rest-mcp/src tooling
uv run --locked --package ignition-rest-mcp --with pytest==9.1.1 pytest -q tooling packages/ignition-rest-mcp/tests
uv run --no-sync python -m tooling.contracts.lint
uv run --no-sync python -m tooling.native.cli validate --project-dir packages/ignition-runtime-bundle/project
uv run --no-sync python -m tooling.native.cli build --project-dir packages/ignition-runtime-bundle/project --output dist/runtime.zip
uv build --package ignition-rest-mcp
uv run --no-sync python -m tooling.compat validate --evidence-dir tests/compatibility/evidence
uv run --no-sync python -m tooling.native.cli release --project-dir packages/ignition-runtime-bundle/project --out-dir dist/release-a --source-revision "$(git rev-parse HEAD)" --evidence-dir tests/compatibility/evidence
uv run --no-sync python -m tooling.native.cli release --project-dir packages/ignition-runtime-bundle/project --out-dir dist/release-b --source-revision "$(git rev-parse HEAD)" --evidence-dir tests/compatibility/evidence
for f in dist/release-a/*; do cmp "$f" "dist/release-b/$(basename "$f")"; done
```

### Environment/tooling facts learned (do not rediscover)

- Commit trailer style: end every commit with `Co-Authored-By: open-router/@preset/qwen-38-flash <noreply@oh-my-pi.local>` (repo style uses a Co-Authored-By model-attribution line).
- This harness session REDACTS literal `Bearer <token>` strings inside tool-call content — in tests build
  such strings at runtime (`"Bearer " + token`) or they arrive as `***`.
- chmod on an already-open SQLite file does NOT break its writes — inject storage failures by
  monkeypatching `SqliteAuditSink.write` or flipping `Database._healthy`/capturing via patched
  `Database.open`, not file mutation.
- httpx MockTransport pre-reads streaming request bodies (masks dispatch phase flags); see
  `_LazyMockTransport` in test_phase3_safety_executor.py.
- FastMCP in-memory `Client.call_tool(...).data` returns MODEL objects (attribute access), not dicts;
  expected-failure calls need `raise_on_error=False`.
- httpx ASGITransport buffers responses: mid-stream disconnect is proven via the `_stream` unit tests +
  will be proven live in slice 11.
- `OperationContext` has `auditor`; lifecycle `invoke_tool(..., audited=True)` is the only auditor source
  for Tools; data-plane routes implement ordering themselves (artifacts/routes.py).
- Settings gained many required fields; the tests/test_config.py `_settings()` helper must list ALL of
  them on every future Settings change.
- Jython runtime bundle untouched so far this phase (slice 10+11 only touch bundle source at slice 11's
  deployed-ZIP step; bundle files were changed at slice 9: handler marker/revision, 0.2.0).

G3 acceptance stays exactly as the runbook lists it; Phase 3 ends at G3; Phase 4 requires a new
user-directed branch.
