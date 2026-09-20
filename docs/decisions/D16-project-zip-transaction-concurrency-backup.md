# D16 — Project ZIP Transaction, Concurrency and Backup

**Status:** DECIDED

## Core limitation
Ignition 8.3 Project Import does not provide a revision-conditioned write/CAS primitive such as ETag/If-Match/expected revision.

Therefore the External FastMCP cannot promise a true ACID Project transaction against external Designer/other writers.

D16 provides strong best-effort lost-update prevention using optimistic concurrency, writer serialization, durable recovery snapshots, and post-import reconciliation.

## Canonical mutation protocol
```text
Acquire per-Gateway + per-Project MCP writer lock
→ apply Designer-session policy
→ export baseline A
→ compute canonical Project content fingerprint A
→ build candidate B from A
→ no semantic change? return no_change
→ validate B
→ persist durable recovery snapshot A
→ fresh export A'
→ require fingerprint(A') == fingerprint(A)
→ POST Project Import B with overwrite
→ never blind-retry ambiguous mutation
→ fresh export C
→ reconcile/verify result
```

## MCP writer concurrency
All Project ZIP mutations for the same Gateway + Project share one exclusive writer lock.

Different Projects may mutate concurrently.

v1 deployment rule for scale-out:
- one active Project-mutating writer instance per Gateway;
- additional replicas may be read-only for Project mutation.

A distributed lock provider may be added later without changing the Tool contract.

## Designer session policy
Active Designer sessions for the target Project are an additional safety signal.

Support server-side policy:
- `deny`;
- `warn`;
- `ignore`.

Production default: `deny`.

The Agent cannot override this per call.

This guard reduces risk but does not eliminate the final time-of-check/time-of-use window.

## Canonical Project fingerprint
Do not use `sha256(project.zip)` as the logical Project version.

Define a versioned Project content fingerprint:
- validate archive;
- canonicalize/sort entry paths;
- hash canonical path + uncompressed content bytes for all entries;
- ignore ZIP-container representation details only.

Do not initially exclude `resource.json` or other unknown content.

Any future normalization of Gateway-generated volatile fields must be explicit, versioned, and proven by live integration tests.

## Fingerprint stability CI
Live Gateway CI must verify:
```text
export A
(no change)
export A again
→ same logical content fingerprint
```

If a supported Gateway version cannot produce a stable fingerprint under defined normalization, Project mutation must not pretend concurrency detection is reliable.

## Durable pre-import recovery snapshot
Before overwrite import:
- baseline A must be durably persisted;
- artifact metadata must include correlation/transaction linkage and integrity information.

If backup persistence fails, abort before mutation.

Do not use `/tmp` or other ephemeral storage as the only recovery copy.

D17 defines artifact storage/retention.

## Mandatory pre-import re-export
Immediately before import, export current A' and compare against baseline A.

If different:
```text
concurrent_modification
import_attempted = false
```

Do not automatically merge external changes.

v1 performs no automatic three-way Project/Perspective merge.

## Import retry policy
Project Import is a mutation and must not be blindly retried after timeout, connection loss, uncertain 5xx, or other ambiguous response.

If outcome is ambiguous:
```text
export current C
```

Then reconcile:
- `C == expected B` → recovered success;
- `C == baseline A` → not applied;
- otherwise → `outcome_unknown` / recovery required.

## Post-import verification
HTTP success is not the transaction end.

Always re-export after import and verify:
- target resource state;
- expected semantic content;
- preservation of unrelated resources;
- Project fingerprint/normalized expected state where supported.

## No blind automatic rollback
v1 automatic rollback is OFF.

A verification failure or unknown outcome:
- preserves recovery artifacts;
- stops further mutation;
- returns diagnosable state.

Rollback is itself a new guarded Project mutation:
- acquire same Project lock;
- inspect current state;
- back up current state;
- verify rollback preconditions;
- import previous snapshot;
- verify again.

## No-op idempotency
If candidate state is semantically equal to baseline:
```text
outcome = no_change
```

Do not create unnecessary backup/import operations.

## Transaction state model
Implementation should use an explicit state machine such as:
```text
PREPARING
BASELINE_CAPTURED
BACKUP_PERSISTED
CANDIDATE_VALIDATED
CONCURRENCY_VERIFIED
IMPORT_SENT
VERIFYING
COMMITTED
OUTCOME_UNKNOWN
RECOVERY_REQUIRED
```

With terminal states such as:
- `NO_CHANGE`;
- `CONFLICTED`;
- `FAILED_PRE_IMPORT`.

## IDs
Keep identities distinct:
- `correlationId` = MCP operation;
- `transactionId` = Project mutation transaction;
- `artifactId` = stored artifact.

## Explicit limitation
There remains a final external-write race between the last preflight export and overwrite import because Ignition does not currently expose revision-conditioned Project import.

D16 must document this limitation rather than claiming impossible atomicity.

```yaml
decision: D16
status: DECIDED
concurrency_model: optimistic
project_writer_lock: per_gateway_project
single_active_project_writer_v1: true
designer_policy_default: deny
pre_import_backup_required: true
pre_import_reexport_required: true
blind_import_retry: forbidden
automatic_merge_v1: false
automatic_rollback_v1: false
post_import_reexport_verify: required
true_cas_available: false
```
