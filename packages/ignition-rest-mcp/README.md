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
| `--bundle-manifest PATH` | — (required) | Release manifest JSON: the whole desired state, including the profile inventories and `testedTuples` |
| `--bundle-zip PATH` | — | Bundle ZIP; its SHA-256 must equal `artifact.sha256` or the run fails as a usage error |
| `--profile NAME` | — | `readonly` (default), `operator`, `configurator` or `full` inventory to check against |
| `--gateway-url URL` | `IGNITION_MCP_SETUP_GATEWAY_URL` | Gateway base URL |
| `--mcp-url URL` | `IGNITION_MCP_SETUP_MCP_URL` | Runtime MCP endpoint URL (required by `doctor` and `verify`; `plan` never touches that plane) |
| `--gateway-token-file PATH` | `IGNITION_MCP_SETUP_GATEWAY_TOKEN` | Ignition API token; the file must be a regular, non-symlink `0600` file holding one line |
| `--mcp-token-file PATH` | `IGNITION_MCP_SETUP_MCP_TOKEN` | Optional bearer token for a `secured`/`static-token` endpoint |
| `--bundle-project NAME` | — | Project the bundle deploys into (default `ignition_runtime`) |
| `--server-config-name NAME` | — | Expected MCP server-config resource; presence only, never written |
| `--timeout-seconds N` | — | Per-request HTTP budget (default 10; `initialize` is bounded at 30) |
| `--allow-insecure-authorize` | — | Permit sending the API token over plain HTTP to a non-loopback Gateway |
| `--json` | — | Machine-readable report on stdout (`plan` still ends with the promise line) |

Tokens are never echoed: they stay out of reports, and any Gateway or MCP error body is truncated
and scrubbed. Plain HTTP to a non-loopback Gateway is refused until `--allow-insecure-authorize` is
passed. Gateway probes are bounded GETs with redirects disabled (1 MiB per JSON response, 16 MiB
for `/openapi.json`, which is hashed and scanned for path keys only); MCP capability presence is
read from the OpenAPI path inventory, so write routes are never called. `initialize` gets exactly
one attempt — `doctor` and `verify` diagnose, they do not wait for a starting Gateway (that
readiness waiting belongs to the live harness).

Commands: `doctor` runs the ordered read-only checks (`gateway-info`, `openapi-sha256`,
`module-installed`, `capabilities.*`, `bundle-project` ownership classification,
`server-config-presence`, `mcp-initialize`, exact `inventory-tools`/`inventory-resources`/
`inventory-prompts`, `bundle-info`, `compatibility`), `plan` reports `CREATE` / `UPDATE` /
`NO CHANGE` / `BLOCKED` intentions including the D21 change class (`patch`, `minor`, `major`,
`downgrade` — the last two need explicit acknowledgement by `apply`), and `verify` runs the D20
acceptance sequence (reachable → `initialize` → exact inventories → `resources/read` and
`prompts/get` smokes → `bundle_info`). Compatibility is mapped deterministically from
`testedTuples` and is never upgraded: an incomplete identity or an unmatched tuple yields
`UNKNOWN`/`UNTESTED`.

Exit codes: `0` success with no `FAIL` (and no `BLOCKED` for `plan`), `1` a failed check or
transport error, `2` usage error (bad flags, rejected manifest, unreadable artifact, credential
file with a loose mode), `3` `plan` reports at least one `BLOCKED` action.
