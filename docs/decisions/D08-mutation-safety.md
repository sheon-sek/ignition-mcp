# D08 — Mutation Safety Model

**Status:** DECIDED

## Safety chain
```text
Input validation
→ Authentication
→ Authorization scope
→ Deployment mutation policy
→ Operation allowlist
→ Target/resource allowlist
→ Capability check
→ Precondition/concurrency check
→ Execute exactly once
→ Bounded post-operation verification
→ Audit
```

No single flag bypasses the chain.

## Default deployment mode
- Read enabled.
- Config mutation disabled.
- Control mutation disabled.
- Admin mutation disabled.

First deployment is read-only.

## Mutation classes
- `CONFIG_MUTATION`
- `CONTROL_MUTATION`
- `ADMIN_MUTATION`

Each Tool also declares `destructive: true/false`.

## Policy intersection
Caller authorization and deployment policy must both allow the operation.

## Target policy
Deny by default.
Empty allowlist means none.
Allowing all requires explicit wildcard.

Examples: project allowlist, Tag provider/path allowlist, generic config resource/operation allowlist.

## Generic config mutation
Disabled by default. When enabled, require authz, class enablement, resource type and operation allowlists, capability/schema validation, verification, and audit.

## Admin
High-impact admin is curated-only and also requires explicit operation allowlisting. Generic resource mutation cannot be used for Gateway restart, backup restore, license/module/API-token/security/secret-provider administration.

## Confirmation
No universal `confirm=true` or name-echo security parameter.
Human approval may be an optional extra guard, never the base security model.

## Optimistic concurrency
Prefer version/signature/ETag/state-hash preconditions.
Mandatory for destructive config mutation, overwrite/replace, and concurrency-sensitive operations.

## Dry run
No fake universal `dry_run=true`. Tool-specific preview/validation is allowed only when accurately side-effect free.

## Retry/idempotency
- Never automatically replay uncertain mutations.
- v1 has no universal idempotency-key contract.
- Do not fake durable idempotency with volatile cache.
- Treat mutation retry safety as false unless proven.

## Verification
Read back post-state when observable. Verification is bounded by attempts/time/cancellation.
If execution may have occurred but final state cannot be established, return `outcome_unknown`.

## Partial mutation
Preserve per-target outcomes; never claim rollback or atomicity unless guaranteed.

## Audit
Record mutation attempts and outcomes, including denied attempts. Redact secrets.

```yaml
decision: D08
status: DECIDED
default_mutations: disabled
target_policy: deny-by-default
universal_confirm: false
universal_dry_run: false
automatic_mutation_replay: false
post_mutation_verification: true
```
