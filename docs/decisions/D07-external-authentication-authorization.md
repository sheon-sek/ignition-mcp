# D07 — External FastMCP Authentication and Authorization

**Status:** DECIDED

**Amendment:** amended by **D07-A** (HTTP / Internal Trusted Deployment), approved while finalizing D18. D18 carries the authoritative text of D07-A. The production-authentication section below is consolidated with D07-A; it no longer states that every production deployment requires authentication plus TLS-grade transport.

## Independent trust boundaries
```text
AI Client
  │ caller credential
  ▼
ignition-rest
  │ deployment-owned Ignition service credential
  ▼
Ignition Gateway
```
Caller credentials are never forwarded to Ignition.

## Production authentication
Production Streamable HTTP supports two deployment profiles. Transport security, authentication, and authorization are independently configurable concerns; production is defined by explicit configuration, not by a mandatory protocol stack.

### `secured`
For deployments reachable from untrusted networks:
- authentication is required;
- supported modes: JWT verification, Remote OAuth, OAuth/OIDC proxy, MultiAuth where appropriate;
- TLS is expected across untrusted networks.

### `trusted-internal`
For internal OT/IT networks, plain HTTP is a first-class production transport:
```text
HTTP
TLS = off
auth = none | static-token
```
- `auth=none` is allowed when explicitly configured as a trusted-internal production profile, including non-loopback binding;
- static token is a valid simple production authentication mode;
- TLS/OAuth/OIDC/JWT remain supported for deployments that need them.

The project does not implement its own full OAuth Authorization Server in v1.

## JWT
Preferred existing-identity-provider mode:
- asymmetric JWT + JWKS;
- issuer/audience/expiry validation;
- key rotation.

Static public-key verification is supported. HMAC JWT is for controlled internal deployments only.

## Unconfigured / unauthenticated binding
`auth=none` is valid only inside an explicitly configured `trusted-internal` profile.

Outside that profile, unauthenticated non-loopback binding fails closed. `auth=none` must never be the accidental default of a production deployment.

## Authorization scopes
Canonical scopes:
- `ignition.read`
- `ignition.config`
- `ignition.control`
- `ignition.admin`

Meanings:
- read: read-only operations;
- config: persistent configuration mutation;
- control: runtime operational mutation;
- admin: high-impact Gateway administration.

Scopes have **no implicit hierarchy**.

Recommended bundles:
- Reader = read
- Configurator = read + config
- Operator = read + control
- Administrator = read + config + control + admin

Bundles are conveniences, not hidden inheritance.

## Enforcement
- filter unauthorized tools from `tools/list`;
- enforce again at call time;
- centralize authz in middleware;
- do not hand-code scope checks in every handler.

Assign scope by operation effect, not module/domain.

## Generic config
- read generic config → `ignition.read`;
- normal generic write → `ignition.config`;
- high-risk resource write → `ignition.admin`.

## Upstream identity
v1 uses a deployment-owned Ignition service identity. No per-user Ignition token or dynamic impersonation in v1.

External audit records caller subject/client/correlation separately from the Gateway service identity.

```yaml
decision: D07
status: DECIDED
amended_by: D07-A
production_profiles: [secured, trusted-internal]
plain_http_production_supported: true
tls_mandatory: false
secured_profile_authentication_required: true
trusted_internal_auth_none_allowed: true
trusted_internal_static_token_allowed: true
unconfigured_unauthenticated_nonloopback: fail_closed
scopes: [ignition.read, ignition.config, ignition.control, ignition.admin]
scope_hierarchy: false
caller_token_forwarded_to_ignition: false
upstream_identity: service-identity
```

## Pre-D26 consistency amendment (C01)
D07 originally required authentication plus TLS-grade transport for all production deployments, rejected static tokens for production, and allowed `none` only for loopback development. D18's D07-A amendment had already overtaken those clauses, leaving two `DECIDED` documents with opposite production rules.

This file has been consolidated onto D07-A: production is a choice between `secured` and `trusted-internal`, plain HTTP is a first-class production transport, `auth=none | static-token` are valid for explicitly configured trusted-internal deployments, and only unconfigured unauthenticated non-loopback binding fails closed. D18 remains the authoritative text of D07-A; the authorization scopes, generic-config mapping, upstream identity, and enforcement rules above are unchanged.

## Phase 4 amendment — Mutation principals (2026-09-22)

**Approved by the project owner during the Phase 4 scoping interview.** This resolves the question Phase 3 deferred: how `static-token` and `auth=none` deployments obtain mutation scopes.

- `static-token` supports several **named** static tokens. Each token has its own deployment-configured scope set, drawn from the four canonical scopes, with no hierarchy. The token's name is its Mutation principal in audit and operation records. The token value is never logged or recorded.
- `auth=none` has no principal. Its effective scopes stay `ignition.read` only, so it can never mutate.
- `jwt` is unchanged: scopes come from the verified token claims.
- The authentication module remains the only constructor of a verified principal (Phase 3 G3 rule).

```yaml
static_token_named_tokens: true
static_token_scopes: per_token_configured
auth_none_scopes: [ignition.read]
```
