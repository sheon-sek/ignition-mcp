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
