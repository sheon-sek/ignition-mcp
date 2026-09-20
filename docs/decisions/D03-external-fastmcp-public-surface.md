# D03 — External FastMCP Public Surface Model

**Status:** DECIDED

## Decision
`ignition-rest` uses a **hybrid public surface**:
1. curated semantic tools;
2. controlled generic configuration-resource tools;
3. MCP resources for capability/schema/artifact metadata.

It does **not** expose every OpenAPI endpoint as a Tool and does **not** expose arbitrary HTTP request execution.

## Curated semantic tools
Primary Agent-facing tools such as `gateway_info`, `project_list`, `project_import`, `audit_query`, `perspective_session_terminate`.

Curated tools own semantic validation, pagination, normalization, authorization, capability checks, safe defaults, mutation guards, and verification.

## Controlled generic configuration layer
Canonical tools:
- `config_resource_search`
- `config_resource_describe`
- `config_resource_names`
- `config_resource_list`
- `config_resource_get`
- `config_resource_create`
- `config_resource_update`
- `config_resource_delete`
- `config_resource_rename`

Constraints:
- `resourceType` must be discovered from the Gateway capability/resource registry.
- Caller never supplies arbitrary REST path.
- Generic reads enabled by default when supported.
- Generic writes implemented but disabled by default.
- Writes require authz, deployment policy, resource/operation allowlists, OpenAPI operation/schema validation, mutation guards, audit, and verification.
- High-impact admin remains curated-only.

## MCP resources
Examples:
```text
ignition://gateway/capabilities
ignition://gateway/openapi-info
ignition://config/resource-types
ignition://config/resource-type/{type}
ignition://artifacts/{artifactId}
```

## OpenAPI role
Use OpenAPI for capability detection, endpoint/schema validation, resource-family discovery, and integration tests; not for generating hundreds of endpoint-shaped tools.

## Forbidden escape hatch
No `rest_request(method, path, body)` or equivalent.

```yaml
decision: D03
status: DECIDED
model: hybrid
openapi_expose_all_endpoints_as_tools: false
generic_rest_request: false
generic_config_reads_default: true
generic_config_writes_default: false
```
